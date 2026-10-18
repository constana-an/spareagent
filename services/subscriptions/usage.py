"""SubscriptionUsageMonitor - observations about a shared Codex runtime, never estimates.

What is tracked: sessions, completed/failed tasks, durations, token counts *as reported by
the run*, throttling/auth errors, reset hints and the freshness of the last observation.

What is NOT tracked: remaining plan quota. Codex exposes no supported measurement of it,
so `plan_remaining` is always NOT_AVAILABLE and capacity is UNKNOWN unless a supported
source reported otherwise. Locally configured limits (owner/group) are reported
separately from the plan's own limits. A rate-limit observation starts a fixed cooldown
for the connection; it is never worked around by using another account.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ExperimentalDispatchAudit,
    ExperimentalTaskConsent,
    SubscriptionConnection,
    SubscriptionSharingPreference,
    SubscriptionUsageObservation,
    Task,
    TaskAttempt,
)

NOT_AVAILABLE = "NOT_AVAILABLE"
UNKNOWN = "UNKNOWN"
RATE_LIMIT_COOLDOWN = timedelta(hours=1)
ACTIVE_ATTEMPT_STATUSES = ("RESERVED", "RUNNING")


def utcnow() -> datetime:
    return datetime.now(UTC)


class SubscriptionUsageMonitor:
    @staticmethod
    async def record(
        session: AsyncSession,
        connection_id: uuid.UUID,
        *,
        event: str,
        source: str,
        attempt_id: uuid.UUID | None = None,
        duration_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_output_tokens: int | None = None,
        capacity_state: str = UNKNOWN,
        reset_hint: str | None = None,
    ) -> None:
        session.add(
            SubscriptionUsageObservation(
                connection_id=connection_id,
                task_attempt_id=attempt_id,
                event=event,
                source=source,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_output_tokens=reasoning_output_tokens,
                capacity_state=capacity_state,
                reset_hint=(reset_hint or None) and reset_hint[:80],
            )
        )
        if event == "AUTH_EXPIRED":
            conn = await session.get(SubscriptionConnection, connection_id, with_for_update=True)
            if conn is not None:
                conn.auth_state = "EXPIRED"

    @classmethod
    async def record_for_lease(cls, ctx: Any, lease_id: uuid.UUID, event: str) -> None:
        """Observation for a remote experimental completion (no-op for other scopes)."""
        async with ctx.sessionmaker() as session, session.begin():
            row = (
                await session.execute(
                    select(TaskAttempt, ExperimentalTaskConsent.connection_id)
                    .join(
                        ExperimentalTaskConsent,
                        ExperimentalTaskConsent.task_id == TaskAttempt.task_id,
                    )
                    .where(TaskAttempt.lease_id == lease_id)
                )
            ).one_or_none()
            if row is None:
                return
            attempt, connection_id = row
            duration = None
            if attempt.started_at and attempt.completed_at:
                duration = int((attempt.completed_at - attempt.started_at).total_seconds() * 1000)
            await cls.record(
                session,
                connection_id,
                event=event,
                source="LOCAL_RUNTIME",
                attempt_id=attempt.id,
                duration_ms=duration,
                capacity_state="LIMITED" if event == "RATE_LIMITED" else UNKNOWN,
            )

    @staticmethod
    async def active_tasks(session: AsyncSession, connection: SubscriptionConnection) -> int:
        return (
            await session.execute(
                select(func.count())
                .select_from(TaskAttempt)
                .join(Task, Task.id == TaskAttempt.task_id)
                .where(
                    TaskAttempt.worker_id == connection.worker_id,
                    TaskAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
                    Task.resource_scope == "EXPERIMENTAL_SHARED_SUBSCRIPTION",
                )
            )
        ).scalar_one()

    @staticmethod
    async def started_last_24h(
        session: AsyncSession, connection_id: uuid.UUID, now: datetime
    ) -> int:
        """Cross-user tasks placed on this connection in the last 24 h (audited ALLOWs)."""
        return (
            await session.execute(
                select(func.count())
                .select_from(ExperimentalDispatchAudit)
                .where(
                    ExperimentalDispatchAudit.connection_id == connection_id,
                    ExperimentalDispatchAudit.decision == "ALLOW",
                    ExperimentalDispatchAudit.created_at >= now - timedelta(hours=24),
                )
            )
        ).scalar_one()

    @staticmethod
    async def rate_limited_until(
        session: AsyncSession, connection_id: uuid.UUID
    ) -> datetime | None:
        last = (
            await session.execute(
                select(func.max(SubscriptionUsageObservation.observed_at)).where(
                    SubscriptionUsageObservation.connection_id == connection_id,
                    SubscriptionUsageObservation.event == "RATE_LIMITED",
                )
            )
        ).scalar_one()
        return last + RATE_LIMIT_COOLDOWN if last else None

    @classmethod
    async def status(
        cls, session: AsyncSession, connection: SubscriptionConnection
    ) -> dict[str, Any]:
        now = utcnow()
        counts = dict(
            (
                await session.execute(
                    select(SubscriptionUsageObservation.event, func.count())
                    .where(SubscriptionUsageObservation.connection_id == connection.id)
                    .group_by(SubscriptionUsageObservation.event)
                )
            ).all()
        )
        totals = (
            await session.execute(
                select(
                    func.coalesce(func.sum(SubscriptionUsageObservation.duration_ms), 0),
                    func.coalesce(func.sum(SubscriptionUsageObservation.input_tokens), 0),
                    func.coalesce(func.sum(SubscriptionUsageObservation.output_tokens), 0),
                    func.coalesce(func.sum(SubscriptionUsageObservation.cached_input_tokens), 0),
                    func.coalesce(
                        func.sum(SubscriptionUsageObservation.reasoning_output_tokens), 0
                    ),
                ).where(SubscriptionUsageObservation.connection_id == connection.id)
            )
        ).one()
        last = (
            await session.execute(
                select(SubscriptionUsageObservation)
                .where(SubscriptionUsageObservation.connection_id == connection.id)
                .order_by(SubscriptionUsageObservation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        prefs = await session.get(SubscriptionSharingPreference, connection.id)
        cooldown = await cls.rate_limited_until(session, connection.id)
        return {
            "connection_id": connection.id,
            "subscription_type": connection.subscription_type,
            "auth_state": connection.auth_state,
            "active_sessions": await cls.active_tasks(session, connection),
            "tasks_completed": counts.get("TASK_COMPLETED", 0),
            "tasks_failed": counts.get("TASK_FAILED", 0),
            "throttling_errors": counts.get("RATE_LIMITED", 0),
            "auth_errors": counts.get("AUTH_EXPIRED", 0),
            "total_duration_ms": int(totals[0]),
            "observed_input_tokens": int(totals[1]),
            "observed_output_tokens": int(totals[2]),
            "observed_cached_input_tokens": int(totals[3]),
            "observed_reasoning_output_tokens": int(totals[4]),
            "token_source": "OWNER_RUNTIME_REPORTED_NOT_INDEPENDENTLY_VERIFIED",
            "tasks_started_last_24h": await cls.started_last_24h(session, connection.id, now),
            "local_limits": None
            if prefs is None
            else {
                "max_concurrent_tasks": prefs.max_concurrent_tasks,
                "max_tasks_per_day": prefs.max_tasks_per_day,
                "max_task_duration_seconds": prefs.max_task_duration_seconds,
            },
            # Never invented: no supported source reports the plan's remaining usage.
            "plan_remaining": NOT_AVAILABLE,
            "plan_limits": NOT_AVAILABLE,
            "capacity_state": last.capacity_state if last else UNKNOWN,
            "reset_hint": last.reset_hint if last else None,
            "rate_limited_until": cooldown if cooldown and cooldown > now else None,
            "last_observation_at": last.observed_at if last else None,
            "last_observation_age_seconds": int((now - last.observed_at).total_seconds())
            if last
            else None,
        }
