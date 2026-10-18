"""Usage recording. Each (provider_name, provider_request_id) is recorded at most once."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import TaskAttempt, UsageRecord
from packages.provider_sdk.types import UsageReport


async def record_usage(
    session: AsyncSession,
    usage: UsageReport,
    *,
    task_attempt_id: uuid.UUID | None = None,
    gateway_request_id: uuid.UUID | None = None,
) -> bool:
    """Insert a usage row; returns False when this provider request was already recorded."""
    if (task_attempt_id is None) == (gateway_request_id is None):
        raise ValueError("exactly one of task_attempt_id / gateway_request_id is required")
    for value in (usage.input_tokens, usage.output_tokens, usage.estimated_cost_cents):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("usage counters must be non-negative integers")
    stmt = (
        pg_insert(UsageRecord)
        .values(
            task_attempt_id=task_attempt_id,
            gateway_request_id=gateway_request_id,
            provider_request_id=usage.provider_request_id,
            provider_name=usage.provider_name,
            model_name=usage.model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_cents=usage.estimated_cost_cents,
        )
        .on_conflict_do_nothing(index_elements=["provider_name", "provider_request_id"])
        .returning(UsageRecord.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def task_spent_cents(session: AsyncSession, task_id: uuid.UUID) -> int:
    stmt = (
        select(func.coalesce(func.sum(UsageRecord.estimated_cost_cents), 0))
        .join(TaskAttempt, TaskAttempt.id == UsageRecord.task_attempt_id)
        .where(TaskAttempt.task_id == task_id)
    )
    return int((await session.execute(stmt)).scalar_one())
