"""Real database/session tests; never invokes a subscription or real Codex."""

import uuid

import httpx
import pytest

from apps.api.main import create_app
from services.context import build_context
from tests.conftest import (
    ADMIN,
    USER_A,
    USER_B,
    execute,
    fetch_val,
    headers,
    make_settings,
    seed_fixtures,
)
from tests.integration.test_experimental_sharing import JOIN_ACKS, TERMS, make_group, ok

pytestmark = pytest.mark.db
ORIGIN = {"Origin": "http://localhost:3000"}


@pytest.fixture
async def fctx(migrated_database, tmp_path):
    ctx = build_context(make_settings(tmp_path, friends_auth_enabled=True, experiment_enabled=True))
    await seed_fixtures(ctx)
    yield ctx
    await ctx.aclose()


@pytest.fixture
async def fclient(fctx):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(fctx.settings, fctx)),
        base_url="http://localhost",
    ) as c:
        yield c


async def invite(c, user=USER_A):
    gid = await make_group(c)
    await ok(
        await c.post(
            f"/v1/experiments/groups/{gid}/invite",
            json={"user_id": str(user)},
            headers=headers(ADMIN),
        ),
        201,
    )
    r = await c.post("/v1/friends/invites", json={"user_id": str(user)}, headers=headers(ADMIN))
    assert r.status_code == 201, r.text
    return gid, r.json()["token"]


async def test_one_time_login_join_and_logout(fclient, fctx):
    gid, token = await invite(fclient)
    assert token not in str(await fetch_val(fctx, "SELECT token_digest FROM friends_login_invites"))
    logged = await fclient.post("/v1/friends/login", json={"token": token}, headers=ORIGIN)
    assert logged.status_code == 200
    cookie = logged.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert logged.headers["cache-control"] == "no-store"
    assert (await fclient.get("/v1/friends/me")).json()["user_id"] == str(USER_A)
    groups = await fclient.get("/v1/experiments/groups")
    assert [g["id"] for g in groups.json()] == [gid]
    joined = await fclient.post(
        f"/v1/experiments/groups/{gid}/join",
        json={"terms_version": TERMS, "acknowledgements": JOIN_ACKS},
        headers=ORIGIN,
    )
    assert joined.status_code == 200
    assert (
        await fclient.post("/v1/friends/login", json={"token": token}, headers=ORIGIN)
    ).status_code == 401
    old = fclient.cookies.get("sa_friends_session")
    assert (await fclient.post("/v1/friends/logout", headers=ORIGIN)).status_code == 204
    fclient.cookies.set("sa_friends_session", old)
    assert (await fclient.get("/v1/friends/me")).status_code == 401


async def test_login_invites_require_operator_and_membership(fclient):
    assert (
        await fclient.post(
            "/v1/friends/invites", json={"user_id": str(USER_A)}, headers=headers(USER_B)
        )
    ).status_code == 403
    assert (
        await fclient.post(
            "/v1/friends/invites", json={"user_id": str(USER_A)}, headers=headers(ADMIN)
        )
    ).status_code == 404
    assert (
        await fclient.post("/v1/friends/login", json={"token": "x" * 43}, headers=ORIGIN)
    ).status_code == 401


async def test_origin_expiry_and_removal_fail_closed(fclient, fctx):
    gid, token = await invite(fclient)
    assert (await fclient.post("/v1/friends/login", json={"token": token})).status_code == 403
    assert (
        await fclient.post(
            "/v1/friends/login", json={"token": token}, headers={"Origin": "https://evil.example"}
        )
    ).status_code == 403
    await execute(fctx, "UPDATE friends_login_invites SET expires_at=now()-interval '1 second'")
    assert (
        await fclient.post("/v1/friends/login", json={"token": token}, headers=ORIGIN)
    ).status_code == 401
    _, token = await invite(fclient)
    assert (
        await fclient.post("/v1/friends/login", json={"token": token}, headers=ORIGIN)
    ).status_code == 200
    assert (
        await fclient.post("/v1/sharing/pause", json={"connection_id": str(uuid.uuid4())})
    ).status_code == 403
    await execute(
        fctx, "UPDATE experimental_group_members SET status='REMOVED' WHERE user_id=:u", u=USER_A
    )
    assert (await fclient.get("/v1/friends/me", headers=headers(ADMIN))).status_code == 401


