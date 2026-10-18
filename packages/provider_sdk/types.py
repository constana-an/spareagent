from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ResourceType = Literal["MANAGED_API", "LICENSED_PROVIDER", "SELF_HOSTED", "PERSONAL_SUBSCRIPTION"]
ArtifactKind = Literal["PATCH", "TEST_REPORT", "LOG", "SUMMARY"]


@dataclass(frozen=True)
class ExecutionRequest:
    task_id: str
    attempt_id: str
    user_id: str
    resource_scope: Literal["PUBLIC_MARKETPLACE", "OWNER_ONLY", "EXPERIMENTAL_SHARED_SUBSCRIPTION"]
    prompt: str
    repository_url: str | None
    commit_sha: str | None
    max_duration_seconds: int
    max_budget_cents: int
    # Extension to the architecture interface: lets deterministic adapters vary per attempt.
    attempt_number: int = 1
    task_type: str = "CODE_FIX"


@dataclass(frozen=True)
class ProviderCapabilities:
    resource_type: ResourceType
    supports_code: bool
    supports_chat: bool
    supports_stream: bool
    max_concurrency: int


@dataclass(frozen=True)
class UsageReport:
    provider_name: str
    provider_request_id: str
    model_name: str
    input_tokens: int
    output_tokens: int
    estimated_cost_cents: int
    billable: bool = False


@dataclass(frozen=True)
class ArtifactPayload:
    kind: ArtifactKind
    name: str
    media_type: str
    content: bytes


@dataclass(frozen=True)
class ProgressEvent:
    message: str
    percent: int


@dataclass(frozen=True)
class ExecutionResult:
    summary: str
    artifacts: tuple[ArtifactPayload, ...]
    usage: UsageReport


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ChatInvocation:
    physical_model: str
    messages: tuple[ChatMessage, ...]
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class ChatResult:
    provider_request_id: str
    content: str
    finish_reason: str
    usage: UsageReport
    extra: dict[str, str] = field(default_factory=dict)
