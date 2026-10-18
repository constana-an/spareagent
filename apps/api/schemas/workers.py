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


class LeaseTask(BaseModel):
    task_id: uuid.UUID
    task_type: str
    repository_url: str
    commit_sha: str
    prompt: str
    max_duration_seconds: int
    max_budget_cents: int


class LeaseAssignment(BaseModel):
    lease_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_number: int
    leased_until: datetime
    lease_seconds: int
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

    kind: Literal["PATCH", "TEST_REPORT", "LOG", "SUMMARY"]
    name: SafeName
    media_type: Literal["text/plain", "text/x-diff", "application/json"]
    content_base64: Annotated[StrictStr, Field(max_length=8 * 1024 * 1024)]


WorkerErrorCode = Literal[
    "TRANSIENT_PROVIDER_ERROR",
    "PERMANENT_PROVIDER_ERROR",
    "NO_RESULT",
    "WORKER_SHUTDOWN",
    "CANCELLED",
    "EXECUTION_ERROR",
]


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
