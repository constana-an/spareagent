"""Week 2 remote worker protocol: enrollment, node credentials, leases, isolation."""

import asyncio
import base64
import logging
import uuid

import httpx
import pytest
from sqlalchemy.exc import DBAPIError

from apps.api.main import create_app
from packages.provider_sdk.mock import MockProviderAdapter
from services.context import build_context
from services.registry.node_auth import NodeAuthService
from services.scheduler.lease_service import LeaseService
from services.scheduler.runner import reconcile_capacity, tick
from tests.conftest import (
    ADMIN,
    PROVIDER,
    PUBLIC_RESOURCE,
    USER_A,
    bearer,
    create_task,
    enroll,
    execute,
    fetch_val,
    headers,
    make_settings,
    register_node,
)

pytestmark = pytest.mark.db

PERSONAL = uuid.UUID("66666666-0000-4000-8000-000000000006")


async def add_resource(ctx, rid, rtype="LICENSED_PROVIDER", status="PENDING", owner=PROVIDER):
    await execute(
        ctx,
        "INSERT INTO resources(id,owner_id,resource_type,authorization_status) "
        "VALUES (:r,:o,:t,:s)",
        r=rid,
        o=owner,
        t=rtype,
        s=status,
    )


async def poll(client, reg, wait=0):
    return await client.get(
        f"/v1/workers/{reg['worker_id']}/leases/next",
        params={"wait_seconds": wait},
        headers=bearer(reg["access_token"]),
    )


async def claimed(client, reg):
    """Create a task, poll it to this node and accept it. Returns (task_id, assignment)."""
    task_id = (await create_task(client)).json()["id"]
    resp = await poll(client, reg)
    assert resp.status_code == 200, resp.text
    assignment = resp.json()
    acc = await client.post(
        f"/v1/leases/{assignment['lease_id']}/accept", headers=bearer(reg["access_token"])
    )
    assert acc.status_code == 204, acc.text
    return task_id, assignment


async def mock_artifacts(assignment) -> list[dict]:
    from packages.provider_sdk.types import ExecutionRequest, ExecutionResult

    t = assignment["task"]
    req = ExecutionRequest(
        t["task_id"],
        assignment["attempt_id"],
        "redacted",
        "PUBLIC_MARKETPLACE",
        t["prompt"],
        t["repository_url"],
        t["commit_sha"],
        t["max_duration_seconds"],
        t["max_budget_cents"],
        assignment["attempt_number"],
        t["task_type"],
    )
    result = [i async for i in MockProviderAdapter().execute(req)][-1]
    assert isinstance(result, ExecutionResult)
    return [
        {
            "kind": a.kind,
            "name": a.name,
            "media_type": a.media_type,
            "content_base64": base64.b64encode(a.content).decode(),
        }
        for a in result.artifacts
    ]


async def complete(client, reg, assignment, **body):
    payload = {"finalization_key": f"fin-{assignment['attempt_id']}", "succeeded": True, **body}
    return await client.post(
        f"/v1/leases/{assignment['lease_id']}/complete",
        json=payload,
        headers=bearer(reg["access_token"]),
    )


# ------------------------------------------------------------------ enrollment
async def test_enrollment_token_issuance_rules(client, ctx):
    own_pending, other = uuid.uuid4(), uuid.uuid4()
    await add_resource(ctx, own_pending)
    await add_resource(ctx, other, owner=ADMIN, status="APPROVED")
    await add_resource(ctx, PERSONAL, rtype="PERSONAL_SUBSCRIPTION", status="APPROVED")

    assert (await enroll(client, issuer=USER_A)).status_code == 403  # consumer
    ok = await enroll(client, issuer=PROVIDER)  # provider, own APPROVED resource
    assert ok.status_code == 201 and ok.json()["token"].startswith("sa_enr_")
    assert (await enroll(client, issuer=PROVIDER, resource=other)).status_code == 404
    pending = await enroll(client, issuer=PROVIDER, resource=own_pending)
    assert pending.status_code == 403
    assert pending.json()["error"]["code"] == "RESOURCE_NOT_APPROVED"
    personal = await enroll(client, issuer=ADMIN, resource=PERSONAL)
    assert personal.status_code == 422
    assert personal.json()["error"]["code"] == "POLICY_VIOLATION"
    assert (await enroll(client, ttl=10**7)).status_code == 422
    # DB trigger blocks personal enrollment even if application checks were bypassed.
    with pytest.raises(DBAPIError, match="PERSONAL_SUBSCRIPTION"):
        await execute(
            ctx,
            "INSERT INTO enrollment_tokens(token_hash,resource_id,issued_by,expires_at) "
            "VALUES (repeat('a',64), :r, :u, now() + interval '1 hour')",
            r=PERSONAL,
            u=ADMIN,
        )


