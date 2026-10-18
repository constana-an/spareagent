"""FastAPI application factory: `uvicorn --factory apps.api.main:create_app`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.config import Settings, load_settings
from apps.api.middleware.request_id import RequestIdMiddleware
from apps.api.routes import admin, experiments, gateway, health, tasks, workers
from services.context import AppContext, build_context
from services.errors import DomainError
from services.gateway.service import GatewayService
from services.observability import configure_logging
from services.registry.node_auth import NodeAuthService

log = logging.getLogger("spareagent.api")

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or "unknown"


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": _request_id(request)}},
    )


def _validation_message(exc: RequestValidationError) -> str:
    # Never echo the submitted input (it may contain secrets); location + reason only.
    parts = []
    for err in exc.errors()[:10]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) or "Invalid request"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        body = getattr(exc, "body", None)
        if (
            request.url.path == "/v1/chat/completions"
            and isinstance(body, dict)
            and body.get("stream") is True
        ):
            return error_response(
                request, 422, "UNSUPPORTED_STREAMING", gateway.UNSUPPORTED_STREAMING.message
            )
        if request.url.path == "/v1/chat/completions" and any(
            e.get("type") == "extra_forbidden" for e in exc.errors()
        ):
            return error_response(request, 422, "UNSUPPORTED_PARAMETER", _validation_message(exc))
        return error_response(request, 422, "VALIDATION_ERROR", _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code
        return error_response(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return error_response(request, 500, "INTERNAL_ERROR", "Internal server error")


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    settings = settings or load_settings()  # raises on unsafe config (fail closed)
    configure_logging(settings.log_level)
    ctx = ctx or build_context(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        log.info(
            "api starting",
            extra={"app_env": settings.app_env, "provider_mode": settings.provider_mode},
        )
        yield
        await ctx.aclose()

    app = FastAPI(
        title="SpareAgent MVP API",
        version="1.0.0",
        description="Week 1 vertical slice: tasks, events, artifacts, mock gateway, health.",
        lifespan=lifespan,
    )
    app.state.ctx = ctx
    app.state.gateway = GatewayService(ctx)
    app.state.node_auth = NodeAuthService(ctx)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    for module in (health, tasks, gateway, workers, admin, experiments):
        app.include_router(module.router)
    return app
