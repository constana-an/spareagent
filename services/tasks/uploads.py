"""Task attachments and the operator-controlled task kind status (design §1-§3, §7).

Members upload files before submitting; an upload belongs to its uploader, expires after
UPLOAD_TTL_HOURS unless a task claims it, and is linked to at most one task. The bytes go
to the content-addressed artifact store; only the node holding a live lease for the task
can fetch them (see `services.execution.remote`).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AuditLog, Task, TaskKind, TaskUpload
from services.context import AppContext
from services.errors import DomainError, forbidden, not_found
from services.tasks import kinds as catalog
from services.tasks.service import Principal

KIND_STATUSES = ("DEVELOPING", "SIMULATED_OK", "LIVE_ACCEPTED", "OPEN", "DISABLED")


def utcnow() -> datetime:
    return datetime.now(UTC)


def kind_error(exc: catalog.KindError, status: int = 422) -> DomainError:
    return DomainError(status, exc.code, str(exc))


def _audit(session: AsyncSession, actor: uuid.UUID, action: str, tid: Any, **meta) -> None:
    session.add(
        AuditLog(
            actor_id=actor,
            action=action,
            target_type="task_kind" if action.startswith("task_kind") else "upload",
            target_id=str(tid),
            metadata_=json.loads(json.dumps(meta, default=str)),
        )
    )


class UploadService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def create(self, principal: Principal, filename: str, content: bytes) -> TaskUpload:
        if not self.ctx.settings.experiment_enabled:
            raise forbidden("EXPERIMENT_DISABLED", "The experiment is disabled")
        try:
            ext, media = catalog.sniff_upload(filename, content)
        except catalog.KindError as exc:
            raise kind_error(exc, 413 if exc.code == "UPLOAD_TOO_LARGE" else 422) from None
        upload_id = uuid.uuid4()
        key = await self.ctx.artifacts.put(str(upload_id), filename, content, media)
        async with self.ctx.sessionmaker() as session, session.begin():
            row = TaskUpload(
                id=upload_id,
                owner_id=principal.user_id,
                storage_key=key,
                sha256=key.rsplit("/", 1)[-1],
                size_bytes=len(content),
                filename=filename.strip(),
                extension=ext,
                media_type=media,
                expires_at=utcnow() + timedelta(hours=catalog.UPLOAD_TTL_HOURS),
            )
            session.add(row)
            _audit(session, principal.user_id, "upload.create", upload_id, size=len(content))
        return row

    @staticmethod
    async def claim(
        session: AsyncSession,
        task: Task,
        owner_id: uuid.UUID,
        upload_ids: list[uuid.UUID],
        kind: catalog.Kind,
    ) -> list[TaskUpload]:
        """Link the requester's unexpired, unclaimed uploads to a new task, in order."""
        try:
            catalog.check_attachment_count(kind, len(upload_ids))
        except catalog.KindError as exc:
            raise kind_error(exc) from None
        if not upload_ids:
            return []
        rows = {
            r.id: r
            for r in (
                await session.execute(
                    select(TaskUpload)
                    .where(TaskUpload.id.in_(upload_ids), TaskUpload.owner_id == owner_id)
                    .with_for_update()
                )
            ).scalars()
        }
        now = utcnow()
        for uid in upload_ids:
            row = rows.get(uid)
            if row is None or row.expires_at <= now:
                raise not_found("UPLOAD_NOT_FOUND", "Attachment not found or expired")
            if row.task_id is not None:
                raise DomainError(409, "UPLOAD_ALREADY_USED", "Attachment already used")
        try:
            catalog.check_attachment_types(kind, [rows[u].extension for u in upload_ids])
        except catalog.KindError as exc:
            raise kind_error(exc) from None
        if sum(rows[u].size_bytes for u in upload_ids) > catalog.MAX_TASK_INPUT_BYTES:
            raise DomainError(413, "ATTACHMENTS_TOO_LARGE", "Attachments exceed 30 MB in total")
        for position, uid in enumerate(upload_ids):
            rows[uid].task_id, rows[uid].position = task.id, position
        return [rows[u] for u in upload_ids]

    @staticmethod
    async def for_tasks(
        session: AsyncSession, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[TaskUpload]]:
        out: dict[uuid.UUID, list[TaskUpload]] = {t: [] for t in task_ids}
        if not task_ids:
            return out
        rows = (
            await session.execute(
                select(TaskUpload)
                .where(TaskUpload.task_id.in_(task_ids))
                .order_by(TaskUpload.task_id, TaskUpload.position)
            )
        ).scalars()
        for row in rows:
            out[row.task_id].append(row)
        return out

    async def purge_expired(self) -> int:
        """Forget unclaimed uploads past their expiry. Their content-addressed bytes are
        no longer referenced by any row and are left to the store's retention."""
        async with self.ctx.sessionmaker() as session, session.begin():
            result = await session.execute(
                delete(TaskUpload)
                .where(TaskUpload.task_id.is_(None), TaskUpload.expires_at <= utcnow())
                .returning(TaskUpload.id)
            )
            return len(list(result.scalars()))


class TaskKindService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    @staticmethod
    async def statuses(session: AsyncSession) -> dict[str, str]:
        return {k.kind: k.status for k in (await session.execute(select(TaskKind))).scalars()}

    async def list(self) -> list[tuple[catalog.Kind, str]]:
        async with self.ctx.sessionmaker() as session:
            status = await self.statuses(session)
        return [
            (k, status.get(k.kind, "DEVELOPING"))
            for k in catalog.CATALOG.values()
            if k.experimental
        ]

    async def set_status(
        self, principal: Principal, kind: str, status: str, report: str | None
    ) -> TaskKind:
        if principal.role != "ADMIN":
            raise forbidden("OPERATOR_ONLY", "Only the operator can change task kinds")
        try:
            catalog.get(kind)
        except catalog.KindError as exc:
            raise not_found(exc.code, str(exc)) from None
        async with self.ctx.sessionmaker() as session, session.begin():
            row = await session.get(TaskKind, kind, with_for_update=True)
            if row is None:
                raise not_found("TASK_KIND_UNKNOWN", "Unknown task kind")
            if status in ("LIVE_ACCEPTED", "OPEN") and not (report or row.acceptance_report):
                raise DomainError(
                    422,
                    "ACCEPTANCE_REPORT_REQUIRED",
                    "Opening a kind for LIVE needs an acceptance report reference",
                )
            previous = row.status
            row.status = status
            if report:
                row.acceptance_report = report
            row.updated_at, row.updated_by = utcnow(), principal.user_id
            _audit(
                session,
                principal.user_id,
                "task_kind.status",
                kind,
                previous=previous,
                status=status,
                report=report,
            )
        return row