async def test_register_rejects_invalid_credentials(client, ctx):
    body = {"resource_id": str(PUBLIC_RESOURCE), "name": "n1", "capabilities": {}}
    cases = [
        {},  # no credential
        headers(PROVIDER),  # development consumer/provider auth is never node auth
        bearer("sa_enr_" + "x" * 43),  # unknown token
        bearer("not-a-token"),
    ]
    for h in cases:
        resp = await client.post("/v1/workers/register", json=body, headers=h)
        assert resp.status_code == 401, (h, resp.text)

    token = (await enroll(client)).json()["token"]
    mismatch = await client.post(
        "/v1/workers/register",
        json={**body, "resource_id": str(uuid.uuid4())},
        headers=bearer(token),
    )
    assert mismatch.status_code == 403
    too_big = await client.post(
        "/v1/workers/register", json={**body, "max_concurrency": 99}, headers=bearer(token)
    )
    assert too_big.status_code == 422
    smuggle = await client.post(
        "/v1/workers/register", json={**body, "public_eligible": True}, headers=bearer(token)
    )
    assert smuggle.status_code == 422
    first = await client.post("/v1/workers/register", json=body, headers=bearer(token))
    assert first.status_code == 201
    reuse = await client.post("/v1/workers/register", json=body, headers=bearer(token))
    assert reuse.status_code == 401 and reuse.json()["error"]["code"] == "ENROLLMENT_INVALID"
    # an access token is not an enrollment token
    as_enroll = await client.post(
        "/v1/workers/register", json=body, headers=bearer(first.json()["access_token"])
    )
    assert as_enroll.status_code == 401

    expired = (await enroll(client)).json()
    await execute(
        ctx,
        "UPDATE enrollment_tokens SET expires_at = created_at - interval '59 minutes', "
        "created_at = created_at - interval '1 hour' WHERE id=:i",
        i=uuid.UUID(expired["id"]),
    )
    resp = await client.post("/v1/workers/register", json=body, headers=bearer(expired["token"]))
    assert resp.status_code == 401
    revoked = (await enroll(client)).json()
    await execute(
        ctx,
        "UPDATE enrollment_tokens SET revoked_at=now() WHERE id=:i",
        i=uuid.UUID(revoked["id"]),
    )
    resp = await client.post("/v1/workers/register", json=body, headers=bearer(revoked["token"]))
    assert resp.status_code == 401
    # registration never grants eligibility
    assert await fetch_val(ctx, "SELECT count(*) FROM worker_nodes WHERE status='OFFLINE'") == 1


async def test_secrets_never_stored_raw_or_logged(client, ctx, caplog):
    caplog.set_level(logging.DEBUG)
    enrollment = (await enroll(client)).json()["token"]
    reg = await register_node(client)
    rotated = (
        await client.post(
            f"/v1/workers/{reg['worker_id']}/credentials/rotate",
            headers=bearer(reg["access_token"]),
        )
    ).json()
    secrets = [enrollment, reg["node_key"], reg["access_token"], rotated["node_key"]]
    dump = await fetch_val(
        ctx,
        "SELECT concat_ws(' ',"
        " (SELECT string_agg(row_to_json(n)::text,' ') FROM node_credentials n),"
        " (SELECT string_agg(row_to_json(e)::text,' ') FROM enrollment_tokens e),"
        " (SELECT string_agg(row_to_json(a)::text,' ') FROM audit_logs a))",
    )
    logs = "\n".join(r.getMessage() + str(vars(r)) for r in caplog.records)
    for secret in secrets:
        assert secret not in dump
        assert secret not in logs
    from services.observability import redact

    assert all(redact(f"token {s}") == "token [REDACTED]" for s in secrets)


