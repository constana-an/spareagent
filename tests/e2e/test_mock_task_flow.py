"""TASK-001 end to end: API create -> local mock worker -> SUCCEEDED with artifacts."""

import uuid

import pytest

from tests.conftest import USER_A, create_task, drain_worker, fetch_val, headers

pytestmark = pytest.mark.db


async def test_task_001_full_lifecycle(client, ctx):
    created = await create_task(client, key="e2e-flow-0001")
    assert created.status_code == 202
    task_id = created.json()["id"]

    await drain_worker(ctx)

    task = (await client.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))).json()
    assert task["status"] == "SUCCEEDED"

    statuses = await fetch_val(
        ctx,
        "SELECT string_agg(payload->>'status', ',' ORDER BY seq) FROM execution_events "
        "WHERE task_id=:t AND payload ? 'status'",
        t=uuid.UUID(task_id),
    )
    assert statuses == "CREATED,QUEUED,RESERVED,RUNNING,VERIFYING,SUCCEEDED"

    attempt = await fetch_val(
        ctx, "SELECT status FROM task_attempts WHERE task_id=:t", t=uuid.UUID(task_id)
    )
    assert attempt == "SUCCEEDED"
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 1
    assert await fetch_val(ctx, "SELECT active_tasks FROM worker_nodes") == 0
    assert await fetch_val(ctx, "SELECT status FROM worker_nodes") == "IDLE"

    arts = (await client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))).json()
    assert len(arts["items"]) == 4

    # Replaying the same create after completion returns the same, finished task.
    replay = await create_task(client, key="e2e-flow-0001")
    assert replay.json()["id"] == task_id and replay.json()["status"] == "SUCCEEDED"


async def test_ten_queued_tasks_drain_with_one_running_at_a_time(client, ctx):
    ids = [(await create_task(client)).json()["id"] for _ in range(10)]
    await drain_worker(ctx, rounds=20)
    statuses = [
        (await client.get(f"/v1/tasks/{t}", headers=headers(USER_A))).json()["status"] for t in ids
    ]
    assert statuses == ["SUCCEEDED"] * 10
    assert await fetch_val(ctx, "SELECT max(attempt_number) FROM task_attempts") == 1
