from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis

from services.queue.publisher import WAKE_KEY

log = logging.getLogger(__name__)


async def wait_for_wake(redis: Redis, timeout_seconds: float) -> bool:
    """Block until a wake-up arrives or the timeout passes. Never raises."""
    try:
        item = await redis.brpop([WAKE_KEY], timeout=max(1, int(timeout_seconds)))
        return item is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("wake-up wait failed; falling back to polling", extra={"err": str(exc)})
        await asyncio.sleep(timeout_seconds)
        return False
