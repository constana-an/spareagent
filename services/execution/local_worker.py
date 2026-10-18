"""Week 1 local mock worker process: `python -m services.execution.local_worker`.

Drives the seeded local worker node(s): heartbeat, lease-expiry recovery, retry promotion,
dispatch, then runs reserved attempts with the mock adapter. Remote outbound-only workers
using the lease HTTP API are Week 2.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid

from sqlalchemy import select, update

from apps.api.config import load_settings
from db.models import TaskAttempt, WorkerNode
from services.context import AppContext, build_context
from services.execution.orchestrator import Orchestrator
from services.observability import configure_logging
from services.queue.consumer import wait_for_wake
from services.scheduler.dispatcher import Dispatcher
from services.scheduler.lease_service import LeaseService, utcnow

log = logging.getLogger("spareagent.worker")


class LocalWorker:
    def __init__(self, ctx: AppContext, node_ids: tuple[uuid.UUID, ...]) -> None:
        self.ctx = ctx
        self.node_ids = node_ids
        self.dispatcher = Dispatcher(ctx)
        self.leases = LeaseService(ctx)
        self.orchestrator = Orchestrator(ctx)

    async def heartbeat(self) -> None:
        """Only liveness fields are touched; a worker can never change resource eligibility."""
        async with self.ctx.sessionmaker() as session, session.begin():
            await session.execute(
                update(WorkerNode)
                .where(WorkerNode.id.in_(self.node_ids))
                .values(last_heartbeat=utcnow())
            )
            await session.execute(
                update(WorkerNode)
                .where(WorkerNode.id.in_(self.node_ids), WorkerNode.status == "OFFLINE")
                .values(status="IDLE")
            )
        for node_id in self.node_ids:
            try:
                await self.ctx.redis.set(f"spareagent:worker:{node_id}:heartbeat", "1", ex=30)
            except Exception as exc:  # noqa: BLE001 - cache only; DB heartbeat is authoritative
                log.debug("heartbeat cache write failed", extra={"err": str(exc)})

    async def reserved_attempts(self) -> list[uuid.UUID]:
        async with self.ctx.sessionmaker() as session:
            rows = await session.execute(
                select(TaskAttempt.id)
                .where(TaskAttempt.worker_id.in_(self.node_ids), TaskAttempt.status == "RESERVED")
                .order_by(TaskAttempt.leased_until)
            )
            return list(rows.scalars())

    async def run_once(self) -> int:
        await self.heartbeat()
        await self.leases.recover_expired()
        await self.dispatcher.promote_retries()
        await self.dispatcher.dispatch_all()
        ran = 0
        for attempt_id in await self.reserved_attempts():
            await self.orchestrator.run_attempt(attempt_id)
            ran += 1
        return ran

    async def run_forever(self, stop: asyncio.Event) -> None:
        log.info("local mock worker started", extra={"nodes": [str(n) for n in self.node_ids]})
        while not stop.is_set():
            try:
                ran = await self.run_once()
            except Exception:  # noqa: BLE001 - keep the loop alive (e.g. DB not migrated yet)
                log.exception("worker iteration failed")
                await asyncio.sleep(self.ctx.settings.worker_poll_seconds)
                continue
            if not ran:
                await wait_for_wake(self.ctx.redis, self.ctx.settings.worker_poll_seconds)


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    ctx = build_context(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await LocalWorker(ctx, settings.local_worker_ids).run_forever(stop)
    finally:
        await ctx.aclose()


if __name__ == "__main__":
    asyncio.run(main())
