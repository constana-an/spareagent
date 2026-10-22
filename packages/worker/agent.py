"""Outbound remote worker agent.

Loop per concurrency slot: long-poll for a lease -> accept -> run the deterministic
MockProviderAdapter -> report progress (renews the lease, carries cancellation) -> submit
artifacts with an idempotent finalization key. Network failures back off exponentially;
completion is retried with the same key so a lost response can never double-finalize.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.provider_sdk.base import ProviderAdapter, ProviderError
from packages.provider_sdk.mock import MockProviderAdapter
from packages.provider_sdk.types import (
    ExecutionRequest,
    ExecutionResult,
    InputFile,
    ProgressEvent,
)
from packages.worker.lease_client import NodeClient, TransportError, WorkerApiError
from packages.worker.local_controls import LocalSharingControl

log = logging.getLogger("spareagent.worker.agent")

FATAL_AUTH_CODES = frozenset(
    {"NODE_CREDENTIAL_REVOKED", "NODE_KEY_EXPIRED", "NODE_AUTH_INVALID", "NODE_AUTH_REQUIRED"}
)
ROTATE_BEFORE = timedelta(days=7)
EXPERIMENTAL_SCOPE = "EXPERIMENTAL_SHARED_SUBSCRIPTION"


@dataclass
class AgentConfig:
    concurrency: int = 1
    poll_wait_seconds: float = 20.0
    heartbeat_interval_seconds: float = 10.0
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    shutdown_grace_seconds: float = 20.0
    completion_retries: int = 8
    capabilities: dict[str, Any] = field(default_factory=dict)
    # Re-evaluated on every heartbeat (tools can be installed while online).
    capabilities_provider: Callable[[], dict[str, Any]] | None = None


class Backoff:
    def __init__(self, base: float, cap: float) -> None:
        self.base, self.cap, self.attempt = base, cap, 0

    def reset(self) -> None:
        self.attempt = 0

    def next_delay(self) -> float:
        delay = min(self.cap, self.base * (2**self.attempt))
        self.attempt += 1
        return delay * random.uniform(0.5, 1.0)  # noqa: S311 - jitter, not crypto

    async def sleep(self, stop: asyncio.Event) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=self.next_delay())


class LeaseLost(Exception):
    pass


class WorkerAgent:
    def __init__(
        self,
        client: NodeClient,
        config: AgentConfig | None = None,
        adapter: ProviderAdapter | None = None,
        *,
        code_adapter: ProviderAdapter | None = None,
        sharing_control: LocalSharingControl | None = None,
        live_adapter: ProviderAdapter | None = None,
    ) -> None:
        self.client = client
        self.config = config or AgentConfig()
        self.adapter = adapter or MockProviderAdapter()
        # Week 3: CODE leases use the sandboxed CodeExecutionAdapter when this node has one.
        self.adapters: dict[str, ProviderAdapter] = {"MOCK": self.adapter}
        if code_adapter is not None:
            self.adapters["CODE"] = code_adapter
        if live_adapter is not None:
            # W4 Milestone D: the owner's own Codex CLI for authorized experimental leases.
            self.adapters["CODEX_LIVE"] = live_adapter
        # Week 4: experimental (cross-user) leases need an explicit, un-paused local opt-in.
        self.sharing_control = sharing_control
        self.fatal: WorkerApiError | None = None
        self.completed: list[tuple[str, str]] = []  # (attempt_id, task_status)
        self.claimed: list[str] = []
        self.active = 0  # leases being executed right now (shown while going offline)

    # ---------------------------------------------------------------- lifecycle
    async def run(self, stop: asyncio.Event, node_key_expires_at: str | None = None) -> None:
        await self._maybe_rotate(node_key_expires_at)
        heartbeat = asyncio.create_task(self._heartbeat_loop(stop))
        slots = [asyncio.create_task(self._slot(stop)) for _ in range(self.config.concurrency)]
        try:
            await asyncio.gather(*slots)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat
            with contextlib.suppress(Exception):
                await self.client.heartbeat("OFFLINE")
        if self.fatal:
            log.error("worker stopped: credential rejected", extra={"code": self.fatal.code})

    async def _maybe_rotate(self, expires_at: str | None) -> None:
        if not expires_at:
            return
        if datetime.fromisoformat(expires_at) - datetime.now(UTC) < ROTATE_BEFORE:
            await self.client.rotate()
            log.info("node key rotated")

    async def _heartbeat_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                caps = (
                    self.config.capabilities_provider()
                    if self.config.capabilities_provider
                    else self.config.capabilities
                )
                await self.client.heartbeat("ONLINE", caps or None)
            except WorkerApiError as exc:
                if exc.status == 401 and exc.code in FATAL_AUTH_CODES:
                    self.fatal = exc
                    stop.set()
                    return
                log.warning("heartbeat rejected", extra={"code": exc.code})
            except TransportError as exc:
                log.warning("heartbeat failed", extra={"err": str(exc)})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self.config.heartbeat_interval_seconds)

    async def _slot(self, stop: asyncio.Event) -> None:
        backoff = Backoff(self.config.backoff_base_seconds, self.config.backoff_max_seconds)
        while not stop.is_set():
            try:
                assignment = await self._poll_or_stop(stop)
                backoff.reset()
                if assignment is not None:
                    await self._execute_with_grace(assignment, stop)
            except WorkerApiError as exc:
                if exc.status == 401 and exc.code in FATAL_AUTH_CODES:
                    self.fatal = exc
                    stop.set()
                    return
                log.warning("api error; backing off", extra={"code": exc.code})
                await backoff.sleep(stop)
            except TransportError as exc:
                log.warning("api unreachable; reconnecting", extra={"err": str(exc)})
                await backoff.sleep(stop)

    async def _poll_or_stop(self, stop: asyncio.Event) -> dict[str, Any] | None:
        poll = asyncio.create_task(self.client.poll(self.config.poll_wait_seconds))
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({poll, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if poll not in done:
            # A lease reserved by an aborted poll is re-delivered or simply expires.
            poll.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await poll
            return None
        stopper.cancel()
        return poll.result()

    async def _execute_with_grace(self, assignment: dict[str, Any], stop: asyncio.Event) -> None:
        job = asyncio.create_task(self.execute(assignment))
        stopper = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait({job, stopper}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            job.cancel()
            stopper.cancel()
            # Let execute() report WORKER_SHUTDOWN before run() returns and the
            # client is closed; otherwise the task stays RUNNING until its lease lapses.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job
            raise
        if job not in done:
            try:
                await asyncio.wait_for(asyncio.shield(job), self.config.shutdown_grace_seconds)
            except TimeoutError:
                job.cancel()  # execute() reports WORKER_SHUTDOWN so the task retries promptly
                with contextlib.suppress(asyncio.CancelledError):
                    await job
        stopper.cancel()
        if job.done() and not job.cancelled() and job.exception():
            raise job.exception()  # type: ignore[misc]

    # ---------------------------------------------------------------- one lease
    async def execute(self, a: dict[str, Any]) -> str | None:
        self.active += 1
        try:
            return await self._execute(a)
        finally:
            self.active -= 1

    async def _execute(self, a: dict[str, Any]) -> str | None:
        lease_id, attempt_id = a["lease_id"], a["attempt_id"]
        task = a["task"]
        scope = a.get("resource_scope") or "PUBLIC_MARKETPLACE"
        if scope == EXPERIMENTAL_SCOPE and not (
            self.sharing_control is not None
            and self.sharing_control.allows_shared_work()
            and a.get("authorization_ref")
            and a.get("execution_profile") in self.adapters
        ):
            # Local kill switch wins over the server: never accept; the reservation lapses.
            log.info("experimental lease declined: local sharing paused or not opted in")
            return None
        try:
            await self.client.accept(lease_id)
        except WorkerApiError as exc:
            if exc.status in (403, 404, 409):
                log.info("lease not accepted", extra={"code": exc.code})
                return None
            raise
        self.claimed.append(attempt_id)
        try:
            inputs = await self._fetch_inputs(lease_id, task.get("inputs") or [])
        except (WorkerApiError, TransportError) as exc:
            return await self._finalize(
                lease_id,
                attempt_id,
                {
                    "succeeded": False,
                    "retryable": True,
                    "error_code": "TRANSIENT_PROVIDER_ERROR",
                    "result_summary": f"Could not fetch the task's attachments: {exc}"[:2000],
                },
            )
        request = ExecutionRequest(
            task_id=task["task_id"],
            attempt_id=attempt_id,
            user_id="redacted",
            resource_scope=scope,
            prompt=task["prompt"],
            repository_url=task["repository_url"],
            commit_sha=task["commit_sha"],
            max_duration_seconds=task["max_duration_seconds"],
            max_budget_cents=task["max_budget_cents"],
            attempt_number=a["attempt_number"],
            task_type=task["task_type"],
            authorization_ref=a.get("authorization_ref"),
            parameters=dict(task.get("parameters") or {}),
            deliverable=task.get("deliverable"),
            inputs=inputs,
            network_access=bool(task.get("network_access")),
        )
        adapter = self.adapters.get(a.get("execution_profile", "MOCK"))
        if adapter is None:
            return await self._finalize(
                lease_id,
                attempt_id,
                {
                    "succeeded": False,
                    "error_code": "EXECUTION_ERROR",
                    "result_summary": "Node has no executor for this execution profile",
                },
            )
        cancel = asyncio.Event()
        renewer = asyncio.create_task(
            self._renew_loop(lease_id, a["lease_seconds"], cancel, adapter, attempt_id)
        )
        payload: dict[str, Any]
        try:
            payload = await self._run_adapter(request, lease_id, cancel, adapter)
        except asyncio.CancelledError:
            payload = {
                "succeeded": False,
                "retryable": True,
                "error_code": "WORKER_SHUTDOWN",
                "result_summary": "Worker shut down before finishing",
            }
            await asyncio.shield(self._finalize(lease_id, attempt_id, payload))
            raise
        except LeaseLost:
            log.warning("lease lost; abandoning attempt", extra={"attempt_id": attempt_id})
            return None
        finally:
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await renewer
        return await self._finalize(lease_id, attempt_id, payload)

    async def _fetch_inputs(self, lease_id: str, specs: list[dict]) -> tuple[InputFile, ...]:
        out = []
        for spec in specs:
            content = await self.client.lease_input(lease_id, spec["input_id"], spec["sha256"])
            out.append(InputFile(spec["filename"], content, spec["sha256"]))
        return tuple(out)

    async def _run_adapter(
        self,
        request: ExecutionRequest,
        lease_id: str,
        cancel: asyncio.Event,
        adapter: ProviderAdapter,
    ) -> dict[str, Any]:
        result: ExecutionResult | None = None
        stream = adapter.execute(request)
        try:
            async with asyncio.timeout(request.max_duration_seconds):
                async for item in stream:
                    if cancel.is_set():
                        break
                    if isinstance(item, ProgressEvent):
                        try:
                            state = await self.client.progress(lease_id, item.message, item.percent)
                        except WorkerApiError as exc:
                            if exc.status == 409:
                                raise LeaseLost from exc
                            raise
                        if state["cancel_requested"]:
                            cancel.set()
                            break
                    elif isinstance(item, ExecutionResult):
                        result = item
        except ProviderError as exc:
            if cancel.is_set():
                return {"succeeded": False, "error_code": "CANCELLED"}
            return {
                "succeeded": False,
                "retryable": exc.retryable,
                "error_code": exc.code,
                "result_summary": f"Provider error: {exc}"[:2000],
                **(
                    {"failure_detail": exc.detail_code} if getattr(exc, "detail_code", None) else {}
                ),
                **({"reset_hint": exc.reset_hint} if getattr(exc, "reset_hint", None) else {}),
            }
        except TimeoutError:
            await adapter.cancel(request.attempt_id)
            return {"succeeded": False, "timed_out": True}
        finally:
            # Always close the generator so executors clean up checkouts/sandboxes.
            with contextlib.suppress(Exception):
                await stream.aclose()
        if cancel.is_set():
            await adapter.cancel(request.attempt_id)
            return {"succeeded": False, "error_code": "CANCELLED"}
        if result is None:
            return {"succeeded": False, "error_code": "NO_RESULT"}
        return {
            "succeeded": True,
            "result_summary": result.summary[:2000],
            **(
                {
                    "observed_usage": {
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "cached_input_tokens": result.usage.cached_input_tokens,
                        "reasoning_output_tokens": result.usage.reasoning_output_tokens,
                    }
                }
                if request.resource_scope == EXPERIMENTAL_SCOPE
                else {}
            ),
            "artifacts": [
                {
                    "kind": art.kind,
                    "name": art.name,
                    "media_type": art.media_type,
                    "content_base64": base64.b64encode(art.content).decode(),
                }
                for art in result.artifacts
            ],
        }

    async def _renew_loop(
        self,
        lease_id: str,
        lease_seconds: int,
        cancel: asyncio.Event,
        adapter: ProviderAdapter,
        attempt_id: str,
    ) -> None:
        interval = max(0.1, min(5.0, lease_seconds / 3))
        renewed_until = time.monotonic() + lease_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                state = await self.client.renew(lease_id)
                renewed_until = time.monotonic() + lease_seconds
                if state["cancel_requested"]:
                    cancel.set()
                    await adapter.cancel(attempt_id)  # stops a running sandbox promptly
            except WorkerApiError as exc:
                if exc.status in (401, 403, 404, 409) or time.monotonic() >= renewed_until:
                    cancel.set()
                    await adapter.cancel(attempt_id)
                    return
            except TransportError:
                if time.monotonic() >= renewed_until:
                    cancel.set()
                    await adapter.cancel(attempt_id)
                    return

    async def _finalize(
        self, lease_id: str, attempt_id: str, payload: dict[str, Any]
    ) -> str | None:
        body = {"finalization_key": f"fin-{attempt_id}", **payload}
        backoff = Backoff(self.config.backoff_base_seconds, self.config.backoff_max_seconds)
        for _ in range(self.config.completion_retries):
            try:
                outcome = await self.client.complete(lease_id, body)
                self.completed.append((attempt_id, outcome["task_status"]))
                return outcome["task_status"]
            except WorkerApiError as exc:
                if exc.status in (403, 404, 409, 422):
                    log.warning("completion rejected", extra={"code": exc.code})
                    return None
            except TransportError:
                pass
            await asyncio.sleep(backoff.next_delay())
        return None
