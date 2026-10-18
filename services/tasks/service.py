"""Consumer-facing task operations: create (idempotent), read, list, cancel."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Artifact, Task
from services.artifacts.store import ArtifactIntegrityError, ArtifactKeyError
from services.context import AppContext
from services.errors import DomainError, conflict, not_found
from services.scheduler import lease_service
from services.tasks import repository as repo
from services.tasks.state_machine import TaskStatus, append_event, is_terminal, transition

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    role: str


@dataclass(frozen=True)
class TaskView:
    task: Task
    result_summary: str | None


def request_digest(normalized: dict[str, Any]) -> str:
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class TaskService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def create(
        self, principal: Principal, idempotency_key: str, normalized: dict[str, Any]
    ) -> tuple[TaskView, bool]:
        """Returns (task, created). Owner identity comes only from the principal."""
        digest = request_digest(normalized)
        created = False
        async with self.ctx.sessionmaker() as session, session.begin():
            stmt = (
                pg_insert(Task)
                .values(
                    consumer_id=principal.user_id,
                    idempotency_key=idempotency_key,
                    request_digest=digest,
                    task_type=normalized["task_type"],
                    repository_url=normalized["repository_url"],
                    commit_sha=normalized["commit_sha"],
                    prompt=normalized["prompt"],
                    resource_scope="PUBLIC_MARKETPLACE",
                    status=TaskStatus.CREATED.value,
                    max_budget_cents=normalized["max_budget_cents"],
                    max_duration_seconds=normalized["max_duration_seconds"],
                )
                .on_conflict_do_nothing(constraint="uniq_consumer_idempotency")
                .returning(Task.id)
            )
            new_id = (await session.execute(stmt)).scalar_one_or_none()
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
                        "Idempotency-Key was already used with a different request body",
                    )
                task = existing
            else:
                created = True
                task = await repo.lock_task(session, new_id)
                if task is None:  # pragma: no cover - inserted in this transaction
                    raise DomainError(500, "INTERNAL_ERROR", "Task vanished during creation")
                await append_event(session, task.id, "task.created", {"status": "CREATED"})
                await transition(session, task, TaskStatus.QUEUED)
            summaries = await repo.result_summaries(session, [task.id])
        if created:
            log.info("task created", extra={"task_id": str(task.id)})
            await self.ctx.publisher.wake(f"task:{task.id}")
        return TaskView(task, summaries.get(task.id)), created

    async def get(self, principal: Principal, task_id: uuid.UUID) -> TaskView:
        async with self.ctx.sessionmaker() as session:
            task = await repo.get_owned_task(session, task_id, principal.user_id)
            if task is None:
                raise not_found("TASK_NOT_FOUND", "Task not found")
            summaries = await repo.result_summaries(session, [task.id])
        return TaskView(task, summaries.get(task.id))

    async def list(
        self, principal: Principal, *, limit: int, cursor: str | None
    ) -> tuple[list[TaskView], str | None]:
        async with self.ctx.sessionmaker() as session:
            tasks, next_cursor = await repo.list_owned_tasks(
                session, principal.user_id, limit=limit, cursor=cursor
            )
            summaries = await repo.result_summaries(session, [t.id for t in tasks])
        return [TaskView(t, summaries.get(t.id)) for t in tasks], next_cursor

    async def cancel(self, principal: Principal, task_id: uuid.UUID) -> TaskView:
        async with self.ctx.sessionmaker() as session, session.begin():
            task = await repo.get_owned_task(session, task_id, principal.user_id, for_update=True)
            if task is None:
                raise not_found("TASK_NOT_FOUND", "Task not found")
            status = TaskStatus(task.status)
            if status in (TaskStatus.CANCELLED, TaskStatus.CANCELLING):
                pass  # idempotent repeat
            elif is_terminal(status):
                raise conflict("TASK_NOT_CANCELLABLE", f"Task is already {status.value}")
            elif status in (TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RETRY_PENDING):
                await transition(
                    session, task, TaskStatus.CANCELLED, payload={"reason": "CANCELLED_BY_USER"}
                )
            elif status == TaskStatus.RESERVED:
                attempt = await repo.active_attempt(session, task.id)
                if attempt is not None:
                    await lease_service.close_attempt(session, attempt, "CANCELLED")
                await transition(
                    session,
                    task,
                    TaskStatus.CANCELLED,
                    payload={"reason": "CANCELLED_BY_USER"},
                    attempt_id=attempt.id if attempt else None,
                )
            else:  # RUNNING / VERIFYING: the worker observes CANCELLING and stops.
                await transition(
                    session, task, TaskStatus.CANCELLING, payload={"reason": "CANCELLED_BY_USER"}
                )
            summaries = await repo.result_summaries(session, [task.id])
        return TaskView(task, summaries.get(task.id))

    async def list_artifacts(self, principal: Principal, task_id: uuid.UUID) -> list[Artifact]:
        async with self.ctx.sessionmaker() as session:
            task = await repo.get_owned_task(session, task_id, principal.user_id)
            if task is None:
                raise not_found("TASK_NOT_FOUND", "Task not found")
            stmt = (
                select(Artifact)
                .where(Artifact.task_id == task.id)
                .order_by(Artifact.created_at, Artifact.kind)
            )
            return list((await session.execute(stmt)).scalars())

    async def read_artifact(
        self, principal: Principal, task_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> tuple[Artifact, bytes]:
        async with self.ctx.sessionmaker() as session:
            task = await repo.get_owned_task(session, task_id, principal.user_id)
            if task is None:
                raise not_found("TASK_NOT_FOUND", "Task not found")
            artifact = (
                await session.execute(
                    select(Artifact).where(Artifact.id == artifact_id, Artifact.task_id == task.id)
                )
            ).scalar_one_or_none()
        if artifact is None:
            raise not_found("ARTIFACT_NOT_FOUND", "Artifact not found")
        try:
            content = await self.ctx.artifacts.get(artifact.storage_key)
        except (ArtifactKeyError, ArtifactIntegrityError, OSError) as exc:
            log.error("artifact read failed", extra={"artifact_id": str(artifact.id)})
            raise DomainError(500, "ARTIFACT_UNAVAILABLE", "Artifact content unavailable") from exc
        return artifact, content
