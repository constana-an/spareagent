"""Task state machine. `tasks.status` is only ever written through `transition()`.

Callers must hold the task row lock (`repository.lock_task`) so that the status update
and the per-task event sequence number are allocated atomically in one transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ExecutionEvent, Task


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    RETRY_PENDING = "RETRY_PENDING"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


S = TaskStatus
TERMINAL: frozenset[TaskStatus] = frozenset({S.SUCCEEDED, S.FAILED, S.CANCELLED, S.TIMED_OUT})

ALLOWED: dict[TaskStatus, frozenset[TaskStatus]] = {
    S.CREATED: frozenset({S.QUEUED, S.CANCELLED}),
    # QUEUED -> FAILED: budget cannot cover the next attempt, or attempts exhausted.
    S.QUEUED: frozenset({S.RESERVED, S.CANCELLED, S.FAILED, S.TIMED_OUT}),
    # RESERVED -> QUEUED on lease expiry; -> FAILED when expiry exhausts max_attempts.
    S.RESERVED: frozenset({S.RUNNING, S.QUEUED, S.CANCELLED, S.FAILED, S.TIMED_OUT}),
    S.RUNNING: frozenset({S.VERIFYING, S.RETRY_PENDING, S.CANCELLING, S.FAILED, S.TIMED_OUT}),
    # VERIFYING -> RETRY_PENDING: independent verification rejected the attempt (W3).
    S.VERIFYING: frozenset({S.SUCCEEDED, S.FAILED, S.CANCELLING, S.RETRY_PENDING}),
    S.RETRY_PENDING: frozenset({S.QUEUED, S.CANCELLED, S.FAILED, S.TIMED_OUT}),
    S.CANCELLING: frozenset({S.CANCELLED}),
    S.SUCCEEDED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
    S.TIMED_OUT: frozenset(),
}

# Event types are fixed by contracts/events.schema.json. CANCELLING has no dedicated
# event type, so it is published as task.progress with payload.status=CANCELLING.
EVENT_TYPE_FOR_STATUS: dict[TaskStatus, str] = {
    S.CREATED: "task.created",
    S.QUEUED: "task.queued",
    S.RESERVED: "task.reserved",
    S.RUNNING: "task.started",
    S.VERIFYING: "task.verifying",
    S.RETRY_PENDING: "task.retrying",
    S.CANCELLING: "task.progress",
    S.SUCCEEDED: "task.succeeded",
    S.FAILED: "task.failed",
    S.CANCELLED: "task.cancelled",
    S.TIMED_OUT: "task.timed_out",
}

EVENT_TYPES = frozenset(EVENT_TYPE_FOR_STATUS.values())


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"Illegal task transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: str, target: str) -> bool:
    return TaskStatus(target) in ALLOWED[TaskStatus(current)]


def validate_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)


def is_terminal(status: str) -> bool:
    return TaskStatus(status) in TERMINAL


async def append_event(
    session: AsyncSession,
    task_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    attempt_id: uuid.UUID | None = None,
) -> ExecutionEvent:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type {event_type}")
    next_seq = (
        await session.execute(
            select(func.coalesce(func.max(ExecutionEvent.seq), 0) + 1).where(
                ExecutionEvent.task_id == task_id
            )
        )
    ).scalar_one()
    event = ExecutionEvent(
        task_id=task_id,
        attempt_id=attempt_id,
        seq=next_seq,
        event_type=event_type,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    return event


async def transition(
    session: AsyncSession,
    task: Task,
    target: TaskStatus,
    *,
    payload: dict[str, Any] | None = None,
    attempt_id: uuid.UUID | None = None,
) -> ExecutionEvent:
    current = task.status
    validate_transition(current, target)
    task.status = target.value
    task.updated_at = datetime.now(UTC)
    await session.flush()
    body = {"from": current, "status": target.value}
    body.update(payload or {})
    return await append_event(
        session, task.id, EVENT_TYPE_FOR_STATUS[target], body, attempt_id=attempt_id
    )
