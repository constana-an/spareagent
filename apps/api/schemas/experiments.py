"""Week 4 experimental-scope request/response models.

Every request model forbids unknown fields, so a client can never smuggle credentials
(access/refresh tokens, cookies, API keys, passwords) into the server: such fields are
rejected with 422 before any handler runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from apps.api.schemas.tasks import TaskCreate

TaskType = Literal["CODE_REVIEW", "WRITE_TESTS", "CODE_FIX"]
Limit = Annotated[StrictInt, Field(ge=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ groups / operator
class GroupCreate(Strict):
    name: Annotated[StrictStr, Field(min_length=1, max_length=100)]
    max_members: Annotated[StrictInt, Field(ge=2, le=20)] | None = None
    max_concurrent_tasks: Annotated[StrictInt, Field(ge=1, le=4)] | None = None
    max_tasks_per_day: Annotated[StrictInt, Field(ge=1, le=50)] | None = None
    max_task_duration_seconds: Annotated[StrictInt, Field(ge=10, le=3600)] | None = None
    allowed_task_types: list[TaskType] | None = Field(default=None, min_length=1)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    status: str
    invited_at: datetime
    joined_at: datetime | None


class AuthorizationOut(BaseModel):
    id: uuid.UUID
    provider_name: str
    authorization_source: str
    authorization_reference: str
    authorization_scope: dict[str, Any]
    verification_status: str
    integration_route_covered: bool
    verified_by: uuid.UUID | None
    verified_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime
    integration_route: str | None = None
    max_participants: int | None = None
    # True only when this record would currently permit LIVE dispatch (verified, covered,
    # unexpired, not revoked) AND the operator switch EXPERIMENT_LIVE_ENABLED is on.
    live_execution_enabled: bool = False


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    sharing_enabled: bool
    max_members: int
    max_concurrent_tasks: int
    max_tasks_per_day: int
    max_task_duration_seconds: int
    allowed_task_types: list[str]
    created_at: datetime
    members: list[MemberOut] = []
    authorization: AuthorizationOut | None = None


class Invite(Strict):
    user_id: uuid.UUID


class Consent(Strict):
    terms_version: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    acknowledgements: dict[StrictStr, StrictBool]


class AuthorizationCreate(Strict):
    provider_name: Literal["openai-codex"]
    authorization_source: Literal["VENDOR_SUPPORT_CORRESPONDENCE", "VENDOR_WRITTEN_AGREEMENT"]
    # An opaque pointer (ticket number, private file id) - never the correspondence text.
    authorization_reference: Annotated[
        StrictStr, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9 ._:#/-]+$")
    ]
    authorization_scope: dict[str, Any] = {}
    # The technical route and participant cap the vendor confirmed (e.g. ticket 10766658:
    # official Codex CLI `codex exec`, at most 5 participants).
    integration_route: Literal["CODEX_CLI_EXEC"] | None = None
    max_participants: Annotated[StrictInt, Field(ge=2, le=20)] | None = None
    expires_at: datetime


class AuthorizationVerify(Strict):
    verification_status: Literal["OPERATOR_VERIFIED", "REJECTED"]
    integration_route_covered: StrictBool = False


class GroupSharing(Strict):
    enabled: StrictBool


class AuditOut(BaseModel):
    id: int
    task_id: uuid.UUID
    connection_id: uuid.UUID | None
    authorization_id: uuid.UUID | None
    decision: str
    reasons: list[str]
    execution_mode: str
    attempt_id: uuid.UUID | None
    created_at: datetime


# ------------------------------------------------------------------ subscriptions / sharing
class SubscriptionConnect(Strict):
    worker_id: uuid.UUID
    device_label: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    subscription_type: Literal["PLUS", "PRO", "UNKNOWN"] = "UNKNOWN"
    # As reported by `spareagent-worker codex-check` on the owner's device.
    auth_method: Literal["CHATGPT", "API_KEY", "NOT_LOGGED_IN", "UNKNOWN"]


class ConnectionOut(BaseModel):
    id: uuid.UUID
    worker_id: uuid.UUID
    provider_name: str
    subscription_type: str
    auth_method: str
    auth_state: str
    device_label: str
    status: str
    last_auth_check_at: datetime | None
    created_at: datetime
    owner_check_passed_at: datetime | None = None
    no_purchased_credits_at: datetime | None = None


class SubscriptionAttest(Strict):
    """Owner statements required before LIVE shared tasks can reach this connection."""

    connection_id: uuid.UUID
    # The `result` field printed by `spareagent-worker codex-check` on the owner's device.
    codex_check_result: Literal[
        "OWNER_ONLY_SUBSCRIPTION_RUN_OK", "AUTH_OK_INFERENCE_SKIPPED", "FAILED_CLOSED", "FAILED"
    ]
    no_purchased_credits: StrictBool
    no_auto_reload: StrictBool


class DeviceEnrollmentCreate(Strict):
    ttl_seconds: Annotated[StrictInt, Field(ge=60, le=86400)] = 3600


class DeviceEnrollmentOut(BaseModel):
    resource_id: uuid.UUID
    token_id: uuid.UUID
    token: str
    expires_at: datetime


class PreferencesIn(Strict):
    connection_id: uuid.UUID
    group_id: uuid.UUID
    max_concurrent_tasks: Annotated[StrictInt, Field(ge=1, le=4)] | None = None
    max_tasks_per_day: Annotated[StrictInt, Field(ge=0, le=50)] | None = None
    max_task_duration_seconds: Annotated[StrictInt, Field(ge=10, le=3600)] | None = None
    allowed_task_types: list[TaskType] | None = Field(default=None, min_length=1)
    require_owner_approval: StrictBool | None = None
    advance_consent_requester_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    # Present only so a client asking for it gets an explicit refusal.
    allow_paid_credits: Literal[False] = False


class PreferencesOut(BaseModel):
    connection_id: uuid.UUID
    group_id: uuid.UUID
    sharing_state: str
    consent_terms_version: str | None
    consent_accepted_at: datetime | None
    max_concurrent_tasks: int
    max_tasks_per_day: int
    max_task_duration_seconds: int
    allowed_task_types: list[str]
    require_owner_approval: bool
    advance_consent_requester_ids: list[str]
    allow_paid_credits: bool
    auto_approve_members: bool = False
    paused_at: datetime | None
    revoked_at: datetime | None


class SharingEnable(Consent):
    connection_id: uuid.UUID


class SharingTarget(Strict):
    connection_id: uuid.UUID


class SubscriptionStatusItem(BaseModel):
    connection: ConnectionOut
    worker_status: str
    last_heartbeat: datetime | None
    preferences: PreferencesOut | None


class SubscriptionStatus(BaseModel):
    items: list[SubscriptionStatusItem]


class UsageOut(BaseModel):
    connection_id: uuid.UUID
    subscription_type: str
    auth_state: str
    active_sessions: int
    tasks_completed: int
    tasks_failed: int
    throttling_errors: int
    auth_errors: int
    total_duration_ms: int
    observed_input_tokens: int
    observed_output_tokens: int
    observed_cached_input_tokens: int
    observed_reasoning_output_tokens: int
    token_source: str
    tasks_started_last_24h: int
    local_limits: dict[str, int] | None
    plan_remaining: str
    plan_limits: str
    capacity_state: str
    reset_hint: str | None
    rate_limited_until: datetime | None
    last_observation_at: datetime | None
    last_observation_age_seconds: int | None


class UsageList(BaseModel):
    items: list[UsageOut]


# ------------------------------------------------------------------ experimental tasks
class NodeOut(BaseModel):
    connection_id: uuid.UUID
    owner_id: uuid.UUID
    device_label: str
    auth_state: str
    online: bool
    allowed_task_types: list[str]
    max_task_duration_seconds: int
    requires_approval: bool


class NodeList(BaseModel):
    items: list[NodeOut]


class ExperimentalTaskCreate(TaskCreate):
    """Reuses the public task validation (repository URL, SHA, prompt); no budget field."""

    task_type: TaskType  # type: ignore[assignment]
    max_budget_cents: None = None  # type: ignore[assignment]
    max_duration_seconds: Annotated[StrictInt, Field(ge=10, le=3600)]
    group_id: uuid.UUID
    connection_id: uuid.UUID
    # LIVE runs on the owner's own Codex CLI and needs every Milestone D gate.
    execution_mode: Literal["SIMULATED", "LIVE"] = "SIMULATED"


class DecisionOut(BaseModel):
    decision: str
    reasons: list[str]
    execution_mode: str
    created_at: datetime


class ExperimentalTaskOut(BaseModel):
    id: uuid.UUID
    status: str
    task_type: str
    resource_scope: str
    execution_mode: Literal["SIMULATED", "LIVE"] = "SIMULATED"
    group_id: uuid.UUID
    connection_id: uuid.UUID
    requester_id: uuid.UUID
    owner_id: uuid.UUID
    repository_url: str
    commit_sha: str
    prompt: str
    max_duration_seconds: int
    verification_status: str = "NOT_RUN"
    approval_status: str
    approval_source: str | None
    last_decision: DecisionOut | None
    created_at: datetime
    result_summary: str | None = None


class ExperimentalTaskList(BaseModel):
    items: list[ExperimentalTaskOut]


class KillSwitchOut(BaseModel):
    groups_disabled: int
    tasks_cancelled: int
