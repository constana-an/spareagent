"""Protocol-only pilot hardening. No real subscriptions or physical-device claims."""

import asyncio
import uuid

import httpx
import pytest

from apps.api.main import create_app
from services.context import build_context
from services.verification.service import from_settings
from tests.conftest import bearer, execute, fetch_val, headers, make_settings
from tests.integration import test_experimental_live as live
from tests.integration.test_week5_reliability import reserved

lctx = live.lctx
lclient = live.lclient
pytestmark = pytest.mark.db


async def test_readiness_scoped_read_only_and_reports_live_off(lclient, lctx):
    gid, conn, node, _ = await live.live_world(
        lclient, max_task_duration_seconds=60, allowed_task_types=["CODE_REVIEW"]
    )
    await execute(
        lctx,
        "UPDATE experimental_groups SET max_members=2,max_task_duration_seconds=60,"
        "allowed_task_types='[\"CODE_REVIEW\"]'::jsonb",
    )
    await lclient.post(
        f"/v1/workers/{node['worker_id']}/heartbeat", headers=bearer(node["access_token"])
    )
    before = await fetch_val(lctx, "SELECT count(*) FROM experimental_dispatch_audit")
    # A second API process with LIVE safely disabled shares only persisted state.
    ctx = build_context(make_settings(lctx.settings.artifact_root.parent, experiment_enabled=True))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(ctx.settings, ctx)),
            base_url="http://localhost",
        ) as c:
            r = await c.get("/v1/workers/me/pilot-readiness", headers=bearer(node["access_token"]))
        assert r.status_code == 200, r.text
        data = r.json()
        ids = {x["id"] for x in data["checks"] if x["status"] == "BLOCKED"}
        assert ids == {"LIVE_EXECUTION_DISABLED"}
        assert data["owner_attestations"]["source"] == "OWNER_REPORTED_NOT_MACHINE_VERIFIED"
        assert data["physical_two_devices"] == "OPERATOR_CONFIRMATION_REQUIRED"
        assert data["per_task_approval"] == "REQUIRED_BEFORE_EACH_TASK"
        assert "prompt" not in r.text and "node_key" not in r.text
    finally:
        await ctx.aclose()
    assert await fetch_val(lctx, "SELECT count(*) FROM experimental_dispatch_audit") == before
    assert await fetch_val(lctx, "SELECT count(*) FROM task_attempts") == 0
    assert (
        await lclient.get("/v1/workers/me/pilot-readiness", headers=headers(live.ALICE))
    ).status_code == 401
    # Another owner's task cannot be inspected by guessing its id.
    assert (
        await lclient.get(
            "/v1/workers/me/pilot-readiness",
            params={"task_id": str(uuid.uuid4())},
            headers=bearer(node["access_token"]),
        )
    ).status_code == 404


async def test_metrics_report_unknowns_and_separate_simulated_and_live(lclient, lctx):
    gid, conn, node, _ = await live.live_world(lclient)
    await lclient.post(
        f"/v1/workers/{node['worker_id']}/heartbeat", headers=bearer(node["access_token"])
    )
    path = f"/v1/experiments/groups/{gid}/metrics"
    data = (await lclient.get(path, headers=headers(live.BOB))).json()
    assert data["participating_devices"] == 1 and data["online_devices"] == 1
    assert data["modes"]["LIVE"]["success_rate"] is None
    assert data["modes"]["LIVE"]["observed_input_tokens"] is None
    task = await live.submit_live(lclient, gid, conn)
    await live.approve(lclient, task["id"])
    await live.run_on_device(lclient, node, observed_usage={"input_tokens": 7, "output_tokens": 2})
    await from_settings(lctx).run_once()
    data = (await lclient.get(path, headers=headers(live.BOB))).json()
    assert data["modes"]["LIVE"]["completed"] == 1
    assert data["modes"]["LIVE"]["observed_input_tokens"] == 7
    assert data["modes"]["LIVE"]["success_rate"] == 1
    assert data["modes"]["LIVE"]["average_completed_latency_ms"] >= 0
    assert data["modes"]["SIMULATED"]["submitted"] == 0
    assert data["plan_remaining"] == "NOT_AVAILABLE"
    assert (await lclient.get(path, headers=headers(live.CAROL))).status_code == 404


async def test_exported_protocol_evidence_never_certifies_real_pilot(lclient, lctx):
    gid, conn, node, _ = await live.live_world(lclient)
    task = await live.submit_live(lclient, gid, conn, prompt="secret prompt must not be exported")
    path = f"/v1/experimental-tasks/{task['id']}/pilot-report"
    assert (await lclient.get(path, headers=headers(live.BOB))).json()["result"] == "NOT_RUN"
    await live.approve(lclient, task["id"])
    await live.run_on_device(lclient, node)
    await from_settings(lctx).run_once()
    r = await lclient.get(path, headers=headers(live.ALICE))
    assert r.status_code == 200
    data = r.json()
    assert data["result"] == "UNCONFIRMED"
    assert data["two_distinct_devices_confirmed"] is False
    assert data["owner_billing_confirmation"] == "MANUAL_REQUIRED"
    assert data["attempts"][0]["verification"]["status"] == "UNVERIFIED"
    assert data["consent_audit_refs"] and data["dispatch_audit_refs"]
    assert "secret prompt" not in r.text and node["node_key"] not in r.text
    assert (await lclient.get(path, headers=headers(live.CAROL))).status_code == 404


