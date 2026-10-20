from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

SafeName = Annotated[StrictStr, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")]


class WorkerRegistration(BaseModel):
    # extra="forbid": a node cannot smuggle eligibility/approval fields into registration.
    model_config = ConfigDict(extra="forbid")

    resource_id: uuid.UUID
    name: SafeName
    capabilities: dict[str, Any]
    max_concurrency: Annotated[StrictInt, Field(ge=1, le=100)] = 1


class RegistrationOut(BaseModel):
    worker_id: uuid.UUID
    node_key: str
    node_key_expires_at: datetime
    access_token: str
    access_token_expires_at: datetime


class AccessTokenOut(BaseModel):
    worker_id: uuid.UUID
    access_token: str
    expires_at: datetime


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ONLINE", "DRAINING", "OFFLINE"] = "ONLINE"
    capabilities: dict[str, Any] | None = None


class WorkerSelf(BaseModel):
    worker_id: uuid.UUID
    resource_id: uuid.UUID
    name: str
    status: str
    max_concurrency: int
    active_tasks: int
    last_heartbeat: datetime | None


class LeaseInput(BaseModel):
    """An attachment the node fetches with GET /v1/leases/{lease_id}/inputs/{input_id}."""

    input_id: uuid.UUID
    filename: str
    size_bytes: int
    sha256: str
    media_type: str


class LeaseTask(BaseModel):
    task_id: uuid.UUID
    task_type: str
    kind: str
    repository_url: str | None
    commit_sha: str | None
    prompt: str
    max_duration_seconds: int
    max_budget_cents: int
    deliverable: dict[str, str] | None = None
    parameters: dict[str, str] = Field(default_factory=dict)
    inputs: list[LeaseInput] = Field(default_factory=list)


class LeaseAssignment(BaseModel):
    lease_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_number: int
    leased_until: datetime
    lease_seconds: int
    execution_profile: Literal["MOCK", "CODE", "CODEX_LIVE"] = "MOCK"
    # W4: experimental leases name their scope and the authorization record they rely on.
    resource_scope: Literal["PUBLIC_MARKETPLACE", "EXPERIMENTAL_SHARED_SUBSCRIPTION"] = (
        "PUBLIC_MARKETPLACE"
    )
    authorization_ref: uuid.UUID | None = None
    task: LeaseTask


class ProgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[StrictStr, Field(max_length=500)]
    percent: Annotated[StrictInt, Field(ge=0, le=100)]


class LeaseStateOut(BaseModel):
    leased_until: datetime | None
    cancel_requested: bool


class ArtifactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # VERIFICATION_REPORT is platform-only and can never be submitted by a node.
    kind: Literal["PATCH", "TEST_REPORT", "LOG", "SUMMARY", "EXECUTION_LOG", "DELIVERABLE"]
    name: SafeName
    # Deliverable types are listed in packages/common/deliverables.py MEDIA_TYPES.
    media_type: Literal[
        "text/plain",
        "text/x-diff",
        "application/json",
        "text/markdown",
        "text/csv",
        "image/png",
        "image/svg+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]
    content_base64: Annotated[StrictStr, Field(max_length=8 * 1024 * 1024)]


WorkerErrorCode = Literal[
    "TRANSIENT_PROVIDER_ERROR",
    "PERMANENT_PROVIDER_ERROR",
    "NO_RESULT",
    "WORKER_SHUTDOWN",
    "CANCELLED",
    "EXECUTION_ERROR",
    # W4 owner-device Codex runtime outcomes (never retried; see D-52).
    "SUBSCRIPTION_USAGE_LIMIT",
    "SUBSCRIPTION_AUTH_UNAVAILABLE",
    "OWNER_ONLY_VIOLATION",
    "SHARED_WORK_REFUSED",
]


class ObservedCodexUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: Annotated[StrictInt, Field(ge=0, le=10**9)]
    output_tokens: Annotated[StrictInt, Field(ge=0, le=10**9)]
    cached_input_tokens: Annotated[StrictInt, Field(ge=0, le=10**9)] = 0
    reasoning_output_tokens: Annotated[StrictInt, Field(ge=0, le=10**9)] = 0


class LeaseCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finalization_key: Annotated[
        StrictStr, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    ]
    succeeded: StrictBool
    result_summary: Annotated[StrictStr, Field(max_length=2000)] | None = None
    retryable: StrictBool = False
    error_code: WorkerErrorCode | None = None
    timed_out: StrictBool = False
    artifacts: Annotated[list[ArtifactIn], Field(max_length=8)] = []
    observed_usage: ObservedCodexUsage | None = None
    reset_hint: Annotated[StrictStr, Field(max_length=80)] | None = None
    failure_detail: Annotated[StrictStr, Field(pattern=r"^[A-Z][A-Z_]{1,39}$")] | None = None


class CompletionOut(BaseModel):
    task_status: str
    replayed: bool


class EnrollmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: uuid.UUID
    ttl_seconds: Annotated[StrictInt, Field(ge=60, le=7 * 86400)] = 3600


class EnrollmentOut(BaseModel):
    id: uuid.UUID
    token: str
    resource_id: uuid.UUID
    expires_at: datetime


class WorkerRevocationOut(BaseModel):
    worker_id: uuid.UUID
    revoked: bool
    leases_expired: int


class ResourceApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    license_reference: str | None = Field(default=None, max_length=500)


class ResourceOut(BaseModel):
    id: uuid.UUID
    resource_type: str
    authorization_status: str
    public_eligible: bool
