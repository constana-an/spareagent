#!/usr/bin/env python3
"""Week 2 end-to-end demonstration with three real remote worker containers.

1. Start API (HTTP + HTTPS), PostgreSQL, Redis, scheduler; stop the in-process local worker.
2. Mint three single-use enrollment tokens (admin) and start three `spareagent-worker`
   containers that register over HTTPS with the dev CA and no database credentials.
3. Submit ten tasks; verify distribution.
4. SIGKILL remote-worker-2 while it is executing; the lease expires and the task is retried.
5. Restart remote-worker-2 (same persistent node identity) and gracefully stop worker 3.
6. Verify: all tasks terminal, no duplicate finalization / usage / artifacts, no secrets in logs.

Writes reports/week2_e2e_report.json and exits non-zero if any check fails. Host needs only
python3 + docker compose (stdlib only).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("BASE_URL", "http://localhost:8000")
ADMIN = "55555555-5555-4555-8555-555555555555"
CONSUMER = "11111111-1111-4111-8111-111111111111"
RESOURCE = "33333333-3333-4333-8333-333333333333"
LEASE_SECONDS = int(os.environ.get("DEMO_LEASE_SECONDS", "8"))
STEP_DELAY = os.environ.get("DEMO_MOCK_STEP_DELAY", "2")
WORKERS = ["remote-worker-1", "remote-worker-2", "remote-worker-3"]
SECRET_RE = re.compile(r"sa_(?:enr|nk|at)_[A-Za-z0-9_\-]{16,}")

checks: list[dict] = []
env = {**os.environ, "LEASE_SECONDS": str(LEASE_SECONDS), "MOCK_STEP_DELAY": STEP_DELAY}


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {msg}", flush=True)


def compose(*args: str, check: bool = True, extra_env: dict | None = None) -> str:
    proc = subprocess.run(
        ["docker", "compose", "--profile", "remote", *args],
        cwd=ROOT,
        env={**env, **(extra_env or {})},
        capture_output=True,
        text=True,
        timeout=600,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{proc.stderr[-2000:]}")
    return proc.stdout


def sql(query: str) -> list[list[str]]:
    out = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "spareagent",
        "-d",
        "spareagent",
        "-At",
        "-F",
        "|",
        "-c",
        query,
    )
    return [line.split("|") for line in out.strip().splitlines() if line]


def scalar(query: str) -> str:
    rows = sql(query)
    return rows[0][0] if rows else ""


def api(method: str, path: str, user: str, body: dict | None = None, key: str | None = None):
    if not BASE.startswith(("http://localhost", "http://127.0.0.1")):
        raise SystemExit("demo talks to the local dev API only")
    req = urllib.request.Request(BASE + path, method=method)  # noqa: S310 - checked above
    req.add_header("X-Dev-User-ID", user)
    if key:
        req.add_header("Idempotency-Key", key)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=30) as resp:  # noqa: S310 - localhost only
        return resp.status, json.loads(resp.read() or b"null")


def check(name: str, ok: bool, detail=None) -> bool:
    checks.append({"check": name, "passed": bool(ok), "detail": detail})
    log(f"{'PASS' if ok else 'FAIL'} {name}" + (f" -> {detail}" if detail is not None else ""))
    return ok


def wait_for(predicate, timeout: float, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def main() -> int:
    started = datetime.now(UTC)
    log(f"bringing up stack (LEASE_SECONDS={LEASE_SECONDS}, mock step delay={STEP_DELAY}s)")
    compose("build", "api")
    compose("rm", "-sf", *WORKERS, check=False)
    for i in (1, 2, 3):
        subprocess.run(
            ["docker", "volume", "rm", "-f", f"spareagent_worker{i}-state"],
            capture_output=True,
            check=False,
        )
    compose("up", "-d", "--wait", "postgres", "redis", "api", "api-tls")
    compose("up", "-d", "scheduler")
    compose("exec", "-T", "api", "python", "scripts/migrate.py")
    compose("exec", "-T", "api", "python", "scripts/seed_dev.py")
    compose("stop", "worker")  # remote workers only for this demonstration
    # A sandbox-capable node left over from `make e2e-code` would also (legitimately) claim
    # work; this demonstration is about exactly three remote nodes.
    subprocess.run(
        ["docker", "compose", "--profile", "code", "stop", "code-worker"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
    )
    sql("UPDATE worker_nodes SET last_heartbeat = NULL WHERE name = 'mock-local'")

    tokens = {}
    for i in range(1, len(WORKERS) + 1):
        status, body = api(
            "POST", "/v1/enrollment-tokens", ADMIN, {"resource_id": RESOURCE, "ttl_seconds": 600}
        )
        if status != 201:
            raise SystemExit(f"enrollment token request failed: {status}")
        tokens[f"WORKER{i}_ENROLLMENT_TOKEN"] = body["token"]
    log("minted 3 single-use enrollment tokens; starting remote workers over HTTPS")
    compose("up", "-d", *WORKERS, extra_env=tokens)

    def registered():
        rows = sql(
            "SELECT name, id FROM worker_nodes WHERE name LIKE 'remote-%' AND revoked_at IS NULL "
            f"AND created_at >= '{started.isoformat()}' "
            "AND last_heartbeat > now() - interval '15 seconds'"
        )
        names = {r[0]: r[1] for r in rows}
        return names if len(names) >= 3 else None

    nodes = wait_for(registered, 60)
    check("three remote workers registered and heartbeating", bool(nodes), nodes)
    if not nodes:
        return finish(started)
    status_out = json.loads(
        compose("exec", "-T", "remote-worker-1", "python", "-m", "packages.worker.cli", "status")
    )
    check(
        "`spareagent-worker status` reports server-side identity",
        status_out.get("server", {}).get("worker_id") == nodes["remote-1"]
        and "sa_nk_" not in json.dumps(status_out),
        {k: status_out.get(k) for k in ("worker_id", "name", "running")},
    )
    leaked_env = [
        line
        for w in WORKERS
        for line in compose("exec", "-T", w, "env").splitlines()
        if re.match(r"(DATABASE_URL|REDIS_URL|POSTGRES_|TEST_DATABASE|NODE_TOKEN_PEPPER)", line)
    ]
    check("remote workers hold no database/Redis/platform credentials", not leaked_env, leaked_env)
    check(
        "enrollment tokens are single use",
        scalar(
            "SELECT count(*) FROM enrollment_tokens WHERE used_at IS NOT NULL AND worker_id IN ("
            + ",".join(f"'{v}'" for v in nodes.values())
            + ")"
        )
        == "3",
    )

    run_tag = started.strftime("%Y%m%d%H%M%S")
    task_ids = []
    for i in range(10):
        status, body = api(
            "POST",
            "/v1/tasks",
            CONSUMER,
            {
                "task_type": "CODE_FIX",
                "repository_url": "https://github.com/example/public-fixture",
                "commit_sha": "0123456789abcdef0123456789abcdef01234567",
                "prompt": f"Week 2 distributed demo task {i}",
                "max_budget_cents": 500,
                "max_duration_seconds": 600,
            },
            key=f"w2-demo-{run_tag}-{i:02d}",
        )
        task_ids.append(body["id"])
    log("submitted 10 tasks")
    id_list = ",".join(f"'{t}'" for t in task_ids)

    victim = nodes["remote-2"]
    running = wait_for(
        lambda: sql(
            f"SELECT id, task_id FROM task_attempts WHERE worker_id='{victim}' "
            f"AND status='RUNNING' AND task_id IN ({id_list})"
        ),
        60,
        0.2,
    )
    check("remote-worker-2 is executing a task before the disconnect", bool(running), running)
    if not running:
        return finish(started, task_ids)
    killed_attempt, killed_task = running[0]
    compose("kill", "-s", "SIGKILL", "remote-worker-2")
    log(f"SIGKILLed remote-worker-2 mid-execution (attempt {killed_attempt})")

    expired = wait_for(
        lambda: scalar(f"SELECT status FROM task_attempts WHERE id='{killed_attempt}'")
        == "EXPIRED",
        LEASE_SECONDS + 30,
    )
    check("killed worker's lease expired (no heartbeat/renewal)", bool(expired))
    compose("start", "remote-worker-2")
    log("restarted remote-worker-2 with its persistent node identity")
    reconnected = wait_for(
        lambda: scalar(
            f"SELECT count(*) FROM worker_nodes WHERE id='{victim}' "
            "AND last_heartbeat > now() - interval '5 seconds'"
        )
        == "1",
        60,
    )
    check("remote-worker-2 reconnected as the same node (no re-enrollment)", bool(reconnected))
    check(
        "only three remote nodes exist (identity persisted)",
        scalar(
            "SELECT count(*) FROM worker_nodes WHERE name LIKE 'remote-%' "
            f"AND created_at >= '{started.isoformat()}'"
        )
        == "3",
    )

    def all_terminal():
        rows = sql(f"SELECT status FROM tasks WHERE id IN ({id_list})")
        done = [r[0] for r in rows if r[0] in ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT")]
        return rows if len(done) == 10 else None

    final = wait_for(all_terminal, 180, 1.0)
    statuses = {}
    for (s,) in final or sql(f"SELECT status FROM tasks WHERE id IN ({id_list})"):
        statuses[s] = statuses.get(s, 0) + 1
    check("all 10 tasks SUCCEEDED", statuses == {"SUCCEEDED": 10}, statuses)

    killed_final = scalar(f"SELECT status FROM tasks WHERE id='{killed_task}'")
    killed_attempts = sql(
        f"SELECT a.attempt_number, a.status, w.name FROM task_attempts a "
        f"JOIN worker_nodes w ON w.id=a.worker_id WHERE a.task_id='{killed_task}' "
        "ORDER BY a.attempt_number"
    )
    check(
        "affected task recovered by retry on another attempt",
        killed_final == "SUCCEEDED" and [r[1] for r in killed_attempts] == ["EXPIRED", "SUCCEEDED"],
        killed_attempts,
    )
    distribution = dict(
        sql(
            "SELECT w.name, count(*) FROM task_attempts a JOIN worker_nodes w "
            f"ON w.id=a.worker_id WHERE a.status='SUCCEEDED' AND a.task_id IN ({id_list}) "
            "GROUP BY w.name ORDER BY w.name"
        )
    )
    claimed_by = dict(
        sql(
            "SELECT w.name, count(*) FROM task_attempts a JOIN worker_nodes w "
            f"ON w.id=a.worker_id WHERE a.task_id IN ({id_list}) GROUP BY w.name ORDER BY w.name"
        )
    )
    check(
        "work distributed: all three workers claimed work, >=2 completed it",
        len(claimed_by) == 3
        and len(distribution) >= 2
        and sum(int(v) for v in distribution.values()) == 10,
        {"succeeded_by_worker": distribution, "attempts_by_worker": claimed_by},
    )
    dup_final = scalar(
        "SELECT count(*) FROM (SELECT task_id FROM execution_events WHERE event_type IN "
        "('task.succeeded','task.failed','task.cancelled','task.timed_out') "
        f"AND task_id IN ({id_list}) GROUP BY task_id HAVING count(*) <> 1) d"
    )
    check("exactly one terminal event per task (no duplicate finalization)", dup_final == "0")
    dup_success = scalar(
        "SELECT count(*) FROM (SELECT task_id FROM task_attempts WHERE status='SUCCEEDED' "
        f"AND task_id IN ({id_list}) GROUP BY task_id HAVING count(*) > 1) d"
    )
    check("at most one successful attempt per task", dup_success == "0")
    usage = sql(
        "SELECT count(*), count(DISTINCT task_attempt_id), coalesce(sum(estimated_cost_cents),0) "
        "FROM usage_records u JOIN task_attempts a ON a.id=u.task_attempt_id "
        f"WHERE a.task_id IN ({id_list})"
    )[0]
    check(
        "one usage record per finalized attempt (no double billing)",
        usage[0] == usage[1] == "10",
        {"rows": usage[0], "distinct_attempts": usage[1], "estimated_cents": usage[2]},
    )
    artifacts = scalar(f"SELECT count(*) FROM artifacts WHERE task_id IN ({id_list})")
    killed_artifacts = scalar(f"SELECT count(*) FROM artifacts WHERE attempt_id='{killed_attempt}'")
    check(
        "40 artifacts (4 per task), none from the killed attempt",
        artifacts == "40" and killed_artifacts == "0",
        {"total": artifacts, "from_killed_attempt": killed_artifacts},
    )
    capacity = scalar(
        "SELECT coalesce(sum(active_tasks),0) FROM worker_nodes WHERE name LIKE 'remote-%'"
    )
    check("all worker capacity released", capacity == "0", capacity)

    # `stop` signals the worker (PID 1 of its container), so the exec session ends with it.
    compose(
        "exec", "-T", "remote-worker-3", "python", "-m", "packages.worker.cli", "stop", check=False
    )
    offline = wait_for(
        lambda: scalar(f"SELECT status FROM worker_nodes WHERE id='{nodes['remote-3']}'")
        == "OFFLINE",
        30,
    )
    check("`spareagent-worker stop` shuts down gracefully and reports OFFLINE", bool(offline))

    logs = compose("logs", "--no-color", "api", "api-tls", "scheduler", *WORKERS)
    raw_tokens = [t for t in tokens.values() if t in logs]
    pattern_hits = SECRET_RE.findall(logs)
    check(
        "no credentials in API, scheduler or worker logs",
        not raw_tokens and not pattern_hits,
        {"raw_token_hits": len(raw_tokens), "pattern_hits": len(pattern_hits)},
    )
    return finish(started, task_ids, distribution=distribution, nodes=nodes)


def finish(started, task_ids=None, **extra) -> int:
    compose("start", "worker", check=False)  # restore the default dev stack
    passed = all(c["passed"] for c in checks) and bool(checks)
    report = {
        "report": "spareagent-week2-remote-workers-e2e",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "lease_seconds": LEASE_SECONDS,
        "mock_step_delay_seconds": float(STEP_DELAY),
        "transport": "https://api-tls:8443 (dev CA)",
        "workers": WORKERS,
        "task_ids": task_ids or [],
        "summary": {
            "passed": passed,
            "checks_total": len(checks),
            "checks_failed": sum(not c["passed"] for c in checks),
        },
        "checks": checks,
        **extra,
    }
    out = ROOT / "reports" / "week2_e2e_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    log(f"report written to {out.relative_to(ROOT)}: {'PASSED' if passed else 'FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
