import uuid

import pytest

from tests.conftest import (
    PROVIDER,
    USER_A,
    USER_B,
    VALID_TASK,
    create_task,
    drain_worker,
    execute,
    fetch_val,
    headers,
)

pytestmark = pytest.mark.db


async def test_create_task_returns_202_queued(client, ctx):
    resp = await create_task(client)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert set(body) == {
        "id",
        "status",
        "task_type",
        "max_budget_cents",
        "created_at",
        "result_summary",
    }
    assert body["status"] == "QUEUED"
    assert body["max_budget_cents"] == 500
    assert resp.headers["x-request-id"]
    events = await fetch_val(
        ctx,
        "SELECT string_agg(event_type, ',' ORDER BY seq) FROM execution_events WHERE task_id=:t",
        t=uuid.UUID(body["id"]),
    )
    assert events == "task.created,task.queued"


async def test_owner_comes_from_principal_only(client, ctx):
    resp = await client.post(
        "/v1/tasks",
        json={**VALID_TASK, "consumer_id": str(USER_B)},
        headers=headers(USER_A, "owner-spoof-1"),
    )
    assert resp.status_code == 422  # unknown fields rejected
    resp = await create_task(client, user=USER_A)
    owner = await fetch_val(
        ctx, "SELECT consumer_id FROM tasks WHERE id=:t", t=uuid.UUID(resp.json()["id"])
    )
    assert owner == USER_A


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"repository_url": "https://evil.example.com/a/b"}, 422),
        ({"repository_url": "git@github.com:octo/repo.git"}, 422),
        ({"commit_sha": "not-a-sha"}, 422),
        ({"max_budget_cents": 0}, 422),
        ({"prompt": "x" * 12001}, 422),
        ({"max_duration_seconds": 5}, 422),
    ],
)
async def test_validation_errors_use_envelope(client, override, code):
    resp = await create_task(client, **override)
    assert resp.status_code == code
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["request_id"] == resp.headers["x-request-id"]


async def test_task_002_missing_sha_is_422(client):
    body = {k: v for k, v in VALID_TASK.items() if k != "commit_sha"}
    resp = await client.post("/v1/tasks", json=body, headers=headers(USER_A, "missing-sha-1"))
    assert resp.status_code == 422


async def test_validation_message_does_not_echo_input(client):
    secret = "sk-proj-SHOULDNOTECHO1234567890"
    resp = await create_task(client, max_budget_cents=secret)
    assert resp.status_code == 422
    assert secret not in resp.text


async def test_idempotency_key_required_and_validated(client):
    resp = await client.post("/v1/tasks", json=VALID_TASK, headers=headers(USER_A))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    resp = await client.post("/v1/tasks", json=VALID_TASK, headers=headers(USER_A, "short"))
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"


async def test_unauthenticated_requests_rejected(client):
    resp = await client.post("/v1/tasks", json=VALID_TASK, headers={"Idempotency-Key": "abcdefgh1"})
    assert resp.status_code == 401
    resp = await client.get("/v1/tasks", headers={"X-Dev-User-ID": "not-a-uuid"})
    assert resp.status_code == 401
    resp = await client.get("/v1/tasks", headers={"X-Dev-User-ID": str(uuid.uuid4())})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_dev_auth_refused_for_non_local_host_header(client):
    resp = await client.get(
        "/v1/tasks", headers={**headers(USER_A), "Host": "spareagent.example.com"}
    )
    assert resp.status_code == 401


async def test_dev_auth_refused_from_untrusted_client(app):
    import httpx

    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 4444))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        resp = await c.get("/v1/tasks", headers=headers(USER_A))
    assert resp.status_code == 401


async def test_auth_001_cross_user_access_is_404_without_leak(client):
    task_id = (await create_task(client, user=USER_A)).json()["id"]
    for path in (
        f"/v1/tasks/{task_id}",
        f"/v1/tasks/{task_id}/artifacts",
        f"/v1/tasks/{task_id}/events",
    ):
        resp = await client.get(path, headers=headers(USER_B))
        assert resp.status_code == 404, path
        assert task_id not in resp.text
    resp = await client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_B))
    assert resp.status_code == 404
    listing = await client.get("/v1/tasks", headers=headers(USER_B))
    assert listing.json() == {"items": [], "next_cursor": None}
    # still queued, untouched by B
    resp = await client.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))
    assert resp.json()["status"] == "QUEUED"


async def test_malformed_task_id_is_404(client):
    resp = await client.get("/v1/tasks/not-a-uuid", headers=headers(USER_A))
    assert resp.status_code == 404


