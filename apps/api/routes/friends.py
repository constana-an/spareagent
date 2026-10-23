"""Invite-only dashboard authentication; never accepts Codex credentials."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select, update

from apps.api.dependencies import get_ctx
from apps.api.middleware.auth import get_principal
from apps.api.schemas.experiments import AuditOut
from apps.api.schemas.self_serve import JoinCodeIn
from db.models import (
    ExperimentalDispatchAudit,
    ExperimentalTaskConsent,
    FriendsLoginInvite,
    FriendsSession,
    User,
)
from services.errors import DomainError
from services.experiments.participation import ExperimentalParticipationService, _audit
from services.experiments.self_serve import SelfServeSharingService
from services.friends_auth import (
    COOKIE,
    MEMBER_COOKIE_DAYS,
    check_origin,
    digest,
    invited,
    remembered_principal,
    session_principal,
)
from services.tasks.service import Principal

router = APIRouter(prefix="/v1/friends", tags=["friends"])


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class InviteOut(BaseModel):
    token: str
    expires_at: datetime


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Annotated[StrictStr, Field(min_length=20, max_length=256)]


class Identity(BaseModel):
    user_id: uuid.UUID
    role: str
    terms_version: str
    live_enabled: bool


def require_enabled(request):
    ctx = get_ctx(request)
    if not ctx.settings.friends_auth_enabled:
        raise DomainError(404, "NOT_FOUND", "Friends authentication disabled")
    return ctx


@router.post(
    "/invites", operation_id="createFriendsLoginInvite", response_model=InviteOut, status_code=201
)
async def create_invite(
    body: InviteCreate,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
) -> InviteOut:
    ctx = require_enabled(request)
    if principal.role != "ADMIN":
        raise DomainError(403, "OPERATOR_ONLY", "Only the operator issues login invites")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(seconds=body.ttl_seconds)
    async with ctx.sessionmaker() as session, session.begin():
        user = await session.get(User, body.user_id)
        if user is None or (user.role != "ADMIN" and not await invited(session, body.user_id)):
            raise DomainError(404, "INVITATION_REQUIRED", "Invite user to an active group first")
        # Issuing another invite invalidates all older unredeemed codes for this user.
        await session.execute(
            update(FriendsLoginInvite)
            .where(FriendsLoginInvite.user_id == user.id, FriendsLoginInvite.used_at.is_(None))
            .values(used_at=datetime.now(UTC))
        )
        session.add(
            FriendsLoginInvite(user_id=user.id, token_digest=digest(token), expires_at=expires)
        )
        _audit(session, principal.user_id, "friends.invite", "user", user.id)
    response.headers["Cache-Control"] = "no-store"
    return InviteOut(token=token, expires_at=expires)


@router.post("/login", operation_id="loginFriends", response_model=Identity)
async def login(body: Login, request: Request, response: Response) -> Identity:
    ctx = require_enabled(request)
    check_origin(request, ctx)
    now = datetime.now(UTC)
    async with ctx.sessionmaker() as session, session.begin():
        row = (
            await session.execute(
                select(FriendsLoginInvite)
                .where(FriendsLoginInvite.token_digest == digest(body.token))
                .with_for_update()
            )
        ).scalar_one_or_none()
        user = await session.get(User, row.user_id) if row else None
        if (
            not row
            or row.used_at
            or row.expires_at <= now
            or not user
            or (user.role != "ADMIN" and not await invited(session, user.id))
        ):
            raise DomainError(401, "INVALID_INVITE", "Invite is invalid, expired or already used")
        row.used_at = now
        await _open_session(session, request, response, ctx, user.id)
        _audit(session, user.id, "friends.login", "user", user.id)
    response.headers["Cache-Control"] = "no-store"
    return identity(user.id, user.role, ctx)


async def _open_session(
    session, request: Request, response: Response, ctx, user_id, *, member: bool = False
) -> None:
    """Rotate any current browser session and set a new one for `user_id`. Members who
    joined with an invite code get a long session and a cookie that outlives it."""
    now = datetime.now(UTC)
    if member:
        lifetime = timedelta(days=ctx.settings.friends_member_session_days)
        cookie_seconds = MEMBER_COOKIE_DAYS * 86400
    else:
        lifetime = timedelta(hours=ctx.settings.friends_session_hours)
        cookie_seconds = ctx.settings.friends_session_hours * 3600
    token = secrets.token_urlsafe(32)
    if request.cookies.get(COOKIE):
        await session.execute(
            update(FriendsSession)
            .where(FriendsSession.token_digest == digest(request.cookies[COOKIE]))
            .values(revoked_at=now)
        )
    session.add(
        FriendsSession(
            user_id=user_id,
            token_digest=digest(token),
            expires_at=now + lifetime,
        )
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=ctx.settings.app_env != "local",
        samesite="strict",
        max_age=cookie_seconds,
        path="/",
    )


@router.post("/join-code", operation_id="joinWithInviteCode", response_model=Identity)
async def join_with_code(body: JoinCodeIn, request: Request, response: Response) -> Identity:
    """Join a one-click sharing group with its invite code; signs the browser in."""
    ctx = require_enabled(request)
    check_origin(request, ctx)
    principal, signed_in = None, False
    if request.cookies.get(COOKIE):
        try:
            principal, signed_in = await session_principal(request, ctx), True
        except DomainError:
            # Expired, or removed from the group: rejoin as the same person rather than
            # as a new participant who would take another seat and lose their history.
            principal = await remembered_principal(request, ctx)
    user_id, role, _ = await SelfServeSharingService(ctx).join_with_code(
        principal, body.code, body.acknowledgements, body.terms_version, nickname=body.nickname
    )
    if not signed_in or principal.user_id != user_id:
        async with ctx.sessionmaker() as session, session.begin():
            await _open_session(session, request, response, ctx, user_id, member=True)
    response.headers["Cache-Control"] = "no-store"
    return identity(user_id, role, ctx)


def identity(user_id, role, ctx):
    return Identity(
        user_id=user_id,
        role=role,
        terms_version=ctx.settings.experiment_terms_version,
        live_enabled=ctx.settings.experiment_live_enabled,
    )


@router.get("/me", operation_id="getFriendsIdentity", response_model=Identity)
async def me(
    request: Request, response: Response, principal: Principal = Depends(get_principal)
) -> Identity:
    ctx = require_enabled(request)
    response.headers["Cache-Control"] = "no-store"
    return identity(principal.user_id, principal.role, ctx)


@router.post("/logout", operation_id="logoutFriends", status_code=204, response_model=None)
async def logout(
    request: Request, response: Response, principal: Principal = Depends(get_principal)
) -> None:
    ctx = require_enabled(request)
    async with ctx.sessionmaker() as session, session.begin():
        await session.execute(
            update(FriendsSession)
            .where(FriendsSession.token_digest == digest(request.cookies.get(COOKIE, "")))
            .values(revoked_at=datetime.now(UTC))
        )
        _audit(session, principal.user_id, "friends.logout", "user", principal.user_id)
    response.delete_cookie(COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/sessions/{user_id}/revoke",
    operation_id="revokeFriendsSessions",
    status_code=204,
    response_model=None,
)
async def revoke_sessions(
    user_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
) -> None:
    ctx = require_enabled(request)
    if principal.role != "ADMIN" and principal.user_id != user_id:
        raise DomainError(403, "FORBIDDEN", "Cannot revoke another participant's sessions")
    now = datetime.now(UTC)
    async with ctx.sessionmaker() as session, session.begin():
        await session.execute(
            update(FriendsSession).where(FriendsSession.user_id == user_id).values(revoked_at=now)
        )
        await session.execute(
            update(FriendsLoginInvite)
            .where(FriendsLoginInvite.user_id == user_id)
            .values(used_at=now)
        )
        _audit(session, principal.user_id, "friends.sessions.revoke", "user", user_id)
    response.headers["Cache-Control"] = "no-store"


@router.get(
    "/groups/{group_id}/audit", operation_id="getFriendsAudit", response_model=list[AuditOut]
)
async def friends_audit(
    group_id: uuid.UUID, request: Request, principal: Principal = Depends(get_principal)
) -> list[AuditOut]:
    ctx = require_enabled(request)
    async with ctx.sessionmaker() as session:
        await ExperimentalParticipationService(ctx)._require_active_member(
            session, group_id, principal.user_id
        )
        rows = (
            (
                await session.execute(
                    select(ExperimentalDispatchAudit)
                    .join(
                        ExperimentalTaskConsent,
                        ExperimentalTaskConsent.task_id == ExperimentalDispatchAudit.task_id,
                    )
                    .where(
                        ExperimentalDispatchAudit.group_id == group_id,
                        (ExperimentalTaskConsent.requester_id == principal.user_id)
                        | (ExperimentalTaskConsent.owner_id == principal.user_id),
                    )
                    .order_by(ExperimentalDispatchAudit.id.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        return [AuditOut.model_validate(row, from_attributes=True) for row in rows]
