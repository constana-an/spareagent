"""Week 4 Milestone D: gated LIVE experimental execution on an owner's own device.

Exercises the real remote-node protocol (device enrollment, long poll, accept, complete)
with the owner's node simulated by the test client - CI never runs Codex or touches a
subscription. Scope modelled on OpenAI Support ticket 10766658: official Codex CLI on the
owner's own computer, relay server without credentials, at most 5 participants, no
purchased credits consumed by another participant's task, review after 6 months.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.exc import DBAPIError

from apps.api.main import create_app
from services.context import build_context
from tests.conftest import (
    ADMIN,
    PUBLIC_RESOURCE,
    USER_A,
    USER_B,
    bearer,
    create_task,
    execute,
    fetch_val,
    headers,
    make_settings,
    seed_fixtures,
)
from tests.integration.test_experimental_sharing import (
    CAROL,
    SHARE_ACKS,
    TERMS,
    invite_join,
    make_group,
    ok,
    task_body,
)

pytestmark = pytest.mark.db

ALICE, BOB = USER_A, USER_B
TICKET = "OpenAI support ticket 10766658"
PATCH = (
    b"diff --git a/test_added.py b/test_added.py\nnew file mode 100644\n"
    b"--- /dev/null\n+++ b/test_added.py\n@@ -0,0 +1,2 @@\n+def test_added():\n+    assert True\n"
)


def build(tmp_path, **overrides):
    values = {"experiment_enabled": True, "experiment_live_enabled": True, **overrides}
    return build_context(make_settings(tmp_path, **values))


@pytest.fixture
async def lctx(migrated_database, tmp_path):
    context = build(tmp_path)
    await seed_fixtures(context)
    await execute(
        context,
        "INSERT INTO users(id,email,role) VALUES (:c,'carol@test.local','CONSUMER')",
        c=CAROL,
    )
    try:
        yield context
    finally:
        await context.aclose()


@pytest.fixture
async def lclient(lctx):
    transport = httpx.ASGITransport(app=create_app(lctx.settings, lctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def authorize_live(c, gid, *, cap=5, covered=True, route="CODEX_CLI_EXEC") -> str:
    body = {
        "provider_name": "openai-codex",
        "authorization_source": "VENDOR_SUPPORT_CORRESPONDENCE",
        "authorization_reference": TICKET,
        "authorization_scope": {"non_commercial": True, "no_purchased_credits": True},
        "expires_at": (datetime.now(UTC) + timedelta(days=182)).isoformat(),
    }
    if route:
        body |= {"integration_route": route, "max_participants": cap}
    auth = await ok(
        await c.post(
            f"/v1/experiments/groups/{gid}/authorizations", json=body, headers=headers(ADMIN)
        ),
        201,
    )
    auth = await ok(
        await c.post(
            f"/v1/experiments/authorizations/{auth['id']}/verify",
            json={"verification_status": "OPERATOR_VERIFIED", "integration_route_covered": covered},
            headers=headers(ADMIN),
        )
    )
    return auth["id"]


async def enroll_device(c, owner=ALICE, executor="codex-subscription") -> dict:
    enr = await ok(
        await c.post("/v1/subscriptions/enrollment-tokens", json={}, headers=headers(owner)), 201
    )
    reg = await ok(
        await c.post(
            "/v1/workers/register",
            json={
                "resource_id": enr["resource_id"],
                "name": "alice-mac",
                "capabilities": {"executor": executor} if executor else {},
                "max_concurrency": 3,
            },
            headers=bearer(enr["token"]),
        ),
        201,
    )
    return {**reg, "resource_id": enr["resource_id"]}


async def connect_attest(c, gid, worker_id, *, attest=True, owner=ALICE, **prefs) -> str:
    conn = await ok(
        await c.post(
            "/v1/subscriptions/connect",
            json={
                "worker_id": worker_id,
                "device_label": "Alice Mac",
                "subscription_type": "PLUS",
                "auth_method": "CHATGPT",
            },
            headers=headers(owner),
        ),
        201,
    )
    await ok(
        await c.put(
            "/v1/sharing/preferences",
            json={"connection_id": conn["id"], "group_id": gid, **prefs},
            headers=headers(owner),
        )
    )
    await ok(
        await c.post(
            "/v1/sharing/enable",
            json={
                "connection_id": conn["id"],
                "terms_version": TERMS,
                "acknowledgements": SHARE_ACKS,
            },
            headers=headers(owner),
        )
    )
    if attest:
        await attest_conn(c, conn["id"], owner=owner)
    return conn["id"]


async def attest_conn(c, conn_id, *, credits_ok=True, owner=ALICE) -> dict:
    return await ok(
        await c.post(
            "/v1/subscriptions/attest",
            json={
                "connection_id": conn_id,
                "codex_check_result": "OWNER_ONLY_SUBSCRIPTION_RUN_OK",
                "no_purchased_credits": credits_ok,
                "no_auto_reload": True,
            },
            headers=headers(owner),
        )
    )


async def live_world(c, *, cap=5, attest=True, executor="codex-subscription", **prefs):
    gid = await make_group(c)
    await invite_join(c, gid, ALICE, BOB)
    auth_id = await authorize_live(c, gid, cap=cap)
    await ok(
        await c.post(
            f"/v1/experiments/groups/{gid}/sharing", json={"enabled": True}, headers=headers(ADMIN)
        )
    )
    node = await enroll_device(c, executor=executor)
    conn = await connect_attest(c, gid, node["worker_id"], attest=attest, **prefs)
    return gid, conn, node, auth_id


async def submit_live(c, gid, conn, status=202, **overrides) -> httpx.Response:
    resp = await c.post(
        "/v1/experimental-tasks",
        json=task_body(gid, conn, execution_mode="LIVE", **overrides),
        headers=headers(BOB, f"live-{uuid.uuid4().hex}"),
    )
    assert resp.status_code == status, resp.text
    return resp.json()


async def approve(c, task_id) -> None:
    await ok(await c.post(f"/v1/experimental-tasks/{task_id}/approve", headers=headers(ALICE)))


async def poll(c, node) -> httpx.Response:
    return await c.get(
        f"/v1/workers/{node['worker_id']}/leases/next?wait_seconds=0",
        headers=bearer(node["access_token"]),
    )


async def run_on_device(c, node, *, artifacts=(("SUMMARY", b"Looks good."),), **completion):
    resp = await poll(c, node)
    assert resp.status_code == 200, resp.text
    lease = resp.json()
    accepted = await c.post(
        f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
    )
    assert accepted.status_code == 204, accepted.text
    body = {
        "finalization_key": f"fin-{lease['attempt_id']}",
        "succeeded": True,
        "result_summary": "done",
        "artifacts": [
            {
                "kind": k,
                "name": f"{k.lower()}.txt",
                "media_type": "text/x-diff" if k == "PATCH" else "text/plain",
                "content_base64": base64.b64encode(v).decode(),
            }
            for k, v in artifacts
        ],
        **completion,
    }
    if not body["succeeded"]:
        body["artifacts"] = []
    done = await ok(
        await c.post(
            f"/v1/leases/{lease['lease_id']}/complete",
            json=body,
            headers=bearer(node["access_token"]),
        )
    )
    return lease, done


async def get_task(c, tid) -> dict:
    return await ok(await c.get(f"/v1/experimental-tasks/{tid}", headers=headers(BOB)))


# ====================================================================== happy path
async def test_live_task_runs_on_owner_device_end_to_end(lclient, lctx):
    gid, conn, node, auth_id = await live_world(lclient)
    group = await ok(await lclient.get(f"/v1/experiments/groups/{gid}", headers=headers(ADMIN)))
    assert group["authorization"]["live_execution_enabled"] is True
    assert group["authorization"]["max_participants"] == 5
    task = await submit_live(
        lclient, gid, conn, prompt="Review it. token=sk-proj-AAAAAAAAAAAAAAAAAAAA"
    )
    assert task["execution_mode"] == "LIVE" and task["approval_status"] == "PENDING"
    # Nothing reaches the device before the owner approves.
    assert (await poll(lclient, node)).status_code == 204
    await approve(lclient, task["id"])
    lease, done = await run_on_device(lclient, node)
    assert lease["execution_profile"] == "CODEX_LIVE"
    assert lease["resource_scope"] == "EXPERIMENTAL_SHARED_SUBSCRIPTION"
    assert lease["authorization_ref"] == auth_id
    assert "sk-proj-AAAA" not in lease["task"]["prompt"]  # redacted before leaving the API
    assert done["task_status"] == "VERIFYING"
    from services.verification.service import from_settings

    assert await from_settings(lctx).run_once() == "SUCCEEDED"
    t = await get_task(lclient, task["id"])
    assert t["status"] == "SUCCEEDED" and t["last_decision"]["execution_mode"] == "LIVE"
    arts = await ok(await lclient.get(f"/v1/tasks/{task['id']}/artifacts", headers=headers(BOB)))
    assert {a["kind"] for a in arts["items"]} == {"SUMMARY", "VERIFICATION_REPORT"}
    assert t["verification_status"] == "UNVERIFIED"
    assert await fetch_val(lctx, "SELECT estimated_cost_cents FROM usage_records") == 0
    assert (
        await fetch_val(lctx, "SELECT event||'/'||source FROM subscription_usage_observations")
        == "TASK_COMPLETED/LOCAL_RUNTIME"
    )
    assert (
        await fetch_val(
            lctx,
            "SELECT max_concurrency FROM worker_nodes WHERE id=:w",
            w=uuid.UUID(node["worker_id"]),
        )
        == 1
    )  # forced for personal devices


# ====================================================================== submission gates
async def test_live_submission_requires_operator_switch(migrated_database, tmp_path):
    ctx = build(tmp_path, experiment_live_enabled=False)
    await seed_fixtures(ctx)
    try:
        transport = httpx.ASGITransport(app=create_app(ctx.settings, ctx))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            gid = await make_group(c)
            await invite_join(c, gid, ALICE, BOB)
            await authorize_live(c, gid)
            node = await enroll_device(c)
            conn = await connect_attest(c, gid, node["worker_id"])
            r = await c.post(
                "/v1/experimental-tasks",
                json=task_body(gid, conn, execution_mode="LIVE"),
                headers=headers(BOB, "live-off-1"),
            )
            assert r.status_code == 403 and r.json()["error"]["code"] == "LIVE_EXECUTION_DISABLED"
            group = await ok(await c.get(f"/v1/experiments/groups/{gid}", headers=headers(ADMIN)))
            assert group["authorization"]["live_execution_enabled"] is False
    finally:
        await ctx.aclose()


async def test_live_submission_requires_attestations_and_covered_route(lclient):
    gid, conn, node, _ = await live_world(lclient, attest=False)
    r = await lclient.post(
        "/v1/experimental-tasks",
        json=task_body(gid, conn, execution_mode="LIVE"),
        headers=headers(BOB, "live-noattest"),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "OWNER_ATTESTATION_MISSING"
    # A failed codex-check or a credits "no" is recorded as missing, not as passed.
    out = await attest_conn(lclient, conn, credits_ok=False)
    assert out["owner_check_passed_at"] and out["no_purchased_credits_at"] is None
    # A route-less record cannot be marked covered.
    auth = await ok(
        await lclient.post(
            f"/v1/experiments/groups/{gid}/authorizations",
            json={
                "provider_name": "openai-codex",
                "authorization_source": "VENDOR_SUPPORT_CORRESPONDENCE",
                "authorization_reference": TICKET,
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
            headers=headers(ADMIN),
        ),
        201,
    )
    r = await lclient.post(
        f"/v1/experiments/authorizations/{auth['id']}/verify",
        json={"verification_status": "OPERATOR_VERIFIED", "integration_route_covered": True},
        headers=headers(ADMIN),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "ROUTE_DETAILS_REQUIRED"
    # The newest record (unverified) is now current, so LIVE is not authorized.
    await attest_conn(lclient, conn)
    r = await lclient.post(
        "/v1/experimental-tasks",
        json=task_body(gid, conn, execution_mode="LIVE"),
        headers=headers(BOB, "live-unauth"),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "LIVE_NOT_AUTHORIZED"


# ====================================================================== dispatch-time gates
async def test_withdrawn_credit_attestation_stops_live_dispatch(lclient):
    gid, conn, node, _ = await live_world(lclient)
    task = await submit_live(lclient, gid, conn)
    await approve(lclient, task["id"])
    await attest_conn(lclient, conn, credits_ok=False)  # owner bought credits after all
    assert (await poll(lclient, node)).status_code == 204
    t = await get_task(lclient, task["id"])
    assert (
        t["status"] == "FAILED" and "CREDITS_ATTESTATION_MISSING" in t["last_decision"]["reasons"]
    )


async def test_group_larger_than_authorized_cap_cannot_run_live(lclient):
    gid, conn, node, _ = await live_world(lclient, cap=2)
    await ok(
        await lclient.post(
            f"/v1/experiments/groups/{gid}/invite",
            json={"user_id": str(CAROL)},
            headers=headers(ADMIN),
        ),
        201,
    )
    task = await submit_live(lclient, gid, conn)
    await approve(lclient, task["id"])
    assert (await poll(lclient, node)).status_code == 204
    t = await get_task(lclient, task["id"])
    assert "GROUP_EXCEEDS_AUTHORIZED_SIZE" in t["last_decision"]["reasons"]


async def test_device_without_codex_executor_gets_nothing(lclient):
    gid, conn, node, _ = await live_world(lclient, executor=None)
    task = await submit_live(lclient, gid, conn)
    await approve(lclient, task["id"])
    assert (await poll(lclient, node)).status_code == 204
    t = await get_task(lclient, task["id"])
    assert t["status"] == "QUEUED" and t["last_decision"]["reasons"] == ["NODE_NOT_CODEX_CAPABLE"]


async def test_personal_device_never_receives_public_or_simulated_work(lclient, lctx):
    gid, conn, node, _ = await live_world(lclient)
    await execute(
        lctx, "UPDATE worker_nodes SET status='OFFLINE' WHERE resource_id=:r", r=PUBLIC_RESOURCE
    )
    public = (await create_task(lclient, user=BOB)).json()
    simulated = await ok(
        await lclient.post(
            "/v1/experimental-tasks", json=task_body(gid, conn), headers=headers(BOB, "sim-on-live")
        ),
        202,
    )
    await approve(lclient, simulated["id"])
    assert (await poll(lclient, node)).status_code == 204
    assert await fetch_val(lctx, "SELECT count(*) FROM task_attempts") == 0
    assert public["status"] == "QUEUED"


# ====================================================================== verification
@pytest.mark.parametrize(
    ("task_type", "artifacts", "expected"),
    [
        ("CODE_REVIEW", (("SUMMARY", b"ok"),), "VERIFYING"),
        ("CODE_REVIEW", (("SUMMARY", b"ok"), ("PATCH", PATCH)), "FAILED"),  # reviews don't edit
        ("WRITE_TESTS", (("SUMMARY", b"ok"),), "FAILED"),  # tests task without a patch
        ("WRITE_TESTS", (("SUMMARY", b"ok"), ("PATCH", PATCH)), "VERIFYING"),
        (
            "CODE_FIX",
            (("SUMMARY", b"ok"), ("PATCH", PATCH.replace(b"test_added.py", b".github/x.yml"))),
            "FAILED",  # patch policy
        ),
    ],
)
async def test_live_results_are_verified(lclient, task_type, artifacts, expected):
    gid, conn, node, _ = await live_world(lclient)
    task = await submit_live(lclient, gid, conn, task_type=task_type)
    await approve(lclient, task["id"])
    _, done = await run_on_device(lclient, node, artifacts=artifacts)
    assert done["task_status"] == expected
    if expected == "FAILED":
        arts = await ok(
            await lclient.get(f"/v1/tasks/{task['id']}/artifacts", headers=headers(BOB))
        )
        assert arts["items"] == []


# ====================================================================== limits on real nodes
async def test_device_reported_usage_limit_starts_cooldown(lclient, lctx):
    gid, conn, node, _ = await live_world(lclient)
    first = await submit_live(lclient, gid, conn)
    await approve(lclient, first["id"])
    _, done = await run_on_device(
        lclient, node, succeeded=False, error_code="SUBSCRIPTION_USAGE_LIMIT", retryable=True
    )
    assert done["task_status"] == "FAILED"
    assert await fetch_val(lctx, "SELECT count(*) FROM task_attempts") == 1  # never retried
    second = await submit_live(lclient, gid, conn)
    await approve(lclient, second["id"])
    assert (await poll(lclient, node)).status_code == 204
    t = await get_task(lclient, second["id"])
    assert t["status"] == "QUEUED" and t["last_decision"]["reasons"] == ["RATE_LIMITED"]


async def test_device_reported_expired_login_blocks_until_reconnect(lclient):
    gid, conn, node, _ = await live_world(lclient)
    first = await submit_live(lclient, gid, conn)
    await approve(lclient, first["id"])
    await run_on_device(lclient, node, succeeded=False, error_code="SUBSCRIPTION_AUTH_UNAVAILABLE")
    second = await submit_live(lclient, gid, conn)
    await approve(lclient, second["id"])
    assert (await poll(lclient, node)).status_code == 204
    assert (
        "SUBSCRIPTION_AUTH_INVALID"
        in (await get_task(lclient, second["id"]))["last_decision"]["reasons"]
    )
    # Owner re-runs `codex login` + reconnects: work resumes.
    await ok(
        await lclient.post(
            "/v1/subscriptions/connect",
            json={
                "worker_id": node["worker_id"],
                "device_label": "Alice Mac",
                "auth_method": "CHATGPT",
            },
            headers=headers(ALICE),
        ),
        201,
    )
    _, done = await run_on_device(lclient, node)
    assert done["task_status"] == "VERIFYING"


# ====================================================================== enrollment & DB guards
async def test_personal_device_enrollment_is_members_only(lclient, lctx):
    r = await lclient.post("/v1/subscriptions/enrollment-tokens", json={}, headers=headers(CAROL))
    assert r.status_code == 403
    gid, conn, node, _ = await live_world(lclient)
    # Marketplace enrollment of a personal resource stays impossible, even for the admin.
    r = await lclient.post(
        "/v1/enrollment-tokens",
        json={"resource_id": node["resource_id"], "ttl_seconds": 600},
        headers=headers(ADMIN),
    )
    assert r.status_code == 422
    # The database refuses a personal enrollment token issued by anyone but the owner.
    with pytest.raises(DBAPIError):
        await execute(
            lctx,
            "INSERT INTO enrollment_tokens(token_hash,resource_id,issued_by,expires_at) "
            "VALUES (repeat('a',64),:r,:u,now()+interval '1 hour')",
            r=uuid.UUID(node["resource_id"]),
            u=ADMIN,
        )
    with pytest.raises(DBAPIError):  # personal resources still can never be public
        await execute(
            lctx,
            "UPDATE resources SET public_eligible=true, authorization_status="
            "'APPROVED' WHERE id=:r",
            r=uuid.UUID(node["resource_id"]),
        )


async def test_database_refuses_unauthorized_live_allow(lclient, lctx):
    gid, conn, node, auth_id = await live_world(lclient, attest=False)
    task = await ok(
        await lclient.post(
            "/v1/experimental-tasks", json=task_body(gid, conn), headers=headers(BOB, "sim-for-db")
        ),
        202,
    )
    insert = (
        "INSERT INTO experimental_dispatch_audit(task_id,group_id,connection_id,authorization_id,"
        "decision,execution_mode) VALUES (:t,:g,:c,:a,'ALLOW','LIVE')"
    )
    params = {"t": uuid.UUID(task["id"]), "g": uuid.UUID(gid), "c": uuid.UUID(conn)}
    with pytest.raises(DBAPIError):  # owner attestations missing
        await execute(lctx, insert, **params, a=uuid.UUID(auth_id))
    await attest_conn(lclient, conn)
    with pytest.raises(DBAPIError):  # no authorization at all
        await execute(lctx, insert, **params, a=None)
    await execute(
        lctx,
        "UPDATE experimental_authorizations SET created_at=now()-interval '2 days',"
        " expires_at=now()-interval '1 day'",
    )
    with pytest.raises(DBAPIError):  # expired (6-month review passed)
        await execute(lctx, insert, **params, a=uuid.UUID(auth_id))
