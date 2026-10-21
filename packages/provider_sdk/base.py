"""Provider adapter protocol.

Adapters execute work; they never decide marketplace eligibility. Authorization and
resource-eligibility decisions belong to `services.scheduler.policy`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from packages.provider_sdk.types import (
    ChatInvocation,
    ChatResult,
    ExecutionRequest,
    ExecutionResult,
    ProgressEvent,
    ProviderCapabilities,
    UsageReport,
)


class ProviderError(Exception):
    retryable = False
    code = "PROVIDER_ERROR"

    def __init__(
        self, message: str, usage: UsageReport | None = None, detail_code: str | None = None
    ) -> None:
        super().__init__(message)
        self.usage = usage
        # A stable, specific reason the member sees (e.g. WRONG_LANGUAGE); optional.
        self.detail_code = detail_code


class TransientProviderError(ProviderError):
    retryable = True
    code = "TRANSIENT_PROVIDER_ERROR"


class PermanentProviderError(ProviderError):
    retryable = False
    code = "PERMANENT_PROVIDER_ERROR"


class ProviderAdapter(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    async def estimate_cents(self, request: ExecutionRequest) -> int: ...

    def execute(self, request: ExecutionRequest) -> AsyncIterator[ProgressEvent | ExecutionResult]:
        """Yields zero or more ProgressEvent items followed by exactly one ExecutionResult."""
        ...

    async def cancel(self, attempt_id: str) -> None: ...

    async def chat(self, invocation: ChatInvocation) -> ChatResult: ...
