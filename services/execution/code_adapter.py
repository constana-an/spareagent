"""Deterministic CodeExecutionAdapter (Week 3): checkout -> allowlisted edit -> sandboxed
trusted tests -> git patch -> artifacts. Implements the ProviderAdapter protocol so the remote
worker drives it exactly like the mock adapter.

Extension point: `EditStrategy`. The catalog-driven strategy applies exact, pre-approved text
replacements. A future model-backed AgentAdapter supplies a different strategy (producing file
edits); repository retrieval, sandboxing, patch policy and platform verification are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from packages.provider_sdk.base import PermanentProviderError, TransientProviderError
from packages.provider_sdk.types import (
    ArtifactPayload,
    ChatInvocation,
    ChatResult,
    ExecutionRequest,
    ExecutionResult,
    ProgressEvent,
    ProviderCapabilities,
    UsageReport,
)
from services.execution import catalog as catalog_mod
from services.execution.catalog import Recipe
from services.execution.patch_policy import PatchRejected, inspect_patch
from services.execution.repository_manager import Checkout, RepositoryError, RepositoryManager
from services.execution.sandbox import DockerSandbox, SandboxError
from services.execution.test_report import build_report
from services.observability import redact

log = logging.getLogger(__name__)

PROVIDER_NAME = "sandbox"
MODEL_NAME = "deterministic-code-v1"
RETRYABLE_REPO_ERRORS = frozenset({"TIMEOUT", "FETCH_FAILED"})


class EditStrategy(Protocol):
    def apply(self, checkout: Checkout, recipe: Recipe) -> list[str]: ...


class CatalogEdits:
    """Apply the recipe's exact replacements. Each must match exactly once in a regular file."""

    def apply(self, checkout: Checkout, recipe: Recipe) -> list[str]:
        root = checkout.path.resolve()
        changed = []
        for edit in recipe.edits:
            target = (root / edit.path).resolve()
            if not target.is_relative_to(root) or edit.path not in recipe.allowed_paths:
                raise PermanentProviderError(f"edit outside allowed paths: {edit.path}")
            if target.is_symlink() or not target.is_file():
                raise PermanentProviderError(f"edit target is not a regular file: {edit.path}")
            text = target.read_text(encoding="utf-8")
            if text.count(edit.old) != 1:
                raise PermanentProviderError(f"recipe does not apply cleanly to {edit.path}")
            target.write_text(text.replace(edit.old, edit.new), encoding="utf-8")
            changed.append(edit.path)
        return changed


def generate_patch(checkout: Checkout) -> bytes:
    checkout.git("add", "-A")
    return checkout.git(
        "diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color", "--full-index", "HEAD"
    ).encode()


