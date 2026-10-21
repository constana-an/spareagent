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

from db.models import Resource, Task, TaskAttempt, TaskUpload, WorkerNode
from packages.provider_sdk.mock import CODE_MODEL, PROVIDER_NAME
from packages.provider_sdk.types import ArtifactPayload, UsageReport
from services.context import AppContext
from services.errors import DomainError, forbidden, not_found
from services.metering.estimator import estimate_tokens
from services.observability import redact
from services.registry.node_auth import NodePrincipal
from services.scheduler.dispatcher import Dispatcher, execution_request
from services.scheduler.lease_service import ACTIVE_ATTEMPT_STATUSES, utcnow
from services.tasks.uploads import UploadService

NODE_STATES = {"ONLINE", "DRAINING", "OFFLINE"}


def assert_path_scope(node: NodePrincipal, worker_id: str) -> None:
    """A node token is valid only for its own worker id."""
    try:
        requested = uuid.UUID(worker_id)
    except ValueError as exc:
        raise forbidden("NODE_SCOPE_MISMATCH", "Credential is not valid for this worker") from exc
    if requested != node.worker_id:
        raise forbidden("NODE_SCOPE_MISMATCH", "Credential is not valid for this worker")


def assignment(
    task: Task, attempt: TaskAttempt, lease_seconds: int, inputs: list | tuple = ()
) -> dict[str, Any]:
    # Prompts are redacted before leaving the trust zone: a remote node never receives
    # credentials a consumer pasted by mistake, nor the consumer's identity.
    return {
        "lease_id": str(attempt.lease_id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "leased_until": attempt.leased_until.isoformat(),
        "lease_seconds": lease_seconds,
        "execution_profile": task.execution_profile,
        "resource_scope": task.resource_scope,
        "task": {
            "task_id": str(task.id),
            "task_type": task.task_type,
            "kind": task.task_type,
            "repository_url": task.repository_url,
            "commit_sha": task.commit_sha.strip() if task.commit_sha else None,
            "prompt": redact(task.prompt),
            "max_duration_seconds": task.max_duration_seconds,
            "max_budget_cents": task.max_budget_cents,
            "deliverable": (task.spec or {}).get("deliverable"),
            "parameters": (task.spec or {}).get("parameters") or {},
            "network_access": bool((task.spec or {}).get("network_access")),
            "inputs": [
                {
                    "input_id": str(u.id),
                    "filename": u.filename,
                    "size_bytes": u.size_bytes,
                    "sha256": u.sha256,
                    "media_type": u.media_type,
                }
                for u in inputs
            ],
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
        self,
        node: NodePrincipal,
        state: str,
        capabilities: dict[str, Any] | None,
        *,
        touch_only: bool = False,
    ) -> str:
        """Liveness + self-reported availability only. Eligibility is never touched here.

        `touch_only` refreshes liveness without changing the node's status; returns status."""
        if state not in NODE_STATES:
            raise DomainError(422, "VALIDATION_ERROR", "state must be ONLINE, DRAINING or OFFLINE")
        async with self.ctx.sessionmaker() as session, session.begin():
            worker = await session.get(WorkerNode, node.worker_id, with_for_update=True)
            assert_alive(worker)
            worker.last_heartbeat = utcnow()
            if touch_only:
                return worker.status
            if capabilities is not None:
                worker.capabilities = capabilities
            if state in ("DRAINING", "OFFLINE"):
                worker.status = state
            elif worker.status in ("OFFLINE", "DRAINING", "ERROR"):
                worker.status = "RUNNING" if worker.active_tasks else "IDLE"
            return worker.status

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
            inputs = (await UploadService.for_tasks(session, [task.id]))[task.id]
            out = assignment(task, attempt, self.ctx.settings.lease_seconds, inputs)
            if task.resource_scope == "EXPERIMENTAL_SHARED_SUBSCRIPTION":
                from db.models import ExperimentalDispatchAudit

                # The authorization the ALLOW decision relied on travels with the lease, so
                # the owner's device can refuse shared work that is not tied to one.
                auth_id = (
                    await session.execute(
                        select(ExperimentalDispatchAudit.authorization_id).where(
                            ExperimentalDispatchAudit.attempt_id == attempt.id,
                            ExperimentalDispatchAudit.decision == "ALLOW",
                        )
                    )
                ).scalar_one_or_none()
                out["authorization_ref"] = str(auth_id) if auth_id else None
        return out

    async def read_input(
        self, node: NodePrincipal, lease_id: uuid.UUID, input_id: uuid.UUID
    ) -> tuple[TaskUpload, bytes]:
        """An attachment of the leased task, only for the node holding a live lease."""
        async with self.ctx.sessionmaker() as session:
            attempt = (
                await session.execute(select(TaskAttempt).where(TaskAttempt.lease_id == lease_id))
            ).scalar_one_or_none()
            if attempt is None or attempt.worker_id != node.worker_id:
                raise not_found("LEASE_NOT_FOUND", "Lease not found")
            if attempt.status not in ACTIVE_ATTEMPT_STATUSES or attempt.leased_until <= utcnow():
                raise DomainError(409, "LEASE_NOT_ACTIVE", "The lease is no longer active")
            upload = await session.get(TaskUpload, input_id)
            if upload is None or upload.task_id != attempt.task_id:
                raise not_found("INPUT_NOT_FOUND", "Input not found")
        return upload, await self.ctx.artifacts.get(upload.storage_key)

    async def _claimer(self, worker_id: uuid.UUID):
        """A personal (owner's own) device claims experimental tasks - LIVE and simulated
        (MOCK, run by the worker's in-process mock executor, no model call) - through the
        separate ExperimentalDispatcher; every other node uses the public Dispatcher, which
        never selects personal resources (unchanged)."""
        async with self.ctx.sessionmaker() as session:
            rtype = (
                await session.execute(
                    select(Resource.resource_type)
                    .join(WorkerNode, WorkerNode.resource_id == Resource.id)
                    .where(WorkerNode.id == worker_id)
                )
            ).scalar_one_or_none()
        if rtype == "PERSONAL_SUBSCRIPTION":
            from services.experiments.dispatcher import ExperimentalDispatcher

            experimental = ExperimentalDispatcher(self.ctx)
            return lambda: experimental.dispatch_once((worker_id,), profiles=("CODEX_LIVE", "MOCK"))
        return lambda: self.dispatcher.dispatch_once((worker_id,))

    async def poll(self, node: NodePrincipal, wait_seconds: float, is_disconnected) -> dict | None:
        """Bounded long poll: return an existing reservation or claim one atomically."""
        wait = max(0.0, min(wait_seconds, self.ctx.settings.lease_poll_max_wait_seconds))
        deadline = time.monotonic() + wait
        first = True
        claim = await self._claimer(node.worker_id)
        while True:
            # Only the start of a poll may bring a node back ONLINE. Later iterations only
            # refresh liveness, so a node that announced OFFLINE/DRAINING while this request
            # was still in flight (e.g. graceful shutdown) is not silently re-onlined.
            status = await self.heartbeat(node, "ONLINE", None, touch_only=not first)
            first = False
            if status in ("OFFLINE", "DRAINING"):
                return None
            found = await self._reserved_for(node.worker_id)
            if found is None:
                if await claim() is not None:
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
        if task.execution_profile == "CODE":
            return UsageReport(
                provider_name="sandbox",
                provider_request_id=f"code-task-{attempt.id}",
                model_name="deterministic-code-v1",
                input_tokens=0,
                output_tokens=0,
                estimated_cost_cents=self.ctx.settings.code_task_estimate_cents,
                billable=False,
            )
        if task.execution_profile == "CODEX_LIVE":
            # Included plan usage on the owner's own account: never priced or billed.
            return UsageReport(
                provider_name="codex-subscription",
                provider_request_id=f"codex-live-{attempt.id}",
                model_name="codex-default",
                input_tokens=0,  # financial ledger: actual counts are separate runtime observations
                output_tokens=0,
                estimated_cost_cents=0,
                billable=False,
            )
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
