from __future__ import annotations

import uuid

from fastapi import Request

from services.context import AppContext
from services.errors import not_found
from services.gateway.service import GatewayService
from services.registry.service import RegistryService
from services.tasks.service import TaskService


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_task_service(request: Request) -> TaskService:
    return TaskService(get_ctx(request))


def get_gateway_service(request: Request) -> GatewayService:
    return request.app.state.gateway


def get_registry_service(request: Request) -> RegistryService:
    return RegistryService(get_ctx(request))


def parse_uuid_or_404(value: str, code: str = "NOT_FOUND") -> uuid.UUID:
    """Malformed ids are reported exactly like missing ones (no existence oracle)."""
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise not_found(code, "Not found") from exc