# ------------------------------------------------------------------ credentials lifecycle
async def test_expired_access_token_and_refresh(client, ctx):
    reg = await register_node(client)
    me = await client.get("/v1/workers/me", headers=bearer(reg["access_token"]))
    assert me.status_code == 200 and me.json()["worker_id"] == reg["worker_id"]
    await execute(
        ctx,
        "UPDATE node_credentials SET issued_at = issued_at - interval '2 hours', "
        "expires_at = issued_at - interval '1 hour' WHERE kind='ACCESS'",
    )
    resp = await client.get("/v1/workers/me", headers=bearer(reg["access_token"]))
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "NODE_TOKEN_EXPIRED"
    fresh = await client.post("/v1/workers/token", headers=bearer(reg["node_key"]))
    assert fresh.status_code == 200
    assert (
        await client.get("/v1/workers/me", headers=bearer(fresh.json()["access_token"]))
    ).status_code == 200
    # an access token cannot be exchanged for another access token
    assert (
        await client.post("/v1/workers/token", headers=bearer(fresh.json()["access_token"]))
    ).status_code == 401


async def test_expired_node_key(client, ctx):
    reg = await register_node(client)
    await execute(
        ctx,
        "UPDATE node_credentials SET issued_at = issued_at - interval '2 days', "
        "expires_at = now() - interval '1 second' WHERE kind='NODE_KEY'",
    )
    resp = await client.post("/v1/workers/token", headers=bearer(reg["node_key"]))
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "NODE_KEY_EXPIRED"


async def test_rotation_revokes_previous_credentials(client):
    reg = await register_node(client)
    new = await client.post(
        f"/v1/workers/{reg['worker_id']}/credentials/rotate", headers=bearer(reg["access_token"])
    )
    assert new.status_code == 200
    new = new.json()
    assert new["worker_id"] == reg["worker_id"] and new["node_key"] != reg["node_key"]
    assert (
        await client.get("/v1/workers/me", headers=bearer(reg["access_token"]))
    ).status_code == 401
    assert (
        await client.post("/v1/workers/token", headers=bearer(reg["node_key"]))
    ).status_code == 401
    assert (
        await client.get("/v1/workers/me", headers=bearer(new["access_token"]))
    ).status_code == 200


async def test_admin_revocation_cuts_off_node_and_requeues_work(client, ctx):
    reg = await register_node(client)
    task_id, assignment = await claimed(client, reg)
    denied = await client.post(
        f"/v1/admin/workers/{reg['worker_id']}/revoke", headers=headers(PROVIDER)
    )
    assert denied.status_code == 403
    resp = await client.post(f"/v1/admin/workers/{reg['worker_id']}/revoke", headers=headers(ADMIN))
    assert resp.status_code == 200 and resp.json()["leases_expired"] == 1
    for call in (
        client.get("/v1/workers/me", headers=bearer(reg["access_token"])),
        client.post("/v1/workers/token", headers=bearer(reg["node_key"])),
        client.post(
            f"/v1/workers/{reg['worker_id']}/heartbeat", headers=bearer(reg["access_token"])
        ),
    ):
        r = await call
        assert r.status_code == 401
    status = await fetch_val(ctx, "SELECT status FROM tasks WHERE id=:t", t=uuid.UUID(task_id))
    assert status == "RETRY_PENDING"
    assert await fetch_val(ctx, "SELECT status FROM task_attempts") == "EXPIRED"
    late = await complete(client, reg, assignment)
    assert late.status_code == 401


