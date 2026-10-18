"""Runs one leased attempt through the provider adapter.

No untrusted code runs here: in Week 1 the adapter is the deterministic mock, which only
produces fixture output. Real sandboxed execution is a Week 2/3 security gate.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from db.models import Task, TaskAttempt
from packages.provider_sdk.base import ProviderError
from packages.provider_sdk.types import ExecutionResult, ProgressEvent
from services.context import AppContext
from services.errors import DomainError
from services.scheduler.dispatcher import execution_request
from services.scheduler.lease_service import CompletionInput, LeaseService

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.leases = LeaseService(ctx)

    async def run_attempt(self, attempt_id: uuid.UUID) -> str | None:
        """Accept, execute and finalize one attempt. Returns the resulting task status."""
        async with self.ctx.sessionmaker() as session:
            row = (
                await session.execute(
                    select(TaskAttempt, Task)
                    .join(Task, Task.id == TaskAttempt.task_id)
                    .where(TaskAttempt.id == attempt_id)
                )
            ).one_or_none()
        if row is None:
            return None
        attempt, task = row
        lease_id, worker_id = attempt.lease_id, attempt.worker_id
        finalization_key = f"fin-{attempt.id}"

        try:
            await self.leases.accept(lease_id, worker_id)
        except DomainError as exc:
            log.info("lease not accepted", extra={"attempt_id": str(attempt_id), "code": exc.code})
            return None

        request = execution_request(task, attempt.id, attempt.attempt_number)
        adapter = self.ctx.adapter
        result: ExecutionResult | None = None
        completion: CompletionInput
        try:
            async with asyncio.timeout(task.max_duration_seconds):
                async for item in adapter.execute(request):
                    if isinstance(item, ProgressEvent):
                        keep_going = await self.leases.progress(
                            lease_id, worker_id, item.message, item.percent
                        )
                        if not keep_going:
                            await adapter.cancel(str(attempt.id))
                            completion = CompletionInput(finalization_key, succeeded=False)
                            break
                    elif isinstance(item, ExecutionResult):
                        result = item
                else:
                    if result is None:
                        completion = CompletionInput(
                            finalization_key,
                            succeeded=False,
                            error_code="NO_RESULT",
                            result_summary="Provider returned no result",
                        )
                    else:
                        completion = CompletionInput(
                            finalization_key,
                            succeeded=True,
                            result_summary=result.summary,
                            artifacts=result.artifacts,
                            usage=result.usage,
                        )
        except ProviderError as exc:
            completion = CompletionInput(
                finalization_key,
                succeeded=False,
                retryable=exc.retryable,
                error_code=exc.code,
                result_summary=f"Provider error: {exc}",
                usage=exc.usage,
            )
        except TimeoutError:
            await adapter.cancel(str(attempt.id))
            completion = CompletionInput(finalization_key, succeeded=False, timed_out=True)
        except DomainError as exc:
            # Lease lost (expired/revoked) mid-run: stop; expiry recovery owns the task now.
            log.warning(
                "lease lost during run", extra={"attempt_id": str(attempt_id), "code": exc.code}
            )
            return None

        try:
            outcome = await self.leases.complete(lease_id, worker_id, completion)
        except DomainError as exc:
            log.warning(
                "completion rejected", extra={"attempt_id": str(attempt_id), "code": exc.code}
            )
            return None
        return outcome.task_status
