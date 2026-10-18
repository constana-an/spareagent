"""Best-effort Redis wake-ups. Redis is never the source of truth: the worker's periodic
queue scan repairs any lost notification."""

from __future__ import annotations

import logging

from redis.asyncio import Redis

WAKE_KEY = "spareagent:wake"
log = logging.getLogger(__name__)


class WakePublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def wake(self, reason: str) -> bool:
        try:
            await self._redis.lpush(WAKE_KEY, reason)
            await self._redis.ltrim(WAKE_KEY, 0, 999)
            return True
        except Exception as exc:  # noqa: BLE001 - best effort by design
            log.warning(
                "wake-up publish failed; periodic scan will recover", extra={"err": str(exc)}
            )
            return False
