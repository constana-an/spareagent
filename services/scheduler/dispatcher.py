"""Dispatch QUEUED tasks onto eligible workers.

Per task, in ONE transaction:
  1. lock the oldest QUEUED task (FOR UPDATE SKIP LOCKED), re-check deadline/attempts;
  2. require budget headroom for the estimated attempt cost (+ safety margin), else FAIL
     without starting any attempt;
  3. select an eligible worker (approved, public, non-personal resource; fresh heartbeat;
     free capacity) with FOR UPDATE SKIP LOCKED, re-checked by ResourceEligibilityPolicy;
  4. create the attempt + lease (60 s), bump worker capacity, transition to RESERVED.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Resource, Task, TaskAttempt, WorkerNode
from packages.provider_sdk.types import ExecutionRequest
from services.context import AppContext
from services.metering.estimator import has_budget_headroom
from services.metering.service import task_spent_cents
from services.scheduler.lease_service import task_deadline_passed, utcnow
from services.scheduler.policy import (
    APPROVED,
    PERSONAL_SUBSCRIPTION,
    PUBLIC_MARKETPLACE,
    PUBLIC_RESOURCE_TYPES,
    ResourceEligibilityPolicy,
)
from services.scheduler.scoring import rank_workers
from services.tasks.state_machine import TaskStatus, transition

log = logging.getLogger(__name__)

SCHEDULABLE_WORKER_STATUSES = ("IDLE", "RESERVED", "RUNNING")


def execution_request(task: Task, attempt_id: uuid.UUID, attempt_number: int) -> ExecutionRequest:
    return ExecutionRequest(
        task_id=str(task.id),
        attempt_id=str(attempt_id),
        user_id=str(task.consumer_id),
        resource_scope=task.resource_scope,  # type: ignore[arg-type]
        prompt=task.prompt,
        repository_url=task.repository_url,
        commit_sha=task.commit_sha.strip(),
        max_duration_seconds=task.max_duration_seconds,
        max_budget_cents=task.max_budget_cents,
        attempt_number=attempt_number,
        task_type=task.task_type,
    )


class Dispatcher:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def eligible_workers(self, session: AsyncSession) -> list[tuple[WorkerNode, Resource]]:
        fresh_after = utcnow() - timedelta(seconds=self.ctx.settings.worker_heartbeat_ttl_seconds)
        stmt = (
            select(WorkerNode, Resource)
            .join(Resource, Resource.id == WorkerNode.resource_id)
            .where(
                WorkerNode.status.in_(SCHEDULABLE_WORKER_STATUSES),
                WorkerNode.active_tasks < WorkerNode.max_concurrency,
                WorkerNode.last_heartbeat.is_not(None),
                WorkerNode.last_heartbeat >= fresh_after,
                Resource.authorization_status == APPROVED,
                Resource.public_eligible.is_(True),
                Resource.resource_type.in_(sorted(PUBLIC_RESOURCE_TYPES)),
                Resource.resource_type != PERSONAL_SUBSCRIPTION,
            )
            .order_by(WorkerNode.id)
            .with_for_update(of=WorkerNode, skip_locked=True)
        )
        rows = [(w, r) for w, r in (await session.execute(stmt)).all()]
        # Defence in depth: never trust the SQL filter (or a tampered fixture) alone.
        return [
            (w, r)
            for w, r in rows
            if ResourceEligibilityPolicy.can_serve_task(r, resource_scope=PUBLIC_MARKETPLACE)
        ]

    async def promote_retries(self, *, limit: int = 50) -> int:
        """RETRY_PENDING -> QUEUED once exponential backoff since the last attempt elapsed."""
        promoted = 0
        base = self.ctx.settings.retry_backoff_base_seconds
        for _ in range(limit):
            async with self.ctx.sessionmaker() as session, session.begin():
                now = utcnow()
                last_attempt = (
                    select(
                        TaskAttempt.task_id,
                        func.max(TaskAttempt.attempt_number).label("n"),
                        func.max(TaskAttempt.completed_at).label("done"),
                    )
                    .group_by(TaskAttempt.task_id)
                    .subquery()
                )
                stmt = (
                    select(Task, last_attempt.c.n, last_attempt.c.done)
                    .outerjoin(last_attempt, last_attempt.c.task_id == Task.id)
                    .where(Task.status == TaskStatus.RETRY_PENDING.value)
                    .order_by(Task.updated_at, Task.id)
                    .with_for_update(of=Task, skip_locked=True)
                )
                ready = None
                for task, n, done in (await session.execute(stmt)).all():
                    if task_deadline_passed(task, now):
                        ready = (task, "timeout")
                        break
                    wait = timedelta(seconds=base * (2 ** max(0, (n or 1) - 1)))
                    if done is None or done + wait <= now:
                        ready = (task, "retry")
                        break
                if ready is None:
                    return promoted
                task, kind = ready
                if kind == "timeout":
                    await transition(
                        session,
                        task,
                        TaskStatus.TIMED_OUT,
                        payload={"reason": "MAX_DURATION_EXCEEDED", "result_summary": "Timed out"},
                    )
                else:
                    await transition(
                        session,
                        task,
                        TaskStatus.QUEUED,
                        payload={"reason": "RETRY_BACKOFF_ELAPSED"},
                    )
                promoted += 1
        return promoted

    async def dispatch_once(self) -> uuid.UUID | None:
        """Try to place the oldest dispatchable task. Returns the new attempt id, if any."""
        async with self.ctx.sessionmaker() as session, session.begin():
            now = utcnow()
            stmt = (
                select(Task)
                .where(
                    Task.status == TaskStatus.QUEUED.value,
                    Task.resource_scope == PUBLIC_MARKETPLACE,
                )
                .order_by(Task.created_at, Task.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            task = (await session.execute(stmt)).scalar_one_or_none()
            if task is None:
                return None

            if task_deadline_passed(task, now):
                await transition(
                    session,
                    task,
                    TaskStatus.TIMED_OUT,
                    payload={
                        "reason": "MAX_DURATION_EXCEEDED",
                        "result_summary": "Task timed out before an eligible worker started it",
                    },
                )
                return None

            attempts_so_far = (
                await session.execute(
                    select(func.count())
                    .select_from(TaskAttempt)
                    .where(TaskAttempt.task_id == task.id)
                )
            ).scalar_one()
            if attempts_so_far >= task.max_attempts:
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

            attempt_id = uuid.uuid4()
            attempt_number = attempts_so_far + 1
            request = execution_request(task, attempt_id, attempt_number)
            estimate = await self.ctx.adapter.estimate_cents(request)
            spent = await task_spent_cents(session, task.id)
            if not has_budget_headroom(
                max_budget_cents=task.max_budget_cents,
                spent_cents=spent,
                estimate_cents=estimate,
                safety_margin_percent=self.ctx.settings.budget_safety_margin_percent,
            ):
                await transition(
                    session,
                    task,
                    TaskStatus.FAILED,
                    payload={
                        "reason": "BUDGET_INSUFFICIENT",
                        "estimated_cents": estimate,
                        "spent_cents": spent,
                        "result_summary": "Budget cannot cover the estimated attempt cost",
                    },
                )
                return None

            candidates = rank_workers([w for w, _ in await self.eligible_workers(session)])
            if not candidates:
                return None  # stays QUEUED; no personal or unapproved fallback, ever
            worker = candidates[0]

            session.add(
                TaskAttempt(
                    id=attempt_id,
                    task_id=task.id,
                    worker_id=worker.id,
                    attempt_number=attempt_number,
                    lease_id=uuid.uuid4(),
                    status="RESERVED",
                    leased_until=now + timedelta(seconds=self.ctx.settings.lease_seconds),
                )
            )
            worker.active_tasks += 1
            if worker.status == "IDLE":
                worker.status = "RESERVED"
            await session.flush()
            await transition(
                session,
                task,
                TaskStatus.RESERVED,
                payload={"attempt_number": attempt_number, "estimated_cents": estimate},
                attempt_id=attempt_id,
            )
        log.info(
            "task reserved",
            extra={"task_id": str(task.id), "attempt_id": str(attempt_id)},
        )
        await self.ctx.publisher.wake(f"attempt:{attempt_id}")
        return attempt_id

    async def dispatch_all(self, *, limit: int = 50) -> int:
        count = 0
        for _ in range(limit):
            async with self.ctx.sessionmaker() as session:
                pending = (
                    await session.execute(
                        select(func.count())
                        .select_from(Task)
                        .where(Task.status == TaskStatus.QUEUED.value)
                    )
                ).scalar_one()
            if not pending:
                break
            attempt = await self.dispatch_once()
            if attempt is None:
                # Either the head task was resolved (timeout/budget) or no worker is free.
                async with self.ctx.sessionmaker() as session:
                    if not await self.eligible_workers(session):
                        break
                continue
            count += 1
        return count
