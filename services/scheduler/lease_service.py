"""Lease lifecycle: accept, progress/renew, complete (exactly-once), expiry recovery.

Lock order is always task -> attempt -> worker to avoid deadlocks with the dispatcher.
A worker can only act on leases bound to its own node id; nothing a worker sends can
change resource eligibility or another task's state.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Artifact, Task, TaskAttempt
from packages.provider_sdk.types import ArtifactPayload, UsageReport
from services.context import AppContext
from services.errors import DomainError, conflict, forbidden, not_found
from services.execution import verifier
from services.experiments.safety import enforce_live_authority
from services.metering.service import record_usage
from services.observability import redact
from services.tasks import repository as repo
from services.tasks.state_machine import TaskStatus, append_event, transition

log = logging.getLogger(__name__)

ACTIVE_ATTEMPT_STATUSES = ("RESERVED", "RUNNING")


def utcnow() -> datetime:
    return datetime.now(UTC)


def task_deadline_passed(
    task: Task, now: datetime, attempt: TaskAttempt | None = None, *, requeued: bool = False
) -> bool:
    """Experimental tasks wait up to 10 minutes, then get their full execution limit.

    A task handed back to the queue after an interrupted attempt (``requeued``) gets a
    fresh waiting window from the moment it re-entered the queue (its last transition)."""
    start, seconds = task.created_at, task.max_duration_seconds
    if task.resource_scope == "EXPERIMENTAL_SHARED_SUBSCRIPTION":
        if attempt is not None and attempt.started_at is not None:
            start = attempt.started_at
        else:
            seconds = 600
            if requeued:
                start = max(task.created_at, task.updated_at)
    return now >= start + timedelta(seconds=seconds)


# Experimental tasks run on one owner's device and use the owner's own plan, so they are
# retried at most once and only when the device was interrupted (design §6): the owner
# chose "stop now and hand back", the worker shut down, or the lease lapsed. A failed or
# rejected run is never repeated automatically.
EXPERIMENTAL_MAX_ATTEMPTS = 2
DEVICE_INTERRUPTIONS = frozenset({"WORKER_SHUTDOWN", "LEASE_EXPIRED"})


def retry_allowed(task: Task, attempt: TaskAttempt, now: datetime, reason: str) -> bool:
    if attempt.attempt_number >= task.max_attempts or task_deadline_passed(task, now, attempt):
        return False
    return task.resource_scope != "EXPERIMENTAL_SHARED_SUBSCRIPTION" or (
        reason in DEVICE_INTERRUPTIONS
    )


async def release_worker(session: AsyncSession, worker_id: uuid.UUID) -> None:
    worker = await repo.lock_worker(session, worker_id)
    if worker is None:  # pragma: no cover - FK guarantees existence
        return
    worker.active_tasks = max(0, worker.active_tasks - 1)
    if worker.active_tasks == 0 and worker.status in ("RESERVED", "RUNNING"):
        worker.status = "IDLE"


async def close_attempt(
    session: AsyncSession, attempt: TaskAttempt, status: str, *, release: bool = True
) -> None:
    """Move an attempt to a closed status and release its worker slot once.

    `release=False` is used by the platform verifier: for CODE tasks the worker slot was
    already released when the worker submitted its result (D-38)."""
    was_active = attempt.status in ACTIVE_ATTEMPT_STATUSES or attempt.status == "VERIFYING"
    attempt.status = status
    attempt.completed_at = attempt.completed_at or utcnow()
    if was_active and release:
        await release_worker(session, attempt.worker_id)


@dataclass(frozen=True)
class CompletionInput:
    finalization_key: str
    succeeded: bool
    result_summary: str | None = None
    retryable: bool = False
    error_code: str | None = None
    timed_out: bool = False
    artifacts: tuple[ArtifactPayload, ...] = field(default_factory=tuple)
    usage: UsageReport | None = None
    observed_usage: dict[str, int] | None = None
    reset_hint: str | None = None
    # 0013 step D: the device's most specific failure code (e.g. WRONG_LANGUAGE)
    failure_detail: str | None = None


@dataclass(frozen=True)
class CompletionOutcome:
    task_status: str
    replayed: bool


async def store_artifacts(
    ctx: AppContext,
    session: AsyncSession,
    task_id: uuid.UUID,
    attempt_id: uuid.UUID,
    artifacts: tuple[ArtifactPayload, ...],
    *,
    published: bool,
    source: str,
) -> None:
    """Content-addressed storage + rows. A later attempt that produces byte-identical,
    still-unpublished content takes the row over (so it can be published once)."""
    for item in artifacts:
        key = await ctx.artifacts.put(str(task_id), item.name, item.content, item.media_type)
        stmt = pg_insert(Artifact).values(
            task_id=task_id,
            attempt_id=attempt_id,
            kind=item.kind,
            storage_key=key,
            sha256=key.rsplit("/", 1)[-1],
            media_type=item.media_type,
            source=source,
            published=published,
            name=item.name if item.kind == "DELIVERABLE" else None,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["storage_key"],
                set_={
                    "attempt_id": attempt_id,
                    "kind": item.kind,
                    "published": published,
                    "name": stmt.excluded.name,
                },
                where=Artifact.published.is_(False),
            )
        )


class LeaseService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    @property
    def lease_window(self) -> timedelta:
        return timedelta(seconds=self.ctx.settings.lease_seconds)

    async def _attempt_for_worker(
        self, session: AsyncSession, lease_id: uuid.UUID, worker_id: uuid.UUID
    ) -> TaskAttempt:
        attempt = (
            await session.execute(select(TaskAttempt).where(TaskAttempt.lease_id == lease_id))
        ).scalar_one_or_none()
        if attempt is None:
            raise not_found("LEASE_NOT_FOUND", "Lease not found")
        if attempt.worker_id != worker_id:
            raise forbidden("LEASE_NOT_OWNED", "Lease belongs to a different worker")
        return attempt

    async def _lock(
        self, session: AsyncSession, lease_id: uuid.UUID, worker_id: uuid.UUID
    ) -> tuple[Task, TaskAttempt]:
        probe = await self._attempt_for_worker(session, lease_id, worker_id)
        task = await repo.lock_task(session, probe.task_id)
        attempt = await repo.lock_attempt(session, probe.id)
        if task is None or attempt is None:  # pragma: no cover - FK guarantees existence
            raise not_found("LEASE_NOT_FOUND", "Lease not found")
        return task, attempt

    async def accept(self, lease_id: uuid.UUID, worker_id: uuid.UUID) -> None:
        async with self.ctx.sessionmaker() as session, session.begin():
            task, attempt = await self._lock(session, lease_id, worker_id)
            now = utcnow()
            if attempt.status == "RUNNING":
                return  # idempotent re-accept
            if attempt.status == "EXPIRED" or attempt.leased_until <= now:
                raise conflict("LEASE_EXPIRED", "Lease has expired")
            if attempt.status != "RESERVED" or task.status != TaskStatus.RESERVED:
                raise conflict("LEASE_REVOKED", "Lease is no longer valid")
            if task_deadline_passed(task, now, attempt):
                raise conflict("LEASE_EXPIRED", "Task deadline has passed")
            if task.execution_profile == "CODEX_LIVE":
                from db.models import ExperimentalTaskConsent
                from services.experiments.dispatcher import ONLINE_STATUSES, ExperimentalDispatcher
                from services.subscriptions.policy import ExperimentalSharingPolicy, Mode
                from services.subscriptions.usage import SubscriptionUsageMonitor

                consent = (
                    await session.execute(
                        select(ExperimentalTaskConsent).where(
                            ExperimentalTaskConsent.task_id == task.id
                        )
                    )
                ).scalar_one()
                snap, conn, _ = await ExperimentalDispatcher(self.ctx).snapshot(
                    session, task, consent
                )
                worker = await repo.lock_worker(session, worker_id)
                snap = replace(
                    snap,
                    active_tasks_on_connection=max(0, snap.active_tasks_on_connection - 1),
                    tasks_started_last_24h=await SubscriptionUsageMonitor.started_last_24h(
                        session, conn.id, now, exclude_task_id=task.id
                    ),
                    node_online=(
                        worker.status in ONLINE_STATUSES
                        and worker.last_heartbeat is not None
                        and worker.last_heartbeat
                        >= now - timedelta(seconds=self.ctx.settings.worker_heartbeat_ttl_seconds)
                    ),
                    # This reservation already counts in active_tasks; discount it.
                    node_at_capacity=max(0, worker.active_tasks - 1) >= worker.max_concurrency,
                )
                decision = ExperimentalSharingPolicy.evaluate(snap, mode=Mode.LIVE)
                if not decision.allowed:
                    raise conflict("EXPERIMENT_POLICY_DENIED", ", ".join(decision.reasons))
            attempt.status = "RUNNING"
            attempt.started_at = now
            attempt.leased_until = now + self.lease_window
            worker = await repo.lock_worker(session, worker_id)
            if worker is not None:
                worker.status = "RUNNING"
            await transition(
                session,
                task,
                TaskStatus.RUNNING,
                payload={"attempt_number": attempt.attempt_number},
                attempt_id=attempt.id,
            )

    async def progress(
        self, lease_id: uuid.UUID, worker_id: uuid.UUID, message: str, percent: int
    ) -> bool:
        """Records progress and renews the lease. Returns False if the task is cancelling."""
        async with self.ctx.sessionmaker() as session, session.begin():
            task, attempt = await self._lock(session, lease_id, worker_id)
            now = utcnow()
            if attempt.status != "RUNNING" or attempt.leased_until <= now:
                raise conflict("LEASE_EXPIRED", "Lease is not active")
            await enforce_live_authority(self.ctx, session, task)
            if task.status == TaskStatus.CANCELLING:
                return False
            if task.status != TaskStatus.RUNNING:
                raise conflict("LEASE_REVOKED", "Task is no longer running")
            attempt.leased_until = now + self.lease_window
            await append_event(
                session,
                task.id,
                "task.progress",
                {"message": redact(message)[:500], "percent": max(0, min(100, int(percent)))},
                attempt_id=attempt.id,
            )
            return True

    async def renew(self, lease_id: uuid.UUID, worker_id: uuid.UUID) -> tuple[datetime, bool]:
        """Extend an active lease without emitting an event.

        Returns (leased_until, cancel_requested). A RESERVED lease is not renewable: it must be
        accepted first, so an unaccepted reservation still lapses after one lease window."""
        async with self.ctx.sessionmaker() as session, session.begin():
            task, attempt = await self._lock(session, lease_id, worker_id)
            now = utcnow()
            if attempt.status != "RUNNING" or attempt.leased_until <= now:
                raise conflict("LEASE_EXPIRED", "Lease is not active")
            if task.status not in (TaskStatus.RUNNING, TaskStatus.CANCELLING):
                raise conflict("LEASE_REVOKED", "Task is no longer running")
            await enforce_live_authority(self.ctx, session, task)
            attempt.leased_until = now + self.lease_window
            return attempt.leased_until, task.status == TaskStatus.CANCELLING

    async def complete(
        self, lease_id: uuid.UUID, worker_id: uuid.UUID, data: CompletionInput
    ) -> CompletionOutcome:
        if len(data.finalization_key) < 8:
            raise DomainError(422, "VALIDATION_ERROR", "finalization_key too short")
        try:
            async with self.ctx.sessionmaker() as session, session.begin():
                return await self._complete_locked(session, lease_id, worker_id, data)
        except IntegrityError as exc:
            # finalization_key is globally unique; reuse across attempts is a conflict.
            raise conflict("FINALIZATION_KEY_CONFLICT", "finalization_key already used") from exc

    async def _complete_locked(
        self,
        session: AsyncSession,
        lease_id: uuid.UUID,
        worker_id: uuid.UUID,
        data: CompletionInput,
    ) -> CompletionOutcome:
        task, attempt = await self._lock(session, lease_id, worker_id)
        now = utcnow()

        if attempt.finalization_key is not None:
            if attempt.finalization_key == data.finalization_key:
                return CompletionOutcome(task.status, replayed=True)
            raise conflict("ALREADY_FINALIZED", "Attempt was already finalized")
        if attempt.status == "EXPIRED" or (
            attempt.status in ACTIVE_ATTEMPT_STATUSES and attempt.leased_until <= now
        ):
            raise conflict("LEASE_EXPIRED", "Lease has expired")
        if attempt.status != "RUNNING":
            raise conflict("LEASE_REVOKED", "Lease is no longer valid")

        await enforce_live_authority(self.ctx, session, task)
        if task.execution_profile == "CODEX_LIVE" and task_deadline_passed(task, now, attempt):
            data = replace(data, succeeded=False, timed_out=True)
        attempt.finalization_key = data.finalization_key
        attempt.completed_at = now
        if task.execution_profile == "CODEX_LIVE":
            from db.models import ExperimentalTaskConsent
            from services.subscriptions.usage import SubscriptionUsageMonitor

            conn_id = (
                await session.execute(
                    select(ExperimentalTaskConsent.connection_id).where(
                        ExperimentalTaskConsent.task_id == task.id
                    )
                )
            ).scalar_one()
            event = (
                "TASK_COMPLETED"
                if data.succeeded and task.status != TaskStatus.CANCELLING
                else {
                    "SUBSCRIPTION_USAGE_LIMIT": "RATE_LIMITED",
                    "SUBSCRIPTION_AUTH_UNAVAILABLE": "AUTH_EXPIRED",
                }.get(data.error_code or "", "TASK_FAILED")
            )
            await SubscriptionUsageMonitor.record(
                session,
                conn_id,
                event=event,
                source="LOCAL_RUNTIME",
                attempt_id=attempt.id,
                duration_ms=int((now - attempt.started_at).total_seconds() * 1000)
                if attempt.started_at
                else None,
                capacity_state="LIMITED" if event == "RATE_LIMITED" else "UNKNOWN",
                reset_hint=redact(data.reset_hint or "") or None,
                **(data.observed_usage or {}),
            )
        if data.usage is not None:
            await record_usage(session, data.usage, task_attempt_id=attempt.id)

        # Cancellation is checked before any artifact is processed or published.
        if task.status == TaskStatus.CANCELLING:
            await close_attempt(session, attempt, "CANCELLED")
            await transition(
                session,
                task,
                TaskStatus.CANCELLED,
                payload={
                    "reason": "CANCELLED_BY_USER",
                    "artifacts_discarded": len(data.artifacts),
                    "result_summary": "Cancelled by user",
                },
                attempt_id=attempt.id,
            )
            return CompletionOutcome(task.status, replayed=False)
        if task.status != TaskStatus.RUNNING:
            raise conflict("LEASE_REVOKED", "Task is no longer running")

        if data.timed_out:
            await close_attempt(session, attempt, "FAILED")
            await transition(
                session,
                task,
                TaskStatus.TIMED_OUT,
                payload={
                    "reason": "MAX_DURATION_EXCEEDED",
                    "result_summary": "Task exceeded max_duration_seconds",
                },
                attempt_id=attempt.id,
            )
            return CompletionOutcome(task.status, replayed=False)

        if data.succeeded and task.execution_profile in ("CODE", "CODEX_LIVE"):
            submission = (
                verifier.verify_codex_live(task.task_type, data.artifacts)
                if task.execution_profile == "CODEX_LIVE"
                else verifier.verify_code_submission(data.artifacts)
            )
            if submission.ok:
                # Independent verification happens asynchronously on platform infrastructure;
                # the worker's claim alone never completes the task (D-37).
                attempt.status = "VERIFYING"
                await release_worker(session, attempt.worker_id)
                await self._store_artifacts(session, task, attempt, data.artifacts, published=False)
                await transition(
                    session,
                    task,
                    TaskStatus.VERIFYING,
                    payload={"verification": "PENDING", "artifact_count": len(data.artifacts)},
                    attempt_id=attempt.id,
                )
                return CompletionOutcome(task.status, replayed=False)
            data = CompletionInput(
                finalization_key=data.finalization_key,
                succeeded=False,
                error_code="INVALID_SUBMISSION",
                result_summary=f"Invalid submission: {submission.reason}",
            )

        if data.succeeded:
            attempt.status = "VERIFYING"
            await transition(session, task, TaskStatus.VERIFYING, attempt_id=attempt.id)
            if task.execution_profile == "CODEX_LIVE":
                report = verifier.verify_codex_live(task.task_type, data.artifacts)
            else:
                report = verifier.verify(data.artifacts, task.task_type)
            if report.ok:
                await self._store_artifacts(session, task, attempt, data.artifacts)
                await close_attempt(session, attempt, "SUCCEEDED")
                await transition(
                    session,
                    task,
                    TaskStatus.SUCCEEDED,
                    payload={
                        "result_summary": redact(data.result_summary or "")[:2000],
                        "artifact_count": len(data.artifacts),
                    },
                    attempt_id=attempt.id,
                )
            else:
                await close_attempt(session, attempt, "FAILED")
                await transition(
                    session,
                    task,
                    TaskStatus.FAILED,
                    payload={
                        "reason": "VERIFICATION_FAILED",
                        "detail": report.reason,
                        "result_summary": f"Verification failed: {report.reason}",
                    },
                    attempt_id=attempt.id,
                )
            return CompletionOutcome(task.status, replayed=False)

        await close_attempt(session, attempt, "FAILED")
        reason = data.error_code or "ATTEMPT_FAILED"
        if data.retryable and retry_allowed(task, attempt, now, reason):
            await transition(
                session,
                task,
                TaskStatus.RETRY_PENDING,
                payload={
                    "reason": reason,
                    "attempt_number": attempt.attempt_number,
                    **({"detail": data.failure_detail} if data.failure_detail else {}),
                },
                attempt_id=attempt.id,
            )
        else:
            await transition(
                session,
                task,
                TaskStatus.FAILED,
                payload={
                    "reason": reason,
                    "result_summary": redact(data.result_summary or f"Failed: {reason}")[:2000],
                    **({"detail": data.failure_detail} if data.failure_detail else {}),
                },
                attempt_id=attempt.id,
            )
        return CompletionOutcome(task.status, replayed=False)

    async def _store_artifacts(
        self,
        session: AsyncSession,
        task: Task,
        attempt: TaskAttempt,
        artifacts: tuple[ArtifactPayload, ...],
        *,
        published: bool = True,
        source: str = "WORKER",
    ) -> None:
        await store_artifacts(
            self.ctx, session, task.id, attempt.id, artifacts, published=published, source=source
        )

    async def recover_expired(self, *, limit: int = 50) -> int:
        """Expire lapsed leases; requeue with a bounded retry or fail after max_attempts."""
        now = utcnow()
        async with self.ctx.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(TaskAttempt.id, TaskAttempt.task_id)
                    .where(
                        TaskAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
                        TaskAttempt.leased_until <= now,
                    )
                    .order_by(TaskAttempt.leased_until)
                    .limit(limit)
                )
            ).all()
        recovered = 0
        for attempt_id, task_id in rows:
            async with self.ctx.sessionmaker() as session, session.begin():
                task = await repo.lock_task(session, task_id)
                attempt = await repo.lock_attempt(session, attempt_id)
                if task is None or attempt is None:  # pragma: no cover
                    continue
                if attempt.status not in ACTIVE_ATTEMPT_STATUSES or attempt.leased_until > now:
                    continue  # finalized or renewed concurrently
                await close_attempt(session, attempt, "EXPIRED")
                recovered += 1
                status = TaskStatus(task.status)
                retry_ok = retry_allowed(task, attempt, now, "LEASE_EXPIRED")
                payload = {"reason": "LEASE_EXPIRED", "attempt_number": attempt.attempt_number}
                if status == TaskStatus.CANCELLING:
                    await transition(
                        session,
                        task,
                        TaskStatus.CANCELLED,
                        payload={**payload, "result_summary": "Cancelled by user"},
                        attempt_id=attempt.id,
                    )
                elif status == TaskStatus.RESERVED and retry_ok:
                    await transition(
                        session, task, TaskStatus.QUEUED, payload=payload, attempt_id=attempt.id
                    )
                elif status == TaskStatus.RUNNING and retry_ok:
                    await transition(
                        session,
                        task,
                        TaskStatus.RETRY_PENDING,
                        payload=payload,
                        attempt_id=attempt.id,
                    )
                elif status in (TaskStatus.RESERVED, TaskStatus.RUNNING):
                    await transition(
                        session,
                        task,
                        TaskStatus.FAILED,
                        payload={
                            **payload,
                            "reason": "MAX_ATTEMPTS_EXHAUSTED",
                            "result_summary": "Lease expired and max attempts were exhausted",
                        },
                        attempt_id=attempt.id,
                    )
                log.warning(
                    "lease expired",
                    extra={"task_id": str(task.id), "attempt_id": str(attempt.id)},
                )
        return recovered