# ------------------------------------------------------------------ isolation
async def test_cross_node_lease_and_path_isolation(client, ctx):
    a = await register_node(client, name="node-a")
    b = await register_node(client, name="node-b")
    _, assignment = await claimed(client, a)
    lease = assignment["lease_id"]
    hb = bearer(b["access_token"])
    for method, path, body in (
        ("POST", f"/v1/leases/{lease}/accept", None),
        ("POST", f"/v1/leases/{lease}/progress", {"message": "x", "percent": 1}),
        ("POST", f"/v1/leases/{lease}/renew", None),
        (
            "POST",
            f"/v1/leases/{lease}/complete",
            {"finalization_key": "steal-key-1", "succeeded": True},
        ),
        ("POST", f"/v1/workers/{a['worker_id']}/heartbeat", None),
        ("GET", f"/v1/workers/{a['worker_id']}/leases/next", None),
        ("POST", f"/v1/workers/{a['worker_id']}/credentials/rotate", None),
    ):
        resp = await client.request(method, path, json=body, headers=hb)
        assert resp.status_code == 403, (path, resp.text)
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 0
    # consumer dev auth never works on node endpoints, node tokens never on consumer endpoints
    assert (
        await client.post(f"/v1/workers/{a['worker_id']}/heartbeat", headers=headers(USER_A))
    ).status_code == 401
    assert (await client.get("/v1/tasks", headers=bearer(a["access_token"]))).status_code == 401


async def test_heartbeat_requires_valid_node_token(client, ctx):
    reg = await register_node(client)
    path = f"/v1/workers/{reg['worker_id']}/heartbeat"
    assert (await client.post(path)).status_code == 401
    assert (await client.post(path, headers=bearer("sa_at_" + "z" * 43))).status_code == 401
    ok = await client.post(
        path,
        json={"state": "ONLINE", "capabilities": {"public_eligible": True, "approved": True}},
        headers=bearer(reg["access_token"]),
    )
    assert ok.status_code == 204
    # capabilities are informational: nothing a node says changes resource eligibility
    assert await fetch_val(ctx, "SELECT count(*) FROM resources WHERE public_eligible") == 1
    smuggle = await client.post(
        path, json={"authorization_status": "APPROVED"}, headers=bearer(reg["access_token"])
    )
    assert smuggle.status_code == 422
    drain = await client.post(path, json={"state": "DRAINING"}, headers=bearer(reg["access_token"]))
    assert drain.status_code == 204
    assert (
        await fetch_val(
            ctx, "SELECT status FROM worker_nodes WHERE id=:w", w=uuid.UUID(reg["worker_id"])
        )
        == "DRAINING"
    )


# ------------------------------------------------------------------ scheduling
async def test_assignment_is_redacted_and_scoped(client):
    reg = await register_node(client)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz012345"
    await create_task(client, prompt=f"use {secret}")
    resp = await poll(client, reg)
    body = resp.json()
    assert secret not in resp.text and "[REDACTED]" in body["task"]["prompt"]
    assert str(USER_A) not in resp.text
    # re-polling before accept re-delivers the same reservation (no double reservation)
    again = await poll(client, reg)
    assert again.json()["lease_id"] == body["lease_id"]


async def test_concurrent_workers_claim_one_task_once(client, ctx):
    nodes = [await register_node(client, name=f"n{i}") for i in range(3)]
    await create_task(client)
    responses = await asyncio.gather(*[poll(client, n, wait=0) for n in nodes])
    assert sorted(r.status_code for r in responses) == [200, 204, 204]
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 1


async def test_capacity_exhaustion(client, ctx):
    reg = await register_node(client, max_concurrency=1)
    await claimed(client, reg)
    await create_task(client)
    resp = await poll(client, reg)
    assert resp.status_code == 204
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 1
    assert (
        await fetch_val(
            ctx, "SELECT active_tasks FROM worker_nodes WHERE id=:w", w=uuid.UUID(reg["worker_id"])
        )
        == 1
    )


async def test_long_poll_returns_when_work_arrives(client):
    reg = await register_node(client)
    waiter = asyncio.create_task(poll(client, reg, wait=3))
    await asyncio.sleep(0.3)
    assert not waiter.done()
    await create_task(client)
    resp = await asyncio.wait_for(waiter, 5)
    assert resp.status_code == 200


