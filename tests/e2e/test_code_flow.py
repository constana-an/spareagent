"""Week 3 end to end (in-process): API -> WorkerAgent(CodeExecutionAdapter) over the node
protocol -> sandboxed tests -> platform VerificationService -> SUCCEEDED -> artifact download."""

import asyncio
import contextlib
import hashlib
import time
import uuid

import pytest

from packages.worker.agent import AgentConfig, WorkerAgent
from services.scheduler.runner import tick
from tests.conftest import (
    PY_SHA,
    PY_URL,
    TS_SHA,
    TS_URL,
    USER_A,
    code_adapter,
    create_task,
    fetch_val,
    headers,
    register_code_node,
    verification_service,
)
from tests.e2e.test_remote_agents import KillableTransport

pytestmark = pytest.mark.db


def make_agent(app, reg, fixture_root, sandbox):
    from packages.worker.lease_client import NodeClient

    transport = KillableTransport(app)
    client = NodeClient(
        "http://testserver",
        worker_id=reg["worker_id"],
        node_key=reg["node_key"],
        allow_insecure_http=True,
        transport=transport,
    )
    agent = WorkerAgent(
        client,
        AgentConfig(
            poll_wait_seconds=0.5,
            heartbeat_interval_seconds=0.5,
            backoff_base_seconds=0.05,
            backoff_max_seconds=0.2,
            completion_retries=2,
            capabilities={"code": True, "sandbox": "docker"},
        ),
        code_adapter=code_adapter(fixture_root, sandbox),
    )
    return agent, client, transport


async def wait_for(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.2)
    return False


async def test_full_code_fix_flow(code_client, code_ctx, fixture_root, sandbox):
    reg = await register_code_node(code_client)
    agent, node_client, _ = make_agent(code_client.app, reg, fixture_root, sandbox)
    verifier = verification_service(code_ctx, fixture_root, sandbox)
    stop = asyncio.Event()
    run = asyncio.create_task(agent.run(stop))

    async def verifier_loop():
        while not stop.is_set():
            if await verifier.run_once() is None:
                await asyncio.sleep(0.2)

    vloop = asyncio.create_task(verifier_loop())
    try:
        ids = []
        for url, sha in ((PY_URL, PY_SHA), (TS_URL, TS_SHA)):
            resp = await create_task(
                code_client, task_type="CODE_FIX", repository_url=url, commit_sha=sha
            )
            ids.append(resp.json()["id"])

        async def done():
            st = [
                (await code_client.get(f"/v1/tasks/{t}", headers=headers(USER_A))).json()["status"]
                for t in ids
            ]
            return all(s in ("SUCCEEDED", "FAILED", "CANCELLED") for s in st)

        assert await wait_for(done)
        for tid in ids:
            task = (await code_client.get(f"/v1/tasks/{tid}", headers=headers(USER_A))).json()
            assert task["status"] == "SUCCEEDED", task
            assert task["result_summary"].startswith("Verified by platform: 3/3")
            listing = (
                await code_client.get(f"/v1/tasks/{tid}/artifacts", headers=headers(USER_A))
            ).json()["items"]
            assert {i["kind"] for i in listing} == {
                "PATCH",
                "TEST_REPORT",
                "EXECUTION_LOG",
                "SUMMARY",
                "VERIFICATION_REPORT",
            }
            for item in listing:
                body = await code_client.get(
                    f"/v1/tasks/{tid}/artifacts/{item['id']}/content", headers=headers(USER_A)
                )
                assert hashlib.sha256(body.content).hexdigest() == item["sha256"]
        events = await fetch_val(
            code_ctx,
            "SELECT string_agg(event_type, ',' ORDER BY seq) FROM execution_events "
            "WHERE task_id=:t",
            t=uuid.UUID(ids[0]),
        )
        assert events.split(",")[-2:] == ["task.verifying", "task.succeeded"]
        assert sandbox.list_containers() == []  # every sandbox (worker + verifier) cleaned up
    finally:
        stop.set()
        await asyncio.gather(run, vloop, return_exceptions=True)
        await node_client.aclose()


async def test_worker_disconnect_during_sandbox_run_recovers(
    code_client, code_ctx, fixture_root, sandbox
):
    """Node dies mid-execution: lease expires, the task is retried and verified; the lost
    attempt never publishes artifacts and its late completion is rejected."""
    object.__setattr__(code_ctx.settings, "lease_seconds", 2)
    object.__setattr__(code_ctx.settings, "retry_backoff_base_seconds", 0)
    reg = await register_code_node(code_client)
    agent, node_client, transport = make_agent(code_client.app, reg, fixture_root, sandbox)
    stop = asyncio.Event()
    run = asyncio.create_task(agent.run(stop))
    task_id = (
        await create_task(
            code_client, task_type="CODE_FIX", repository_url=PY_URL, commit_sha=PY_SHA
        )
    ).json()["id"]
    try:
        assert await wait_for(lambda: _claimed(agent), 30)
        transport.killed = True  # network partition while the sandbox is running
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run
        lost = agent.claimed[-1]

        # scheduler notices the expired lease and requeues the task
        async def requeued():
            await tick(code_ctx)
            st = await fetch_val(
                code_ctx, "SELECT status FROM task_attempts WHERE id=:a", a=uuid.UUID(lost)
            )
            return st == "EXPIRED"

        assert await wait_for(requeued, 30)
        reborn, reborn_client, _ = make_agent(code_client.app, reg, fixture_root, sandbox)
        stop2 = asyncio.Event()
        run2 = asyncio.create_task(reborn.run(stop2))
        verifier = verification_service(code_ctx, fixture_root, sandbox)

        async def succeeded():
            await tick(code_ctx)
            await verifier.run_once()
            return (await code_client.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))).json()[
                "status"
            ] == "SUCCEEDED"

        assert await wait_for(succeeded, 90)
        stop2.set()
        await asyncio.gather(run2, return_exceptions=True)
        await reborn_client.aclose()
        assert (
            await fetch_val(
                code_ctx, "SELECT count(*) FROM artifacts WHERE attempt_id=:a", a=uuid.UUID(lost)
            )
            == 0
        )
        assert await fetch_val(code_ctx, "SELECT count(*) FROM verification_results") == 1
        assert await fetch_val(code_ctx, "SELECT max(active_tasks) FROM worker_nodes") == 0
        await asyncio.sleep(1)
        assert sandbox.list_containers() == []
    finally:
        stop.set()
        await node_client.aclose()


async def _claimed(agent):
    return bool(agent.claimed)