async def test_authorization_expiry_during_execution_stops_and_discards(lclient, lctx):
    task, lease, _, node, _ = await reserved(lclient)
    await lclient.post(
        f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
    )
    await execute(
        lctx,
        "UPDATE experimental_authorizations SET created_at=now()-interval '2 days',"
        "expires_at=now()-interval '1 day'",
    )
    renew = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/renew", headers=bearer(node["access_token"])
    )
    assert renew.json()["cancel_requested"] is True
    r = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/complete",
        headers=bearer(node["access_token"]),
        json={"finalization_key": f"fin-{lease['attempt_id']}", "succeeded": True},
    )
    assert r.json()["task_status"] == "CANCELLED"
    assert await fetch_val(lctx, "SELECT count(*) FROM artifacts") == 0
    assert (
        await fetch_val(
            lctx,
            "SELECT count(*) FROM subscription_usage_observations WHERE event='TASK_COMPLETED'",
        )
        == 0
    )


async def test_authorization_expires_during_verification_no_success_publication(lclient, lctx):
    gid, conn, node, _ = await live.live_world(lclient)
    task = await live.submit_live(lclient, gid, conn)
    await live.approve(lclient, task["id"])
    await live.run_on_device(lclient, node)
    verifier = from_settings(lctx)
    job = await verifier.claim()
    outcome = await verifier.verify(job)
    await execute(
        lctx,
        "UPDATE experimental_authorizations SET created_at=now()-interval '2 days',"
        "expires_at=now()-interval '1 day'",
    )
    assert await verifier.finalize(job, outcome) == "CANCELLED"
    assert (
        await fetch_val(lctx, "SELECT count(*) FROM artifacts WHERE source='WORKER' AND published")
        == 0
    )


async def test_live_concurrent_submit_reserves_only_one_slot(lclient, lctx):
    gid, conn, node, _ = await live.live_world(lclient)
    tasks = await asyncio.gather(*(live.submit_live(lclient, gid, conn) for _ in range(2)))
    for task in tasks:
        await live.approve(lclient, task["id"])
    results = await asyncio.gather(*(live.poll(lclient, node) for _ in range(2)))
    # Re-poll can re-deliver the same unaccepted lease. It cannot reserve another task.
    ids = {r.json()["attempt_id"] for r in results if r.status_code == 200}
    assert len(ids) == 1
    assert await fetch_val(lctx, "SELECT count(*) FROM task_attempts WHERE status='RESERVED'") == 1
    assert await fetch_val(lctx, "SELECT max(active_tasks) FROM worker_nodes") == 1


async def test_live_prompt_validation_blocks_oversized_and_secret_requests(lclient):
    gid, conn, _, _ = await live.live_world(lclient)
    for prompt in ("x" * 12001, "token=sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA"):
        r = await lclient.post(
            "/v1/experimental-tasks",
            headers=headers(live.BOB, f"prompt-{uuid.uuid4()}"),
            json={**live.task_body(gid, conn, execution_mode="LIVE"), "prompt": prompt},
        )
        # Secrets are redacted by existing validation; oversized requests must be refused.
        assert r.status_code == (422 if len(prompt) > 12000 else 202)
        assert "sk-proj-AAAA" not in r.text


async def test_live_duplicate_completion_is_atomic_across_api_restart(lclient, lctx, tmp_path):
    gid, conn, node, _ = await live.live_world(lclient)
    task = await live.submit_live(lclient, gid, conn)
    await live.approve(lclient, task["id"])
    lease, _ = await live.run_on_device(
        lclient, node, observed_usage={"input_tokens": 9, "output_tokens": 3}
    )
    restarted = build_context(
        make_settings(tmp_path, experiment_enabled=True, experiment_live_enabled=True)
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(restarted.settings, restarted)),
            base_url="http://localhost",
        ) as c:
            # A dropped HTTP response may cause the same finalization to be retried.
            r = await c.post(
                f"/v1/leases/{lease['lease_id']}/complete",
                headers=bearer(node["access_token"]),
                json={
                    "finalization_key": f"fin-{lease['attempt_id']}",
                    "succeeded": True,
                    "observed_usage": {"input_tokens": 1000000, "output_tokens": 1000000},
                },
            )
        assert r.status_code == 200 and r.json()["replayed"] is True
    finally:
        await restarted.aclose()
    assert await fetch_val(lctx, "SELECT count(*) FROM subscription_usage_observations") == 1
    assert await fetch_val(lctx, "SELECT input_tokens FROM subscription_usage_observations") == 9
    assert await fetch_val(lctx, "SELECT count(*) FROM artifacts") == 1


async def test_late_live_success_after_deadline_is_timeout_not_artifacts(lclient, lctx):
    task, lease, _, node, _ = await reserved(lclient)
    await lclient.post(
        f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
    )
    await execute(lctx, "UPDATE tasks SET created_at=now()-interval '2 hours'")
    r = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/complete",
        headers=bearer(node["access_token"]),
        json={"finalization_key": f"fin-{lease['attempt_id']}", "succeeded": True},
    )
    assert r.json()["task_status"] == "TIMED_OUT"
    assert await fetch_val(lctx, "SELECT count(*) FROM artifacts") == 0
    assert (
        await fetch_val(
            lctx,
            "SELECT count(*) FROM subscription_usage_observations WHERE event='TASK_COMPLETED'",
        )
        == 0
    )


async def test_new_authorization_cannot_silently_replace_running_task_authority(lclient, lctx):
    task, lease, _, node, _ = await reserved(lclient)
    await lclient.post(
        f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
    )
    await live.authorize_live(lclient, task["group_id"])
    r = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/renew", headers=bearer(node["access_token"])
    )
    assert r.json()["cancel_requested"] is True
    assert await fetch_val(lctx, "SELECT count(*) FROM artifacts") == 0
