"""Three real WorkerAgent instances against the API (in-process ASGI transport).

Covers: distribution across workers, disconnect mid-execution with lease-expiry recovery,
reconnect with persistent identity, graceful shutdown hand-back, and the invariants of no
duplicate finalization / usage / artifact publication.
"""

import asyncio
import contextlib
import time
import uuid

import httpx
import pytest

from apps.api.main import create_app
from packages.provider_sdk.mock import MockProviderAdapter
from packages.worker.agent import AgentConfig, WorkerAgent
from packages.worker.lease_client import NodeClient
from services.context import build_context
from services.scheduler.runner import tick
from tests.conftest import (
    USER_A,
    create_task,
    fetch_val,
    headers,
    make_settings,
    register_node,
    seed_fixtures,
)

pytestmark = pytest.mark.db

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


class KillableTransport(httpx.AsyncBaseTransport):
    """Simulates a network partition: once killed, every request fails to connect."""

    def __init__(self, app) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.killed = False

    async def handle_async_request(self, request):
        if self.killed:
            raise httpx.ConnectError("simulated disconnect")
        return await self.inner.handle_async_request(request)


def agent_for(app, reg, *, step_delay=0.3, **cfg):
    transport = KillableTransport(app)
    client = NodeClient(
        "http://testserver",
        worker_id=reg["worker_id"],
        node_key=reg["node_key"],
        allow_insecure_http=True,
        transport=transport,
    )
    config = AgentConfig(
        poll_wait_seconds=0.5,
        heartbeat_interval_seconds=0.5,
        backoff_base_seconds=0.05,
        backoff_max_seconds=0.2,
        shutdown_grace_seconds=0.1,
        completion_retries=2,
        **cfg,
    )
    agent = WorkerAgent(client, config, MockProviderAdapter(step_delay_seconds=step_delay))
    return agent, client, transport


@pytest.fixture
async def remote_env(tmp_path, migrated_database):
    settings = make_settings(
        tmp_path,
        lease_seconds=2,
        lease_poll_max_wait_seconds=0.5,
        lease_poll_interval_seconds=0.05,
        retry_backoff_base_seconds=0,
    )
    ctx = build_context(settings)
    await seed_fixtures(ctx)
    app = create_app(settings, ctx)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")
    try:
        yield ctx, app, client
    finally:
        await client.aclose()
        await ctx.aclose()


async def statuses(client, ids):
    return [
        (await client.get(f"/v1/tasks/{t}", headers=headers(USER_A))).json()["status"] for t in ids
    ]


async def scheduler_loop(ctx, stop):
    while not stop.is_set():
        await tick(ctx)
        await asyncio.sleep(0.1)


