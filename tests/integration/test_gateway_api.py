import uuid

import pytest

from services.gateway.aliases import GatewayAlias
from services.gateway.service import GatewayService
from tests.conftest import PROVIDER, PUBLIC_RESOURCE, USER_A, execute, fetch_val, headers

pytestmark = pytest.mark.db

CHAT = {
    "model": "mock-chat",
    "stream": False,
    "messages": [{"role": "user", "content": "Say hello"}],
}


async def test_gate_001_models_lists_mock_chat(client):
    resp = await client.get("/v1/models", headers=headers(USER_A))
    assert resp.status_code == 200
    assert resp.json() == {
        "object": "list",
        "data": [{"id": "mock-chat", "object": "model", "owned_by": "spareagent"}],
    }


async def test_models_requires_auth(client):
    assert (await client.get("/v1/models")).status_code == 401


async def test_gate_002_nonstream_response_and_usage_persisted(client, ctx):
    resp = await client.post("/v1/chat/completions", json=CHAT, headers=headers(USER_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"id", "object", "created", "model", "choices", "usage"}
    assert body["object"] == "chat.completion" and body["model"] == "mock-chat"
    choice = body["choices"][0]
    assert choice["index"] == 0 and choice["message"]["role"] == "assistant"
    assert choice["finish_reason"] == "stop"
    u = body["usage"]
    assert u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"] > 0
    assert resp.headers["x-spareagent-billable"] == "false"
    assert await fetch_val(ctx, "SELECT status FROM gateway_requests") == "SUCCEEDED"
    row_tokens = await fetch_val(
        ctx,
        "SELECT input_tokens + output_tokens FROM usage_records "
        "WHERE gateway_request_id IS NOT NULL",
    )
    assert row_tokens == u["total_tokens"]
    assert await fetch_val(ctx, "SELECT estimated_cost_cents FROM usage_records") == 0
    # deterministic content
    again = await client.post("/v1/chat/completions", json=CHAT, headers=headers(USER_A))
    assert again.json()["choices"][0]["message"]["content"] == choice["message"]["content"]
    assert again.json()["id"] != body["id"]


async def test_gate_003_stream_true_is_422(client, ctx):
    resp = await client.post(
        "/v1/chat/completions", json={**CHAT, "stream": True}, headers=headers(USER_A)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_STREAMING"
    # even combined with other unsupported params
    resp = await client.post(
        "/v1/chat/completions", json={**CHAT, "stream": True, "n": 2}, headers=headers(USER_A)
    )
    assert resp.json()["error"]["code"] == "UNSUPPORTED_STREAMING"
    assert await fetch_val(ctx, "SELECT count(*) FROM gateway_requests") == 0


async def test_unknown_alias_404(client):
    resp = await client.post(
        "/v1/chat/completions", json={**CHAT, "model": "gpt-4o"}, headers=headers(USER_A)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "MODEL_NOT_FOUND"


async def test_unsupported_parameters_and_bad_values(client):
    resp = await client.post("/v1/chat/completions", json={**CHAT, "n": 3}, headers=headers(USER_A))
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "UNSUPPORTED_PARAMETER"
    for bad in (
        {**CHAT, "messages": []},
        {**CHAT, "messages": [{"role": "tool", "content": "x"}]},
        {**CHAT, "max_tokens": 0},
        {**CHAT, "temperature": 3},
    ):
        resp = await client.post("/v1/chat/completions", json=bad, headers=headers(USER_A))
        assert resp.status_code == 422, bad


async def test_temperature_and_max_tokens_accepted(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={**CHAT, "temperature": 1, "max_tokens": 3},
        headers=headers(USER_A),
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["finish_reason"] == "length"
    assert resp.json()["usage"]["completion_tokens"] == 3


async def test_alias_hidden_when_backing_resource_revoked(client, ctx):
    await execute(
        ctx,
        "UPDATE resources SET public_eligible=false, authorization_status='REVOKED' WHERE id=:r",
        r=PUBLIC_RESOURCE,
    )
    assert (await client.get("/v1/models", headers=headers(USER_A))).json()["data"] == []
    resp = await client.post("/v1/chat/completions", json=CHAT, headers=headers(USER_A))
    assert resp.status_code == 404


async def test_gate_004_personal_subscription_alias_never_routable(ctx):
    personal = uuid.uuid4()
    await execute(
        ctx,
        "INSERT INTO resources(id,owner_id,resource_type,authorization_status) "
        "VALUES (:r,:p,'PERSONAL_SUBSCRIPTION','APPROVED')",
        r=personal,
        p=PROVIDER,
    )
    service = GatewayService(
        ctx,
        aliases=(
            GatewayAlias("mock-chat", "mock", "mock-chat-v1", PUBLIC_RESOURCE),
            GatewayAlias("my-chatgpt-plan", "mock", "mock-chat-v1", personal),
        ),
    )
    models = [m["id"] for m in await service.list_models()]
    assert models == ["mock-chat"]
    from services.errors import DomainError
    from services.tasks.service import Principal

    with pytest.raises(DomainError) as info:
        await service.chat(
            Principal(USER_A, "CONSUMER"),
            {"model": "my-chatgpt-plan", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert info.value.status_code == 404
    assert await fetch_val(ctx, "SELECT count(*) FROM gateway_requests") == 0
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 0


async def test_rate_limit_429(tmp_path, migrated_database):
    import httpx

    from apps.api.main import create_app
    from services.context import build_context
    from tests.conftest import make_settings, seed_fixtures

    ctx = build_context(make_settings(tmp_path, gateway_rate_limit_per_minute=2))
    await seed_fixtures(ctx)
    try:
        app = create_app(ctx.settings, ctx)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as c:
            codes = [
                (
                    await c.post("/v1/chat/completions", json=CHAT, headers=headers(USER_A))
                ).status_code
                for _ in range(3)
            ]
            assert codes == [200, 200, 429]
            last = await c.post("/v1/chat/completions", json=CHAT, headers=headers(USER_A))
            assert last.json()["error"]["code"] == "RATE_LIMITED"
    finally:
        await ctx.aclose()
