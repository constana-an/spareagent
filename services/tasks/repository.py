from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from sqlalchemy import or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ExecutionEvent, ExperimentalTaskConsent, Task, TaskAttempt, WorkerNode
from services.errors import DomainError

TERMINAL_EVENT_TYPES = ("task.succeeded", "task.failed", "task.cancelled", "task.timed_out")


async def lock_task(session: AsyncSession, task_id: uuid.UUID) -> Task | None:
    stmt = (
        select(Task)
        .where(Task.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_attempt(session: AsyncSession, attempt_id: uuid.UUID) -> TaskAttempt | None:
    stmt = (
        select(TaskAttempt)
        .where(TaskAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_worker(session: AsyncSession, worker_id: uuid.UUID) -> WorkerNode | None:
    stmt = (
        select(WorkerNode)
        .where(WorkerNode.id == worker_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_owned_task(
    session: AsyncSession, task_id: uuid.UUID, owner_id: uuid.UUID, *, for_update: bool = False
) -> Task | None:
    """Consumer or the explicitly selected experimental device owner; never group-wide."""
    owner_consent = (
        select(ExperimentalTaskConsent.id)
        .where(
            ExperimentalTaskConsent.task_id == Task.id,
            ExperimentalTaskConsent.owner_id == owner_id,
        )
        .exists()
    )
    stmt = select(Task).where(Task.id == task_id, or_(Task.consumer_id == owner_id, owner_consent))
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def active_attempt(session: AsyncSession, task_id: uuid.UUID) -> TaskAttempt | None:
    stmt = (
        select(TaskAttempt)
        .where(TaskAttempt.task_id == task_id, TaskAttempt.status.in_(("RESERVED", "RUNNING")))
        .order_by(TaskAttempt.attempt_number.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def encode_cursor(created_at: datetime, task_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(task_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_raw, id_raw = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(created_raw), uuid.UUID(id_raw)
    except Exception as exc:  # noqa: BLE001
        raise DomainError(400, "INVALID_CURSOR", "Malformed pagination cursor") from exc


async def list_owned_tasks(
    session: AsyncSession, owner_id: uuid.UUID, *, limit: int, cursor: str | None
) -> tuple[list[Task], str | None]:
    stmt = select(Task).where(Task.consumer_id == owner_id)
    if cursor:
        created_at, task_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Task.created_at, Task.id) < tuple_(created_at, task_id))
    stmt = stmt.order_by(Task.created_at.desc(), Task.id.desc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return rows, next_cursor


async def result_summaries(
    session: AsyncSession, task_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """`result_summary` is derived from the terminal event payload (no extra column)."""
    if not task_ids:
        return {}
    stmt = (
        select(ExecutionEvent.task_id, ExecutionEvent.payload)
        .where(
            ExecutionEvent.task_id.in_(task_ids),
            ExecutionEvent.event_type.in_(TERMINAL_EVENT_TYPES),
        )
        .order_by(ExecutionEvent.task_id, ExecutionEvent.seq.desc())
        .distinct(ExecutionEvent.task_id)
    )
    out: dict[uuid.UUID, str | None] = {}
    for task_id, payload in (await session.execute(stmt)).all():
        out[task_id] = payload.get("result_summary")
    return out


async def events_after(
    session: AsyncSession, task_id: uuid.UUID, after_seq: int, limit: int = 500
) -> list[ExecutionEvent]:
    stmt = (
        select(ExecutionEvent)
        .where(ExecutionEvent.task_id == task_id, ExecutionEvent.seq > after_seq)
        .order_by(ExecutionEvent.seq)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())
