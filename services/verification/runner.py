"""Platform verifier process: `python -m services.verification.runner`.

Refuses to start unless the sandbox health check passes, reaps stale sandboxes, then
verifies submitted CODE attempts one at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from apps.api.config import load_settings
from services.context import build_context
from services.observability import configure_logging
from services.verification.service import from_settings

log = logging.getLogger("spareagent.verifier")


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    ctx = build_context(settings)
    service = from_settings(ctx)
    health = await asyncio.to_thread(service.sandbox.health)
    log.info("sandbox healthy", extra={"checks": health["checks"]})
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set():
            try:
                await asyncio.to_thread(service.sandbox.reap_stale, 600)
                did = await service.run_once()
            except Exception:  # noqa: BLE001 - keep running (e.g. before migrations)
                log.exception("verifier iteration failed")
                did = None
            if did is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
    finally:
        await ctx.aclose()


if __name__ == "__main__":
    asyncio.run(main())
