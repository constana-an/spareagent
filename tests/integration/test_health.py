import httpx
import pytest

from apps.api.main import create_app
from services.context import build_context
from tests.conftest import make_settings

pytestmark = pytest.mark.db


async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


async def test_readyz_ok_with_db_and_redis(client):
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.parametrize(
    ("override", "failing"),
    [
        ({"redis_url": "redis://127.0.0.1:1/0"}, "redis"),
        ({"database_url": "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"}, "database"),
    ],
)
async def test_readyz_503_when_dependency_down(tmp_path, migrated_database, override, failing):
    ctx = build_context(make_settings(tmp_path, **override))
    app = create_app(ctx.settings, ctx)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as c:
            resp = await c.get("/readyz")
    finally:
        await ctx.aclose()
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable" and body["checks"][failing] == "unavailable"
    assert "nopass" not in resp.text


async def test_dev_auth_disabled_fails_closed(tmp_path, migrated_database):
    ctx = build_context(make_settings(tmp_path, dev_auth_only=False))
    app = create_app(ctx.settings, ctx)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as c:
            resp = await c.get(
                "/v1/tasks", headers={"X-Dev-User-ID": "aaaaaaaa-0000-4000-8000-00000000000a"}
            )
    finally:
        await ctx.aclose()
    assert resp.status_code == 401
