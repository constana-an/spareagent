"""Server side of the outbound remote-worker protocol (Week 2).

Remote workers never talk to PostgreSQL/Redis: they hold only node-scoped bearer tokens and
use these HTTPS operations. All state changes go through the same Dispatcher / LeaseService
code paths as the in-process local worker, so both execution modes share one lifecycle.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from sqlalchemy import select

from db.models import Task, TaskAttempt, WorkerNode
from packages.provider_sdk.mock import CODE_MODEL, PROVIDER_NAME
from packages.provider_sdk.types import ArtifactPayload, UsageReport
from services.context import AppContext
from services.errors import DomainError, forbidden, not_found
from services.metering.estimator import estimate_tokens
from services.observability import redact
from services.registry.node_auth import NodePrincipal
from services.scheduler.dispatcher import Dispatcher, execution_request
from services.scheduler.lease_service import utcnow

NODE_STATES = {"ONLINE", "DRAINING", "OFFLINE"}


def assert_path_scope(node: NodePrincipal, worker_id: str) -> None:
    """A node token is valid only for its own worker id."""
    try:
        requested = uuid.UUID(worker_id)
    except ValueError as exc:
        raise forbidden("NODE_SCOPE_MISMATCH", "Credential is not valid for this worker") from exc
    if requested != node.worker_id:
        raise forbidden("NODE_SCOPE_MISMATCH", "Credential is not valid for this worker")


def assignment(task: Task, attempt: TaskAttempt, lease_seconds: int) -> dict[str, Any]:
    # Prompts are redacted before leaving the trust zone: a remote node never receives
    # credentials a consumer pasted by mistake, nor the consumer's identity.
    return {
        "lease_id": str(attempt.lease_id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "leased_until": attempt.leased_until.isoformat(),
        "lease_seconds": lease_seconds,
        "task": {
            "task_id": str(task.id),
            "task_type": task.task_type,
            "repository_url": task.repository_url,
            "commit_sha": task.commit_sha.strip(),
            "prompt": redact(task.prompt),
            "max_duration_seconds": task.max_duration_seconds,
            "max_budget_cents": task.max_budget_cents,
        },
    }


class RemoteWorkerService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.dispatcher = Dispatcher(ctx)

    async def describe(self, node: NodePrincipal) -> dict[str, Any]:
        async with self.ctx.sessionmaker() as session:
            worker = await session.get(WorkerNode, node.worker_id)
        assert_alive(worker)
        return {
            "worker_id": str(worker.id),
            "resource_id": str(worker.resource_id),
            "name": worker.name,
            "status": worker.status,
            "max_concurrency": worker.max_concurrency,
            "active_tasks": worker.active_tasks,
            "last_heartbeat": worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
        }

    async def heartbeat(
        self, node: NodePrincipal, state: str, capabilities: dict[str, Any] | None
    ) -> None:
        """Liveness + self-reported availability only. Eligibility is never touched here."""
        if state not in NODE_STATES:
            raise DomainError(422, "VALIDATION_ERROR", "state must be ONLINE, DRAINING or OFFLINE")
        async with self.ctx.sessionmaker() as session, session.begin():
            worker = await session.get(WorkerNode, node.worker_id, with_for_update=True)
            assert_alive(worker)
            worker.last_heartbeat = utcnow()
            if capabilities is not None:
                worker.capabilities = capabilities
            if state in ("DRAINING", "OFFLINE"):
                worker.status = state
            elif worker.status in ("OFFLINE", "DRAINING", "ERROR"):
                worker.status = "RUNNING" if worker.active_tasks else "IDLE"

    async def _reserved_for(self, worker_id: uuid.UUID) -> dict[str, Any] | None:
        async with self.ctx.sessionmaker() as session:
            row = (
                await session.execute(
                    select(TaskAttempt, Task)
                    .join(Task, Task.id == TaskAttempt.task_id)
                    .where(
                        TaskAttempt.worker_id == worker_id,
                        TaskAttempt.status == "RESERVED",
                        TaskAttempt.leased_until > utcnow(),
                    )
                    .order_by(TaskAttempt.leased_until)
                    .limit(1)
                )
            ).first()
        if row is None:
            return None
        attempt, task = row
        return assignment(task, attempt, self.ctx.settings.lease_seconds)

    async def poll(self, node: NodePrincipal, wait_seconds: float, is_disconnected) -> dict | None:
        """Bounded long poll: return an existing reservation or claim one atomically."""
        wait = max(0.0, min(wait_seconds, self.ctx.settings.lease_poll_max_wait_seconds))
        deadline = time.monotonic() + wait
        while True:
            await self.heartbeat(node, "ONLINE", None)
            found = await self._reserved_for(node.worker_id)
            if found is None:
                if await self.dispatcher.dispatch_once((node.worker_id,)) is not None:
                    found = await self._reserved_for(node.worker_id)
            if found is not None:
                return found
            if time.monotonic() >= deadline or await is_disconnected():
                return None
            await asyncio.sleep(
                min(self.ctx.settings.lease_poll_interval_seconds, deadline - time.monotonic())
            )

    async def server_usage(
        self,
        lease_id: uuid.UUID,
        worker_id: uuid.UUID,
        artifacts: tuple[ArtifactPayload, ...],
        produced_output: bool,
    ) -> UsageReport:
        """Usage is computed by the API, never taken from the worker (no self-billing)."""
        async with self.ctx.sessionmaker() as session:
            row = (
                await session.execute(
                    select(TaskAttempt, Task)
                    .join(Task, Task.id == TaskAttempt.task_id)
                    .where(TaskAttempt.lease_id == lease_id)
                )
            ).one_or_none()
        if row is None:
            raise not_found("LEASE_NOT_FOUND", "Lease not found")
        attempt, task = row
        if attempt.worker_id != worker_id:
            raise forbidden("LEASE_NOT_OWNED", "Lease belongs to a different worker")
        request = execution_request(task, attempt.id, attempt.attempt_number)
        output_tokens = 0
        if produced_output:
            output_tokens = sum(
                estimate_tokens(a.content.decode("utf-8", "replace"))
                for a in artifacts
                if a.kind in ("SUMMARY", "PATCH")
            )
        return UsageReport(
            provider_name=PROVIDER_NAME,
            provider_request_id=f"mock-task-{attempt.id}",
            model_name=CODE_MODEL,
            input_tokens=estimate_tokens(task.prompt),
            output_tokens=output_tokens,
            estimated_cost_cents=await self.ctx.adapter.estimate_cents(request),
            billable=False,
        )


def assert_alive(worker: WorkerNode | None) -> None:
    if worker is None or worker.revoked_at is not None:
        raise DomainError(401, "NODE_CREDENTIAL_REVOKED", "Node credential revoked")
