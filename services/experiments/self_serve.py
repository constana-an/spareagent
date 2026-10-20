"""SelfServeSharingService - one-click capacity sharing for a provider's own group.

A newcomer's worker app signs up with the operator's signup code (shipped in the worker
bundle), which creates a provider account, the provider's own group and a one-time device
enrollment. The group copies the operator-verified template authorization, so LIVE is only
possible while the operator's record is valid. Afterwards every owner action is
authenticated with the node credential of the owner's own device:

* go_online   - connect, attest, set limits, accept the sharing terms, enable sharing with
                standing approval for the group's active members, and issue an invite code;
* go_offline  - pause sharing (queued tasks wait, running ones finish);
* settings    - member and task limits, adjustable at any time within the operator's caps;
* invite code - rotate; others join the group with it from the dashboard.

Every existing gate (operator switches, authorization, attestations, policy recheck at
dispatch/accept/renew, local pause) still applies. Nothing here handles credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ExperimentalAuthorization,
    ExperimentalGroup,
    ExperimentalGroupMember,
    ExperimentalTaskConsent,
    Resource,
    SubscriptionConnection,
    SubscriptionSharingPreference,
    Task,
    User,
    WorkerNode,
)
from services.context import AppContext
from services.errors import DomainError, conflict, forbidden, not_found
from services.experiments.participation import (
    JOIN_ACKNOWLEDGEMENTS,
    SHARING_ACKNOWLEDGEMENTS,
    ExperimentalParticipationService,
    _audit,
    _require_acks,
    current_authorization,
)
from services.subscriptions.policy import PERSONAL_SUBSCRIPTION
from services.subscriptions.usage import SubscriptionUsageMonitor
from services.tasks.service import Principal

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion
INVITE_LENGTH = 12  # 60 bits
SELF_SERVE_DOMAIN = "self-serve.spareagent.local"
ENROLLMENT_TTL_SECONDS = 900
DEFAULT_LIMITS = {
    "max_concurrent_tasks": 1,
    "max_tasks_per_day": 5,
    "max_task_duration_seconds": 600,
}
SELF_SERVE_TASK_TYPES = ["CODE_REVIEW"]
OPEN_MEMBER_STATUSES = ("INVITED", "ACTIVE")


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_invite_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def invite_digest(code: str) -> str:
    return hashlib.sha256(normalize_invite_code(code).encode()).hexdigest()


def new_invite_code() -> str:
    raw = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LENGTH))
    return "-".join(raw[i : i + 4] for i in range(0, INVITE_LENGTH, 4))


def authorization_usable(auth: ExperimentalAuthorization | None, now: datetime) -> bool:
    return bool(
        auth is not None
        and auth.verification_status == "OPERATOR_VERIFIED"
        and auth.integration_route_covered
        and auth.integration_route == "CODEX_CLI_EXEC"
        and auth.revoked_at is None
        and auth.expires_at > now
    )


class SelfServeSharingService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.participation = ExperimentalParticipationService(ctx)

    # ================================================================ helpers
    def _require_enabled(self) -> None:
        s = self.ctx.settings
        if not (s.self_serve_enabled and s.experiment_enabled):
            raise not_found("NOT_FOUND", "One-click sharing is not enabled on this server")

    def member_cap(self, auth: ExperimentalAuthorization | None) -> int:
        cap = self.ctx.settings.experiment_max_group_members
        if auth is not None and auth.max_participants is not None:
            cap = min(cap, auth.max_participants)
        return cap

    async def _member_count(self, session: AsyncSession, group_id: uuid.UUID) -> int:
        return (
            await session.execute(
                select(func.count())
                .select_from(ExperimentalGroupMember)
                .where(
                    ExperimentalGroupMember.group_id == group_id,
                    ExperimentalGroupMember.status.in_(OPEN_MEMBER_STATUSES),
                )
            )
        ).scalar_one()

    async def _owner_group(
        self, session: AsyncSession, worker_id: uuid.UUID, *, lock: bool = False
    ) -> tuple[uuid.UUID, ExperimentalGroup]:
        """The owner of this node's personal resource and their self-serve group."""
        worker = await session.get(WorkerNode, worker_id)
        resource = await session.get(Resource, worker.resource_id) if worker else None
        if resource is None or resource.resource_type != PERSONAL_SUBSCRIPTION:
            raise forbidden("NOT_SELF_SERVE_NODE", "This device is not a one-click sharing node")
        stmt = (
            select(ExperimentalGroup)
            .where(ExperimentalGroup.owner_id == resource.owner_id)
            .order_by(ExperimentalGroup.created_at.desc())
            .limit(1)
        )
        if lock:
            stmt = stmt.with_for_update()
        group = (await session.execute(stmt)).scalar_one_or_none()
        if group is None:
            raise not_found("SELF_SERVE_GROUP_NOT_FOUND", "No one-click sharing group")
        return resource.owner_id, group

    async def _connection(
        self, session: AsyncSession, worker_id: uuid.UUID
    ) -> SubscriptionConnection | None:
        return (
            await session.execute(
                select(SubscriptionConnection).where(SubscriptionConnection.worker_id == worker_id)
            )
        ).scalar_one_or_none()

    def _set_invite_code(self, group: ExperimentalGroup) -> str:
        code = new_invite_code()
        group.invite_code_digest = invite_digest(code)
        group.invite_code_rotated_at = utcnow()
        return code

    # ================================================================ signup (no session)
    async def signup(
        self,
        signup_code: str,
        device_label: str,
        acknowledgements: dict[str, bool],
        terms_version: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        expected = self.ctx.settings.self_serve_signup_code
        if expected is None or not hmac.compare_digest(
            signup_code.encode(), expected.get_secret_value().encode()
        ):
            raise DomainError(401, "SIGNUP_CODE_INVALID", "The signup code is not valid")
        _require_acks(acknowledgements, SHARING_ACKNOWLEDGEMENTS)
        if terms_version != self.ctx.settings.experiment_terms_version:
            raise DomainError(422, "TERMS_VERSION_MISMATCH", "Please accept the current terms")
        now = utcnow()
        async with self.ctx.sessionmaker() as session, session.begin():
            # FOR SHARE: a concurrent template revocation (FOR UPDATE + cascade) cannot
            # commit between this check and the copy below.
            template = await session.get(
                ExperimentalAuthorization,
                self.ctx.settings.self_serve_authorization_id,
                with_for_update={"read": True},
            )
            if not authorization_usable(template, now):
                raise conflict(
                    "SELF_SERVE_NOT_AUTHORIZED",
                    "The operator's sharing authorization is missing, revoked or expired",
                )
            user = User(email=f"owner-{uuid.uuid4().hex}@{SELF_SERVE_DOMAIN}", role="PROVIDER")
            session.add(user)
            await session.flush()
            group = ExperimentalGroup(
                name=device_label[:100],
                created_by=user.id,
                owner_id=user.id,
                max_members=self.member_cap(template),
                allowed_task_types=SELF_SERVE_TASK_TYPES,
                **DEFAULT_LIMITS,
            )
            session.add(group)
            await session.flush()
            session.add(
                ExperimentalGroupMember(
                    group_id=group.id,
                    user_id=user.id,
                    invited_by=user.id,
                    status="ACTIVE",
                    joined_at=now,
                    terms_version=terms_version,
                    acknowledgements={k: True for k in JOIN_ACKNOWLEDGEMENTS},
                )
            )
            # The operator's verified record, applied to this group. It is revoked/expired
            # together with the template only by the operator's own actions on each copy.
            session.add(
                ExperimentalAuthorization(
                    group_id=group.id,
                    provider_name=template.provider_name,
                    authorization_source=template.authorization_source,
                    authorization_reference=template.authorization_reference,
                    authorization_scope={
                        **(template.authorization_scope or {}),
                        "self_serve_template_id": str(template.id),
                    },
                    integration_route=template.integration_route,
                    max_participants=template.max_participants,
                    expires_at=template.expires_at,
                    created_by=template.created_by,
                    verification_status="OPERATOR_VERIFIED",
                    integration_route_covered=True,
                    verified_by=template.verified_by,
                    verified_at=now,
                )
            )
            _audit(
                session,
                user.id,
                "self_serve.signup",
                "group",
                group.id,
                template=str(template.id),
                terms_version=terms_version,
            )
        resource_id, issued = await self._node_auth().create_personal_enrollment_token(
            Principal(user.id, user.role), ENROLLMENT_TTL_SECONDS
        )
        return {
            "user_id": user.id,
            "group_id": group.id,
            "resource_id": resource_id,
            "enrollment_token": issued.secret,
            "enrollment_expires_at": issued.expires_at,
        }

    def _node_auth(self):
        from services.registry.node_auth import NodeAuthService

        return NodeAuthService(self.ctx)

    # ================================================================ owner (node auth)
    async def go_online(self, worker_id: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        _require_acks(body["acknowledgements"], SHARING_ACKNOWLEDGEMENTS)
        async with self.ctx.sessionmaker() as session:
            owner_id, group = await self._owner_group(session, worker_id)
            owner = await session.get(User, owner_id)
        principal = Principal(owner_id, owner.role)
        svc = self.participation
        conn = await svc.connect(
            principal,
            {
                "worker_id": worker_id,
                "device_label": body["device_label"],
                "subscription_type": body["subscription_type"],
                "auth_method": "CHATGPT",
            },
        )
        await svc.attest(
            principal,
            conn.id,
            {
                "codex_check_result": body["codex_check_result"],
                "no_purchased_credits": body["no_purchased_credits"],
                "no_auto_reload": body["no_auto_reload"],
            },
        )
        await svc.set_preferences(
            principal,
            conn.id,
            group.id,
            {
                "max_concurrent_tasks": 1,
                "max_tasks_per_day": group.max_tasks_per_day,
                "max_task_duration_seconds": group.max_task_duration_seconds,
                "allowed_task_types": group.allowed_task_types,
            },
        )
        await svc.enable(principal, conn.id, body["acknowledgements"], body["terms_version"])
        invite_code = None
        async with self.ctx.sessionmaker() as session, session.begin():
            _, group = await self._owner_group(session, worker_id, lock=True)
            prefs = await session.get(SubscriptionSharingPreference, conn.id, with_for_update=True)
            prefs.auto_approve_members = bool(body["auto_approve_members"])
            group.sharing_enabled = True
            if group.invite_code_digest is None:
                invite_code = self._set_invite_code(group)
            _audit(
                session,
                owner_id,
                "self_serve.online",
                "connection",
                conn.id,
                auto_approve_members=prefs.auto_approve_members,
            )
        status = await self.status(worker_id)
        status["invite_code"] = invite_code
        return status

    async def go_offline(self, worker_id: uuid.UUID) -> dict[str, Any]:
        self._require_enabled()
        async with self.ctx.sessionmaker() as session:
            owner_id, _ = await self._owner_group(session, worker_id)
            owner = await session.get(User, owner_id)
            conn = await self._connection(session, worker_id)
            prefs = await session.get(SubscriptionSharingPreference, conn.id) if conn else None
        if prefs is not None and prefs.sharing_state == "ENABLED":
            await self.participation.pause(Principal(owner_id, owner.role), conn.id)
        return await self.status(worker_id)

    async def update_settings(self, worker_id: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        async with self.ctx.sessionmaker() as session, session.begin():
            owner_id, group = await self._owner_group(session, worker_id, lock=True)
            if body.get("max_members") is not None:
                cap = self.member_cap(await current_authorization(session, group.id))
                floor = max(2, await self._member_count(session, group.id))
                if not floor <= body["max_members"] <= cap:
                    raise DomainError(
                        422,
                        "MEMBER_LIMIT_OUT_OF_RANGE",
                        f"max_members must be between {floor} and {cap}",
                    )
                group.max_members = body["max_members"]
            conn = await self._connection(session, worker_id)
            prefs = (
                await session.get(SubscriptionSharingPreference, conn.id, with_for_update=True)
                if conn
                else None
            )
            for key in ("max_tasks_per_day", "max_task_duration_seconds"):
                if body.get(key) is not None:
                    setattr(group, key, body[key])
                    if prefs is not None:
                        setattr(prefs, key, body[key])
            if prefs is not None and body.get("auto_approve_members") is not None:
                prefs.auto_approve_members = body["auto_approve_members"]
            if prefs is not None:
                prefs.updated_at = utcnow()
            _audit(
                session,
                owner_id,
                "self_serve.settings",
                "group",
                group.id,
                **{k: v for k, v in body.items() if v is not None},
            )
        return await self.status(worker_id)

    async def rotate_invite_code(self, worker_id: uuid.UUID) -> dict[str, Any]:
        self._require_enabled()
        async with self.ctx.sessionmaker() as session, session.begin():
            owner_id, group = await self._owner_group(session, worker_id, lock=True)
            if group.status != "ACTIVE":
                raise conflict("GROUP_DISABLED", "Group is disabled")
            code = self._set_invite_code(group)
            _audit(session, owner_id, "self_serve.invite_code.rotate", "group", group.id)
        status = await self.status(worker_id)
        status["invite_code"] = code
        return status

    async def remove_member(self, worker_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
        """Remove a member and rotate the invite code so they cannot simply rejoin."""
        self._require_enabled()
        async with self.ctx.sessionmaker() as session, session.begin():
            owner_id, group = await self._owner_group(session, worker_id, lock=True)
            if user_id == owner_id:
                raise DomainError(422, "CANNOT_REMOVE_OWNER", "The owner cannot be removed")
            member = await self.participation._membership(session, group.id, user_id)
            if member is None or member.status == "REMOVED":
                raise not_found("MEMBER_NOT_FOUND", "Member not found")
            member.status, member.removed_at = "REMOVED", utcnow()
            await self.participation._cancel_tasks(
                session,
                (ExperimentalTaskConsent.group_id == group.id)
                & (ExperimentalTaskConsent.requester_id == user_id),
                "MEMBER_REMOVED",
            )
            code = self._set_invite_code(group)
            _audit(session, owner_id, "self_serve.member.remove", "group", group.id, user=user_id)
        status = await self.status(worker_id)
        status["invite_code"] = code
        return status

    async def dashboard_login(self, worker_id: uuid.UUID) -> dict[str, Any]:
        """A one-time dashboard sign-in code for the owner of this device."""
        from db.models import FriendsLoginInvite
        from services.friends_auth import digest

        self._require_enabled()
        token = secrets.token_urlsafe(32)
        expires = utcnow() + timedelta(minutes=5)
        async with self.ctx.sessionmaker() as session, session.begin():
            owner_id, _ = await self._owner_group(session, worker_id)
            session.add(
                FriendsLoginInvite(user_id=owner_id, token_digest=digest(token), expires_at=expires)
            )
            _audit(session, owner_id, "self_serve.dashboard_login", "user", owner_id)
        return {
            "token": token,
            "expires_at": expires,
            "dashboard_url": self.ctx.settings.friends_web_origin,
        }

    async def status(self, worker_id: uuid.UUID) -> dict[str, Any]:
        self._require_enabled()
        now = utcnow()
        async with self.ctx.sessionmaker() as session:
            owner_id, group = await self._owner_group(session, worker_id)
            members = (
                (
                    await session.execute(
                        select(ExperimentalGroupMember)
                        .where(
                            ExperimentalGroupMember.group_id == group.id,
                            ExperimentalGroupMember.status.in_(OPEN_MEMBER_STATUSES),
                        )
                        .order_by(ExperimentalGroupMember.invited_at)
                    )
                )
                .scalars()
                .all()
            )
            auth = await current_authorization(session, group.id)
            conn = await self._connection(session, worker_id)
            prefs = await session.get(SubscriptionSharingPreference, conn.id) if conn else None
            started = (
                await SubscriptionUsageMonitor.started_last_24h(session, conn.id, now)
                if conn
                else 0
            )
            tasks = (
                await session.execute(
                    select(Task, ExperimentalTaskConsent)
                    .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
                    .where(ExperimentalTaskConsent.owner_id == owner_id)
                    .order_by(Task.created_at.desc())
                    .limit(20)
                )
            ).all()
        live_blockers = []
        if not self.ctx.settings.experiment_live_enabled:
            live_blockers.append("LIVE_EXECUTION_DISABLED")
        if not authorization_usable(auth, now):
            live_blockers.append("AUTHORIZATION_INVALID")
        if group.status != "ACTIVE":
            live_blockers.append("GROUP_DISABLED")
        return {
            "group": {
                "id": group.id,
                "name": group.name,
                "status": group.status,
                "sharing_enabled": group.sharing_enabled,
                "max_members": group.max_members,
                "member_cap": self.member_cap(auth),
                "max_tasks_per_day": group.max_tasks_per_day,
                "max_task_duration_seconds": group.max_task_duration_seconds,
                "has_invite_code": group.invite_code_digest is not None,
                "invite_code_rotated_at": group.invite_code_rotated_at,
            },
            "members": [
                {
                    "user_id": m.user_id,
                    "status": m.status,
                    "joined_at": m.joined_at,
                    "is_owner": m.user_id == owner_id,
                }
                for m in members
            ],
            "sharing": None
            if prefs is None
            else {
                "connection_id": prefs.connection_id,
                "state": prefs.sharing_state,
                "auto_approve_members": prefs.auto_approve_members,
            },
            "live_ready": not live_blockers,
            "live_blockers": live_blockers,
            "tasks_started_last_24h": started,
            "tasks": [
                {
                    "id": t.id,
                    "status": t.status,
                    "task_type": t.task_type,
                    "execution_mode": "LIVE"
                    if t.execution_profile == "CODEX_LIVE"
                    else "SIMULATED",
                    "requester_id": c.requester_id,
                    "approval_status": c.status,
                    "approval_source": c.consent_source,
                    "created_at": t.created_at,
                }
                for t, c in tasks
            ],
            "dashboard_url": self.ctx.settings.friends_web_origin,
            "invite_code": None,
        }

    # ================================================================ joining (dashboard)
    async def join_with_code(
        self,
        principal: Principal | None,
        code: str,
        acknowledgements: dict[str, bool],
        terms_version: str,
    ) -> tuple[uuid.UUID, str, uuid.UUID]:
        """Join a self-serve group with its invite code. Without a dashboard session a new
        participant account is created. Returns (user_id, role, group_id)."""
        self._require_enabled()
        _require_acks(acknowledgements, JOIN_ACKNOWLEDGEMENTS)
        if terms_version != self.ctx.settings.experiment_terms_version:
            raise DomainError(422, "TERMS_VERSION_MISMATCH", "Please accept the current terms")
        if len(normalize_invite_code(code)) != INVITE_LENGTH:
            raise DomainError(404, "INVITE_CODE_INVALID", "Invite code is not valid")
        async with self.ctx.sessionmaker() as session, session.begin():
            group = (
                await session.execute(
                    select(ExperimentalGroup)
                    .where(ExperimentalGroup.invite_code_digest == invite_digest(code))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if group is None or group.status != "ACTIVE":
                raise DomainError(404, "INVITE_CODE_INVALID", "Invite code is not valid")
            member = None
            if principal is not None:
                member = await self.participation._membership(session, group.id, principal.user_id)
                if member is not None and member.status == "ACTIVE":
                    return principal.user_id, principal.role, group.id
            if await self._member_count(session, group.id) >= group.max_members:
                raise conflict("GROUP_FULL", f"Group is limited to {group.max_members} members")
            if principal is None:
                user = User(email=f"member-{uuid.uuid4().hex}@{SELF_SERVE_DOMAIN}", role="CONSUMER")
                session.add(user)
                await session.flush()
                principal = Principal(user.id, user.role)
            now = utcnow()
            if member is None:
                member = ExperimentalGroupMember(
                    group_id=group.id, user_id=principal.user_id, invited_by=group.owner_id
                )
                session.add(member)
            member.status, member.removed_at = "ACTIVE", None
            member.joined_at, member.terms_version = now, terms_version
            member.acknowledgements = {k: True for k in JOIN_ACKNOWLEDGEMENTS}
            _audit(session, principal.user_id, "self_serve.join", "group", group.id)
        return principal.user_id, principal.role, group.id
