"""ExperimentalDispatcher - places EXPERIMENTAL_SHARED_SUBSCRIPTION tasks.

Separate from `services.scheduler.dispatcher.Dispatcher`, which only ever selects
PUBLIC_MARKETPLACE tasks and public, non-personal resources (unchanged). This dispatcher:

* locks the oldest QUEUED experimental task (FOR UPDATE SKIP LOCKED), builds a policy
  snapshot from the database (never from client or node claims) and evaluates
  ExperimentalSharingPolicy - SIMULATED for MOCK tasks, LIVE (with the extra Milestone D
  gates) for CODEX_LIVE tasks;
* appends every decision to the append-only `experimental_dispatch_audit` table (a
  repeated identical DENY for the same task is not re-recorded);
* on ALLOW creates the attempt + lease on the owner's node with the same locking and
  capacity rules as the public dispatcher; the attempt then runs through the existing
  LeaseService/verification lifecycle;
* fails the task on a final DENY (e.g. not a member, authorization invalid, revoked) and
  leaves it QUEUED on a transient one (paused, offline, at capacity, awaiting approval).
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ExperimentalDispatchAudit,
    ExperimentalGroup,
    ExperimentalGroupMember,
    ExperimentalTaskConsent,
    Resource,
    SubscriptionConnection,
    SubscriptionSharingPreference,
    Task,
    TaskAttempt,
)
from services.context import AppContext
from services.experiments.participation import current_authorization
from services.scheduler.lease_service import task_deadline_passed, utcnow
from services.subscriptions.policy import (
    EXPERIMENTAL_SCOPE,
    AuthorizationSnapshot,
    Decision,
    DispatchSnapshot,
    ExperimentalSharingPolicy,
    Mode,
)
from services.subscriptions.usage import SubscriptionUsageMonitor
from services.tasks import repository as repo
from services.tasks.state_machine import TaskStatus, transition

log = logging.getLogger(__name__)

SCAN_LIMIT = 20
LIVE_PROFILE = "CODEX_LIVE"
ONLINE_STATUSES = ("IDLE", "RESERVED", "RUNNING")


class ExperimentalDispatcher:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def snapshot(
        self, session: AsyncSession, task: Task, consent: ExperimentalTaskConsent
    ) -> tuple[DispatchSnapshot, SubscriptionConnection, uuid.UUID | None]:
        now = utcnow()
        group = await session.get(ExperimentalGroup, consent.group_id)
        conn = await session.get(SubscriptionConnection, consent.connection_id)
        prefs = await session.get(SubscriptionSharingPreference, consent.connection_id)
        worker = await repo.lock_worker(session, conn.worker_id)
        resource = await session.get(Resource, worker.resource_id)
        members = frozenset(
            (
                await session.execute(
                    select(ExperimentalGroupMember.user_id).where(
                        ExperimentalGroupMember.group_id == group.id,
                        ExperimentalGroupMember.status == "ACTIVE",
                    )
                )
            ).scalars()
        )
        auth = await current_authorization(session, group.id)
        scope_types = (auth.authorization_scope or {}).get("task_types") if auth else None
        fresh_after = now - timedelta(seconds=self.ctx.settings.worker_heartbeat_ttl_seconds)
        prefs_group_ok = prefs is not None and prefs.group_id == group.id
        snap = DispatchSnapshot(
            now=now,
            experiment_enabled=self.ctx.settings.experiment_enabled,
            task_scope=task.resource_scope,
            task_type=task.task_type,
            task_max_duration_seconds=task.max_duration_seconds,
            requester_id=consent.requester_id,
            owner_id=conn.owner_id,
            group_status=group.status,
            group_sharing_enabled=group.sharing_enabled,
            group_allowed_task_types=frozenset(group.allowed_task_types),
            group_max_task_duration_seconds=group.max_task_duration_seconds,
            group_max_concurrent_tasks=group.max_concurrent_tasks,
            group_max_tasks_per_day=group.max_tasks_per_day,
            active_member_ids=members,
            authorization=None
            if auth is None
            else AuthorizationSnapshot(
                id=auth.id,
                verification_status=auth.verification_status,
                integration_route_covered=auth.integration_route_covered,
                expires_at=auth.expires_at,
                revoked_at=auth.revoked_at,
                scope_task_types=frozenset(scope_types) if isinstance(scope_types, list) else None,
                integration_route=auth.integration_route,
                max_participants=auth.max_participants,
            ),
            sharing_state=prefs.sharing_state if prefs_group_ok else "DISABLED",
            consent_accepted=bool(prefs_group_ok and prefs.consent_accepted_at),
            allow_paid_credits=bool(prefs and prefs.allow_paid_credits),
            owner_allowed_task_types=frozenset(prefs.allowed_task_types if prefs else ()),
            owner_max_task_duration_seconds=prefs.max_task_duration_seconds if prefs else 0,
            owner_max_concurrent_tasks=prefs.max_concurrent_tasks if prefs else 0,
            owner_max_tasks_per_day=prefs.max_tasks_per_day if prefs else 0,
            task_consent_status=consent.status,
            resource_type=resource.resource_type,
            resource_owner_id=resource.owner_id,
            resource_public_eligible=resource.public_eligible,
            connection_status=conn.status,
            connection_auth_state=conn.auth_state,
            node_revoked=worker.revoked_at is not None,
            node_online=(
                worker.status in ONLINE_STATUSES
                and worker.last_heartbeat is not None
                and worker.last_heartbeat >= fresh_after
                and worker.active_tasks < worker.max_concurrency
            ),
            active_tasks_on_connection=await SubscriptionUsageMonitor.active_tasks(session, conn),
            tasks_started_last_24h=await SubscriptionUsageMonitor.started_last_24h(
                session, conn.id, now
            ),
            rate_limited_until=await SubscriptionUsageMonitor.rate_limited_until(session, conn.id),
            live_enabled=self.ctx.settings.experiment_live_enabled,
            group_member_count=(
                await session.execute(
                    select(func.count())
                    .select_from(ExperimentalGroupMember)
                    .where(
                        ExperimentalGroupMember.group_id == group.id,
                        ExperimentalGroupMember.status.in_(("INVITED", "ACTIVE")),
                    )
                )
            ).scalar_one(),
            group_max_members=group.max_members,
            owner_check_passed=conn.owner_check_passed_at is not None,
            no_purchased_credits=conn.no_purchased_credits_at is not None,
            node_executor=(worker.capabilities or {}).get("executor"),
        )
        return snap, conn, auth.id if auth else None

    async def _audit(
        self,
        session: AsyncSession,
        task: Task,
        consent: ExperimentalTaskConsent,
        decision: Decision,
        authorization_id: uuid.UUID | None,
        attempt_id: uuid.UUID | None = None,
    ) -> None:
        reasons = [r.value for r in decision.reasons]
        verdict = "ALLOW" if decision.allowed else "DENY"
        if not decision.allowed:
            last = (
                await session.execute(
                    select(ExperimentalDispatchAudit)
                    .where(ExperimentalDispatchAudit.task_id == task.id)
                    .order_by(ExperimentalDispatchAudit.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last is not None and last.decision == verdict and last.reasons == reasons:
                return
        session.add(
            ExperimentalDispatchAudit(
                task_id=task.id,
                group_id=consent.group_id,
                connection_id=consent.connection_id,
                authorization_id=authorization_id,
                decision=verdict,
                reasons=reasons,
                execution_mode=decision.mode.value,
                attempt_id=attempt_id,
            )
        )

    async def dispatch_once(
        self,
        worker_ids: tuple[uuid.UUID, ...] | None = None,
        *,
        profiles: tuple[str, ...] = ("MOCK",),
    ) -> uuid.UUID | None:
        """`profiles` selects simulated (MOCK) and/or LIVE (CODEX_LIVE) tasks; a node
        only ever claims the kind its executor can run."""
        async with self.ctx.sessionmaker() as session, session.begin():
            stmt = (
                select(Task, ExperimentalTaskConsent)
                .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
                .where(
                    Task.status == TaskStatus.QUEUED.value,
                    Task.resource_scope == EXPERIMENTAL_SCOPE,
                    Task.execution_profile.in_(profiles),
                )
                .order_by(Task.created_at, Task.id)
                .limit(SCAN_LIMIT)
                .with_for_update(of=Task, skip_locked=True)
            )
            if worker_ids is not None:
                stmt = stmt.join(
                    SubscriptionConnection,
                    SubscriptionConnection.id == ExperimentalTaskConsent.connection_id,
                ).where(SubscriptionConnection.worker_id.in_(worker_ids))
            for task, consent in (await session.execute(stmt)).all():
                placed = await self._consider(session, task, consent)
                if placed is not None:
                    attempt_id = placed[0]
                    break
            else:
                return None
        log.info("experimental task reserved", extra={"task_id": str(task.id)})
        await self.ctx.publisher.wake(f"attempt:{attempt_id}")
        return attempt_id

    async def _consider(
        self, session: AsyncSession, task: Task, consent: ExperimentalTaskConsent
    ) -> tuple[uuid.UUID, uuid.UUID] | None:
        now = utcnow()
        if task_deadline_passed(task, now):
            await transition(
                session,
                task,
                TaskStatus.TIMED_OUT,
                payload={
                    "reason": "MAX_DURATION_EXCEEDED",
                    "result_summary": "Timed out before an opted-in node accepted it",
                },
            )
            return None
        attempts = (
            await session.execute(
                select(func.count()).select_from(TaskAttempt).where(TaskAttempt.task_id == task.id)
            )
        ).scalar_one()
        if attempts >= task.max_attempts:
            await transition(
                session,
                task,
                TaskStatus.FAILED,
                payload={
                    "reason": "MAX_ATTEMPTS_EXHAUSTED",
                    "result_summary": "Max attempts exhausted",
                },
            )
            return None

        snap, conn, auth_id = await self.snapshot(session, task, consent)
        mode = Mode.LIVE if task.execution_profile == LIVE_PROFILE else Mode.SIMULATED
        decision = ExperimentalSharingPolicy.evaluate(snap, mode=mode)
        if not decision.allowed:
            await self._audit(session, task, consent, decision, auth_id)
            if not decision.transient:
                await transition(
                    session,
                    task,
                    TaskStatus.FAILED,
                    payload={
                        "reason": "EXPERIMENT_POLICY_DENIED",
                        "policy_reasons": [r.value for r in decision.reasons],
                        "result_summary": "Not permitted by the experiment policy: "
                        + ", ".join(r.value for r in decision.reasons),
                    },
                )
            return None

        worker = await repo.lock_worker(session, conn.worker_id)
        attempt_id = uuid.uuid4()
        session.add(
            TaskAttempt(
                id=attempt_id,
                task_id=task.id,
                worker_id=worker.id,
                attempt_number=attempts + 1,
                lease_id=uuid.uuid4(),
                status="RESERVED",
                leased_until=now + timedelta(seconds=self.ctx.settings.lease_seconds),
            )
        )
        worker.active_tasks += 1
        if worker.status == "IDLE":
            worker.status = "RESERVED"
        await session.flush()
        await self._audit(session, task, consent, decision, auth_id, attempt_id)
        await transition(
            session,
            task,
            TaskStatus.RESERVED,
            payload={
                "attempt_number": attempts + 1,
                "execution_mode": decision.mode.value,
            },
            attempt_id=attempt_id,
        )
        return attempt_id, worker.id

    async def dispatch_all(
        self, *, limit: int = 20, worker_ids: tuple[uuid.UUID, ...] | None = None
    ) -> int:
        placed = 0
        for _ in range(limit):
            if await self.dispatch_once(worker_ids) is None:
                break
            placed += 1
        return placed
