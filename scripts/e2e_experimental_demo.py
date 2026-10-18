"""Week 4 reproducible local demonstration (Milestone C, SIMULATED only).

Drives the real API in-process against a migrated PostgreSQL + Redis:
operator creates an invitation-only group and records/verifies an authorization record,
Alice joins, connects her (simulated) device and opts in, Bob joins and submits tasks,
Alice approves one and rejects one, a paused/revoked node receives nothing, a rate-limit
is observed, and the operator pulls the kill switch. No Codex process, no subscription,
no network, no credentials. Writes reports/week4_experimental_demo.json.

    DATABASE_URL=postgresql+asyncpg://... REDIS_URL=redis://... \\
        python scripts/e2e_experimental_demo.py

The database must be a disposable local one: the script inserts demo users and rows.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.config import Settings  # noqa: E402
from apps.api.main import create_app  # noqa: E402
from db.migrator import migrate  # noqa: E402
from services.context import build_context  # noqa: E402
from services.experiments.participation import (  # noqa: E402
    JOIN_ACKNOWLEDGEMENTS,
    SHARING_ACKNOWLEDGEMENTS,
)
from services.experiments.simulation import ExperimentalSimulation  # noqa: E402

RUN = uuid.uuid4().hex[:8]
OPERATOR, ALICE, BOB = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
ALICE_RES, ALICE_NODE = uuid.uuid4(), uuid.uuid4()


def h(user: uuid.UUID, key: str | None = None) -> dict[str, str]:
    out = {"X-Dev-User-ID": str(user)}
    if key:
        out["Idempotency-Key"] = key
    return out


async def call(c: httpx.AsyncClient, method: str, path: str, user, expect: int, **kw) -> dict:
    resp = await c.request(method, path, headers=h(user, kw.pop("key", None)), **kw)
    if resp.status_code != expect:
        raise SystemExit(f"{method} {path} -> {resp.status_code} {resp.text}")
    return resp.json() if resp.content else {}


async def main() -> int:
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://spareagent:spareagent@localhost:55432/spareagent"
    )
    await migrate(database_url)
    settings = Settings(
        app_env="local",
        dev_auth_only=True,
        database_url=database_url,
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:56379/0"),
        artifact_root=Path(os.environ.get("ARTIFACT_ROOT", ROOT / ".demo-artifacts")),
        experiment_enabled=True,
        log_level="WARNING",
    )
    ctx = build_context(settings)
    report: dict = {"run": RUN, "execution_mode": "SIMULATED", "steps": []}

    def step(name: str, **data) -> None:
        report["steps"].append({"step": name, **json.loads(json.dumps(data, default=str))})
        print(f"[demo] {name}: {json.dumps(data, default=str)}")

    async with ctx.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users(id,email,role) VALUES (:o,:oe,'ADMIN'),(:a,:ae,'CONSUMER'),"
                "(:b,:be,'CONSUMER')"
            ),
            {
                "o": OPERATOR,
                "oe": f"operator-{RUN}@demo.local",
                "a": ALICE,
                "ae": f"alice-{RUN}@demo.local",
                "b": BOB,
                "be": f"bob-{RUN}@demo.local",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO resources(id,owner_id,resource_type,authorization_status,"
                "public_eligible) VALUES (:r,:a,'PERSONAL_SUBSCRIPTION','PENDING',false)"
            ),
            {"r": ALICE_RES, "a": ALICE},
        )
        await conn.execute(
            text(
                "INSERT INTO worker_nodes(id,resource_id,name,status,max_concurrency,"
                "last_heartbeat) VALUES (:w,:r,'alice-laptop (simulated)','IDLE',1,now())"
            ),
            {"w": ALICE_NODE, "r": ALICE_RES},
        )

    sim = ExperimentalSimulation(ctx, (ALICE_NODE,))
    terms = settings.experiment_terms_version
    try:
        transport = httpx.ASGITransport(app=create_app(settings, ctx))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            group = await call(
                c,
                "POST",
                "/v1/experiments/groups",
                OPERATOR,
                201,
                json={"name": f"friends-{RUN}", "max_members": 3},
            )
            gid = group["id"]
            for user in (ALICE, BOB):
                await call(
                    c,
                    "POST",
                    f"/v1/experiments/groups/{gid}/invite",
                    OPERATOR,
                    201,
                    json={"user_id": str(user)},
                )
                await call(
                    c,
                    "POST",
                    f"/v1/experiments/groups/{gid}/join",
                    user,
                    200,
                    json={
                        "terms_version": terms,
                        "acknowledgements": dict.fromkeys(JOIN_ACKNOWLEDGEMENTS, True),
                    },
                )
            auth = await call(
                c,
                "POST",
                f"/v1/experiments/groups/{gid}/authorizations",
                OPERATOR,
                201,
                json={
                    "provider_name": "openai-codex",
                    "authorization_source": "VENDOR_SUPPORT_CORRESPONDENCE",
                    "authorization_reference": "private-record DEMO-ONLY",
                    "authorization_scope": {"non_commercial": True},
                    "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                },
            )
            auth = await call(
                c,
                "POST",
                f"/v1/experiments/authorizations/{auth['id']}/verify",
                OPERATOR,
                200,
                json={"verification_status": "OPERATOR_VERIFIED"},
            )
            await call(
                c,
                "POST",
                f"/v1/experiments/groups/{gid}/sharing",
                OPERATOR,
                200,
                json={"enabled": True},
            )
            step(
                "group ready",
                group_id=gid,
                authorization=auth["verification_status"],
                live_execution_enabled=auth["live_execution_enabled"],
            )

            conn = await call(
                c,
                "POST",
                "/v1/subscriptions/connect",
                ALICE,
                201,
                json={
                    "worker_id": str(ALICE_NODE),
                    "device_label": "Alice laptop",
                    "subscription_type": "PLUS",
                    "auth_method": "CHATGPT",
                },
            )
            cid = conn["id"]
            await call(
                c,
                "PUT",
                "/v1/sharing/preferences",
                ALICE,
                200,
                json={"connection_id": cid, "group_id": gid, "max_tasks_per_day": 5},
            )
            refused = await c.post(
                "/v1/sharing/enable",
                headers=h(ALICE),
                json={"connection_id": cid, "terms_version": terms, "acknowledgements": {}},
            )
            step("sharing without consent refused", status=refused.status_code)
            await call(
                c,
                "POST",
                "/v1/sharing/enable",
                ALICE,
                200,
                json={
                    "connection_id": cid,
                    "terms_version": terms,
                    "acknowledgements": dict.fromkeys(SHARING_ACKNOWLEDGEMENTS, True),
                },
            )

            def body(prompt: str) -> dict:
                return {
                    "group_id": gid,
                    "connection_id": cid,
                    "task_type": "CODE_REVIEW",
                    "repository_url": "https://github.com/example/public-fixture",
                    "commit_sha": "0123456789abcdef0123456789abcdef01234567",
                    "prompt": prompt,
                    "max_duration_seconds": 300,
                }

            good = await call(
                c,
                "POST",
                "/v1/experimental-tasks",
                BOB,
                202,
                json=body("Review the parser"),
                key=f"demo-{RUN}-1",
            )
            bad = await call(
                c,
                "POST",
                "/v1/experimental-tasks",
                BOB,
                202,
                json=body("Review something Alice declines"),
                key=f"demo-{RUN}-2",
            )
            await sim.run_once()
            waiting = await call(c, "GET", f"/v1/experimental-tasks/{good['id']}", BOB, 200)
            step(
                "awaiting owner approval",
                status=waiting["status"],
                reasons=waiting["last_decision"]["reasons"],
            )
            await call(c, "POST", f"/v1/experimental-tasks/{good['id']}/approve", ALICE, 200)
            rejected = await call(
                c, "POST", f"/v1/experimental-tasks/{bad['id']}/reject", ALICE, 200
            )
            step("owner rejected", status=rejected["status"])
            await sim.run_once()
            done = await call(c, "GET", f"/v1/experimental-tasks/{good['id']}", BOB, 200)
            arts = await call(c, "GET", f"/v1/tasks/{good['id']}/artifacts", BOB, 200)
            step(
                "approved task finished",
                status=done["status"],
                mode=done["last_decision"]["execution_mode"],
                artifacts=sorted(a["kind"] for a in arts["items"]),
            )

            limited = await call(
                c,
                "POST",
                "/v1/experimental-tasks",
                BOB,
                202,
                json=body("Review [sim:rate-limit]"),
                key=f"demo-{RUN}-3",
            )
            await call(c, "POST", f"/v1/experimental-tasks/{limited['id']}/approve", ALICE, 200)
            await sim.run_once()
            usage = (await call(c, "GET", "/v1/subscriptions/usage", ALICE, 200))["items"][0]
            step(
                "rate limit observed",
                task=(await call(c, "GET", f"/v1/experimental-tasks/{limited['id']}", BOB, 200))[
                    "status"
                ],
                plan_remaining=usage["plan_remaining"],
                capacity=usage["capacity_state"],
                rate_limited_until=usage["rate_limited_until"],
            )

            await call(c, "POST", "/v1/sharing/pause", ALICE, 200, json={"connection_id": cid})
            paused = await c.post(
                "/v1/experimental-tasks", json=body("while paused"), headers=h(BOB, f"demo-{RUN}-4")
            )
            step("paused node refuses new tasks", status=paused.status_code)
            await call(c, "POST", "/v1/sharing/revoke", ALICE, 200, json={"connection_id": cid})
            kill = await call(c, "POST", "/v1/experiments/kill-switch", OPERATOR, 200)
            step("kill switch", **kill)
            audit = await call(c, "GET", f"/v1/experiments/groups/{gid}/audit", OPERATOR, 200)
            step(
                "audit",
                decisions=[
                    (a["decision"], a["execution_mode"], a["reasons"]) for a in reversed(audit)
                ],
            )
    finally:
        await ctx.aclose()

    ok = (
        report["steps"][1]["status"] == 422
        and report["steps"][4]["status"] == "SUCCEEDED"
        and report["steps"][4]["mode"] == "SIMULATED"
        and report["steps"][5]["task"] == "FAILED"
        and report["steps"][6]["status"] == 404
    )
    report["result"] = "PASS" if ok else "FAIL"
    out = ROOT / "reports" / "week4_experimental_demo.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"[demo] result={report['result']} report={out.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