class CodeExecutionAdapter:
    name = PROVIDER_NAME

    def __init__(
        self,
        repositories: RepositoryManager,
        sandbox: DockerSandbox,
        *,
        catalog: tuple[Recipe, ...] = catalog_mod.CATALOG,
        edits: EditStrategy | None = None,
        cost_cents: int = 25,
    ) -> None:
        self.repositories = repositories
        self.sandbox = sandbox
        self.catalog = catalog
        self.edits = edits or CatalogEdits()
        self.cost_cents = cost_cents
        self._cancel: dict[str, threading.Event] = {}

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("MANAGED_API", True, False, False, 1)

    async def estimate_cents(self, request: ExecutionRequest) -> int:
        return self.cost_cents

    def _usage(self, request: ExecutionRequest) -> UsageReport:
        return UsageReport(
            PROVIDER_NAME, f"code-task-{request.attempt_id}", MODEL_NAME, 0, 0, self.cost_cents
        )

    async def cancel(self, attempt_id: str) -> None:
        event = self._cancel.get(attempt_id)
        if event:
            event.set()

    async def chat(self, invocation: ChatInvocation) -> ChatResult:  # pragma: no cover
        raise PermanentProviderError("chat is not supported by the code executor")

    async def execute(
        self, request: ExecutionRequest
    ) -> AsyncIterator[ProgressEvent | ExecutionResult]:
        recipe = catalog_mod.lookup(
            request.repository_url or "", request.commit_sha or "", self.catalog
        )
        if recipe is None:
            raise PermanentProviderError("UNSUPPORTED_REPOSITORY: not in the execution allowlist")
        cancel = threading.Event()
        self._cancel[request.attempt_id] = cancel
        log_lines = [f"attempt={request.attempt_number} executor={MODEL_NAME}"]
        checkout: Checkout | None = None
        try:
            yield ProgressEvent("Fetching pinned commit", 10)
            try:
                checkout = await asyncio.to_thread(
                    self.repositories.checkout, request.repository_url, request.commit_sha
                )
            except RepositoryError as exc:
                err = (
                    TransientProviderError
                    if exc.code in RETRYABLE_REPO_ERRORS
                    else PermanentProviderError
                )
                raise err(f"REPOSITORY_{exc.code}: {exc.message}") from exc
            log_lines.append(
                f"checkout {checkout.owner}/{checkout.repo}@{checkout.commit_sha} "
                f"files={checkout.file_count} bytes={checkout.tree_bytes}"
            )

            yield ProgressEvent("Applying allowlisted modification", 30)
            changed = await asyncio.to_thread(self.edits.apply, checkout, recipe)
            log_lines.append(f"edited {changed}")

            yield ProgressEvent("Running trusted tests in sandbox", 50)
            try:
                result = await asyncio.to_thread(
                    self.sandbox.execute,
                    checkout.path,
                    list(recipe.test_command),
                    run_id=request.attempt_id,
                    timeout=min(self.sandbox.limits.timeout_seconds, request.max_duration_seconds)
                    if request.max_duration_seconds is not None
                    else self.sandbox.limits.timeout_seconds,
                    cancel_event=cancel,
                )
            except SandboxError as exc:
                raise TransientProviderError(f"SANDBOX_{exc.code}") from exc
            if result.cancelled:
                raise PermanentProviderError("CANCELLED: sandbox stopped on request")
            log_lines += [
                f"sandbox container={result.container} exit={result.exit_code} "
                f"timed_out={result.timed_out} oom={result.oom_killed} ms={result.duration_ms} "
                f"removed={result.removed}",
                "--- stdout ---",
                result.stdout,
                "--- stderr ---",
                result.stderr,
            ]
            report = build_report(
                framework=recipe.framework,
                base_commit=checkout.commit_sha,
                command=list(recipe.test_command),
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=result.timed_out,
                duration_ms=result.duration_ms,
            )
            patch = await asyncio.to_thread(generate_patch, checkout)
            try:
                inspect_patch(patch, recipe.allowed_paths)
            except PatchRejected as exc:
                raise PermanentProviderError(f"PATCH_POLICY: {exc.reason}") from exc
            yield ProgressEvent("Tests finished; packaging artifacts", 90)
            verdict = "passed" if report["ok"] else "FAILED"
            summary = (
                f"{recipe.summary} Tests {verdict}: {report['passed']}/{report['total']} "
                f"({recipe.framework}) at {checkout.commit_sha[:12]}. Awaiting platform "
                "verification."
            )
            yield ExecutionResult(
                summary=summary,
                artifacts=(
                    ArtifactPayload("SUMMARY", "summary.txt", "text/plain", summary.encode()),
                    ArtifactPayload("PATCH", "changes.patch", "text/x-diff", patch),
                    ArtifactPayload(
                        "TEST_REPORT",
                        "tests.json",
                        "application/json",
                        json.dumps(report, sort_keys=True).encode(),
                    ),
                    ArtifactPayload(
                        "EXECUTION_LOG",
                        "execution.log",
                        "text/plain",
                        redact("\n".join(log_lines) + "\n").encode(),
                    ),
                ),
                usage=self._usage(request),
            )
        finally:
            self._cancel.pop(request.attempt_id, None)
            if checkout is not None:
                await asyncio.to_thread(checkout.cleanup)


def from_env() -> CodeExecutionAdapter:
    """Worker-side construction. Fixture mirrors only when explicitly configured (dev)."""
    mirror = os.environ.get("SPAREAGENT_FIXTURE_ROOT")
    return CodeExecutionAdapter(
        RepositoryManager(mirror_root=Path(mirror) if mirror else None),
        DockerSandbox(image=os.environ.get("SPAREAGENT_SANDBOX_IMAGE", "spareagent-sandbox:dev")),
    )
