"""Week 4 Milestones B + C: invitation-only experimental sharing, consent and the
simulated cross-user scheduler, end to end through the API and the existing task
lifecycle. Deterministic: no Codex, no subscription, no network."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.exc import DBAPIError

from apps.api.main import create_app
from services.context import build_context
from services.experiments.simulation import ExperimentalSimulation
from services.scheduler.lease_service import LeaseService
from tests.conftest import (
    ADMIN,
    PUBLIC_WORKER,
    USER_A,
    USER_B,
    create_task,
    drain_worker,
    execute,
    fetch_val,
    headers,
    make_settings,
    seed_fixtures,
)

pytestmark = pytest.mark.db

ALICE, BOB = USER_A, USER_B  # Alice shares her plan; Bob is a friend who asks for work
CAROL = uuid.UUID("eeeeeeee-0000-4000-8000-00000000000e")  # never invited
ALICE_RES = uuid.UUID("77777777-0000-4000-8000-000000000007")
ALICE_NODE = uuid.UUID("88888888-0000-4000-8000-000000000008")
TERMS = "2026-10-experimental-v1"
JOIN_ACKS = {
    "non_commercial_experimental_use": True,
    "no_payment_or_compensation": True,
    "participation_revocable_any_time": True,
}
SHARE_ACKS = {
    **JOIN_ACKS,
    "subscription_usage_may_be_consumed_within_confirmed_scope": True,
    "device_executes_opted_in_workloads": True,
    "owner_controls_local_credentials": True,
    "owner_may_add_restrictions": True,
}


@pytest.fixture
async def xctx(migrated_database, tmp_path):
    context = build_context(make_settings(tmp_path, experiment_enabled=True))
    await seed_fixtures(context)
    await execute(
        context,
        "INSERT INTO users(id,email,role) VALUES (:c,'carol@test.local','CONSUMER')",
        c=CAROL,
    )
    # Alice's own device. Personal resources cannot enroll through the API (D-22), so the
    # simulated node is created directly, exactly like a local fixture.
    await execute(
        context,
        "INSERT INTO resources(id,owner_id,resource_type,authorization_status,public_eligible)"
        " VALUES (:r,:o,'PERSONAL_SUBSCRIPTION','PENDING',false)",
        r=ALICE_RES,
        o=ALICE,
    )
    await execute(
        context,
        "INSERT INTO worker_nodes(id,resource_id,name,status,max_concurrency,last_heartbeat)"
        " VALUES (:w,:r,'alice-laptop','IDLE',1,now())",
        w=ALICE_NODE,
        r=ALICE_RES,
    )
    try:
        yield context
    finally:
        await context.aclose()


@pytest.fixture
async def xclient(xctx):
    transport = httpx.ASGITransport(app=create_app(xctx.settings, xctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


def sim(ctx) -> ExperimentalSimulation:
    return ExperimentalSimulation(ctx, (ALICE_NODE,))


async def ok(resp: httpx.Response, status: int = 200) -> dict:
    assert resp.status_code == status, resp.text
    return resp.json()


async def make_group(c, **limits) -> str:
    group = await ok(
        await c.post(
            "/v1/experiments/groups", json={"name": "friends", **limits}, headers=headers(ADMIN)
        ),
        201,
    )
    return group["id"]


async def invite_join(c, gid, *users) -> None:
    for u in users:
        await ok(
            await c.post(
                f"/v1/experiments/groups/{gid}/invite",
                json={"user_id": str(u)},
                headers=headers(ADMIN),
            ),
            201,
        )
        await ok(
            await c.post(
                f"/v1/experiments/groups/{gid}/join",
                json={"terms_version": TERMS, "acknowledgements": JOIN_ACKS},
                headers=headers(u),
            )
        )


async def authorize(c, gid, *, verify=True, expires=None) -> str:
    auth = await ok(
        await c.post(
            f"/v1/experiments/groups/{gid}/authorizations",
            json={
                "provider_name": "openai-codex",
                "authorization_source": "VENDOR_SUPPORT_CORRESPONDENCE",
                "authorization_reference": "support-ticket #12345",
                "authorization_scope": {"non_commercial": True, "max_members": 5},
                "expires_at": (expires or datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
            headers=headers(ADMIN),
        ),
        201,
    )
    assert auth["verification_status"] == "UNVERIFIED" and auth["live_execution_enabled"] is False
    if verify:
        auth = await ok(
            await c.post(
                f"/v1/experiments/authorizations/{auth['id']}/verify",
                json={
                    "verification_status": "OPERATOR_VERIFIED",
                    "integration_route_covered": False,
                },
                headers=headers(ADMIN),
            )
        )
    return auth["id"]


async def connect_and_share(c, gid, **prefs) -> str:
    conn = await ok(
        await c.post(
            "/v1/subscriptions/connect",
            json={
                "worker_id": str(ALICE_NODE),
                "device_label": "Alice laptop",
                "subscription_type": "PLUS",
                "auth_method": "CHATGPT",
            },
            headers=headers(ALICE),
        ),
        201,
    )
    await ok(
        await c.put(
            "/v1/sharing/preferences",
            json={"connection_id": conn["id"], "group_id": gid, **prefs},
            headers=headers(ALICE),
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
            headers=headers(ALICE),
        )
    )
    return conn["id"]


async def world(c, *, group_sharing=True, verify=True, expires=None, **prefs):
    gid = await make_group(c)
    await invite_join(c, gid, ALICE, BOB)
    await authorize(c, gid, verify=verify, expires=expires)
    if group_sharing:
        await ok(
            await c.post(
                f"/v1/experiments/groups/{gid}/sharing",
                json={"enabled": True},
                headers=headers(ADMIN),
            )
        )
    conn = await connect_and_share(c, gid, **prefs)
    return gid, conn


def task_body(gid, conn, **overrides) -> dict:
    return {
        "group_id": gid,
        "connection_id": conn,
        "task_type": "CODE_REVIEW",
        "repository_url": "https://github.com/example/public-fixture",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "prompt": "Review the parser for edge cases",
        "max_duration_seconds": 300,
        **overrides,
    }


async def submit(c, gid, conn, user=BOB, status=202, **overrides) -> dict:
    return await ok(
        await c.post(
            "/v1/experimental-tasks",
            json=task_body(gid, conn, **overrides),
            headers=headers(user, f"exp-{uuid.uuid4().hex}"),
        ),
        status,
    )


async def get_task(c, tid, user=BOB) -> dict:
    return await ok(await c.get(f"/v1/experimental-tasks/{tid}", headers=headers(user)))


# ====================================================================== acceptance 10
async def test_consented_task_completes_through_existing_verification(xclient, xctx):
    gid, conn = await world(xclient)
    nodes = await ok(
        await xclient.get(f"/v1/experimental-tasks/nodes?group_id={gid}", headers=headers(BOB))
    )
    assert [n["connection_id"] for n in nodes["items"]] == [conn]
    assert nodes["items"][0]["requires_approval"] is True

    task = await submit(xclient, gid, conn)
    assert task["status"] == "QUEUED" and task["approval_status"] == "PENDING"
    assert task["resource_scope"] == "EXPERIMENTAL_SHARED_SUBSCRIPTION"

    await sim(xctx).run_once()  # owner has not approved: nothing runs
    t = await get_task(xclient, task["id"])
    assert t["status"] == "QUEUED"
    assert t["last_decision"]["reasons"] == ["OWNER_APPROVAL_MISSING"]

    incoming = await ok(
        await xclient.get("/v1/experimental-tasks?role=owner", headers=headers(ALICE))
    )
    assert [i["id"] for i in incoming["items"]] == [task["id"]]
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE))
    )
    await sim(xctx).run_once()

    t = await get_task(xclient, task["id"])
    assert t["status"] == "SUCCEEDED", t
    assert t["approval_source"] == "OWNER_APPROVAL"
    assert t["last_decision"]["decision"] == "ALLOW"
    assert t["last_decision"]["execution_mode"] == "SIMULATED"
    # Requester downloads results through the existing artifact contract.
    arts = await ok(await xclient.get(f"/v1/tasks/{task['id']}/artifacts", headers=headers(BOB)))
    assert {a["kind"] for a in arts["items"]} >= {"SUMMARY", "PATCH"}
    # No money: usage recorded at zero cost, never billable.
    assert (
        await fetch_val(
            xctx,
            "SELECT sum(estimated_cost_cents) FROM usage_records u JOIN task_attempts a "
            "ON a.id=u.task_attempt_id WHERE a.task_id=:t",
            t=uuid.UUID(task["id"]),
        )
        == 0
    )
    usage = await ok(await xclient.get("/v1/subscriptions/usage", headers=headers(ALICE)))
    item = usage["items"][0]
    assert item["tasks_completed"] == 1
    assert item["plan_remaining"] == "NOT_AVAILABLE" and item["capacity_state"] == "UNKNOWN"
    # Strangers see nothing.
    for outsider in (CAROL, ADMIN):
        r = await xclient.get(f"/v1/experimental-tasks/{task['id']}", headers=headers(outsider))
        assert r.status_code == 404


async def test_advance_consent_skips_per_task_approval(xclient, xctx):
    gid, conn = await world(
        xclient, require_owner_approval=False, advance_consent_requester_ids=[str(BOB)]
    )
    task = await submit(xclient, gid, conn)
    assert task["approval_status"] == "APPROVED" and task["approval_source"] == "ADVANCE_CONSENT"
    await sim(xctx).run_once()
    assert (await get_task(xclient, task["id"]))["status"] == "SUCCEEDED"


# ====================================================================== acceptance 2, 3
async def test_sharing_cannot_be_enabled_without_consent(xclient):
    gid = await make_group(xclient)
    await invite_join(xclient, gid, ALICE)
    conn = await ok(
        await xclient.post(
            "/v1/subscriptions/connect",
            json={"worker_id": str(ALICE_NODE), "device_label": "laptop", "auth_method": "CHATGPT"},
            headers=headers(ALICE),
        ),
        201,
    )
    enable = {"connection_id": conn["id"], "terms_version": TERMS}
    r = await xclient.post(
        "/v1/sharing/enable",
        json={**enable, "acknowledgements": SHARE_ACKS},
        headers=headers(ALICE),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "PREFERENCES_REQUIRED"
    await ok(
        await xclient.put(
            "/v1/sharing/preferences",
            json={"connection_id": conn["id"], "group_id": gid},
            headers=headers(ALICE),
        )
    )
    for acks in ({}, {**SHARE_ACKS, "no_payment_or_compensation": False}, JOIN_ACKS):
        r = await xclient.post(
            "/v1/sharing/enable", json={**enable, "acknowledgements": acks}, headers=headers(ALICE)
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "CONSENT_REQUIRED"
    r = await xclient.post(
        "/v1/sharing/enable",
        json={**enable, "terms_version": "old", "acknowledgements": SHARE_ACKS},
        headers=headers(ALICE),
    )
    assert r.json()["error"]["code"] == "TERMS_VERSION_MISMATCH"
    status = await ok(await xclient.get("/v1/subscriptions/status", headers=headers(ALICE)))
    assert status["items"][0]["preferences"]["sharing_state"] == "DISABLED"


async def test_server_never_accepts_credentials_or_api_key_logins(xclient, xctx):
    base = {"worker_id": str(ALICE_NODE), "device_label": "laptop", "auth_method": "CHATGPT"}
    for leak in ("access_token", "refresh_token", "api_key", "cookies", "password", "id_token"):
        r = await xclient.post(
            "/v1/subscriptions/connect", json={**base, leak: "secret-value"}, headers=headers(ALICE)
        )
        assert r.status_code == 422, leak
    for method in ("API_KEY", "NOT_LOGGED_IN", "UNKNOWN"):
        r = await xclient.post(
            "/v1/subscriptions/connect",
            json={**base, "auth_method": method},
            headers=headers(ALICE),
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "SUBSCRIPTION_AUTH_REQUIRED"
    # Nobody can attach someone else's (or a non-personal) runtime.
    for user, worker in ((BOB, ALICE_NODE), (ALICE, PUBLIC_WORKER)):
        r = await xclient.post(
            "/v1/subscriptions/connect",
            json={**base, "worker_id": str(worker)},
            headers=headers(user),
        )
        assert r.status_code == 404
    # No credential-shaped column exists anywhere in the experimental schema.
    cols = await fetch_val(
        xctx,
        "SELECT string_agg(column_name, ',') FROM information_schema.columns WHERE table_name IN "
        "('subscription_connections','subscription_sharing_preferences','experimental_authorizations',"
        "'subscription_usage_observations','experimental_task_consents')",
    )
    names = [n for n in cols.split(",") if not n.endswith("_tokens")]  # usage counts only
    for word in ("token", "secret", "password", "cookie", "api_key", "credential"):
        assert not [n for n in names if word in n], word


# ====================================================================== acceptance 4
async def test_non_members_cannot_submit(xclient, xctx):
    gid, conn = await world(xclient)
    r = await xclient.post(
        "/v1/experimental-tasks", json=task_body(gid, conn), headers=headers(CAROL, "exp-carol-1")
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_A_MEMBER"
    # Invited but not joined (no consent) is not enough either.
    await ok(
        await xclient.post(
            f"/v1/experiments/groups/{gid}/invite",
            json={"user_id": str(CAROL)},
            headers=headers(ADMIN),
        ),
        201,
    )
    r = await xclient.post(
        "/v1/experimental-tasks", json=task_body(gid, conn), headers=headers(CAROL, "exp-carol-2")
    )
    assert r.status_code == 403
    # Uninvited users cannot join or even see the group.
    r = await xclient.post(
        f"/v1/experiments/groups/{gid}/join",
        json={"terms_version": TERMS, "acknowledgements": JOIN_ACKS},
        headers=headers(ADMIN),
    )
    assert r.status_code == 404
    # A member removed between submission and dispatch is denied at dispatch time.
    task = await submit(xclient, gid, conn)
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE))
    )
    await execute(
        xctx,
        "UPDATE experimental_group_members SET status='REMOVED', removed_at=now() WHERE user_id=:u",
        u=BOB,
    )
    await sim(xctx).run_once()
    t = await get_task(xclient, task["id"])
    assert t["status"] == "FAILED" and "REQUESTER_NOT_MEMBER" in t["last_decision"]["reasons"]


# ====================================================================== acceptance 5
async def test_owner_can_reject(xclient, xctx):
    gid, conn = await world(xclient)
    task = await submit(xclient, gid, conn)
    r = await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(BOB))
    assert r.status_code == 404  # only the owner decides
    t = await ok(
        await xclient.post(f"/v1/experimental-tasks/{task['id']}/reject", headers=headers(ALICE))
    )
    assert t["status"] == "CANCELLED" and t["approval_status"] == "REJECTED"
    await sim(xctx).run_once()
    assert (
        await fetch_val(
            xctx, "SELECT count(*) FROM task_attempts WHERE task_id=:t", t=uuid.UUID(task["id"])
        )
        == 0
    )
    r = await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE))
    assert r.status_code == 409


# ====================================================================== acceptance 6
async def test_paused_provider_receives_no_new_tasks(xclient, xctx):
    gid, conn = await world(xclient)
    task = await submit(xclient, gid, conn)
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE))
    )
    await ok(
        await xclient.post(
            "/v1/sharing/pause", json={"connection_id": conn}, headers=headers(ALICE)
        )
    )
    for _ in range(2):
        await sim(xctx).run_once()
    t = await get_task(xclient, task["id"])
    assert t["status"] == "QUEUED" and t["last_decision"]["reasons"] == ["OWNER_PAUSED"]
    # Identical repeated denials are audited once.
    assert (
        await fetch_val(
            xctx,
            "SELECT count(*) FROM experimental_dispatch_audit "
            "WHERE task_id=:t AND reasons ? 'OWNER_PAUSED'",
            t=uuid.UUID(task["id"]),
        )
        == 1
    )
    # New submissions to a paused node wait, with the reason (D-93).
    r = await xclient.post(
        "/v1/experimental-tasks", json=task_body(gid, conn), headers=headers(BOB, "exp-paused-1")
    )
    assert r.status_code == 202, r.text
    assert (await get_task(xclient, r.json()["id"]))["last_decision"]["reasons"] == ["OWNER_PAUSED"]
    # Resuming requires fresh consent and then the queued task runs.
    await ok(
        await xclient.post(
            "/v1/sharing/enable",
            json={"connection_id": conn, "terms_version": TERMS, "acknowledgements": SHARE_ACKS},
            headers=headers(ALICE),
        )
    )
    await sim(xctx).run_once()
    assert (await get_task(xclient, task["id"]))["status"] == "SUCCEEDED"


# ====================================================================== acceptance 7
async def test_revocation_cancels_and_prevents_assignment(xclient, xctx):
    gid, conn = await world(xclient)
    queued = await submit(xclient, gid, conn)
    reserved = await submit(xclient, gid, conn)
    for t in (reserved, queued):
        await ok(
            await xclient.post(f"/v1/experimental-tasks/{t['id']}/approve", headers=headers(ALICE))
        )
    s = sim(xctx)
    await s.heartbeat()
    # Reserve one attempt on Alice's node without running it.
    assert await s.dispatcher.dispatch_once((ALICE_NODE,)) is not None
    pending = await submit(xclient, gid, conn)

    r = await xclient.post("/v1/sharing/revoke", json={"connection_id": conn}, headers=headers(BOB))
    assert r.status_code == 404  # only the owner can revoke
    await ok(
        await xclient.post(
            "/v1/sharing/revoke", json={"connection_id": conn}, headers=headers(ALICE)
        )
    )
    statuses = {
        t["id"]: (await get_task(xclient, t["id"]))["status"] for t in (queued, reserved, pending)
    }
    assert set(statuses.values()) == {"CANCELLED"}
    assert (await get_task(xclient, pending["id"]))["approval_status"] == "WITHDRAWN"
    assert (
        await fetch_val(xctx, "SELECT active_tasks FROM worker_nodes WHERE id=:w", w=ALICE_NODE)
        == 0
    )
    r = await xclient.post(
        "/v1/experimental-tasks",
        json=task_body(gid, conn),
        headers=headers(BOB, "exp-after-revoke"),
    )
    assert r.status_code == 404
    await s.run_once()
    assert (
        await fetch_val(
            xctx, "SELECT count(*) FROM task_attempts WHERE status IN ('RESERVED','RUNNING')"
        )
        == 0
    )


async def test_revocation_stops_a_running_task(xclient, xctx):
    gid, conn = await world(xclient)
    task = await submit(xclient, gid, conn)
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE))
    )
    s = sim(xctx)
    await s.heartbeat()
    attempt = await s.dispatcher.dispatch_once((ALICE_NODE,))
    lease = await fetch_val(xctx, "SELECT lease_id FROM task_attempts WHERE id=:a", a=attempt)
    leases = LeaseService(xctx)
    await leases.accept(lease, ALICE_NODE)
    await ok(
        await xclient.post(
            "/v1/sharing/revoke", json={"connection_id": conn}, headers=headers(ALICE)
        )
    )
    # The node's next progress report learns it must stop.
    assert await leases.progress(lease, ALICE_NODE, "working", 50) is False
    assert (await get_task(xclient, task["id"]))["status"] == "CANCELLING"


async def test_operator_kill_switch(xclient, xctx):
    gid, conn = await world(xclient)
    task = await submit(xclient, gid, conn)
    r = await xclient.post("/v1/experiments/kill-switch", headers=headers(ALICE))
    assert r.status_code == 403
    out = await ok(await xclient.post("/v1/experiments/kill-switch", headers=headers(ADMIN)))
    assert out == {"groups_disabled": 1, "tasks_cancelled": 1}
    assert (await get_task(xclient, task["id"]))["status"] == "CANCELLED"
    r = await xclient.post(
        "/v1/experimental-tasks", json=task_body(gid, conn), headers=headers(BOB, "exp-after-kill")
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "GROUP_DISABLED"


# ====================================================================== acceptance 8
@pytest.mark.parametrize("case", ["unverified", "expired", "revoked", "missing", "group_off"])
async def test_unauthorized_or_expired_experiment_cannot_run(xclient, xctx, case):
    gid = await make_group(xclient)
    await invite_join(xclient, gid, ALICE, BOB)
    await ok(
        await xclient.post(
            f"/v1/experiments/groups/{gid}/sharing", json={"enabled": True}, headers=headers(ADMIN)
        )
    )
    if case != "missing":
        auth_id = await authorize(xclient, gid, verify=case != "unverified")
        if case == "expired":
            await execute(
                xctx,
                "UPDATE experimental_authorizations SET created_at=now()-"
                "interval '2 days', expires_at=now()-interval '1 day' WHERE id=:i",
                i=uuid.UUID(auth_id),
            )
        if case == "revoked":
            await ok(
                await xclient.post(
                    f"/v1/experiments/authorizations/{auth_id}/revoke", headers=headers(ADMIN)
                )
            )
    conn = await connect_and_share(xclient, gid)
    if case == "group_off":
        await authorize(xclient, gid)
        await ok(
            await xclient.post(
                f"/v1/experiments/groups/{gid}/sharing",
                json={"enabled": False},
                headers=headers(ADMIN),
            )
        )
    task = await submit(xclient, gid, conn)
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE))
    )
    await sim(xctx).run_once()
    t = await get_task(xclient, task["id"])
    expected = {
        "unverified": ("FAILED", "AUTHORIZATION_NOT_VERIFIED"),
        "expired": ("FAILED", "AUTHORIZATION_EXPIRED"),
        "revoked": ("FAILED", "AUTHORIZATION_REVOKED"),
        "missing": ("FAILED", "AUTHORIZATION_MISSING"),
        "group_off": ("QUEUED", "GROUP_SHARING_DISABLED"),
    }[case]
    assert t["status"] == expected[0] and expected[1] in t["last_decision"]["reasons"]
    assert (
        await fetch_val(
            xctx, "SELECT count(*) FROM task_attempts WHERE task_id=:t", t=uuid.UUID(task["id"])
        )
        == 0
    )


async def test_experiment_disabled_globally(tmp_path, migrated_database):
    ctx = build_context(make_settings(tmp_path, experiment_enabled=False))
    await seed_fixtures(ctx)
    try:
        transport = httpx.ASGITransport(app=create_app(ctx.settings, ctx))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            r = await c.post(
                "/v1/experimental-tasks",
                json=task_body(str(uuid.uuid4()), str(uuid.uuid4())),
                headers=headers(BOB, "exp-disabled-1"),
            )
            assert r.status_code == 403 and r.json()["error"]["code"] == "EXPERIMENT_DISABLED"
    finally:
        await ctx.aclose()


async def test_only_the_operator_manages_groups_and_authorizations(xclient):
    r = await xclient.post("/v1/experiments/groups", json={"name": "x"}, headers=headers(ALICE))
    assert r.status_code == 403
    gid = await make_group(xclient)
    for path, body in (
        (f"/v1/experiments/groups/{gid}/invite", {"user_id": str(CAROL)}),
        (f"/v1/experiments/groups/{gid}/sharing", {"enabled": True}),
    ):
        assert (await xclient.post(path, json=body, headers=headers(ALICE))).status_code == 403
    auth_id = await authorize(xclient, gid, verify=False)
    r = await xclient.post(
        f"/v1/experiments/authorizations/{auth_id}/verify",
        json={"verification_status": "OPERATOR_VERIFIED"},
        headers=headers(ALICE),
    )
    assert r.status_code == 403
    # Free-text correspondence cannot be stored as the reference.
    r = await xclient.post(
        f"/v1/experiments/groups/{gid}/authorizations",
        json={
            "provider_name": "openai-codex",
            "authorization_source": "VENDOR_SUPPORT_CORRESPONDENCE",
            "authorization_reference": "Dear user,\nwe confirm",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
        headers=headers(ADMIN),
    )
    assert r.status_code == 422


async def test_group_membership_is_capped(xclient, xctx):
    r = await xclient.post(
        "/v1/experiments/groups", json={"name": "big", "max_members": 50}, headers=headers(ADMIN)
    )
    assert r.status_code == 422
    gid = await make_group(xclient, max_members=2)
    await invite_join(xclient, gid, ALICE, BOB)
    r = await xclient.post(
        f"/v1/experiments/groups/{gid}/invite", json={"user_id": str(CAROL)}, headers=headers(ADMIN)
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "GROUP_FULL"


# ====================================================================== acceptance 9
async def test_personal_subscription_never_routed_through_public_marketplace(xclient, xctx):
    gid, conn = await world(xclient)
    # A public task with only Alice's personal node available stays QUEUED.
    await execute(xctx, "UPDATE worker_nodes SET status='OFFLINE' WHERE id=:w", w=PUBLIC_WORKER)
    public = (await create_task(xclient, user=BOB)).json()
    await drain_worker(xctx, nodes=(ALICE_NODE,))
    await sim(xctx).run_once()
    assert (
        await fetch_val(xctx, "SELECT status FROM tasks WHERE id=:t", t=uuid.UUID(public["id"]))
        == "QUEUED"
    )
    # And an experimental task is never picked by the public worker/dispatcher.
    await execute(
        xctx,
        "UPDATE worker_nodes SET status='IDLE', last_heartbeat=now() WHERE id=:w",
        w=PUBLIC_WORKER,
    )
    exp = await submit(xclient, gid, conn)
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{exp['id']}/approve", headers=headers(ALICE))
    )
    await drain_worker(xctx)
    assert (await get_task(xclient, exp["id"]))["status"] == "QUEUED"
    assert (
        await fetch_val(xctx, "SELECT status FROM tasks WHERE id=:t", t=uuid.UUID(public["id"]))
        == "SUCCEEDED"
    )
    # Database invariants hold even against direct writes.
    with pytest.raises(DBAPIError):
        await execute(
            xctx,
            "UPDATE resources SET public_eligible=true, "
            "authorization_status='APPROVED' WHERE id=:r",
            r=ALICE_RES,
        )
    with pytest.raises(DBAPIError):
        await execute(
            xctx, "UPDATE tasks SET execution_profile='CODE' WHERE id=:t", t=uuid.UUID(exp["id"])
        )
    with pytest.raises(DBAPIError):
        await execute(
            xctx, "UPDATE tasks SET task_type='CODE_REVIEW' WHERE id=:t", t=uuid.UUID(public["id"])
        )


# ====================================================================== acceptance 11
async def test_failed_task_never_reports_verified_success(xclient, xctx):
    gid, conn = await world(xclient)
    for marker in ("[mock:fail]", "[mock:bad-patch]"):
        task = await submit(xclient, gid, conn, prompt=f"Review {marker}")
        await ok(
            await xclient.post(
                f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE)
            )
        )
        await sim(xctx).run_once()
        assert (await get_task(xclient, task["id"]))["status"] == "FAILED"
        arts = await ok(
            await xclient.get(f"/v1/tasks/{task['id']}/artifacts", headers=headers(BOB))
        )
        assert arts["items"] == []


async def test_interrupted_task_does_not_succeed(xclient, xctx):
    gid, conn = await world(xclient)
    task = await submit(xclient, gid, conn)
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE))
    )
    s = sim(xctx)
    await s.heartbeat()
    attempt = await s.dispatcher.dispatch_once((ALICE_NODE,))
    lease = await fetch_val(xctx, "SELECT lease_id FROM task_attempts WHERE id=:a", a=attempt)
    await LeaseService(xctx).accept(lease, ALICE_NODE)
    # The owner's device vanishes mid-run: the lease lapses and recovery takes over.
    await execute(
        xctx,
        "UPDATE task_attempts SET leased_until=now()-interval '1 second' WHERE id=:a",
        a=attempt,
    )
    await LeaseService(xctx).recover_expired()
    t = await get_task(xclient, task["id"])
    # A lost device is an interruption: re-run once (design §6), never published as done.
    assert t["status"] == "RETRY_PENDING"
    assert (
        await fetch_val(
            xctx, "SELECT count(*) FROM artifacts WHERE task_id=:t", t=uuid.UUID(task["id"])
        )
        == 0
    )


# ====================================================================== acceptance 12
async def test_rate_limit_is_recorded_and_pauses_assignment(xclient, xctx):
    gid, conn = await world(xclient, max_tasks_per_day=5)
    limited = await submit(xclient, gid, conn, prompt="Review [sim:rate-limit]")
    await ok(
        await xclient.post(
            f"/v1/experimental-tasks/{limited['id']}/approve", headers=headers(ALICE)
        )
    )
    await sim(xctx).run_once()
    t = await get_task(xclient, limited["id"])
    assert t["status"] == "FAILED" and "usage limit" in (t["result_summary"] or "").lower()
    # Exactly one attempt: never retried on another account or credential.
    assert (
        await fetch_val(
            xctx, "SELECT count(*) FROM task_attempts WHERE task_id=:t", t=uuid.UUID(limited["id"])
        )
        == 1
    )
    nxt = await submit(xclient, gid, conn)
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{nxt['id']}/approve", headers=headers(ALICE))
    )
    await sim(xctx).run_once()
    t = await get_task(xclient, nxt["id"])
    assert t["status"] == "QUEUED" and t["last_decision"]["reasons"] == ["RATE_LIMITED"]
    usage = (await ok(await xclient.get("/v1/subscriptions/usage", headers=headers(ALICE))))[
        "items"
    ][0]
    assert usage["throttling_errors"] == 1 and usage["rate_limited_until"] is not None
    assert usage["capacity_state"] == "LIMITED" and usage["plan_remaining"] == "NOT_AVAILABLE"
    assert usage["reset_hint"] == "try again in 1 hour"


async def test_daily_and_duration_limits(xclient, xctx):
    gid, conn = await world(xclient, max_tasks_per_day=1, max_task_duration_seconds=300)
    r = await xclient.post(
        "/v1/experimental-tasks",
        json=task_body(gid, conn, max_duration_seconds=301),
        headers=headers(BOB, "exp-too-long"),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "DURATION_LIMIT"
    r = await xclient.put(
        "/v1/sharing/preferences",
        json={"connection_id": conn, "group_id": gid, "max_task_duration_seconds": 3600},
        headers=headers(ALICE),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "LIMIT_ABOVE_GROUP_MAXIMUM"
    first, second = await submit(xclient, gid, conn), await submit(xclient, gid, conn)
    for t in (first, second):
        await ok(
            await xclient.post(f"/v1/experimental-tasks/{t['id']}/approve", headers=headers(ALICE))
        )
    await sim(xctx).run_once()  # second is held by CONCURRENCY_LIMIT while first runs
    assert (await get_task(xclient, first["id"]))["status"] == "SUCCEEDED"
    await sim(xctx).run_once()
    t = await get_task(xclient, second["id"])
    assert t["status"] == "QUEUED" and t["last_decision"]["reasons"] == ["DAILY_LIMIT"]


async def test_limits_only_update_keeps_mac_app_settings(xclient, xctx):
    # The web "configure" form sends only daily and duration limits; whatever the
    # owner set in the Mac app (concurrency, kinds, approval, consent) must survive.
    kinds = ["CODE_CHANGE", "CODE_REVIEW", "TRANSLATE_DOCUMENT"]
    gid = await make_group(xclient, max_concurrent_tasks=3, allowed_task_types=kinds)
    await invite_join(xclient, gid, ALICE, BOB)
    await authorize(xclient, gid)
    await ok(
        await xclient.post(
            f"/v1/experiments/groups/{gid}/sharing",
            json={"enabled": True},
            headers=headers(ADMIN),
        )
    )
    conn = await connect_and_share(
        xclient,
        gid,
        max_concurrent_tasks=3,
        allowed_task_types=kinds,
        require_owner_approval=False,
        advance_consent_requester_ids=[str(BOB)],
    )
    prefs = await ok(
        await xclient.put(
            "/v1/sharing/preferences",
            json={
                "connection_id": conn,
                "group_id": gid,
                "max_tasks_per_day": 2,
                "max_task_duration_seconds": 120,
            },
            headers=headers(ALICE),
        )
    )
    assert prefs["max_tasks_per_day"] == 2 and prefs["max_task_duration_seconds"] == 120
    assert prefs["max_concurrent_tasks"] == 3
    assert prefs["allowed_task_types"] == kinds
    assert prefs["require_owner_approval"] is False
    assert prefs["advance_consent_requester_ids"] == [str(BOB)]


async def test_expired_owner_login_blocks_assignment(xclient, xctx):
    gid, conn = await world(xclient)
    first = await submit(xclient, gid, conn, prompt="Review [sim:auth-expired]")
    second = await submit(xclient, gid, conn)
    for t in (first, second):
        await ok(
            await xclient.post(f"/v1/experimental-tasks/{t['id']}/approve", headers=headers(ALICE))
        )
    await sim(xctx).run_once()
    assert (await get_task(xclient, first["id"]))["status"] == "FAILED"
    await sim(xctx).run_once()
    t = await get_task(xclient, second["id"])
    assert t["status"] == "QUEUED" and "SUBSCRIPTION_AUTH_INVALID" in t["last_decision"]["reasons"]
    status = await ok(await xclient.get("/v1/subscriptions/status", headers=headers(ALICE)))
    assert status["items"][0]["connection"]["auth_state"] == "EXPIRED"


# ====================================================================== acceptance 13
async def test_paid_credits_can_never_be_enabled(xclient, xctx):
    gid, conn = await world(xclient)
    r = await xclient.put(
        "/v1/sharing/preferences",
        json={"connection_id": conn, "group_id": gid, "allow_paid_credits": True},
        headers=headers(ALICE),
    )
    assert r.status_code == 422
    with pytest.raises(DBAPIError):
        await execute(xctx, "UPDATE subscription_sharing_preferences SET allow_paid_credits=true")
    # Experimental tasks carry no budget and accept none.
    r = await xclient.post(
        "/v1/experimental-tasks",
        json={**task_body(gid, conn), "max_budget_cents": 100},
        headers=headers(BOB, "exp-budget-1"),
    )
    assert r.status_code == 422


# ====================================================================== audit integrity
async def test_dispatch_audit_is_append_only(xclient, xctx):
    gid, conn = await world(xclient)
    task = await submit(xclient, gid, conn)
    await sim(xctx).run_once()
    assert await fetch_val(xctx, "SELECT count(*) FROM experimental_dispatch_audit") == 1
    for sql in (
        "UPDATE experimental_dispatch_audit SET decision='ALLOW'",
        "DELETE FROM experimental_dispatch_audit",
    ):
        with pytest.raises(DBAPIError):
            await execute(xctx, sql)
    with pytest.raises(DBAPIError):  # a LIVE allow can never be recorded
        await execute(
            xctx,
            "INSERT INTO experimental_dispatch_audit(task_id,group_id,decision,"
            "execution_mode) VALUES (:t,:g,'ALLOW','LIVE')",
            t=uuid.UUID(task["id"]),
            g=uuid.UUID(gid),
        )
    audit = await ok(
        await xclient.get(f"/v1/experiments/groups/{gid}/audit", headers=headers(ADMIN))
    )
    assert audit[0]["decision"] == "DENY" and audit[0]["execution_mode"] == "SIMULATED"
    r = await xclient.get(f"/v1/experiments/groups/{gid}/audit", headers=headers(ALICE))
    assert r.status_code == 403


# ====================================================================== contract shapes
async def test_experimental_responses_match_contract(xclient, xctx):
    from tests.integration.test_contract import schema_validator

    gid, conn = await world(xclient)
    task = await submit(xclient, gid, conn)
    schema_validator("#/components/schemas/ExperimentalTask").validate(task)
    await ok(
        await xclient.post(f"/v1/experimental-tasks/{task['id']}/approve", headers=headers(ALICE))
    )
    await sim(xctx).run_once()
    schema_validator("#/components/schemas/ExperimentalTask").validate(
        await get_task(xclient, task["id"])
    )
    for path, name, user in (
        (f"/v1/experiments/groups/{gid}", "ExperimentalGroup", BOB),
        ("/v1/subscriptions/status", "SubscriptionStatus", ALICE),
        ("/v1/subscriptions/usage", "SubscriptionUsageList", ALICE),
        (f"/v1/experimental-tasks/nodes?group_id={gid}", "ExperimentalNodeList", BOB),
        ("/v1/experimental-tasks?role=owner", "ExperimentalTaskList", ALICE),
    ):
        schema_validator(f"#/components/schemas/{name}").validate(
            await ok(await xclient.get(path, headers=headers(user)))
        )
