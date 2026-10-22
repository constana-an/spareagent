"""One-click sharing: provider signup, invite-code groups, standing approval, adjustable
limits and the online/offline switch. The owner's device is simulated by the test client;
no Codex or subscription is ever used."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import SecretStr

from apps.api.main import create_app
from services.context import build_context
from tests.conftest import (
    ADMIN,
    bearer,
    execute,
    fetch_val,
    headers,
    make_settings,
    seed_fixtures,
)
from tests.integration.test_experimental_live import authorize_live, poll
from tests.integration.test_experimental_sharing import (
    JOIN_ACKS,
    SHARE_ACKS,
    TERMS,
    make_group,
    ok,
    task_body,
)

pytestmark = pytest.mark.db
ORIGIN = {"Origin": "http://localhost:3000"}
SIGNUP_CODE = "test-signup-code-0123456789"


def settings(tmp_path, template_id=None, **overrides):
    values = {
        "experiment_enabled": True,
        "experiment_live_enabled": True,
        "friends_auth_enabled": True,
        **overrides,
    }
    if template_id is not None:
        values |= {
            "self_serve_enabled": True,
            "self_serve_signup_code": SecretStr(SIGNUP_CODE),
            "self_serve_authorization_id": template_id,
        }
    return make_settings(tmp_path, **values)


@asynccontextmanager
async def client_for(ctx):
    transport = httpx.ASGITransport(app=create_app(ctx.settings, ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


@pytest.fixture
async def octx(migrated_database, tmp_path):
    """The operator's verified authorization is the template self-serve groups copy."""
    base = build_context(settings(tmp_path))
    await seed_fixtures(base)
    async with client_for(base) as c:
        gid = await make_group(c)
        template = await authorize_live(c, gid)
    await base.aclose()
    ctx = build_context(settings(tmp_path, template_id=uuid.UUID(template)))
    try:
        yield ctx
    finally:
        await ctx.aclose()


@pytest.fixture
async def oc(octx):
    async with client_for(octx) as c:
        yield c


ONLINE = {
    "device_label": "Alice Mac",
    "subscription_type": "PLUS",
    "terms_version": TERMS,
    "acknowledgements": SHARE_ACKS,
    "codex_check_result": "OWNER_ONLY_SUBSCRIPTION_RUN_OK",
    "no_purchased_credits": True,
    "no_auto_reload": True,
    "auto_approve_members": True,
}


async def signup_device(c) -> dict:
    signup = await ok(
        await c.post(
            "/v1/self-serve/signup",
            json={
                "signup_code": SIGNUP_CODE,
                "device_label": "Alice Mac",
                "terms_version": TERMS,
                "acknowledgements": SHARE_ACKS,
            },
        ),
        201,
    )
    reg = await ok(
        await c.post(
            "/v1/workers/register",
            json={
                "resource_id": signup["resource_id"],
                "name": "alice-mac",
                "capabilities": {"executor": "codex-subscription"},
                "max_concurrency": 1,
            },
            headers=bearer(signup["enrollment_token"]),
        ),
        201,
    )
    return {**reg, **signup}


async def go_online(c, node, **overrides) -> dict:
    return await ok(
        await c.post(
            "/v1/workers/me/sharing/online",
            json={**ONLINE, **overrides},
            headers=bearer(node["access_token"]),
        )
    )


async def owner(c, node, method, path, **kw) -> httpx.Response:
    return await c.request(
        method, f"/v1/workers/me/sharing{path}", headers=bearer(node["access_token"]), **kw
    )


async def join(ctx, code, status=200) -> tuple[httpx.AsyncClient, dict]:
    """A newcomer opens the invite link in their own browser."""
    transport = httpx.ASGITransport(app=create_app(ctx.settings, ctx))
    c = httpx.AsyncClient(transport=transport, base_url="http://localhost")
    resp = await c.post(
        "/v1/friends/join-code",
        json={"code": code, "terms_version": TERMS, "acknowledgements": JOIN_ACKS},
        headers=ORIGIN,
    )
    assert resp.status_code == status, resp.text
    return c, resp.json()


