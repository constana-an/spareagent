"""What members and providers see about a task while it waits, runs or ends (design §6).

* progress: the device's latest progress event (percent and a short message);
* queue_position: 1-based place among the QUEUED tasks waiting for the same device;
* failure: a stable code (the most specific one recorded) and a category the UI explains
  in plain words with a suggestion. Codes come from the device check, the platform
  verifier or the lifecycle; nothing here is inferred from free text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ExecutionEvent,
    ExperimentalTaskConsent,
    SubscriptionConnection,
    SubscriptionSharingPreference,
    Task,
    TaskAttempt,
    VerificationResult,
    WorkerNode,
)
from services.tasks.repository import TERMINAL_EVENT_TYPES

FAILURE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "INPUT": (
        "INPUT_INVALID",
        "SOURCE_MISSING",
        "ATTACHMENT_COUNT_INVALID",
        "ATTACHMENT_TYPE_NOT_ACCEPTED",
    ),
    "TIMEOUT": ("MAX_DURATION_EXCEEDED", "VERIFICATION_TIMEOUT"),
    "WAIT_TIMEOUT": ("APPROVAL_WAIT_EXCEEDED",),
    "OUTPUT_LIMIT": ("FILE_TOO_LARGE", "FILES_TOO_LARGE", "TOO_MANY_FILES", "PAYLOAD_TOO_LARGE"),
    "DEVICE_INTERRUPTED": (
        "WORKER_SHUTDOWN",
        "LEASE_EXPIRED",
        "MAX_ATTEMPTS_EXHAUSTED",
        "NO_RESULT",
        "TRANSIENT_PROVIDER_ERROR",
    ),
    "CHECK_FAILED": (
        "MISSING_OUTPUT",
        "MALFORMED_OUTPUT",
        "EMPTY_OUTPUT",
        "PARAGRAPH_COUNT_MISMATCH",
        "WRONG_LANGUAGE",
        "NOT_TRANSLATED",
        "REPORT_TOO_SHORT",
        "REPORT_DOES_NOT_CITE_INPUTS",
        "FILE_TYPE_NOT_ALLOWED",
        "NO_DELIVERABLE",
        "DUPLICATE_NAMES",
        "INVALID_LIVE_ARTIFACTS",
        "INVALID_SUBMISSION",
        "MISSING_ARTIFACTS",
        "ARTIFACT_INTEGRITY",
        "PATCH_POLICY",
        "PATCH_DOES_NOT_APPLY",
        "TESTS_FAILED",
        "WITHHELD",
        "VERIFICATION_FAILED",
    ),
    "AUTHORIZATION": (
        "EXPERIMENT_POLICY_DENIED",
        "LIVE_AUTHORITY_WITHDRAWN",
        "OWNER_ONLY_VIOLATION",
        "SHARED_WORK_REFUSED",
    ),
    "LOGIN": ("SUBSCRIPTION_AUTH_UNAVAILABLE",),
    # Codex finished normally but wrote nothing at all: it declined the request.
    "REFUSED": ("MODEL_DECLINED",),
    "USAGE_LIMIT": ("SUBSCRIPTION_USAGE_LIMIT",),
    "CANCELLED": ("CANCELLED_BY_USER", "CANCELLED", "CANCELLED_BEFORE_PUBLICATION"),
}
_CATEGORY = {code: cat for cat, codes in FAILURE_CATEGORIES.items() for code in codes}


def utcnow() -> datetime:
    return datetime.now(UTC)


def failure_category(code: str | None) -> str:
    return _CATEGORY.get(code or "", "OTHER")


async def task_details(session: AsyncSession, tasks: list[Task]) -> dict[uuid.UUID, dict[str, Any]]:
    ids = [t.id for t in tasks]
    out: dict[uuid.UUID, dict[str, Any]] = {
        t: {"progress": None, "queue_position": None, "failure": None, "started_at": None}
        for t in ids
    }
    if not ids:
        return out
    progress = (
        await session.execute(
            select(ExecutionEvent.task_id, ExecutionEvent.payload, ExecutionEvent.created_at)
            .where(ExecutionEvent.task_id.in_(ids), ExecutionEvent.event_type == "task.progress")
            .order_by(ExecutionEvent.task_id, ExecutionEvent.seq.desc())
            .distinct(ExecutionEvent.task_id)
        )
    ).all()
    for task_id, payload, at in progress:
        if "percent" in payload:
            out[task_id]["progress"] = {
                "percent": payload.get("percent"),
                "message": payload.get("message"),
                "at": at,
            }
    started = (
        await session.execute(
            select(TaskAttempt.task_id, func.max(TaskAttempt.started_at))
            .where(TaskAttempt.task_id.in_(ids))
            .group_by(TaskAttempt.task_id)
        )
    ).all()
    for task_id, at in started:
        out[task_id]["started_at"] = at

    for task in tasks:
        if task.status == "QUEUED":
            out[task.id]["queue_position"] = await _queue_position(session, task)

    ended = [t.id for t in tasks if t.status in ("FAILED", "TIMED_OUT", "CANCELLED")]
    if ended:
        finals = (
            await session.execute(
                select(ExecutionEvent.task_id, ExecutionEvent.payload)
                .where(
                    ExecutionEvent.task_id.in_(ended),
                    ExecutionEvent.event_type.in_(TERMINAL_EVENT_TYPES),
                )
                .order_by(ExecutionEvent.task_id, ExecutionEvent.seq.desc())
                .distinct(ExecutionEvent.task_id)
            )
        ).all()
        verdicts = dict(
            (
                await session.execute(
                    select(VerificationResult.task_id, VerificationResult.reason)
                    .where(
                        VerificationResult.task_id.in_(ended),
                        VerificationResult.status == "FAILED",
                    )
                    .order_by(VerificationResult.task_id, VerificationResult.started_at.desc())
                    .distinct(VerificationResult.task_id)
                )
            ).all()
        )
        for task_id, payload in finals:
            code = (
                payload.get("detail")
                or (
                    verdicts.get(task_id)
                    if payload.get("reason") == "VERIFICATION_FAILED"
                    else None
                )
                or payload.get("reason")
            )
            out[task_id]["failure"] = {"code": code, "category": failure_category(code)}
    return out


async def device_wait_reasons(
    session: AsyncSession, connection_id: uuid.UUID, heartbeat_ttl_seconds: int
) -> list[str]:
    """Why a queued task cannot start right now, read from the owner's current state.

    Dispatch decisions are only recorded while the owner's device polls, so a device that
    is offline never records one; members still need to see why their task waits."""
    row = (
        await session.execute(
            select(SubscriptionSharingPreference.sharing_state, WorkerNode.last_heartbeat)
            .join(
                SubscriptionConnection,
                SubscriptionConnection.id == SubscriptionSharingPreference.connection_id,
            )
            .join(WorkerNode, WorkerNode.id == SubscriptionConnection.worker_id)
            .where(SubscriptionSharingPreference.connection_id == connection_id)
        )
    ).one_or_none()
    if row is None:
        return []
    state, beat = row
    if state == "PAUSED":
        return ["OWNER_PAUSED"]
    fresh = beat is not None and beat >= utcnow() - timedelta(seconds=heartbeat_ttl_seconds)
    return [] if fresh else ["PROVIDER_OFFLINE"]


async def _queue_position(session: AsyncSession, task: Task) -> int:
    consent = (
        await session.execute(
            select(ExperimentalTaskConsent.connection_id).where(
                ExperimentalTaskConsent.task_id == task.id
            )
        )
    ).scalar_one_or_none()
    if consent is None:
        return 1
    ahead = (
        await session.execute(
            select(func.count())
            .select_from(Task)
            .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
            .where(
                ExperimentalTaskConsent.connection_id == consent,
                Task.status == "QUEUED",
                Task.created_at < task.created_at,
            )
        )
    ).scalar_one()
    return ahead + 1
