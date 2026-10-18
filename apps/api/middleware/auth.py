"""Authentication.

Week 1 supports only DEV_AUTH_ONLY: the `X-Dev-User-ID` header selects a seeded user, and
only for requests that are local (trusted client network AND a localhost Host header).
Config validation already refuses DEV_AUTH_ONLY outside APP_ENV=local. JWT/OIDC is not
implemented yet, so with dev auth disabled every authenticated route fails closed (401).
User identity is never read from request bodies.
"""

from __future__ import annotations

import ipaddress
import uuid

from fastapi import Request

from db.models import User
from services.context import AppContext
from services.errors import DomainError
from services.tasks.service import Principal

_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_local_request(request: Request, ctx: AppContext) -> bool:
    client = request.client.host if request.client else None
    if client is None:
        return False
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return False
    if not any(address in net for net in ctx.settings.trusted_networks):
        return False
    return _hostname(request.headers.get("host", "")) in _LOCAL_HOSTNAMES


def unauthenticated(message: str) -> DomainError:
    return DomainError(401, "UNAUTHENTICATED", message)


async def get_principal(request: Request) -> Principal:
    ctx: AppContext = request.app.state.ctx
    if not ctx.settings.dev_auth_only:
        raise unauthenticated("No authentication method is enabled in this build (JWT is W2+)")
    if not is_local_request(request, ctx):
        raise unauthenticated("Development authentication is only accepted from localhost")
    raw = request.headers.get("x-dev-user-id")
    if not raw:
        raise unauthenticated("Missing X-Dev-User-ID header")
    try:
        user_id = uuid.UUID(raw)
    except ValueError as exc:
        raise unauthenticated("Invalid X-Dev-User-ID header") from exc
    async with ctx.sessionmaker() as session:
        user = await session.get(User, user_id)
    if user is None:
        raise unauthenticated("Unknown development user")
    return Principal(user_id=user.id, role=user.role)