async def submit_as(browser, status, connection_id, mode="LIVE") -> dict:
    resp = await browser.post(
        "/v1/experimental-tasks",
        json=task_body(status["group"]["id"], connection_id, execution_mode=mode),
        headers={**ORIGIN, "Idempotency-Key": f"oc-{uuid.uuid4().hex}"},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


# ====================================================================== happy path
async def test_one_click_online_invite_auto_approve_and_offline(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    code = status["invite_code"]
    assert code and len(code.replace("-", "")) == 12
    assert status["sharing"]["state"] == "ENABLED" and status["sharing"]["auto_approve_members"]
    assert status["group"]["sharing_enabled"] and status["live_ready"]
    assert status["group"]["member_cap"] == 5
    # Only the digest is stored.
    assert code not in str(
        await fetch_val(
            octx, "SELECT invite_code_digest FROM experimental_groups WHERE owner_id IS NOT NULL"
        )
    )
    # The copy of the operator record covers this group.
    assert (
        await fetch_val(
            octx,
            "SELECT count(*) FROM experimental_authorizations WHERE group_id=:g "
            "AND verification_status='OPERATOR_VERIFIED' AND integration_route_covered",
            g=uuid.UUID(status["group"]["id"]),
        )
        == 1
    )

    bob, me = await join(octx, code.lower().replace("-", ""))  # forgiving input
    try:
        assert me["role"] == "CONSUMER"
        nodes = await ok(
            await bob.get(f"/v1/experimental-tasks/nodes?group_id={status['group']['id']}")
        )
        target = nodes["items"][0]
        assert target["requires_approval"] is False
        task = await submit_as(bob, status, target["connection_id"])
        assert task["approval_status"] == "APPROVED"
        assert task["approval_source"] == "OWNER_STANDING_APPROVAL"
        # No owner click needed: the device receives the LIVE lease directly.
        lease = await poll(oc, node)
        assert lease.status_code == 200, lease.text
        assert lease.json()["execution_profile"] == "CODEX_LIVE"

        offline = await ok(await owner(oc, node, "POST", "/offline"))
        assert offline["sharing"]["state"] == "PAUSED"
        resp = await bob.post(
            "/v1/experimental-tasks",
            json=task_body(status["group"]["id"], target["connection_id"], execution_mode="LIVE"),
            headers={**ORIGIN, "Idempotency-Key": f"oc-{uuid.uuid4().hex}"},
        )
        # A paused device runs nothing new, but the request waits and says why (D-93).
        assert resp.status_code == 202, resp.text
        waiting = await ok(await bob.get(f"/v1/experimental-tasks/{resp.json()['id']}"))
        assert waiting["status"] == "QUEUED"
        assert waiting["last_decision"]["reasons"] == ["OWNER_PAUSED"]
        # One more click brings it back; the invite code is not re-issued.
        again = await go_online(oc, node)
        assert again["sharing"]["state"] == "ENABLED" and again["invite_code"] is None
    finally:
        await bob.aclose()


async def test_owner_sees_tasks_and_members_in_status(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    bob, _ = await join(octx, status["invite_code"])
    try:
        conn = status["sharing"]["connection_id"]
        await submit_as(bob, status, conn, mode="SIMULATED")
        view = await ok(await owner(oc, node, "GET", ""))
        assert len(view["members"]) == 2 and sum(m["is_owner"] for m in view["members"]) == 1
        assert view["tasks"][0]["approval_source"] == "OWNER_STANDING_APPROVAL"
        assert view["invite_code"] is None  # never re-disclosed
    finally:
        await bob.aclose()


# ====================================================================== limits
async def test_member_and_task_limits_are_adjustable_within_operator_cap(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    code = status["invite_code"]
    bob, _ = await join(octx, code)
    carol, _ = await join(octx, code)
    try:
        too_big = await owner(oc, node, "PUT", "/settings", json={"max_members": 6})
        assert (
            too_big.status_code == 422
            and too_big.json()["error"]["code"] == "MEMBER_LIMIT_OUT_OF_RANGE"
        )
        below_current = await owner(oc, node, "PUT", "/settings", json={"max_members": 2})
        assert below_current.status_code == 422  # three people are already in
        view = await ok(
            await owner(
                oc,
                node,
                "PUT",
                "/settings",
                json={"max_members": 3, "max_tasks_per_day": 12, "max_task_duration_seconds": 900},
            )
        )
        assert view["group"]["max_members"] == 3
        assert view["group"]["max_tasks_per_day"] == 12
        assert (
            await fetch_val(
                octx,
                "SELECT max_tasks_per_day FROM subscription_sharing_preferences "
                "WHERE connection_id=:c",
                c=uuid.UUID(view["sharing"]["connection_id"]),
            )
            == 12
        )
        _, full = await join(octx, code, status=409)
        assert full["error"]["code"] == "GROUP_FULL"
        # Raising the limit again lets the next person in.
        await ok(await owner(oc, node, "PUT", "/settings", json={"max_members": 4}))
        dave, _ = await join(octx, code)
        await dave.aclose()
    finally:
        await bob.aclose()
        await carol.aclose()


async def test_switching_auto_approval_off_holds_queued_live_tasks(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    bob, _ = await join(octx, status["invite_code"])
    try:
        conn = status["sharing"]["connection_id"]
        held = await submit_as(bob, status, conn)
        assert held["approval_source"] == "OWNER_STANDING_APPROVAL"
        await ok(await owner(oc, node, "PUT", "/settings", json={"auto_approve_members": False}))
        assert (await poll(oc, node)).status_code == 204  # standing approval withdrawn
        later = await submit_as(bob, status, conn)
        assert later["approval_status"] == "PENDING"
        await ok(await owner(oc, node, "PUT", "/settings", json={"auto_approve_members": True}))
        assert (await poll(oc, node)).status_code == 200  # the held task runs again
    finally:
        await bob.aclose()


# ====================================================================== invite codes / members
async def test_rotating_or_removing_invalidates_the_old_code(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    old = status["invite_code"]
    bob, bob_me = await join(octx, old)
    try:
        rotated = await ok(await owner(oc, node, "POST", "/invite-code"))
        assert rotated["invite_code"] and rotated["invite_code"] != old
        await join(octx, old, status=404)
        conn = status["sharing"]["connection_id"]
        await ok(await owner(oc, node, "PUT", "/settings", json={"auto_approve_members": False}))
        pending = await submit_as(bob, status, conn)
        removed = await ok(await owner(oc, node, "DELETE", f"/members/{bob_me['user_id']}"))
        assert removed["invite_code"] != rotated["invite_code"]
        assert [m["is_owner"] for m in removed["members"]] == [True]
        assert (
            await fetch_val(
                octx, "SELECT status FROM tasks WHERE id=:t", t=uuid.UUID(pending["id"])
            )
            == "CANCELLED"
        )
        # The removed member's browser session no longer works.
        assert (await bob.get("/v1/friends/me")).status_code == 401
        cannot = await owner(oc, node, "DELETE", f"/members/{status['members'][0]['user_id']}")
        assert cannot.status_code == 422
    finally:
        await bob.aclose()


async def test_dashboard_login_for_the_owner(oc, octx):
    node = await signup_device(oc)
    await go_online(oc, node)
    login = await ok(await owner(oc, node, "POST", "/dashboard-login"))
    async with client_for(octx) as browser:
        me = await browser.post("/v1/friends/login", json={"token": login["token"]}, headers=ORIGIN)
        assert me.status_code == 200 and me.json()["role"] == "PROVIDER"
        assert me.json()["user_id"] == node["user_id"]


async def expire_sessions(ctx, user_id) -> None:
    await execute(
        ctx,
        "UPDATE friends_sessions SET expires_at = now() - interval '1 hour' WHERE user_id = :u",
        u=uuid.UUID(user_id),
    )


async def members_of(oc, node) -> list[str]:
    status = await ok(await owner(oc, node, "GET", ""))
    return sorted(m["user_id"] for m in status["members"])


async def test_member_session_is_long_and_rejoining_keeps_the_same_account(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    bob, bob_me = await join(octx, status["invite_code"])
    try:
        cookie = next(c for c in bob.cookies.jar if c.name == "sa_friends_session")
        assert cookie.expires - time.time() > 399 * 86400  # outlives the 90-day session
        days = await fetch_val(
            octx,
            "SELECT round(extract(epoch FROM expires_at - created_at) / 86400) "
            "FROM friends_sessions "
            "WHERE user_id = :u",
            u=uuid.UUID(bob_me["user_id"]),
        )
        assert days == octx.settings.friends_member_session_days
        task = await submit_as(bob, status, status["sharing"]["connection_id"])
        before = await members_of(oc, node)

        # Months later the session has expired; the browser still holds the cookie.
        await expire_sessions(octx, bob_me["user_id"])
        assert (await bob.get("/v1/friends/me")).status_code == 401
        again = await bob.post(
            "/v1/friends/join-code",
            json={
                "code": status["invite_code"],
                "terms_version": TERMS,
                "acknowledgements": JOIN_ACKS,
            },
            headers=ORIGIN,
        )
        assert again.status_code == 200, again.text
        assert again.json()["user_id"] == bob_me["user_id"]
        assert await members_of(oc, node) == before  # no extra seat taken
        mine = (await bob.get("/v1/experimental-tasks")).json()["items"]
        assert task["id"] in [t["id"] for t in mine]  # history kept
    finally:
        await bob.aclose()


async def test_removed_member_rejoins_with_a_new_code_as_the_same_account(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    bob, bob_me = await join(octx, status["invite_code"])
    try:
        removed = await ok(await owner(oc, node, "DELETE", f"/members/{bob_me['user_id']}"))
        assert (await bob.get("/v1/friends/me")).status_code == 401
        back = await bob.post(
            "/v1/friends/join-code",
            json={
                "code": removed["invite_code"],
                "terms_version": TERMS,
                "acknowledgements": JOIN_ACKS,
            },
            headers=ORIGIN,
        )
        assert back.status_code == 200, back.text
        assert back.json()["user_id"] == bob_me["user_id"]
        assert len(await members_of(oc, node)) == 2
    finally:
        await bob.aclose()


async def test_after_logout_the_browser_no_longer_identifies_the_member(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    bob, bob_me = await join(octx, status["invite_code"])
    try:
        stale = bob.cookies.get("sa_friends_session")
        assert (await bob.post("/v1/friends/logout", headers=ORIGIN)).status_code == 204
        # Even a copy of the old cookie gives nothing once logged out.
        bob.cookies.set("sa_friends_session", stale)
        assert (await bob.get("/v1/friends/me")).status_code == 401
        assert (
            await bob.post(
                "/v1/friends/join-code",
                json={
                    "code": status["invite_code"],
                    "terms_version": TERMS,
                    "acknowledgements": JOIN_ACKS,
                },
                headers=ORIGIN,
            )
        ).json()["user_id"] != bob_me["user_id"]
    finally:
        await bob.aclose()


# ====================================================================== gates
async def test_signup_requires_the_bundle_code_and_acknowledgements(oc):
    body = {
        "signup_code": "wrong-code-wrong-code-123",
        "device_label": "Mallory",
        "terms_version": TERMS,
        "acknowledgements": SHARE_ACKS,
    }
    assert (await oc.post("/v1/self-serve/signup", json=body)).status_code == 401
    body |= {"signup_code": SIGNUP_CODE, "acknowledgements": JOIN_ACKS}
    resp = await oc.post("/v1/self-serve/signup", json=body)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "CONSENT_REQUIRED"


async def test_self_serve_is_off_unless_the_operator_enables_it(migrated_database, tmp_path):
    ctx = build_context(settings(tmp_path))
    await seed_fixtures(ctx)
    try:
        async with client_for(ctx) as c:
            resp = await c.post(
                "/v1/self-serve/signup",
                json={
                    "signup_code": SIGNUP_CODE,
                    "device_label": "x",
                    "terms_version": TERMS,
                    "acknowledgements": SHARE_ACKS,
                },
            )
            assert resp.status_code == 404
            join = await c.post(
                "/v1/friends/join-code",
                json={
                    "code": "ABCD-EFGH-JKLM",
                    "terms_version": TERMS,
                    "acknowledgements": JOIN_ACKS,
                },
                headers=ORIGIN,
            )
            assert join.status_code == 404
    finally:
        await ctx.aclose()


async def test_revoked_template_blocks_new_signups(oc, octx):
    template = await fetch_val(
        octx,
        "SELECT id FROM experimental_authorizations WHERE group_id IN "
        "(SELECT id FROM experimental_groups WHERE owner_id IS NULL)",
    )
    await ok(
        await oc.post(f"/v1/experiments/authorizations/{template}/revoke", headers=headers(ADMIN))
    )
    resp = await oc.post(
        "/v1/self-serve/signup",
        json={
            "signup_code": SIGNUP_CODE,
            "device_label": "Alice Mac",
            "terms_version": TERMS,
            "acknowledgements": SHARE_ACKS,
        },
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "SELF_SERVE_NOT_AUTHORIZED"


async def test_join_code_requires_dashboard_origin(oc):
    resp = await oc.post(
        "/v1/friends/join-code",
        json={"code": "ABCD-EFGH-JKLM", "terms_version": TERMS, "acknowledgements": JOIN_ACKS},
    )
    assert resp.status_code == 403


# ====================================================== template revocation (D-73)
async def revoke_template(oc, octx):
    template = octx.settings.self_serve_authorization_id
    await ok(
        await oc.post(f"/v1/experiments/authorizations/{template}/revoke", headers=headers(ADMIN))
    )


async def test_revoked_template_refuses_new_live_tasks_in_existing_group(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    bob, _ = await join(octx, status["invite_code"])
    try:
        await revoke_template(oc, octx)
        view = await ok(await owner(oc, node, "GET", ""))
        assert view["live_ready"] is False
        resp = await bob.post(
            "/v1/experimental-tasks",
            json=task_body(
                status["group"]["id"], status["sharing"]["connection_id"], execution_mode="LIVE"
            ),
            headers={**ORIGIN, "Idempotency-Key": "revoked-template-new-task"},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "LIVE_NOT_AUTHORIZED"
    finally:
        await bob.aclose()


async def test_revoked_template_stops_already_queued_live_task(oc, octx):
    node = await signup_device(oc)
    status = await go_online(oc, node)
    bob, _ = await join(octx, status["invite_code"])
    try:
        await submit_as(bob, status, status["sharing"]["connection_id"])
        await revoke_template(oc, octx)
        lease = await poll(oc, node)
        assert lease.status_code != 200, lease.text
    finally:
        await bob.aclose()
