"""CodexSubscriptionAdapter - runs coding tasks with the owner's own ChatGPT plan via the
official Codex CLI, on the owner's device.

Hard rules enforced here:

* Owner-only by default. A request runs when `resource_scope == "OWNER_ONLY"` and the
  requester is the configured owner.
* Shared (experimental) work only with explicit, local, revocable opt-in. A request with
  scope EXPERIMENTAL_SHARED_SUBSCRIPTION runs only if this adapter was constructed with a
  `SharedWorkConsent` (the owner started the worker with `--accept-shared-tasks` and
  attested no purchased credits / no auto-reload), the local `sharing pause` switch is
  off, and the lease names the authorization record the server relied on (OpenAI ticket
  10766658 scope: official Codex CLI on the owner's own computer, small group, no
  purchased credits consumed, review after 6 months). Anything else is refused before
  Codex is started.
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

import asyncio
import hashlib
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

from packages.common.observability import redact
from packages.common.repository_manager import RepositoryError, RepositoryManager
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
from packages.worker.local_controls import LocalSharingControl
from packages.worker.subscription_auth import (
    AuthMethod,
    AuthStatus,
    SubscriptionAuthError,
    probe_login,
)

PROVIDER_NAME = "codex-subscription"
ALLOWED_TASK_TYPES = frozenset({"CODE_REVIEW", "WRITE_TESTS", "CODE_FIX"})


class OwnerOnlyViolation(PermanentProviderError):
    code = "OWNER_ONLY_VIOLATION"


class SharedWorkRefused(PermanentProviderError):
    code = "SHARED_WORK_REFUSED"


@dataclass(frozen=True)
class SharedWorkConsent:
    """The owner's on-device opt-in to experimental shared tasks."""

    sharing_control: LocalSharingControl
    no_purchased_credits_attested: bool
    repository_manager: RepositoryManager | None = None


SHARED_PREAMBLE = (
    "You are running a task submitted by another member of a small, non-commercial "
    "SpareAgent experiment, on the repository checked out in the current directory. "
    "Work only inside this directory. Do not use the network, read files outside it, or "
    "print environment variables or credentials. Task:\n\n"
)


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
        shared: SharedWorkConsent | None = None,
    ) -> None:
        if not owner_ref:
            raise ValueError("owner_ref is required")
        if shared is not None and not shared.no_purchased_credits_attested:
            raise ValueError("shared work requires the no-purchased-credits attestation")
        self.owner_ref = owner_ref
        self.shared = shared
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

    def _assert_allowed(self, request: ExecutionRequest) -> bool:
        """Returns True for shared (experimental) work, False for the owner's own task."""
        if request.task_type not in ALLOWED_TASK_TYPES:
            raise PermanentProviderError(f"Task type {request.task_type} is not allowed")
        if request.resource_scope == "OWNER_ONLY" and request.user_id == self.owner_ref:
            return False
        if request.resource_scope != "EXPERIMENTAL_SHARED_SUBSCRIPTION":
            raise OwnerOnlyViolation("Codex subscription execution is owner-only for this scope")
        if not request.prompt or len(request.prompt) > 12000 or "\x00" in request.prompt:
            raise SharedWorkRefused("Shared prompt must be 1-12000 characters without NUL bytes")
        if self.shared is None:
            raise SharedWorkRefused("This device has not opted in to shared tasks")
        if not self.shared.sharing_control.allows_shared_work():
            raise SharedWorkRefused("Sharing is paused on this device")
        if not request.authorization_ref:
            raise SharedWorkRefused("Shared task carries no authorization reference")
        if not request.repository_url or not request.commit_sha:
            raise SharedWorkRefused("Shared tasks must name a pinned repository commit")
        return True

    async def _shared_workspace(self, request: ExecutionRequest):
        manager = self.shared.repository_manager or RepositoryManager()
        try:
            return await asyncio.to_thread(
                manager.checkout, request.repository_url, request.commit_sha
            )
        except RepositoryError as exc:
            raise PermanentProviderError(f"Repository checkout refused: {exc.code}") from exc

    def _usage(self, request: ExecutionRequest, run: CodexRunResult | None) -> UsageReport:
        u = run.usage if run else None
        return UsageReport(
            provider_name=PROVIDER_NAME,
            provider_request_id=f"codex-{request.attempt_id}",
            model_name=self.runtime.config.model or "codex-default",
            input_tokens=u.input_tokens if u else 0,
            output_tokens=u.output_tokens if u else 0,
            estimated_cost_cents=0,
            billable=False,
            cached_input_tokens=u.cached_input_tokens if u else 0,
            reasoning_output_tokens=u.reasoning_output_tokens if u else 0,
        )

    async def execute(
        self, request: ExecutionRequest
    ) -> AsyncIterator[ProgressEvent | ExecutionResult]:
        shared = self._assert_allowed(request)
        status = await self.check_auth()
        if status.method != AuthMethod.CHATGPT:
            raise SubscriptionUnavailable(
                f"ChatGPT subscription login required (found {status.method.value}); "
                "API-key usage is never used as a fallback"
            ) from SubscriptionAuthError(status)
        yield ProgressEvent("Verified local ChatGPT login (credentials stay on device)", 5)

        checkout = None
        if shared:
            checkout = await self._shared_workspace(request)
            workdir = checkout.path
            yield ProgressEvent("Checked out the pinned commit", 8)
        else:
            workdir = self._workspace(request)
        # Reviews never modify files; other shared tasks may only write inside the checkout.
        sandbox = "read-only" if request.task_type == "CODE_REVIEW" or not shared else None
        if shared and sandbox is None:
            sandbox = "workspace-write"
        prompt = SHARED_PREAMBLE + request.prompt if shared else request.prompt
        run: CodexRunResult | None = None
        try:
            percent = 10
            async for item in self.runtime.run(
                request.attempt_id,
                prompt,
                workdir,
                timeout_seconds=request.max_duration_seconds,
                sandbox=sandbox if shared else None,
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
                "billing=NOT_OBSERVABLE api_key_used=false\n"
            )
            artifacts = [
                ArtifactPayload("SUMMARY", "summary.md", "text/plain", summary.encode()),
                ArtifactPayload("LOG", "codex.log", "text/plain", log_text.encode()),
            ]
            patch = b""
            if checkout is not None:
                await asyncio.to_thread(checkout.git, "add", "--intent-to-add", "--all")
                patch = (
                    await asyncio.to_thread(
                        checkout.git, "diff", "--no-ext-diff", "--no-textconv", "--binary"
                    )
                ).encode()
            if patch:
                if redact(patch.decode(errors="replace")).encode() != patch:
                    raise PermanentProviderError("Patch contains possible credentials; withheld")
                artifacts.append(ArtifactPayload("PATCH", "changes.patch", "text/x-diff", patch))
            yield ProgressEvent("Codex run complete", 95)
            yield ExecutionResult(
                summary=summary[:2000], artifacts=tuple(artifacts), usage=self._usage(request, run)
            )
        finally:
            if checkout is not None:
                checkout.cleanup()
            elif not self._keep_workspace:
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


def owner_ref_for(user_id: str) -> str:
    """Stable, non-reversible reference used in local reports instead of raw identities."""
    return hashlib.sha256(f"spareagent-owner:{user_id}".encode()).hexdigest()[:16]
