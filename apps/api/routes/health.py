from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from services.context import AppContext

router = APIRouter(tags=["health"])
CHECK_TIMEOUT_SECONDS = 2.0


@router.get("/healthz", operation_id="health")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _check_db(ctx: AppContext) -> None:
    async with ctx.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis(ctx: AppContext) -> None:
    if not await ctx.redis.ping():
        raise RuntimeError("redis ping failed")


@router.get("/readyz", operation_id="ready")
async def readyz(request: Request) -> JSONResponse:
    ctx: AppContext = request.app.state.ctx
    checks: dict[str, str] = {}
    for name, probe in (("database", _check_db), ("redis", _check_redis)):
        try:
            await asyncio.wait_for(probe(ctx), timeout=CHECK_TIMEOUT_SECONDS)
            checks[name] = "ok"
        except Exception:  # noqa: BLE001 - report, never leak connection details
            checks[name] = "unavailable"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "unavailable", "checks": checks},
    )
