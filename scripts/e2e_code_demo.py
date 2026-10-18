#!/usr/bin/env python3
"""Week 3 end-to-end demonstration: sandboxed deterministic code fix + independent verification.

Real containers: API (HTTP + HTTPS), PostgreSQL, Redis, scheduler, platform `verifier`, and a
sandbox-capable `code-worker` node running `spareagent-worker start --executor code`.

Happy path (python-calc and ts-strings fixtures): submit -> assign -> pinned checkout ->
allowlisted edit -> tests in sandbox -> patch -> independent verification -> SUCCEEDED ->
download + hash-check artifacts -> no sandbox containers left.

Failure demos: (a) isolation probe repository whose tests try to read host secrets / reach the
network (passes only if blocked); (b) a forged submission (path-traversal patch + fake passing
report) from a malicious node -> verification FAILED, never published -> retry by the honest
node -> SUCCEEDED.

Writes reports/week3_e2e_report.json; exits non-zero if any check fails. Stdlib only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8000"
ADMIN = "55555555-5555-4555-8555-555555555555"
CONSUMER = "11111111-1111-4111-8111-111111111111"
RESOURCE = "33333333-3333-4333-8333-333333333333"
FIXTURES = {
    "python-calc": "44d957e53add3b73f7eaaa9755b1525ff32bb8c3",
    "ts-strings": "115e49e8bffba58e74c029279a772fde67ffe9a6",
    "python-probe": "5ba6b859e9b8819a030de9f54a80c5c815f42e48",
}
SECRET_RE = re.compile(r"sa_(?:enr|nk|at)_[A-Za-z0-9_\-]{16,}")


def docker_gid() -> str:
    try:
        return str(os.stat("/var/run/docker.sock").st_gid) if sys.platform == "linux" else "0"
    except OSError:
        return "0"


ENV = {
    **os.environ,
    "CODE_EXECUTION_ENABLED": "true",
    "CODE_EXECUTION_RESOURCE_IDS": RESOURCE,
    "DOCKER_GID": docker_gid(),
}
checks: list[dict] = []


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {msg}", flush=True)


def compose(*args: str, check: bool = True, extra: dict | None = None) -> str:
    proc = subprocess.run(
        ["docker", "compose", "--profile", "code", *args],
        cwd=ROOT,
        env={**ENV, **(extra or {})},
        capture_output=True,
        text=True,
        timeout=900,
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


def http(method: str, path: str, *, user=None, token=None, body=None, key=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method)  # noqa: S310 - localhost only
    if user:
        req.add_header("X-Dev-User-ID", user)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if key:
        req.add_header("Idempotency-Key", key)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=60) as resp:  # noqa: S310
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"null"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def check(name: str, ok: bool, detail=None) -> bool:
    checks.append({"check": name, "passed": bool(ok), "detail": detail})
    log(f"{'PASS' if ok else 'FAIL'} {name}" + (f" -> {detail}" if detail is not None else ""))
    return ok


def wait_for(fn, timeout: float, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def sandboxes() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=spareagent.sandbox=1", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return out.split()


def submit(name: str, tag: str) -> str:
    status, body = http(
        "POST",
        "/v1/tasks",
        user=CONSUMER,
        key=f"w3-{tag}-{name}",
        body={
            "task_type": "CODE_FIX",
            "repository_url": f"https://github.com/spareagent-fixtures/{name}",
            "commit_sha": FIXTURES[name],
            "prompt": f"Fix the failing test in {name}",
            "max_budget_cents": 500,
            "max_duration_seconds": 600,
        },
    )
    assert status == 202, body  # noqa: S101 - demo harness
    return body["id"]


def task(task_id: str) -> dict:
    return http("GET", f"/v1/tasks/{task_id}", user=CONSUMER)[1]


def wait_terminal(task_id: str, timeout: float = 240) -> dict:
    return wait_for(
        lambda: (t := task(task_id))["status"] in ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT")
        and t,
        timeout,
        1.0,
    ) or task(task_id)


def artifacts(task_id: str) -> dict[str, dict]:
    items = http("GET", f"/v1/tasks/{task_id}/artifacts", user=CONSUMER)[1]["items"]
    out = {}
    for item in items:
        status, content = http(
            "GET", f"/v1/tasks/{task_id}/artifacts/{item['id']}/content", user=CONSUMER, raw=True
        )
        out[item["kind"]] = {
            **item,
            "content": content,
            "hash_ok": hashlib.sha256(content).hexdigest() == item["sha256"],
        }
    return out


def main() -> int:
    started = datetime.now(UTC)
    tag = started.strftime("%Y%m%d%H%M%S")
    log("building images (app + sandbox)")
    compose("build", "api")
    subprocess.run(
        ["docker", "compose", "--profile", "build-only", "build", "sandbox-image"],
        cwd=ROOT,
        env=ENV,
        check=True,
        capture_output=True,
    )
    compose("rm", "-sf", "code-worker", check=False)
    subprocess.run(
        ["docker", "volume", "rm", "-f", "spareagent_code-worker-state"],
        capture_output=True,
        check=False,
    )
    compose("up", "-d", "--wait", "postgres", "redis", "api", "api-tls")
    # Every platform process must run the current image (a stale pre-W3 local worker would
    # otherwise claim CODE tasks; the DB trigger still prevents an unverified success).
    compose("up", "-d", "--force-recreate", "scheduler", "worker")
    compose("exec", "-T", "api", "python", "scripts/migrate.py")
    compose("exec", "-T", "api", "python", "scripts/seed_dev.py")
    compose("up", "-d", "verifier")  # waits for fixtures (one-shot) to complete

    status, body = http(
        "POST",
        "/v1/enrollment-tokens",
        user=ADMIN,
        body={"resource_id": RESOURCE, "ttl_seconds": 600},
    )
    compose("up", "-d", "code-worker", extra={"CODE_WORKER_ENROLLMENT_TOKEN": body["token"]})
    node = wait_for(
        lambda: scalar(
            "SELECT id FROM worker_nodes WHERE name='code-worker-1' AND revoked_at IS NULL "
            f"AND created_at >= '{started.isoformat()}' AND capabilities->>'sandbox'='docker' "
            "AND last_heartbeat > now() - interval '15 seconds'"
        ),
        120,
    )
    check("code worker registered over HTTPS with a healthy sandbox", bool(node), node)
    if not node:
        return finish(started)
    leaked = [
        line
        for line in compose("exec", "-T", "code-worker", "env").splitlines()
        if re.match(r"(DATABASE_URL|REDIS_URL|POSTGRES_|NODE_TOKEN_PEPPER)", line)
    ]
    check("code worker holds no database/Redis/platform secrets", not leaked, leaked)

    # ------------------------------------------------------------ happy path (py + ts)
    results = {}
    for name in ("python-calc", "ts-strings"):
        tid = submit(name, tag)
        profile = scalar(f"SELECT execution_profile FROM tasks WHERE id='{tid}'")
        final = wait_terminal(tid)
        arts = artifacts(tid)
        report = json.loads(arts.get("VERIFICATION_REPORT", {}).get("content", b"{}") or b"{}")
        test_report = json.loads(arts.get("TEST_REPORT", {}).get("content", b"{}") or b"{}")
        events = [
            r[0]
            for r in sql(
                f"SELECT payload->>'status' FROM execution_events "
                f"WHERE task_id='{tid}' AND payload ? 'status' ORDER BY seq"
            )
        ]
        results[name] = {
            "task_id": tid,
            "status": final["status"],
            "events": events,
            "verification": report.get("status"),
            "reason": report.get("reason"),
        }
        check(
            f"{name}: CODE task SUCCEEDED after platform verification",
            profile == "CODE"
            and final["status"] == "SUCCEEDED"
            and events[-2:] == ["VERIFYING", "SUCCEEDED"],
            {"profile": profile, "events": events},
        )
        check(
            f"{name}: worker ran tests at the pinned commit inside the sandbox",
            test_report.get("ok") is True
            and test_report.get("base_commit") == FIXTURES[name]
            and b"removed=True" in arts.get("EXECUTION_LOG", {}).get("content", b""),
            {k: test_report.get(k) for k in ("framework", "passed", "total", "base_commit")},
        )
        check(
            f"{name}: verifier re-applied the patch to a clean checkout and re-ran tests",
            report.get("status") == "PASSED" and all(c["passed"] for c in report.get("checks", [])),
            [c["check"] for c in report.get("checks", [])],
        )
        check(
            f"{name}: 5 artifacts downloadable with matching SHA-256",
            set(arts) == {"PATCH", "TEST_REPORT", "EXECUTION_LOG", "SUMMARY", "VERIFICATION_REPORT"}
            and all(a["hash_ok"] for a in arts.values()),
            {k: v["source"] for k, v in arts.items()},
        )
        patch = arts.get("PATCH", {}).get("content", b"").decode()
        results[name]["patch"] = patch
        check(
            f"{name}: patch touches only the allowlisted file",
            patch.count("diff --git") == 1,
            patch.splitlines()[0] if patch else None,
        )

    # ------------------------------------------------------------ host-secret probe
    tid = submit("python-probe", tag)
    final = wait_terminal(tid)
    arts = artifacts(tid)
    log_text = arts.get("EXECUTION_LOG", {}).get("content", b"").decode()
    probes = re.findall(r"^(test_\w+) \(.*\) \.\.\. (ok|FAIL|ERROR)$", log_text, re.MULTILINE)
    check(
        "isolation probe: host secrets, host paths, network and rootfs writes all blocked",
        final["status"] == "SUCCEEDED" and len(probes) == 6 and all(r == "ok" for _, r in probes),
        dict(probes),
    )
    results["python-probe"] = {"task_id": tid, "status": final["status"], "probes": dict(probes)}

    # ------------------------------------------------------------ forged submission
    compose("stop", "code-worker")
    _, enr = http("POST", "/v1/enrollment-tokens", user=ADMIN, body={"resource_id": RESOURCE})
    _, reg = http(
        "POST",
        "/v1/workers/register",
        token=enr["token"],
        body={"resource_id": RESOURCE, "name": f"forger-{tag}", "capabilities": {}},
    )
    tok = reg["access_token"]
    http(
        "POST",
        f"/v1/workers/{reg['worker_id']}/heartbeat",
        token=tok,
        body={"state": "ONLINE", "capabilities": {"sandbox": "docker"}},
    )  # self-reported lie
    forged_task = submit("python-calc", f"{tag}-forged")
    status, lease = http(
        "GET", f"/v1/workers/{reg['worker_id']}/leases/next?wait_seconds=10", token=tok
    )
    check("malicious node claimed the CODE task", status == 200, status)
    http("POST", f"/v1/leases/{lease['lease_id']}/accept", token=tok)
    evil_patch = (
        b"diff --git a/../../etc/cron.d/pwn b/../../etc/cron.d/pwn\nnew file mode 100644\n"
        b"--- /dev/null\n+++ b/../../etc/cron.d/pwn\n@@ -0,0 +1 @@\n+* * * * * root sh\n"
    )
    fake_report = {
        "schema_version": 1,
        "framework": "unittest",
        "base_commit": FIXTURES["python-calc"],
        "command": ["python3"],
        "exit_code": 0,
        "passed": 3,
        "failed": 0,
        "total": 3,
        "ok": True,
        "timed_out": False,
        "duration_ms": 1,
    }

    def art(kind, name, media, content):
        return {
            "kind": kind,
            "name": name,
            "media_type": media,
            "content_base64": base64.b64encode(content).decode(),
        }

    status, done = http(
        "POST",
        f"/v1/leases/{lease['lease_id']}/complete",
        token=tok,
        body={
            "finalization_key": f"fin-{lease['attempt_id']}",
            "succeeded": True,
            "result_summary": "All tests passed (trust me)",
            "artifacts": [
                art("PATCH", "p.patch", "text/x-diff", evil_patch),
                art("TEST_REPORT", "t.json", "application/json", json.dumps(fake_report).encode()),
                art("SUMMARY", "s.txt", "text/plain", b"forged summary"),
                art("EXECUTION_LOG", "l.txt", "text/plain", b"forged log"),
            ],
        },
    )
    check(
        "forged 'success' is only accepted for verification (VERIFYING)",
        done.get("task_status") == "VERIFYING",
        done,
    )
    vr = wait_for(
        lambda: sql(
            f"SELECT status, reason FROM verification_results "
            f"WHERE attempt_id='{lease['attempt_id']}' AND status IN ('PASSED','FAILED')"
        ),
        120,
    )
    check(
        "independent verification rejected the forged patch",
        bool(vr) and vr[0] == ["FAILED", "PATCH_POLICY"],
        vr,
    )
    after = task(forged_task)
    published = http("GET", f"/v1/tasks/{forged_task}/artifacts", user=CONSUMER)[1]["items"]
    check(
        "forged artifacts never published; task scheduled for retry",
        after["status"] in ("RETRY_PENDING", "QUEUED")
        and [i["kind"] for i in published] == ["VERIFICATION_REPORT"],
        {"status": after["status"], "published": [i["kind"] for i in published]},
    )
    compose("start", "code-worker")
    final = wait_terminal(forged_task)
    attempts = sql(
        f"SELECT a.attempt_number, a.status, w.name FROM task_attempts a JOIN worker_nodes w "
        f"ON w.id=a.worker_id WHERE a.task_id='{forged_task}' ORDER BY 1"
    )
    check(
        "honest node's retry passed verification",
        final["status"] == "SUCCEEDED" and [r[1] for r in attempts] == ["FAILED", "SUCCEEDED"],
        attempts,
    )
    results["forged"] = {
        "task_id": forged_task,
        "status": final["status"],
        "attempts": attempts,
        "first_verification": vr,
    }

    # ------------------------------------------------------------ invariants + cleanup
    dup = scalar(
        "SELECT count(*) FROM (SELECT task_id FROM execution_events WHERE event_type IN "
        "('task.succeeded','task.failed') GROUP BY task_id HAVING count(*) > 1) d"
    )
    check("no duplicate finalization", dup == "0")
    usage_dup = scalar(
        "SELECT count(*) FROM (SELECT task_attempt_id FROM usage_records "
        "GROUP BY task_attempt_id HAVING count(*) > 1) d"
    )
    check("no duplicate usage records", usage_dup == "0")
    time.sleep(2)
    left = sandboxes()
    check("all sandbox containers cleaned up", left == [], left)
    logs = compose("logs", "--no-color", "api", "api-tls", "verifier", "code-worker", "scheduler")
    check("no credentials in logs", not SECRET_RE.findall(logs) and body["token"] not in logs)
    return finish(started, results)


def finish(started, results=None) -> int:
    passed = bool(checks) and all(c["passed"] for c in checks)
    report = {
        "report": "spareagent-week3-secure-execution-e2e",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "fixtures": FIXTURES,
        "summary": {
            "passed": passed,
            "checks_total": len(checks),
            "checks_failed": sum(not c["passed"] for c in checks),
        },
        "checks": checks,
        "results": results or {},
    }
    out = ROOT / "reports" / "week3_e2e_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    log(f"report written to {out.relative_to(ROOT)}: {'PASSED' if passed else 'FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
