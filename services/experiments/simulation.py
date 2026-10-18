"""Milestone C: deterministic simulation of consenting experimental nodes.

Each simulated node stands in for an owner's device. It runs reserved experimental
attempts through the *existing* Orchestrator -> LeaseService -> verifier lifecycle with
`SimulatedSubscriptionAdapter` - the deterministic mock with zero cost and fault markers
that mimic subscription-runtime failures. No Codex process, no subscription, no network.

Prompt markers:
    `[sim:rate-limit]`   -> plan usage limit reached (no retry, cooldown observation)
    `[sim:auth-expired]` -> owner's ChatGPT session expired (connection marked EXPIRED)
    plus the mock's own `[mock:fail]`, `[mock:bad-patch]` markers.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select, update

from db.models import ExperimentalTaskConsent, Task, TaskAttempt, WorkerNode
from packages.provider_sdk.codex_subscription import (
    SubscriptionLimitReached,
    SubscriptionUnavailable,
)
from packages.provider_sdk.mock import MockProviderAdapter
from packages.provider_sdk.types import (
    ExecutionRequest,
    ExecutionResult,
    ProgressEvent,
    UsageReport,
)
from services.context import AppContext
from services.execution.orchestrator import Orchestrator
from services.experiments.dispatcher import ExperimentalDispatcher
from services.scheduler.lease_service import utcnow
from services.subscriptions.usage import SubscriptionUsageMonitor

SIM_PROVIDER = "simulated-subscription"


class SimulatedSubscriptionAdapter(MockProviderAdapter):
    name = SIM_PROVIDER

    def __init__(self) -> None:
        super().__init__()
        self.errors: dict[str, str] = {}  # attempt_id -> observation event

    async def estimate_cents(self, request: ExecutionRequest) -> int:
        return 0

    def _usage(self, request: ExecutionRequest, output_tokens: int) -> UsageReport:
        base = super()._usage(request, output_tokens)
        return dataclasses.replace(
            base,
            provider_name=SIM_PROVIDER,
            provider_request_id=f"sim-{request.attempt_id}",
            estimated_cost_cents=0,
            billable=False,
        )

    async def execute(
        self, request: ExecutionRequest
    ) -> AsyncIterator[ProgressEvent | ExecutionResult]:
        if "[sim:rate-limit]" in request.prompt:
            self.errors[request.attempt_id] = "RATE_LIMITED"
            raise SubscriptionLimitReached(
                "Simulated plan usage limit", "try again in 1 hour", self._usage(request, 0)
            )
        if "[sim:auth-expired]" in request.prompt:
            self.errors[request.attempt_id] = "AUTH_EXPIRED"
            raise SubscriptionUnavailable("Simulated expired ChatGPT session")
        async for item in super().execute(request):
            yield item


class ExperimentalSimulation:
    def __init__(self, ctx: AppContext, node_ids: tuple[uuid.UUID, ...]) -> None:
        self.adapter = SimulatedSubscriptionAdapter()
        self.ctx = dataclasses.replace(ctx, adapter=self.adapter)
        self.node_ids = node_ids
        self.dispatcher = ExperimentalDispatcher(self.ctx)
        self.orchestrator = Orchestrator(self.ctx)

    async def heartbeat(self) -> None:
        async with self.ctx.sessionmaker() as session, session.begin():
            await session.execute(
                update(WorkerNode)
                .where(WorkerNode.id.in_(self.node_ids), WorkerNode.revoked_at.is_(None))
                .values(last_heartbeat=utcnow())
            )
            await session.execute(
                update(WorkerNode)
                .where(
                    WorkerNode.id.in_(self.node_ids),
                    WorkerNode.status == "OFFLINE",
                    WorkerNode.revoked_at.is_(None),
                )
                .values(status="IDLE")
            )

    async def reserved(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        async with self.ctx.sessionmaker() as session:
            rows = await session.execute(
                select(TaskAttempt.id, ExperimentalTaskConsent.connection_id)
                .join(Task, Task.id == TaskAttempt.task_id)
                .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
                .where(
                    TaskAttempt.worker_id.in_(self.node_ids),
                    TaskAttempt.status == "RESERVED",
                    Task.resource_scope == "EXPERIMENTAL_SHARED_SUBSCRIPTION",
                )
                .order_by(TaskAttempt.leased_until)
            )
            return [(a, c) for a, c in rows.all()]

    async def run_once(self, *, heartbeat: bool = True) -> int:
        if heartbeat:
            await self.heartbeat()
        await self.dispatcher.dispatch_all(worker_ids=self.node_ids)
        ran = 0
        for attempt_id, connection_id in await self.reserved():
            started = time.monotonic()
            status = await self.orchestrator.run_attempt(attempt_id)
            event = self.adapter.errors.pop(str(attempt_id), None) or (
                "TASK_COMPLETED" if status in ("SUCCEEDED", "VERIFYING") else "TASK_FAILED"
            )
            async with self.ctx.sessionmaker() as session, session.begin():
                await SubscriptionUsageMonitor.record(
                    session,
                    connection_id,
                    event=event,
                    source="SIMULATED",
                    attempt_id=attempt_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    capacity_state="LIMITED" if event == "RATE_LIMITED" else "UNKNOWN",
                    reset_hint="try again in 1 hour" if event == "RATE_LIMITED" else None,
                )
            ran += 1
        return ran
