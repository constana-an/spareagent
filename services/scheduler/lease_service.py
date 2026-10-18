"""Lease lifecycle: accept, progress/renew, complete (exactly-once), expiry recovery.

Lock order is always task -> attempt -> worker to avoid deadlocks with the dispatcher.
A worker can only act on leases bound to its own node id; nothing a worker sends can
change resource eligibility or another task's state.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
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
from services.metering.service import record_usage
from services.observability import redact
from services.tasks import repository as repo
from services.tasks.state_machine import TaskStatus, append_event, transition

log = logging.getLogger(__name__)

ACTIVE_ATTEMPT_STATUSES = ("RESERVED", "RUNNING")


def utcnow() -> datetime:
    return datetime.now(UTC)


def task_deadline_passed(task: Task, now: datetime) -> bool:
    return now >= task.created_at + timedelta(seconds=task.max_duration_seconds)


async def release_worker(session: AsyncSession, worker_id: uuid.UUID) -> None:
    worker = await repo.lock_worker(session, worker_id)
    if worker is None:  # pragma: no cover - FK guarantees existence
        return
    worker.active_tasks = max(0, worker.active_tasks - 1)
    if worker.active_tasks == 0 and worker.status in ("RESERVED", "RUNNING"):
        worker.status = "IDLE"


async def close_attempt(session: AsyncSession, attempt: TaskAttempt, status: str) -> None:
    """Move an active attempt to a closed status and release its worker slot once."""
    was_active = attempt.status in ACTIVE_ATTEMPT_STATUSES or attempt.status == "VERIFYING"
    attempt.status = status
    attempt.completed_at = attempt.completed_at or utcnow()
    if was_active:
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


@dataclass(frozen=True)
class CompletionOutcome:
    task_status: str
    replayed: bool


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

        attempt.finalization_key = data.finalization_key
        attempt.completed_at = now
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

        if data.succeeded:
            attempt.status = "VERIFYING"
            await transition(session, task, TaskStatus.VERIFYING, attempt_id=attempt.id)
            report = verifier.verify(data.artifacts)
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
        if (
            data.retryable
            and attempt.attempt_number < task.max_attempts
            and not task_deadline_passed(task, now)
        ):
            await transition(
                session,
                task,
                TaskStatus.RETRY_PENDING,
                payload={"reason": reason, "attempt_number": attempt.attempt_number},
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
    ) -> None:
        for item in artifacts:
            key = await self.ctx.artifacts.put(
                str(task.id), item.name, item.content, item.media_type
            )
            await session.execute(
                pg_insert(Artifact)
                .values(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    kind=item.kind,
                    storage_key=key,
                    sha256=key.rsplit("/", 1)[-1],
                    media_type=item.media_type,
                )
                .on_conflict_do_nothing(index_elements=["storage_key"])
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
                retry_ok = attempt.attempt_number < task.max_attempts and not task_deadline_passed(
                    task, now
                )
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
