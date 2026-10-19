"""ExperimentalParticipationService - invitation-only, non-commercial capacity sharing.

Roles: the operator (ADMIN) creates groups, invites members, records and verifies the
authorization record and owns the global kill switch. Members join with explicit
acknowledgements. A subscription owner connects their own device, sets limits, opts in
(separately from joining), and can pause or revoke at any time. Requesters submit tasks
to a specific opted-in owner, who approves each task or grants scoped advance consent.

No endpoint here accepts, stores or returns credentials of any kind.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    AuditLog,
    ExperimentalAuthorization,
    ExperimentalDispatchAudit,
    ExperimentalGroup,
    ExperimentalGroupMember,
    ExperimentalTaskConsent,
    SubscriptionConnection,
    SubscriptionSharingPreference,
    Task,
    WorkerNode,
)
from services.context import AppContext
from services.errors import DomainError, conflict, forbidden, not_found
from services.scheduler import lease_service
from services.subscriptions.policy import EXPERIMENTAL_SCOPE
from services.subscriptions.usage import SubscriptionUsageMonitor
from services.tasks import repository as repo
from services.tasks.service import Principal
from services.tasks.state_machine import TaskStatus, append_event, is_terminal, transition

# What a member accepts on joining the group.
JOIN_ACKNOWLEDGEMENTS = (
    "non_commercial_experimental_use",
    "no_payment_or_compensation",
    "participation_revocable_any_time",
)
# What an owner additionally accepts before their subscription can be shared.
SHARING_ACKNOWLEDGEMENTS = (
    "non_commercial_experimental_use",
    "no_payment_or_compensation",
    "subscription_usage_may_be_consumed_within_confirmed_scope",
    "device_executes_opted_in_workloads",
    "participation_revocable_any_time",
    "owner_controls_local_credentials",
    "owner_may_add_restrictions",
)
TASK_TYPES = ("CODE_REVIEW", "WRITE_TESTS", "CODE_FIX")
LIMIT_FIELDS = ("max_concurrent_tasks", "max_tasks_per_day", "max_task_duration_seconds")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _require_admin(principal: Principal) -> None:
    if principal.role != "ADMIN":
        raise forbidden("OPERATOR_ONLY", "Only the experiment operator may do this")


def _require_acks(given: dict[str, bool], required: tuple[str, ...]) -> None:
    missing = [k for k in required if given.get(k) is not True]
    if missing:
        raise DomainError(422, "CONSENT_REQUIRED", f"Missing acknowledgements: {missing}")


def _audit(session: AsyncSession, actor: uuid.UUID, action: str, ttype: str, tid: Any, **meta):
    session.add(
        AuditLog(
            actor_id=actor,
            action=action,
            target_type=ttype,
            target_id=str(tid),
            metadata_=json.loads(json.dumps(meta, default=str)),
        )
    )


async def force_cancel(session: AsyncSession, task: Task, reason: str) -> bool:
    """Cancel an experimental task on the platform's authority (revocation, kill switch,
    owner rejection). Same semantics as a user cancel. Caller holds the task lock."""
    status = TaskStatus(task.status)
    if is_terminal(status) or status == TaskStatus.CANCELLING:
        return False
    payload = {"reason": reason, "result_summary": f"Cancelled: {reason}"}
    if status == TaskStatus.RESERVED:
        attempt = await repo.active_attempt(session, task.id)
        if attempt is not None:
            await lease_service.close_attempt(session, attempt, "CANCELLED")
        await transition(
            session,
            task,
            TaskStatus.CANCELLED,
            payload=payload,
            attempt_id=attempt.id if attempt else None,
        )
    elif status in (TaskStatus.RUNNING, TaskStatus.VERIFYING):
        # The executing node observes CANCELLING on its next progress/renew and stops.
        await transition(session, task, TaskStatus.CANCELLING, payload=payload)
    else:
        await transition(session, task, TaskStatus.CANCELLED, payload=payload)
    return True


@dataclass(frozen=True)
class ExperimentalTaskView:
    task: Task
    consent: ExperimentalTaskConsent
    last_decision: ExperimentalDispatchAudit | None


class ExperimentalParticipationService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # ================================================================ operator
    async def create_group(
        self, principal: Principal, name: str, limits: dict[str, Any]
    ) -> ExperimentalGroup:
        _require_admin(principal)
        cap = self.ctx.settings.experiment_max_group_members
        max_members = limits.get("max_members") or cap
        if max_members > cap:
            raise DomainError(422, "GROUP_TOO_LARGE", f"max_members cannot exceed {cap}")
        types = limits.get("allowed_task_types") or list(TASK_TYPES)
        async with self.ctx.sessionmaker() as session, session.begin():
            group = ExperimentalGroup(
                name=name,
                created_by=principal.user_id,
                max_members=max_members,
                allowed_task_types=sorted(set(types)),
                **{k: limits[k] for k in LIMIT_FIELDS if limits.get(k) is not None},
            )
            session.add(group)
            await session.flush()
            _audit(session, principal.user_id, "experiment.group.create", "group", group.id)
        return group

    async def _group(self, session: AsyncSession, group_id: uuid.UUID, *, lock=False):
        group = await session.get(ExperimentalGroup, group_id, with_for_update=lock)
        if group is None:
            raise not_found("GROUP_NOT_FOUND", "Group not found")
        return group

    async def invite(
        self, principal: Principal, group_id: uuid.UUID, user_id: uuid.UUID
    ) -> ExperimentalGroupMember:
        _require_admin(principal)
        async with self.ctx.sessionmaker() as session, session.begin():
            group = await self._group(session, group_id, lock=True)
            if group.status != "ACTIVE":
                raise conflict("GROUP_DISABLED", "Group is disabled")
            existing = (
                await session.execute(
                    select(ExperimentalGroupMember).where(
                        ExperimentalGroupMember.group_id == group_id,
                        ExperimentalGroupMember.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status != "REMOVED":
                return existing
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(ExperimentalGroupMember)
                    .where(
                        ExperimentalGroupMember.group_id == group_id,
                        ExperimentalGroupMember.status.in_(("INVITED", "ACTIVE")),
                    )
                )
            ).scalar_one()
            if count >= group.max_members:
                raise conflict("GROUP_FULL", f"Group is limited to {group.max_members} members")
            if existing is not None:
                existing.status, existing.removed_at = "INVITED", None
                existing.invited_by, existing.invited_at = principal.user_id, utcnow()
                member = existing
            else:
                member = ExperimentalGroupMember(
                    group_id=group_id, user_id=user_id, invited_by=principal.user_id
                )
                session.add(member)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise not_found("USER_NOT_FOUND", "User not found") from exc
            _audit(
                session,
                principal.user_id,
                "experiment.member.invite",
                "group",
                group_id,
                user_id=user_id,
            )
        return member

    async def record_authorization(
        self, principal: Principal, group_id: uuid.UUID, body: dict[str, Any]
    ) -> ExperimentalAuthorization:
        _require_admin(principal)
        async with self.ctx.sessionmaker() as session, session.begin():
            await self._group(session, group_id)
            auth = ExperimentalAuthorization(
                group_id=group_id,
                provider_name=body["provider_name"],
                authorization_source=body["authorization_source"],
                authorization_reference=body["authorization_reference"],
                authorization_scope=body.get("authorization_scope") or {},
                integration_route=body.get("integration_route"),
                max_participants=body.get("max_participants"),
                expires_at=body["expires_at"],
                created_by=principal.user_id,
            )
            session.add(auth)
            try:
                await session.flush()
            except (IntegrityError, DBAPIError) as exc:
                raise DomainError(422, "VALIDATION_ERROR", "Invalid authorization record") from exc
            _audit(
                session,
                principal.user_id,
                "experiment.authorization.record",
                "authorization",
                auth.id,
                source=auth.authorization_source,
            )
        return auth

    async def verify_authorization(
        self,
        principal: Principal,
        authorization_id: uuid.UUID,
        *,
        status: str,
        integration_route_covered: bool,
    ) -> ExperimentalAuthorization:
        _require_admin(principal)
        async with self.ctx.sessionmaker() as session, session.begin():
            auth = await session.get(
                ExperimentalAuthorization, authorization_id, with_for_update=True
            )
            if auth is None:
                raise not_found("AUTHORIZATION_NOT_FOUND", "Authorization not found")
            if auth.revoked_at is not None:
                raise conflict("AUTHORIZATION_REVOKED", "Authorization was revoked")
            if integration_route_covered and (
                auth.integration_route is None or auth.max_participants is None
            ):
                raise DomainError(
                    422,
                    "ROUTE_DETAILS_REQUIRED",
                    "Record integration_route and max_participants before marking it covered",
                )
            auth.verification_status = status
            auth.integration_route_covered = integration_route_covered
            auth.verified_by, auth.verified_at = principal.user_id, utcnow()
            _audit(
                session,
                principal.user_id,
                "experiment.authorization.verify",
                "authorization",
                auth.id,
                status=status,
                integration_route_covered=integration_route_covered,
            )
        return auth

    async def revoke_authorization(
        self, principal: Principal, authorization_id: uuid.UUID
    ) -> ExperimentalAuthorization:
        _require_admin(principal)
        async with self.ctx.sessionmaker() as session, session.begin():
            auth = await session.get(
                ExperimentalAuthorization, authorization_id, with_for_update=True
            )
            if auth is None:
                raise not_found("AUTHORIZATION_NOT_FOUND", "Authorization not found")
            auth.revoked_at = auth.revoked_at or utcnow()
            _audit(
                session,
                principal.user_id,
                "experiment.authorization.revoke",
                "authorization",
                auth.id,
            )
        return auth

    async def set_group_sharing(
        self, principal: Principal, group_id: uuid.UUID, enabled: bool
    ) -> ExperimentalGroup:
        _require_admin(principal)
        async with self.ctx.sessionmaker() as session, session.begin():
            group = await self._group(session, group_id, lock=True)
            if enabled and group.status != "ACTIVE":
                raise conflict("GROUP_DISABLED", "Group is disabled")
            group.sharing_enabled = enabled
            _audit(
                session,
                principal.user_id,
                "experiment.group.sharing",
                "group",
                group_id,
                enabled=enabled,
            )
        return group

    async def kill_switch(self, principal: Principal) -> dict[str, int]:
        """Disable every group and cancel every unfinished experimental task."""
        _require_admin(principal)
        async with self.ctx.sessionmaker() as session, session.begin():
            groups = (
                await session.execute(
                    update(ExperimentalGroup)
                    .where(ExperimentalGroup.status == "ACTIVE")
                    .values(status="DISABLED", sharing_enabled=False, disabled_at=utcnow())
                    .returning(ExperimentalGroup.id)
                )
            ).all()
            cancelled = await self._cancel_tasks(
                session, ExperimentalTaskConsent.id.is_not(None), "EXPERIMENT_KILL_SWITCH"
            )
            _audit(
                session,
                principal.user_id,
                "experiment.kill_switch",
                "experiment",
                "*",
                groups=len(groups),
                cancelled=cancelled,
            )
        return {"groups_disabled": len(groups), "tasks_cancelled": cancelled}

    async def _cancel_tasks(self, session: AsyncSession, where: Any, reason: str) -> int:
        task_ids = (
            (
                await session.execute(
                    select(Task.id)
                    .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
                    .where(where, Task.resource_scope == EXPERIMENTAL_SCOPE)
                    .order_by(Task.id)
                )
            )
            .scalars()
            .all()
        )
        cancelled = 0
        for task_id in task_ids:
            task = await repo.lock_task(session, task_id)
            if task is not None and await force_cancel(session, task, reason):
                cancelled += 1
        await session.execute(
            update(ExperimentalTaskConsent)
            .where(where, ExperimentalTaskConsent.status == "PENDING")
            .values(status="WITHDRAWN")
        )
        return cancelled

    async def audit_log(
        self, principal: Principal, group_id: uuid.UUID, limit: int = 100
    ) -> list[ExperimentalDispatchAudit]:
        _require_admin(principal)
        async with self.ctx.sessionmaker() as session:
            await self._group(session, group_id)
            return list(
                (
                    await session.execute(
                        select(ExperimentalDispatchAudit)
                        .where(ExperimentalDispatchAudit.group_id == group_id)
                        .order_by(ExperimentalDispatchAudit.id.desc())
                        .limit(limit)
                    )
                ).scalars()
            )

    # ================================================================ members
    async def _membership(
        self, session: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID
    ) -> ExperimentalGroupMember | None:
        return (
            await session.execute(
                select(ExperimentalGroupMember).where(
                    ExperimentalGroupMember.group_id == group_id,
                    ExperimentalGroupMember.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    async def _require_active_member(
        self, session: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        member = await self._membership(session, group_id, user_id)
        if member is None or member.status != "ACTIVE":
            raise forbidden("NOT_A_MEMBER", "You are not an active member of this group")

    async def get_group(
        self, principal: Principal, group_id: uuid.UUID
    ) -> tuple[ExperimentalGroup, list[ExperimentalGroupMember], ExperimentalAuthorization | None]:
        async with self.ctx.sessionmaker() as session:
            group = await self._group(session, group_id)
            if principal.role != "ADMIN":
                member = await self._membership(session, group_id, principal.user_id)
                if member is None or member.status == "REMOVED":
                    raise not_found("GROUP_NOT_FOUND", "Group not found")  # no oracle
            members = list(
                (
                    await session.execute(
                        select(ExperimentalGroupMember)
                        .where(ExperimentalGroupMember.group_id == group_id)
                        .order_by(ExperimentalGroupMember.invited_at)
                    )
                ).scalars()
            )
            auth = await current_authorization(session, group_id)
        return group, members, auth

    async def join(
        self,
        principal: Principal,
        group_id: uuid.UUID,
        acknowledgements: dict[str, bool],
        terms_version: str,
    ) -> ExperimentalGroupMember:
        _require_acks(acknowledgements, JOIN_ACKNOWLEDGEMENTS)
        if terms_version != self.ctx.settings.experiment_terms_version:
            raise DomainError(422, "TERMS_VERSION_MISMATCH", "Please accept the current terms")
        async with self.ctx.sessionmaker() as session, session.begin():
            group = await self._group(session, group_id)
            member = await self._membership(session, group_id, principal.user_id)
            if member is None or member.status == "REMOVED":
                raise not_found("GROUP_NOT_FOUND", "Group not found")  # invitation-only
            if group.status != "ACTIVE":
                raise conflict("GROUP_DISABLED", "Group is disabled")
            if member.status == "INVITED":
                member.status, member.joined_at = "ACTIVE", utcnow()
                member.terms_version = terms_version
                member.acknowledgements = {k: True for k in JOIN_ACKNOWLEDGEMENTS}
                _audit(session, principal.user_id, "experiment.member.join", "group", group_id)
        return member

    # ================================================================ subscription owner
    async def connect(self, principal: Principal, body: dict[str, Any]) -> SubscriptionConnection:
        if body["auth_method"] != "CHATGPT":
            raise DomainError(
                422,
                "SUBSCRIPTION_AUTH_REQUIRED",
                "Only a ChatGPT subscription login can be connected",
            )
        async with self.ctx.sessionmaker() as session, session.begin():
            existing = (
                await session.execute(
                    select(SubscriptionConnection).where(
                        SubscriptionConnection.worker_id == body["worker_id"]
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.owner_id != principal.user_id:
                    raise not_found("WORKER_NOT_FOUND", "Worker not found")
                existing.status, existing.auth_state = "CONNECTED", "VALID"
                existing.device_label = body["device_label"]
                existing.subscription_type = body["subscription_type"]
                existing.last_auth_check_at = utcnow()
                return existing
            conn = SubscriptionConnection(
                owner_id=principal.user_id,
                worker_id=body["worker_id"],
                subscription_type=body["subscription_type"],
                auth_method="CHATGPT",
                device_label=body["device_label"],
                last_auth_check_at=utcnow(),
            )
            session.add(conn)
            try:
                await session.flush()
            except (IntegrityError, DBAPIError) as exc:
                # Not the caller's own personal runtime (DB trigger) or unknown worker.
                raise not_found("WORKER_NOT_FOUND", "Worker not found") from exc
            _audit(session, principal.user_id, "subscription.connect", "connection", conn.id)
        return conn

    async def _owned_connection(
        self, session: AsyncSession, principal: Principal, connection_id: uuid.UUID, *, lock=False
    ) -> SubscriptionConnection:
        conn = await session.get(SubscriptionConnection, connection_id, with_for_update=lock)
        if conn is None or conn.owner_id != principal.user_id:
            raise not_found("CONNECTION_NOT_FOUND", "Subscription connection not found")
        return conn

    async def subscription_status(self, principal: Principal) -> list[dict[str, Any]]:
        async with self.ctx.sessionmaker() as session:
            conns = (
                (
                    await session.execute(
                        select(SubscriptionConnection)
                        .where(SubscriptionConnection.owner_id == principal.user_id)
                        .order_by(SubscriptionConnection.created_at)
                    )
                )
                .scalars()
                .all()
            )
            out = []
            for conn in conns:
                worker = await session.get(WorkerNode, conn.worker_id)
                prefs = await session.get(SubscriptionSharingPreference, conn.id)
                out.append(
                    {
                        "connection": conn,
                        "worker_status": worker.status if worker else "UNKNOWN",
                        "last_heartbeat": worker.last_heartbeat if worker else None,
                        "preferences": prefs,
                    }
                )
        return out

    async def usage(self, principal: Principal) -> list[dict[str, Any]]:
        async with self.ctx.sessionmaker() as session:
            conns = (
                (
                    await session.execute(
                        select(SubscriptionConnection).where(
                            SubscriptionConnection.owner_id == principal.user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [await SubscriptionUsageMonitor.status(session, c) for c in conns]

    async def set_preferences(
        self,
        principal: Principal,
        connection_id: uuid.UUID,
        group_id: uuid.UUID,
        prefs_in: dict[str, Any],
    ) -> SubscriptionSharingPreference:
        async with self.ctx.sessionmaker() as session, session.begin():
            await self._owned_connection(session, principal, connection_id, lock=True)
            group = await self._group(session, group_id)
            await self._require_active_member(session, group_id, principal.user_id)
            for key in LIMIT_FIELDS:
                value = prefs_in.get(key)
                if value is not None and value > getattr(group, key):
                    raise DomainError(
                        422,
                        "LIMIT_ABOVE_GROUP_MAXIMUM",
                        f"{key} cannot exceed the group's {getattr(group, key)}",
                    )
            types = prefs_in.get("allowed_task_types")
            if types is not None and not set(types) <= set(group.allowed_task_types):
                raise DomainError(422, "TASK_TYPE_NOT_ALLOWED", "Task type not allowed by group")
            prefs = await session.get(
                SubscriptionSharingPreference, connection_id, with_for_update=True
            )
            if prefs is None:
                prefs = SubscriptionSharingPreference(
                    connection_id=connection_id, group_id=group_id
                )
                session.add(prefs)
            elif prefs.group_id != group_id:
                if prefs.sharing_state == "ENABLED":
                    raise conflict("SHARING_ACTIVE", "Pause or revoke before switching groups")
                prefs.group_id = group_id
            for key in LIMIT_FIELDS:
                if prefs_in.get(key) is not None:
                    setattr(prefs, key, prefs_in[key])
            if types is not None:
                prefs.allowed_task_types = sorted(set(types))
            if prefs_in.get("require_owner_approval") is not None:
                prefs.require_owner_approval = prefs_in["require_owner_approval"]
            if prefs_in.get("advance_consent_requester_ids") is not None:
                prefs.advance_consent_requester_ids = sorted(
                    {str(x) for x in prefs_in["advance_consent_requester_ids"]}
                )
            prefs.updated_at = utcnow()
            await session.flush()
            _audit(session, principal.user_id, "sharing.preferences", "connection", connection_id)
        return prefs

    async def enable(
        self,
        principal: Principal,
        connection_id: uuid.UUID,
        acknowledgements: dict[str, bool],
        terms_version: str,
    ) -> SubscriptionSharingPreference:
        _require_acks(acknowledgements, SHARING_ACKNOWLEDGEMENTS)
        if terms_version != self.ctx.settings.experiment_terms_version:
            raise DomainError(422, "TERMS_VERSION_MISMATCH", "Please accept the current terms")
        async with self.ctx.sessionmaker() as session, session.begin():
            conn = await self._owned_connection(session, principal, connection_id, lock=True)
            prefs = await session.get(
                SubscriptionSharingPreference, connection_id, with_for_update=True
            )
            if prefs is None:
                raise conflict("PREFERENCES_REQUIRED", "Set sharing preferences first")
            await self._require_active_member(session, prefs.group_id, principal.user_id)
            if conn.status != "CONNECTED" or conn.auth_state != "VALID":
                raise conflict("SUBSCRIPTION_AUTH_INVALID", "Reconnect your local Codex login")
            now = utcnow()
            prefs.sharing_state = "ENABLED"
            prefs.consent_terms_version = terms_version
            prefs.consent_accepted_at = now
            prefs.paused_at = prefs.revoked_at = None
            prefs.updated_at = now
            _audit(
                session,
                principal.user_id,
                "sharing.enable",
                "connection",
                connection_id,
                terms_version=terms_version,
            )
        return prefs

    async def pause(self, principal: Principal, connection_id: uuid.UUID):
        """No new tasks are assigned while paused; queued tasks wait, running ones finish."""
        return await self._set_state(principal, connection_id, "PAUSED")

    async def revoke(self, principal: Principal, connection_id: uuid.UUID):
        """Immediate: no new assignment, and unfinished tasks on this connection are
        cancelled (a running node observes CANCELLING and stops)."""
        return await self._set_state(principal, connection_id, "REVOKED")

    async def _set_state(self, principal: Principal, connection_id: uuid.UUID, state: str):
        async with self.ctx.sessionmaker() as session, session.begin():
            await self._owned_connection(session, principal, connection_id, lock=True)
            prefs = await session.get(
                SubscriptionSharingPreference, connection_id, with_for_update=True
            )
            if prefs is None:
                raise conflict("PREFERENCES_REQUIRED", "Sharing was never configured")
            now = utcnow()
            if state == "PAUSED" and prefs.sharing_state == "REVOKED":
                raise conflict("SHARING_REVOKED", "Sharing is revoked; enable it again first")
            prefs.sharing_state = state
            prefs.updated_at = now
            if state == "PAUSED":
                prefs.paused_at = now
            else:
                prefs.revoked_at = now
            cancelled = 0
            if state == "REVOKED":
                cancelled = await self._cancel_tasks(
                    session,
                    ExperimentalTaskConsent.connection_id == connection_id,
                    "OWNER_REVOKED_SHARING",
                )
            _audit(
                session,
                principal.user_id,
                f"sharing.{state.lower()}",
                "connection",
                connection_id,
                tasks_cancelled=cancelled,
            )
        return prefs

    # ================================================================ requesters
    async def available_nodes(
        self, principal: Principal, group_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        async with self.ctx.sessionmaker() as session:
            await self._require_active_member(session, group_id, principal.user_id)
            rows = (
                await session.execute(
                    select(SubscriptionConnection, SubscriptionSharingPreference, WorkerNode)
                    .join(
                        SubscriptionSharingPreference,
                        SubscriptionSharingPreference.connection_id == SubscriptionConnection.id,
                    )
                    .join(WorkerNode, WorkerNode.id == SubscriptionConnection.worker_id)
                    .where(
                        SubscriptionSharingPreference.group_id == group_id,
                        SubscriptionSharingPreference.sharing_state == "ENABLED",
                        SubscriptionConnection.status == "CONNECTED",
                    )
                    .order_by(SubscriptionConnection.created_at)
                )
            ).all()
            return [
                {
                    "connection_id": c.id,
                    "owner_id": c.owner_id,
                    "device_label": c.device_label,
                    "auth_state": c.auth_state,
                    "online": w.revoked_at is None and w.status != "OFFLINE",
                    "allowed_task_types": p.allowed_task_types,
                    "max_task_duration_seconds": p.max_task_duration_seconds,
                    "requires_approval": not (
                        not p.require_owner_approval
                        and str(principal.user_id) in p.advance_consent_requester_ids
                    ),
                }
                for c, p, w in rows
            ]

    async def _require_live_ready(
        self, session: AsyncSession, group_id: uuid.UUID, conn: SubscriptionConnection
    ) -> None:
        """Fail fast at submission when LIVE cannot possibly run; the dispatcher re-checks
        everything (and the database re-checks the authorization) before any execution."""
        if not self.ctx.settings.experiment_live_enabled:
            raise forbidden("LIVE_EXECUTION_DISABLED", "Live execution is switched off")
        auth = await current_authorization(session, group_id)
        if (
            auth is None
            or auth.verification_status != "OPERATOR_VERIFIED"
            or not auth.integration_route_covered
            or auth.integration_route != "CODEX_CLI_EXEC"
            or auth.revoked_at is not None
            or auth.expires_at <= utcnow()
        ):
            raise conflict("LIVE_NOT_AUTHORIZED", "No verified authorization covers live execution")
        if conn.owner_check_passed_at is None or conn.no_purchased_credits_at is None:
            raise conflict(
                "OWNER_ATTESTATION_MISSING",
                "The owner has not recorded codex-check and the no-purchased-credits attestation",
            )

    async def attest(
        self, principal: Principal, connection_id: uuid.UUID, body: dict[str, Any]
    ) -> SubscriptionConnection:
        """Owner statements required for LIVE sharing. Withdrawing either one (false) stops
        live assignment to this connection immediately."""
        async with self.ctx.sessionmaker() as session, session.begin():
            conn = await self._owned_connection(session, principal, connection_id, lock=True)
            now = utcnow()
            check_ok = body["codex_check_result"] == "OWNER_ONLY_SUBSCRIPTION_RUN_OK"
            conn.owner_check_passed_at = now if check_ok else None
            no_credits = body["no_purchased_credits"] and body["no_auto_reload"]
            conn.no_purchased_credits_at = now if no_credits else None
            _audit(
                session,
                principal.user_id,
                "subscription.attest",
                "connection",
                connection_id,
                codex_check=check_ok,
                no_purchased_credits=no_credits,
            )
        return conn

    async def create_task(
        self, principal: Principal, idempotency_key: str, body: dict[str, Any]
    ) -> tuple[ExperimentalTaskView, bool]:
        if not self.ctx.settings.experiment_enabled:
            raise forbidden("EXPERIMENT_DISABLED", "The experiment is disabled")
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        async with self.ctx.sessionmaker() as session, session.begin():
            group = await self._group(session, body["group_id"])
            await self._require_active_member(session, group.id, principal.user_id)
            conn = await session.get(SubscriptionConnection, body["connection_id"])
            prefs = await session.get(SubscriptionSharingPreference, body["connection_id"])
            if (
                conn is None
                or prefs is None
                or prefs.group_id != group.id
                or prefs.sharing_state != "ENABLED"
            ):
                raise not_found("NODE_NOT_AVAILABLE", "No opted-in node with that id")
            if group.status != "ACTIVE":
                raise conflict("GROUP_DISABLED", "Group is disabled")
            allowed = set(group.allowed_task_types) & set(prefs.allowed_task_types)
            if body["task_type"] not in allowed:
                raise DomainError(422, "TASK_TYPE_NOT_ALLOWED", "Task type not allowed")
            if body["max_duration_seconds"] > min(
                group.max_task_duration_seconds, prefs.max_task_duration_seconds
            ):
                raise DomainError(422, "DURATION_LIMIT", "Duration exceeds the allowed limit")
            live = body.get("execution_mode") == "LIVE"
            if live:
                await self._require_live_ready(session, group.id, conn)
            new_id = (
                await session.execute(
                    pg_insert(Task)
                    .values(
                        consumer_id=principal.user_id,
                        idempotency_key=idempotency_key,
                        request_digest=digest,
                        task_type=body["task_type"],
                        repository_url=body["repository_url"],
                        commit_sha=body["commit_sha"],
                        prompt=body["prompt"],
                        resource_scope=EXPERIMENTAL_SCOPE,
                        status=TaskStatus.CREATED.value,
                        max_budget_cents=1,  # no money is involved; the column requires >= 1
                        max_duration_seconds=body["max_duration_seconds"],
                        max_attempts=1,
                        execution_profile="CODEX_LIVE" if live else "MOCK",
                    )
                    .on_conflict_do_nothing(constraint="uniq_consumer_idempotency")
                    .returning(Task.id)
                )
            ).scalar_one_or_none()
            if new_id is None:
                existing = (
                    await session.execute(
                        select(Task).where(
                            Task.consumer_id == principal.user_id,
                            Task.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one()
                if existing.request_digest.strip() != digest:
                    raise conflict(
                        "IDEMPOTENCY_CONFLICT",
                        "Idempotency-Key was already used with a different request",
                    )
                return await self._view(session, existing), False
            task = await repo.lock_task(session, new_id)
            assert task is not None  # noqa: S101 - inserted in this transaction
            advance = (
                not live
                and not prefs.require_owner_approval
                and str(principal.user_id) in prefs.advance_consent_requester_ids
            )
            consent = ExperimentalTaskConsent(
                task_id=task.id,
                group_id=group.id,
                requester_id=principal.user_id,
                connection_id=conn.id,
                owner_id=conn.owner_id,
                status="APPROVED" if advance else "PENDING",
                consent_source="ADVANCE_CONSENT" if advance else None,
                decided_at=utcnow() if advance else None,
            )
            session.add(consent)
            await append_event(
                session, task.id, "task.created", {"status": "CREATED", "scope": EXPERIMENTAL_SCOPE}
            )
            await transition(
                session, task, TaskStatus.QUEUED, payload={"owner_approval": consent.status}
            )
            await session.flush()
            view = ExperimentalTaskView(task, consent, None)
        await self.ctx.publisher.wake(f"task:{task.id}")
        return view, True

    async def _view(self, session: AsyncSession, task: Task) -> ExperimentalTaskView:
        consent = (
            await session.execute(
                select(ExperimentalTaskConsent).where(ExperimentalTaskConsent.task_id == task.id)
            )
        ).scalar_one()
        last = (
            await session.execute(
                select(ExperimentalDispatchAudit)
                .where(ExperimentalDispatchAudit.task_id == task.id)
                .order_by(ExperimentalDispatchAudit.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return ExperimentalTaskView(task, consent, last)

    async def get_task(self, principal: Principal, task_id: uuid.UUID) -> ExperimentalTaskView:
        async with self.ctx.sessionmaker() as session:
            consent = (
                await session.execute(
                    select(ExperimentalTaskConsent).where(
                        ExperimentalTaskConsent.task_id == task_id
                    )
                )
            ).scalar_one_or_none()
            if consent is None or principal.user_id not in (consent.requester_id, consent.owner_id):
                raise not_found("TASK_NOT_FOUND", "Task not found")
            task = await session.get(Task, task_id)
            return await self._view(session, task)

    async def list_tasks(self, principal: Principal, role: str) -> list[ExperimentalTaskView]:
        column = (
            ExperimentalTaskConsent.owner_id
            if role == "owner"
            else ExperimentalTaskConsent.requester_id
        )
        async with self.ctx.sessionmaker() as session:
            tasks = (
                (
                    await session.execute(
                        select(Task)
                        .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
                        .where(column == principal.user_id)
                        .order_by(Task.created_at.desc())
                        .limit(100)
                    )
                )
                .scalars()
                .all()
            )
            return [await self._view(session, t) for t in tasks]

    async def decide(
        self, principal: Principal, task_id: uuid.UUID, *, approve: bool
    ) -> ExperimentalTaskView:
        async with self.ctx.sessionmaker() as session, session.begin():
            task = await repo.lock_task(session, task_id)
            consent = (
                await session.execute(
                    select(ExperimentalTaskConsent)
                    .where(ExperimentalTaskConsent.task_id == task_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            # Only the owner whose subscription would run the task can decide.
            if task is None or consent is None or consent.owner_id != principal.user_id:
                raise not_found("TASK_NOT_FOUND", "Task not found")
            if consent.status != "PENDING":
                if consent.status == ("APPROVED" if approve else "REJECTED"):
                    return await self._view(session, task)  # idempotent
                raise conflict("ALREADY_DECIDED", f"Task was already {consent.status}")
            if is_terminal(TaskStatus(task.status)):
                raise conflict("TASK_FINISHED", f"Task is already {task.status}")
            if approve and lease_service.task_deadline_passed(task, utcnow()):
                raise conflict(
                    "APPROVAL_WINDOW_EXPIRED",
                    "The 10-minute approval and acceptance window expired",
                )
            consent.status = "APPROVED" if approve else "REJECTED"
            consent.consent_source = "OWNER_APPROVAL"
            consent.decided_at = utcnow()
            if not approve:
                await force_cancel(session, task, "OWNER_REJECTED")
            else:
                await append_event(
                    session,
                    task.id,
                    "task.progress",
                    {"message": "Owner approved the task", "percent": 0},
                )
            _audit(
                session,
                principal.user_id,
                "experiment.task.approve" if approve else "experiment.task.reject",
                "task",
                task_id,
            )
            await session.flush()
            view = await self._view(session, task)
        await self.ctx.publisher.wake(f"task:{task_id}")
        return view


async def current_authorization(
    session: AsyncSession, group_id: uuid.UUID, provider_name: str = "openai-codex"
) -> ExperimentalAuthorization | None:
    """Most recent authorization record for the group/provider (verified or not)."""
    return (
        await session.execute(
            select(ExperimentalAuthorization)
            .where(
                ExperimentalAuthorization.group_id == group_id,
                ExperimentalAuthorization.provider_name == provider_name,
            )
            .order_by(ExperimentalAuthorization.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
