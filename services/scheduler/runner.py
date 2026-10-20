"""Central scheduler maintenance loop: `python -m services.scheduler.runner`.

Runs independently of any worker so remote-only deployments still recover:
* expire lapsed leases (worker disconnect/crash) and requeue or fail them;
* promote RETRY_PENDING tasks after backoff;
* reconcile worker capacity counters against the authoritative attempt rows (repairs drift
  after an API or worker crash). All steps are idempotent and safe to run concurrently with
  the API and workers because they use the same row locks.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy import func, select

from apps.api.config import load_settings
from db.models import TaskAttempt, WorkerNode
from services.context import AppContext, build_context
from services.observability import configure_logging
from services.scheduler.dispatcher import Dispatcher
from services.scheduler.lease_service import ACTIVE_ATTEMPT_STATUSES, LeaseService

log = logging.getLogger("spareagent.scheduler")


async def reconcile_capacity(ctx: AppContext) -> int:
    """Set active_tasks = number of active attempts per worker. Returns rows corrected."""
    fixed = 0
    async with ctx.sessionmaker() as session, session.begin():
        active = (
            select(TaskAttempt.worker_id, func.count().label("n"))
            .where(TaskAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES))
            .group_by(TaskAttempt.worker_id)
            .subquery()
        )
        rows = (
            await session.execute(
                select(WorkerNode, func.coalesce(active.c.n, 0))
                .outerjoin(active, active.c.worker_id == WorkerNode.id)
                .order_by(WorkerNode.id)
                .with_for_update(of=WorkerNode, skip_locked=True)
            )
        ).all()
        for worker, actual in rows:
            actual = int(actual)
            if worker.active_tasks != actual:
                log.warning(
                    "capacity drift repaired",
                    extra={"worker_id": str(worker.id), "was": worker.active_tasks, "now": actual},
                )
                worker.active_tasks = min(actual, worker.max_concurrency)
                if actual == 0 and worker.status in ("RESERVED", "RUNNING"):
                    worker.status = "IDLE"
                fixed += 1
    return fixed


async def tick(ctx: AppContext) -> dict[str, int]:
    expired = await LeaseService(ctx).recover_expired()
    promoted = await Dispatcher(ctx).promote_retries()
    fixed = await reconcile_capacity(ctx)
    from services.tasks.uploads import UploadService

    purged = await UploadService(ctx).purge_expired()
    return {
        "expired": expired,
        "promoted": promoted,
        "capacity_fixed": fixed,
        "uploads_purged": purged,
    }


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    ctx = build_context(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("scheduler started")
    try:
        while not stop.is_set():
            try:
                result = await tick(ctx)
                if any(result.values()):
                    log.info("scheduler tick", extra=result)
            except Exception:  # noqa: BLE001 - keep running (e.g. before migrations)
                log.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass
    finally:
        await ctx.aclose()


if __name__ == "__main__":
    asyncio.run(main())