async def test_personal_subscription_node_never_receives_work(client, ctx):
    """Even a node that somehow exists for a personal resource (compromised registry) gets
    nothing: dispatch eligibility is enforced on every claim."""
    await add_resource(ctx, PERSONAL, rtype="PERSONAL_SUBSCRIPTION", status="APPROVED")
    wid = uuid.uuid4()
    await execute(
        ctx,
        "INSERT INTO worker_nodes(id,resource_id,name,status,last_heartbeat) "
        "VALUES (:w,:r,'personal','IDLE',now())",
        w=wid,
        r=PERSONAL,
    )
    async with ctx.sessionmaker() as session, session.begin():
        issued = await NodeAuthService(ctx)._issue_pair(session, wid)
    reg = {"worker_id": str(wid), "access_token": issued.access_token.secret}
    task_id = (await create_task(client)).json()["id"]
    assert (await poll(client, reg)).status_code == 204
    assert await fetch_val(ctx, "SELECT status FROM tasks WHERE id=:t", t=uuid.UUID(task_id)) == (
        "QUEUED"
    )


# ------------------------------------------------------------------ completion
async def test_remote_success_duplicate_completion_and_server_usage(client, ctx):
    reg = await register_node(client)
    task_id, assignment = await claimed(client, reg)
    arts = await mock_artifacts(assignment)
    first = await complete(client, reg, assignment, result_summary="done", artifacts=arts)
    assert first.status_code == 200 and first.json() == {
        "task_status": "SUCCEEDED",
        "replayed": False,
    }
    again = await complete(client, reg, assignment, result_summary="done", artifacts=arts)
    assert again.json() == {"task_status": "SUCCEEDED", "replayed": True}
    other = await complete(client, reg, assignment, finalization_key="different-key-1")
    assert other.status_code == 409
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 1
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 4
    assert (
        await fetch_val(
            ctx, "SELECT count(*) FROM execution_events WHERE event_type='task.succeeded'"
        )
        == 1
    )
    # the server computed the cost; nodes cannot report their own usage
    assert await fetch_val(ctx, "SELECT estimated_cost_cents FROM usage_records") == 10
    selfbill = await complete(client, reg, assignment, usage={"estimated_cost_cents": 99999})
    assert selfbill.status_code == 422
    assert await fetch_val(ctx, "SELECT active_tasks FROM worker_nodes WHERE name='node-1'") == 0
    t = (await client.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))).json()
    assert t["status"] == "SUCCEEDED"


async def test_stale_completion_after_lease_expiry(client, ctx):
    reg = await register_node(client)
    task_id, assignment = await claimed(client, reg)
    await execute(ctx, "UPDATE task_attempts SET leased_until = now() - interval '1 second'")
    resp = await complete(client, reg, assignment, artifacts=await mock_artifacts(assignment))
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "LEASE_EXPIRED"
    await LeaseService(ctx).recover_expired()
    resp = await complete(client, reg, assignment)
    assert resp.status_code == 409
    renew = await client.post(
        f"/v1/leases/{assignment['lease_id']}/renew", headers=bearer(reg["access_token"])
    )
    assert renew.status_code == 409
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 0
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 0


async def test_cancellation_during_remote_execution(client, ctx):
    reg = await register_node(client)
    task_id, assignment = await claimed(client, reg)
    await client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_A))
    prog = await client.post(
        f"/v1/leases/{assignment['lease_id']}/progress",
        json={"message": "working", "percent": 50},
        headers=bearer(reg["access_token"]),
    )
    assert prog.json()["cancel_requested"] is True
    done = await complete(
        client,
        reg,
        assignment,
        succeeded=False,
        error_code="CANCELLED",
        artifacts=await mock_artifacts(assignment),
    )
    assert done.json()["task_status"] == "CANCELLED"
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 0


