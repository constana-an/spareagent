import asyncio

import pytest

from tests.conftest import USER_A, USER_B, VALID_TASK, create_task, fetch_val, headers

pytestmark = pytest.mark.db


async def test_idemp_001_same_key_same_body_same_task(client, ctx):
    first = await create_task(client, key="idem-key-0001")
    second = await create_task(client, key="idem-key-0001")
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert await fetch_val(ctx, "SELECT count(*) FROM tasks") == 1
    # replay does not emit extra events
    assert await fetch_val(ctx, "SELECT count(*) FROM execution_events") == 2


async def test_idemp_001_normalized_body(client):
    a = await create_task(client, key="idem-key-norm", commit_sha=VALID_TASK["commit_sha"].upper())
    b = await create_task(client, key="idem-key-norm")
    assert a.json()["id"] == b.json()["id"]


async def test_idemp_002_same_key_different_body_conflict(client, ctx):
    await create_task(client, key="idem-key-0002")
    resp = await create_task(client, key="idem-key-0002", prompt="something else")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await fetch_val(ctx, "SELECT count(*) FROM tasks") == 1


async def test_keys_are_scoped_per_user(client, ctx):
    a = await create_task(client, user=USER_A, key="shared-key-01")
    b = await create_task(client, user=USER_B, key="shared-key-01")
    assert a.json()["id"] != b.json()["id"]
    assert await fetch_val(ctx, "SELECT count(*) FROM tasks") == 2


async def test_idemp_003_ten_concurrent_submissions_one_row(client, ctx):
    responses = await asyncio.gather(
        *[
            client.post("/v1/tasks", json=VALID_TASK, headers=headers(USER_A, "concurrent-0001"))
            for _ in range(10)
        ]
    )
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["id"] for r in responses}) == 1
    assert await fetch_val(ctx, "SELECT count(*) FROM tasks") == 1
    assert await fetch_val(ctx, "SELECT count(*) FROM execution_events") == 2


async def test_concurrent_conflicting_bodies(client, ctx):
    bodies = [{**VALID_TASK, "prompt": f"prompt {i}"} for i in range(5)]
    responses = await asyncio.gather(
        *[
            client.post("/v1/tasks", json=b, headers=headers(USER_A, "concurrent-0002"))
            for b in bodies
        ]
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 409, 409, 409, 409]
    assert await fetch_val(ctx, "SELECT count(*) FROM tasks") == 1