async def wait_terminal(client, ids, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = await statuses(client, ids)
        if all(s in TERMINAL for s in current):
            return current
        await asyncio.sleep(0.2)
    raise AssertionError(f"tasks not terminal: {await statuses(client, ids)}")


async def assert_no_duplicates(ctx, ids):
    for tid in ids:
        t = uuid.UUID(tid)
        assert (
            await fetch_val(
                ctx,
                "SELECT count(*) FROM execution_events WHERE task_id=:t AND event_type IN "
                "('task.succeeded','task.failed','task.cancelled','task.timed_out')",
                t=t,
            )
            == 1
        )
        assert (
            await fetch_val(
                ctx,
                "SELECT count(*) FROM task_attempts WHERE task_id=:t AND status='SUCCEEDED'",
                t=t,
            )
            <= 1
        )
        assert await fetch_val(ctx, "SELECT count(*) FROM artifacts WHERE task_id=:t", t=t) in (
            0,
            4,
        )
    assert (
        await fetch_val(
            ctx,
            "SELECT count(*) FROM (SELECT task_attempt_id FROM usage_records "
            "GROUP BY task_attempt_id HAVING count(*) > 1) d",
        )
        == 0
    )


async def test_three_workers_disconnect_and_recover(remote_env):
    ctx, app, client = remote_env
    regs = [await register_node(client, name=f"remote-{i}") for i in range(1, 4)]
    agents = [agent_for(app, r) for r in regs]
    stops = [asyncio.Event() for _ in agents]
    runs = [asyncio.create_task(a.run(s)) for (a, _, _), s in zip(agents, stops, strict=True)]
    sched_stop = asyncio.Event()
    sched = asyncio.create_task(scheduler_loop(ctx, sched_stop))
    try:
        ids = [
            (await create_task(client, key=f"e2e-remote-{i:02d}")).json()["id"] for i in range(10)
        ]

        # Disconnect worker 2 as soon as it is executing something.
        victim, victim_client, victim_transport = agents[1]
        deadline = time.monotonic() + 15
        while not victim.claimed and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert victim.claimed, "worker 2 never received work"
        victim_transport.killed = True
        runs[1].cancel()  # process dies; its completion cannot reach the API
        with contextlib.suppress(asyncio.CancelledError):
            await runs[1]
        lost_attempt = victim.claimed[-1]

        # Reconnect worker 2 with the same persistent identity (node key) after a pause.
        await asyncio.sleep(2.5)
        reborn, reborn_client, _ = agent_for(app, regs[1])
        stops[1] = asyncio.Event()
        runs[1] = asyncio.create_task(reborn.run(stops[1]))

        final = await wait_terminal(client, ids)
        assert final == ["SUCCEEDED"] * 10

        lost_status = await fetch_val(
            ctx, "SELECT status FROM task_attempts WHERE id=:a", a=uuid.UUID(lost_attempt)
        )
        assert lost_status == "EXPIRED"
        per_worker = {
            r["worker_id"]: await fetch_val(
                ctx,
                "SELECT count(*) FROM task_attempts WHERE worker_id=:w AND status='SUCCEEDED'",
                w=uuid.UUID(r["worker_id"]),
            )
            for r in regs
        }
        assert sum(per_worker.values()) == 10
        assert sum(1 for n in per_worker.values() if n > 0) >= 2
        await assert_no_duplicates(ctx, ids)
        assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 40
        assert await fetch_val(ctx, "SELECT max(active_tasks) FROM worker_nodes") == 0
    finally:
        for s in stops:
            s.set()
        sched_stop.set()
        await asyncio.gather(*runs, sched, return_exceptions=True)
        for _, c, _ in agents:
            await c.aclose()
        with contextlib.suppress(Exception):
            await reborn_client.aclose()


async def test_graceful_shutdown_hands_lease_back(remote_env):
    ctx, app, client = remote_env
    reg = await register_node(client)
    agent, node_client, _ = agent_for(app, reg, step_delay=5)
    stop = asyncio.Event()
    run = asyncio.create_task(agent.run(stop))
    try:
        task_id = (await create_task(client)).json()["id"]
        deadline = time.monotonic() + 10
        while not agent.claimed and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert agent.claimed
        stop.set()  # SIGTERM: grace (0.1 s) is shorter than the 5 s mock step
        await asyncio.wait_for(run, 10)
        status = (await client.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))).json()
        # handed back immediately (no 2 s lease wait): retry is pending, not stuck RUNNING
        assert status["status"] in ("RETRY_PENDING", "QUEUED")
        reason = await fetch_val(
            ctx,
            "SELECT payload->>'reason' FROM execution_events WHERE event_type='task.retrying'",
        )
        assert reason == "WORKER_SHUTDOWN"
        wstatus = await fetch_val(
            ctx, "SELECT status FROM worker_nodes WHERE id=:w", w=uuid.UUID(reg["worker_id"])
        )
        assert wstatus == "OFFLINE"
    finally:
        stop.set()
        await node_client.aclose()


async def test_revoked_worker_stops_itself(remote_env):
    ctx, app, client = remote_env
    reg = await register_node(client)
    agent, node_client, _ = agent_for(app, reg)
    stop = asyncio.Event()
    run = asyncio.create_task(agent.run(stop))
    try:
        await asyncio.sleep(0.3)
        from tests.conftest import ADMIN

        r = await client.post(
            f"/v1/admin/workers/{reg['worker_id']}/revoke", headers=headers(ADMIN)
        )
        assert r.status_code == 200
        await asyncio.wait_for(run, 10)
        assert agent.fatal is not None and agent.fatal.status == 401
    finally:
        stop.set()
        await node_client.aclose()


async def test_worker_survives_api_outage_with_backoff(remote_env):
    ctx, app, client = remote_env
    reg = await register_node(client)
    agent, node_client, transport = agent_for(app, reg, step_delay=0)
    stop = asyncio.Event()
    transport.killed = True  # API unreachable at start
    run = asyncio.create_task(agent.run(stop))
    try:
        task_id = (await create_task(client)).json()["id"]
        await asyncio.sleep(0.5)
        assert not agent.claimed
        transport.killed = False  # API back: worker reconnects on its own
        final = await wait_terminal(client, [task_id], timeout=15)
        assert final == ["SUCCEEDED"]
    finally:
        stop.set()
        await asyncio.wait_for(run, 10)
        await node_client.aclose()