async def test_server_decides_retryability(client, ctx):
    reg = await register_node(client)
    t1, a1 = await claimed(client, reg)
    r1 = await complete(
        client, reg, a1, succeeded=False, retryable=True, error_code="PERMANENT_PROVIDER_ERROR"
    )
    assert r1.json()["task_status"] == "FAILED"  # node's retryable=true is ignored
    t2, a2 = await claimed(client, reg)
    r2 = await complete(
        client, reg, a2, succeeded=False, retryable=True, error_code="TRANSIENT_PROVIDER_ERROR"
    )
    assert r2.json()["task_status"] == "RETRY_PENDING"
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 2


async def test_completion_payload_limits(client, ctx):
    reg = await register_node(client)
    _, assignment = await claimed(client, reg)
    bad = await complete(
        client,
        reg,
        assignment,
        artifacts=[
            {"kind": "PATCH", "name": "p", "media_type": "text/x-diff", "content_base64": "@@"}
        ],
    )
    assert bad.status_code == 422
    html = await complete(
        client,
        reg,
        assignment,
        artifacts=[{"kind": "LOG", "name": "x", "media_type": "text/html", "content_base64": ""}],
    )
    assert html.status_code == 422
    path = await complete(
        client,
        reg,
        assignment,
        artifacts=[
            {"kind": "LOG", "name": "../x", "media_type": "text/plain", "content_base64": ""}
        ],
    )
    assert path.status_code == 422


# ------------------------------------------------------------------ restarts / reconciliation
async def test_api_restart_during_task_processing(tmp_path, migrated_database, client, ctx):
    reg = await register_node(client)
    task_id, assignment = await claimed(client, reg)
    await ctx.aclose()  # the original API process "dies"

    ctx2 = build_context(make_settings(tmp_path / "restart"))
    try:
        app2 = create_app(ctx2.settings, ctx2)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app2), base_url="http://localhost"
        ) as c2:
            await tick(ctx2)  # scheduler reconciliation on restart must not disturb live leases
            prog = await c2.post(
                f"/v1/leases/{assignment['lease_id']}/progress",
                json={"message": "still here", "percent": 60},
                headers=bearer(reg["access_token"]),
            )
            assert prog.status_code == 200 and prog.json()["cancel_requested"] is False
            done = await complete(c2, reg, assignment, artifacts=await mock_artifacts(assignment))
            assert done.json()["task_status"] == "SUCCEEDED"
            t = (await c2.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))).json()
            assert t["status"] == "SUCCEEDED"
    finally:
        await ctx2.aclose()


async def test_reconcile_capacity_repairs_drift(client, ctx):
    reg = await register_node(client, max_concurrency=2)
    await claimed(client, reg)
    await execute(ctx, "UPDATE worker_nodes SET active_tasks = 0 WHERE name='node-1'")
    await execute(
        ctx,
        "UPDATE worker_nodes SET active_tasks = 1, status='RUNNING' WHERE name <> 'node-1'",
    )
    assert await reconcile_capacity(ctx) == 2
    assert await fetch_val(ctx, "SELECT active_tasks FROM worker_nodes WHERE name='node-1'") == 1
    assert await fetch_val(ctx, "SELECT active_tasks FROM worker_nodes WHERE name <> 'node-1'") == 0


async def test_offline_announcement_not_undone_by_inflight_poll(client, ctx):
    """Regression (CI): a graceful-shutdown OFFLINE heartbeat sent while the node's earlier
    long poll is still running server-side must stick; the poll must not re-online it."""
    reg = await register_node(client)
    waiter = asyncio.create_task(poll(client, reg, wait=2))
    await asyncio.sleep(0.3)
    off = await client.post(
        f"/v1/workers/{reg['worker_id']}/heartbeat",
        json={"state": "OFFLINE"},
        headers=bearer(reg["access_token"]),
    )
    assert off.status_code == 204
    await create_task(client)  # work arriving must not be claimed by an OFFLINE node
    resp = await asyncio.wait_for(waiter, 5)
    assert resp.status_code == 204
    status = await fetch_val(
        ctx, "SELECT status FROM worker_nodes WHERE id=:w", w=uuid.UUID(reg["worker_id"])
    )
    assert status == "OFFLINE"
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 0
    # a fresh poll (node restarted) brings it back online and claims the work
    again = await poll(client, reg)
    assert again.status_code == 200
