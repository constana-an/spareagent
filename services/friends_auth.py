"""Opaque, revocable friends sessions. Only SHA-256 digests are persisted."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select

from db.models import ExperimentalGroup, ExperimentalGroupMember, FriendsSession, User
from services.context import AppContext
from services.errors import DomainError
from services.tasks.service import Principal

COOKIE = "sa_friends_session"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_origin(request: Request, ctx: AppContext) -> None:
    # Browser writes must come through the configured same-origin dashboard. Requiring
    # Origin also prevents form/CSRF login attacks; operator scripts must supply it.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("origin") != ctx.settings.friends_web_origin:
            raise DomainError(403, "ORIGIN_REJECTED", "Dashboard origin required")


async def invited(session, user_id, *, active_only=False) -> bool:
    return (
        await session.execute(
            select(ExperimentalGroupMember.id)
            .join(ExperimentalGroup, ExperimentalGroup.id == ExperimentalGroupMember.group_id)
            .where(
                ExperimentalGroupMember.user_id == user_id,
                ExperimentalGroupMember.status.in_(
                    ("ACTIVE",) if active_only else ("INVITED", "ACTIVE")
                ),
                ExperimentalGroup.status == "ACTIVE",
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None


async def session_principal(request: Request, ctx: AppContext) -> Principal:
    check_origin(request, ctx)
    token = request.cookies.get(COOKIE, "")
    if not 20 <= len(token) <= 256:
        raise DomainError(401, "UNAUTHENTICATED", "Session required")
    async with ctx.sessionmaker() as session:
        row = (
            await session.execute(
                select(FriendsSession).where(
                    FriendsSession.token_digest == digest(token),
                    FriendsSession.expires_at > datetime.now(UTC),
                    FriendsSession.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        user = await session.get(User, row.user_id) if row else None
        if user is None or (user.role != "ADMIN" and not await invited(session, user.id)):
            raise DomainError(401, "UNAUTHENTICATED", "Session expired or access withdrawn")
        return Principal(user_id=user.id, role=user.role)