async def test_list_pagination(client):
    ids = [(await create_task(client)).json()["id"] for _ in range(5)]
    page1 = (await client.get("/v1/tasks?limit=2", headers=headers(USER_A))).json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = (
        await client.get(
            f"/v1/tasks?limit=2&cursor={page1['next_cursor']}", headers=headers(USER_A)
        )
    ).json()
    page3 = (
        await client.get(
            f"/v1/tasks?limit=2&cursor={page2['next_cursor']}", headers=headers(USER_A)
        )
    ).json()
    seen = [t["id"] for t in page1["items"] + page2["items"] + page3["items"]]
    assert sorted(seen) == sorted(ids) and len(set(seen)) == 5
    assert page3["next_cursor"] is None
    bad = await client.get("/v1/tasks?cursor=@@@", headers=headers(USER_A))
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_CURSOR"
    assert (await client.get("/v1/tasks?limit=101", headers=headers(USER_A))).status_code == 422


async def test_cancel_queued_task(client, ctx):
    task_id = (await create_task(client)).json()["id"]
    resp = await client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_A))
    assert resp.status_code == 202
    assert resp.json()["status"] == "CANCELLED"
    again = await client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_A))
    assert again.status_code == 202 and again.json()["status"] == "CANCELLED"
    # TASK-003: the worker never picks it up, so no artifacts appear afterwards.
    await drain_worker(ctx)
    tid = uuid.UUID(task_id)
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts WHERE task_id=:t", t=tid) == 0
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts WHERE task_id=:t", t=tid) == 0


async def test_cancel_terminal_task_conflicts(client, ctx):
    task_id = (await create_task(client)).json()["id"]
    await drain_worker(ctx)
    resp = await client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_A))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TASK_NOT_CANCELLABLE"


async def test_cancel_reserved_task_releases_worker(client, ctx):
    from services.scheduler.dispatcher import Dispatcher

    task_id = (await create_task(client)).json()["id"]
    assert await Dispatcher(ctx).dispatch_once() is not None
    resp = await client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_A))
    assert resp.json()["status"] == "CANCELLED"
    assert await fetch_val(ctx, "SELECT active_tasks FROM worker_nodes") == 0
    assert await fetch_val(ctx, "SELECT status FROM task_attempts") == "CANCELLED"


async def test_unknown_route_uses_error_envelope(client):
    resp = await client.get("/v1/nope", headers=headers(USER_A))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_auth_002_provider_cannot_self_approve(client, ctx):
    rid = uuid.uuid4()
    await execute(
        ctx,
        "INSERT INTO resources(id,owner_id,resource_type) VALUES (:r,:p,'LICENSED_PROVIDER')",
        r=rid,
        p=PROVIDER,
    )
    resp = await client.post(f"/v1/admin/resources/{rid}/approve", headers=headers(PROVIDER))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ADMIN_REQUIRED"
    assert await fetch_val(ctx, "SELECT public_eligible FROM resources WHERE id=:r", r=rid) is False
    # worker control plane cannot be used with user (dev) credentials either (Week 2: 401)
    resp = await client.post(
        "/v1/workers/register",
        json={"resource_id": str(rid), "name": "x", "capabilities": {}},
        headers=headers(PROVIDER),
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "NODE_AUTH_REQUIRED"


async def test_admin_approval_and_personal_refusal(client, ctx):
    from tests.conftest import ADMIN

    rid, personal = uuid.uuid4(), uuid.uuid4()
    await execute(
        ctx,
        "INSERT INTO resources(id,owner_id,resource_type) VALUES "
        "(:r,:p,'LICENSED_PROVIDER'),(:q,:p,'PERSONAL_SUBSCRIPTION')",
        r=rid,
        q=personal,
        p=PROVIDER,
    )
    ok = await client.post(
        f"/v1/admin/resources/{rid}/approve",
        json={"license_reference": "LIC-1"},
        headers=headers(ADMIN),
    )
    assert ok.status_code == 200 and ok.json()["public_eligible"] is True
    denied = await client.post(f"/v1/admin/resources/{personal}/approve", headers=headers(ADMIN))
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "POLICY_VIOLATION"
    assert (
        await fetch_val(ctx, "SELECT public_eligible FROM resources WHERE id=:r", r=personal)
        is False
    )
    actions = await fetch_val(ctx, "SELECT string_agg(action, ',' ORDER BY id) FROM audit_logs")
    assert actions == "resource.approve,resource.approve.denied"
