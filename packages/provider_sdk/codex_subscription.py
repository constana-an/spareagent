"""CodexSubscriptionAdapter - runs coding tasks with the owner's own ChatGPT plan via the
official Codex CLI, on the owner's device.

Hard rules enforced here (not configurable):

* OWNER-ONLY. A request is executed only when `resource_scope == "OWNER_ONLY"` and the
  requester is the configured owner. Cross-user execution through a personal ChatGPT
  plan is NOT enabled in this build (see docs/WEEK4_SUBSCRIPTION_STATUS.md, blocker B-1);
  the experimental scheduler routes cross-user work only to the deterministic mock.
* ChatGPT login only. Before every run the official `codex login status` must report a
  ChatGPT login; an API-key login, a missing login or an unreadable status fails closed.
* No API-key substitution / paid fallback. The child gets an allow-listed environment
  without OPENAI_API_KEY / CODEX_API_KEY / base URLs, and `forced_login_method="chatgpt"`.
  If subscription auth fails the adapter raises; it never retries with other credentials.
* No credential handling. Tokens stay in Codex's own store; the adapter never reads them,
  and every string it emits is passed through `redact()`.

Usage reported here is observational (token counts from Codex's event stream). It is NOT
a remaining-quota measurement, and `estimated_cost_cents` is always 0 / non-billable.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from packages.provider_sdk.base import PermanentProviderError, ProviderError
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
from packages.worker.codex_runtime import CodexEvent, CodexRunResult, CodexRuntime
from packages.worker.subscription_auth import (
    AuthMethod,
    AuthStatus,
    SubscriptionAuthError,
    probe_login,
)
from services.observability import redact

PROVIDER_NAME = "codex-subscription"
ALLOWED_TASK_TYPES = frozenset({"CODE_REVIEW", "WRITE_TESTS", "CODE_FIX"})


class OwnerOnlyViolation(PermanentProviderError):
    code = "OWNER_ONLY_VIOLATION"


class SubscriptionUnavailable(PermanentProviderError):
    """Subscription auth missing/expired/revoked or an API-key login: fail closed."""

    code = "SUBSCRIPTION_AUTH_UNAVAILABLE"


class SubscriptionLimitReached(ProviderError):
    """The plan's usage/rate limit was hit. Not retried here and never worked around by
    switching accounts or credentials; the platform decides about a later retry."""

    retryable = False
    code = "SUBSCRIPTION_USAGE_LIMIT"

    def __init__(self, message: str, reset_hint: str | None, usage: UsageReport | None) -> None:
        super().__init__(message, usage)
        self.reset_hint = reset_hint


WorkspaceFactory = Callable[[ExecutionRequest], Path]


def _temp_workspace(_request: ExecutionRequest) -> Path:
    return Path(tempfile.mkdtemp(prefix="spareagent-codex-"))


class CodexSubscriptionAdapter:
    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        owner_ref: str,
        runtime: CodexRuntime | None = None,
        workspace_factory: WorkspaceFactory | None = None,
        keep_workspace: bool = False,
    ) -> None:
        if not owner_ref:
            raise ValueError("owner_ref is required")
        self.owner_ref = owner_ref
        self.runtime = runtime or CodexRuntime()
        self._workspace = workspace_factory or _temp_workspace
        self._keep_workspace = keep_workspace or workspace_factory is not None
        self.last_auth: AuthStatus | None = None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            resource_type="PERSONAL_SUBSCRIPTION",
            supports_code=True,
            supports_chat=False,
            supports_stream=True,
            max_concurrency=1,
        )

    async def estimate_cents(self, request: ExecutionRequest) -> int:
        return 0  # included plan usage is never priced, sold or billed

    async def check_auth(self) -> AuthStatus:
        self.last_auth = await probe_login(self.runtime.config.codex_bin)
        return self.last_auth

    def _assert_owner_only(self, request: ExecutionRequest) -> None:
        if request.resource_scope != "OWNER_ONLY" or request.user_id != self.owner_ref:
            raise OwnerOnlyViolation(
                "Codex subscription execution is owner-only in this build; "
                "cross-user execution is disabled"
            )
        if request.task_type not in ALLOWED_TASK_TYPES:
            raise PermanentProviderError(f"Task type {request.task_type} is not allowed")

    def _usage(self, request: ExecutionRequest, run: CodexRunResult | None) -> UsageReport:
        u = run.usage if run else None
        return UsageReport(
            provider_name=PROVIDER_NAME,
            provider_request_id=f"codex-{request.attempt_id}",
            model_name=self.runtime.config.model or "codex-default",
            input_tokens=u.input_tokens if u else 0,
            output_tokens=(u.output_tokens + u.reasoning_output_tokens) if u else 0,
            estimated_cost_cents=0,
            billable=False,
        )

    async def execute(
        self, request: ExecutionRequest
    ) -> AsyncIterator[ProgressEvent | ExecutionResult]:
        self._assert_owner_only(request)
        status = await self.check_auth()
        if status.method != AuthMethod.CHATGPT:
            raise SubscriptionUnavailable(
                f"ChatGPT subscription login required (found {status.method.value}); "
                "API-key usage is never used as a fallback"
            ) from SubscriptionAuthError(status)
        yield ProgressEvent("Verified local ChatGPT login (credentials stay on device)", 5)

        workdir = self._workspace(request)
        run: CodexRunResult | None = None
        try:
            percent = 10
            async for item in self.runtime.run(
                request.attempt_id,
                request.prompt,
                workdir,
                timeout_seconds=request.max_duration_seconds,
            ):
                if isinstance(item, CodexRunResult):
                    run = item
                elif isinstance(item, CodexEvent) and item.kind == "progress":
                    percent = min(90, percent + 5)
                    yield ProgressEvent(redact(item.message)[:200], percent)
            assert run is not None  # noqa: S101 - runtime always yields a result
            self._raise_for(run, request)
            summary = redact(run.final_message or "Codex finished without a final message")
            log_text = redact(
                f"provider={PROVIDER_NAME} auth=CHATGPT thread={run.thread_id or '-'}\n"
                f"input_tokens={run.usage.input_tokens} "
                f"cached_input_tokens={run.usage.cached_input_tokens} "
                f"output_tokens={run.usage.output_tokens} "
                f"reasoning_output_tokens={run.usage.reasoning_output_tokens}\n"
                "billing=included-plan-usage api_key_used=false\n"
            )
            artifacts = [
                ArtifactPayload("SUMMARY", "summary.md", "text/markdown", summary.encode()),
                ArtifactPayload("LOG", "codex.log", "text/plain", log_text.encode()),
            ]
            patch = await _git_diff(workdir)
            if patch:
                artifacts.append(ArtifactPayload("PATCH", "changes.patch", "text/x-diff", patch))
            yield ProgressEvent("Codex run complete", 95)
            yield ExecutionResult(
                summary=summary[:2000], artifacts=tuple(artifacts), usage=self._usage(request, run)
            )
        finally:
            if not self._keep_workspace:
                shutil.rmtree(workdir, ignore_errors=True)

    def _raise_for(self, run: CodexRunResult, request: ExecutionRequest) -> None:
        usage = self._usage(request, run)
        if run.cancelled:
            raise PermanentProviderError("Codex run cancelled", usage=usage)
        if run.timed_out:
            raise TimeoutError
        if run.error_class == "AUTH":
            raise SubscriptionUnavailable(
                "ChatGPT session expired or was revoked; re-run `codex login` on this device",
                usage=usage,
            )
        if run.error_class == "USAGE_LIMIT":
            raise SubscriptionLimitReached(
                "ChatGPT plan usage limit reached", run.reset_hint, usage
            )
        if not run.succeeded:
            raise PermanentProviderError(
                f"Codex run failed: {run.error_message or 'unknown error'}", usage=usage
            )

    async def cancel(self, attempt_id: str) -> None:
        await self.runtime.cancel(attempt_id)

    async def chat(self, invocation: ChatInvocation) -> ChatResult:
        raise PermanentProviderError("Chat is not offered through personal subscriptions")


async def _git_diff(workdir: Path) -> bytes:
    """Patch of the owner-side workspace, if it is a git repository (workspace-write mode)."""
    import asyncio

    if not (workdir / ".git").exists():
        return b""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(workdir),
        "-c",
        "core.hooksPath=/dev/null",
        "diff",
        "--no-ext-diff",
        "--binary",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out if out.startswith(b"diff --git ") else b""


def owner_ref_for(user_id: str) -> str:
    """Stable, non-reversible reference used in local reports instead of raw identities."""
    return hashlib.sha256(f"spareagent-owner:{user_id}".encode()).hexdigest()[:16]