async def test_sessions_survive_restart_and_revocation(fclient, fctx):
    _, token = await invite(fclient)
    await fclient.post("/v1/friends/login", json={"token": token}, headers=ORIGIN)
    cookie = fclient.cookies.get("sa_friends_session")
    app = create_app(fctx.settings, fctx)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        cookies={"sa_friends_session": cookie},
    ) as restarted:
        assert (await restarted.get("/v1/friends/me")).status_code == 200
        assert (
            await restarted.post(f"/v1/friends/sessions/{USER_B}/revoke", headers=ORIGIN)
        ).status_code == 403
        assert (
            await restarted.post(f"/v1/friends/sessions/{USER_A}/revoke", headers=ORIGIN)
        ).status_code == 204
        assert (await restarted.get("/v1/friends/me")).status_code == 401


async def test_session_expiry_and_production_dev_header(fclient, fctx, tmp_path):
    _, token = await invite(fclient)
    await fclient.post("/v1/friends/login", json={"token": token}, headers=ORIGIN)
    await execute(fctx, "UPDATE friends_sessions SET expires_at=now()-interval '1 second'")
    assert (await fclient.get("/v1/friends/me")).status_code == 401
    settings = make_settings(
        tmp_path,
        app_env="production",
        dev_auth_only=False,
        friends_auth_enabled=True,
        friends_web_origin="https://friends.example",
        node_token_pepper="test-production-secret-at-least-16-chars",
    )
    ctx = build_context(settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, ctx)),
            base_url="https://friends.example",
        ) as c:
            assert (await c.get("/v1/friends/me", headers=headers(ADMIN))).status_code == 401
    finally:
        await ctx.aclose()


async def test_operator_local_setup_creates_private_invites_without_enabling_live(
    fctx, tmp_path, monkeypatch, capsys
):
    import json
    import stat
    from argparse import Namespace

    from scripts import friends_setup

    monkeypatch.setattr(friends_setup, "load_settings", lambda: fctx.settings)
    output = tmp_path / "private-codes.json"
    await friends_setup.provision(
        Namespace(
            operator="admin@test.local",
            alice="new-alice@test.local",
            bob="new-bob@test.local",
            group="private-beta",
            output=output,
        )
    )
    result = json.loads(output.read_text())
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert len(result["logins"]) == 3 and result["LIVE"].startswith("OFF")
    assert await fetch_val(fctx, "SELECT count(*) FROM experimental_authorizations") == 0
    assert await fetch_val(fctx, "SELECT sharing_enabled FROM experimental_groups") is False
    assert await fetch_val(fctx, "SELECT count(*) FROM subscription_connections") == 0
    assert result["logins"]["new-alice@test.local"]["token"] not in capsys.readouterr().out


async def test_production_session_cookie_is_secure(fclient, fctx, tmp_path):
    _, token = await invite(fclient)
    settings = make_settings(
        tmp_path,
        app_env="production",
        dev_auth_only=False,
        friends_auth_enabled=True,
        friends_web_origin="https://friends.example",
        node_token_pepper="test-production-secret-at-least-16-chars",
    )
    ctx = build_context(settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, ctx)),
            base_url="https://friends.example",
        ) as c:
            r = await c.post(
                "/v1/friends/login",
                json={"token": token},
                headers={"Origin": "https://friends.example"},
            )
            assert r.status_code == 200 and "Secure" in r.headers["set-cookie"]
            assert (await c.get("/v1/friends/me")).status_code == 200
    finally:
        await ctx.aclose()
