from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from apps.api.dependencies import get_ctx, get_task_service, parse_uuid_or_404
from apps.api.middleware.auth import get_principal
from apps.api.schemas.tasks import (
    IDEMPOTENCY_KEY,
    ArtifactList,
    ArtifactOut,
    TaskCreate,
    TaskList,
    TaskOut,
)
from services.context import AppContext
from services.errors import DomainError, not_found
from services.tasks import repository as repo
from services.tasks.service import Principal, TaskService, TaskView
from services.tasks.state_machine import is_terminal

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def to_out(view: TaskView) -> TaskOut:
    t = view.task
    return TaskOut(
        id=t.id,
        status=t.status,
        task_type=t.task_type,
        max_budget_cents=t.max_budget_cents,
        created_at=t.created_at,
        result_summary=view.result_summary,
    )


@router.post("", operation_id="createTask", status_code=202, response_model=TaskOut)
async def create_task(
    body: TaskCreate,
    principal: Principal = Depends(get_principal),
    service: TaskService = Depends(get_task_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskOut:
    if idempotency_key is None:
        raise DomainError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise DomainError(
            400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be 8-128 chars of [A-Za-z0-9._:-]"
        )
    view, _created = await service.create(principal, idempotency_key, body.model_dump())
    return to_out(view)


@router.get("", operation_id="listTasks", response_model=TaskList)
async def list_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    principal: Principal = Depends(get_principal),
    service: TaskService = Depends(get_task_service),
) -> TaskList:
    views, next_cursor = await service.list(principal, limit=limit, cursor=cursor)
    return TaskList(items=[to_out(v) for v in views], next_cursor=next_cursor)


@router.get("/{task_id}", operation_id="getTask", response_model=TaskOut)
async def get_task(
    task_id: str,
    principal: Principal = Depends(get_principal),
    service: TaskService = Depends(get_task_service),
) -> TaskOut:
    return to_out(await service.get(principal, parse_uuid_or_404(task_id, "TASK_NOT_FOUND")))


@router.post(
    "/{task_id}/cancel", operation_id="cancelTask", status_code=202, response_model=TaskOut
)
async def cancel_task(
    task_id: str,
    principal: Principal = Depends(get_principal),
    service: TaskService = Depends(get_task_service),
) -> TaskOut:
    return to_out(await service.cancel(principal, parse_uuid_or_404(task_id, "TASK_NOT_FOUND")))


@router.get("/{task_id}/artifacts", operation_id="listTaskArtifacts", response_model=ArtifactList)
async def list_artifacts(
    task_id: str,
    principal: Principal = Depends(get_principal),
    service: TaskService = Depends(get_task_service),
) -> ArtifactList:
    items = await service.list_artifacts(principal, parse_uuid_or_404(task_id, "TASK_NOT_FOUND"))
    return ArtifactList(
        items=[
            ArtifactOut(id=a.id, kind=a.kind, sha256=a.sha256.strip(), media_type=a.media_type)
            for a in items
        ]
    )


@router.get(
    "/{task_id}/artifacts/{artifact_id}/content",
    operation_id="getTaskArtifactContent",
    response_class=Response,
)
async def artifact_content(
    task_id: str,
    artifact_id: str,
    principal: Principal = Depends(get_principal),
    service: TaskService = Depends(get_task_service),
) -> Response:
    artifact, content = await service.read_artifact(
        principal,
        parse_uuid_or_404(task_id, "TASK_NOT_FOUND"),
        parse_uuid_or_404(artifact_id, "ARTIFACT_NOT_FOUND"),
    )
    return Response(
        content=content,
        # Served as an attachment with nosniff: artifact bytes are never rendered as HTML.
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.kind.lower()}-{artifact.id}"',
            "X-Content-Type-Options": "nosniff",
            "X-Artifact-SHA256": artifact.sha256.strip(),
        },
    )


def _sse(event_id: int, event_type: str, data: dict) -> str:
    return (
        f"id: {event_id}\nevent: {event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
    )


@router.get(
    "/{task_id}/events",
    operation_id="streamTaskEvents",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def stream_events(
    task_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    ctx: AppContext = Depends(get_ctx),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    tid = parse_uuid_or_404(task_id, "TASK_NOT_FOUND")
    async with ctx.sessionmaker() as session:
        if await repo.get_owned_task(session, tid, principal.user_id) is None:
            raise not_found("TASK_NOT_FOUND", "Task not found")
    after = 0
    if last_event_id not in (None, ""):
        if not last_event_id.isdigit() or len(last_event_id) > 18:
            raise DomainError(400, "INVALID_LAST_EVENT_ID", "Last-Event-ID must be an event seq")
        after = int(last_event_id)

    settings = ctx.settings

    async def generate() -> AsyncIterator[str]:
        cursor = after
        last_sent = time.monotonic()
        yield "retry: 2000\n\n"
        while True:
            async with ctx.sessionmaker() as session:
                # Status is read BEFORE events: a terminal status implies every event up to
                # and including the terminal one is already committed and visible.
                task = await repo.get_owned_task(session, tid, principal.user_id)
                if task is None:  # pragma: no cover - ownership cannot change
                    return
                terminal = is_terminal(task.status)
                events = await repo.events_after(session, tid, cursor)
            for ev in events:
                cursor = ev.seq
                yield _sse(
                    ev.seq,
                    ev.event_type,
                    {
                        "task_id": str(ev.task_id),
                        "seq": ev.seq,
                        "type": ev.event_type,
                        "timestamp": ev.created_at.isoformat(),
                        "payload": ev.payload,
                    },
                )
                last_sent = time.monotonic()
            if events:
                continue  # drain backlog before sleeping
            if terminal:
                return
            if await request.is_disconnected():
                return
            if time.monotonic() - last_sent >= settings.sse_keepalive_seconds:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(settings.sse_poll_interval_seconds)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
