"""One-click sharing request/response models. Requests forbid unknown fields."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr

from apps.api.schemas.experiments import Strict, TaskType

Tool = Literal["code", "doc", "git", "python-data", "network"]  # services.tasks.kinds.TOOLS

Acks = dict[str, StrictBool]


class SignupIn(Strict):
    signup_code: Annotated[StrictStr, Field(min_length=16, max_length=256)]
    device_label: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    terms_version: StrictStr
    acknowledgements: Acks


class SignupOut(BaseModel):
    user_id: uuid.UUID
    group_id: uuid.UUID
    resource_id: uuid.UUID
    enrollment_token: str
    enrollment_expires_at: datetime


class OnlineIn(Strict):
    device_label: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    subscription_type: Literal["PLUS", "PRO", "UNKNOWN"] = "PLUS"
    terms_version: StrictStr
    acknowledgements: Acks
    # The `result` printed by the owner-only check that the worker app just ran.
    codex_check_result: Literal[
        "OWNER_ONLY_SUBSCRIPTION_RUN_OK", "AUTH_OK_INFERENCE_SKIPPED", "FAILED_CLOSED", "FAILED"
    ]
    no_purchased_credits: StrictBool
    no_auto_reload: StrictBool
    auto_approve_members: StrictBool = True


class SettingsIn(Strict):
    max_members: Annotated[StrictInt, Field(ge=2, le=20)] | None = None
    max_tasks_per_day: Annotated[StrictInt, Field(ge=1, le=50)] | None = None
    max_task_duration_seconds: Annotated[StrictInt, Field(ge=60, le=3600)] | None = None
    auto_approve_members: StrictBool | None = None
    # 0013: the owner's device capabilities (design §5)
    max_concurrent_tasks: Annotated[StrictInt, Field(ge=1, le=3)] | None = None
    allowed_task_types: Annotated[list[TaskType], Field(min_length=1, max_length=10)] | None = None
    allowed_tools: Annotated[list[Tool], Field(max_length=10)] | None = None


class GroupSummary(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    sharing_enabled: bool
    max_members: int
    member_cap: int
    max_tasks_per_day: int
    max_task_duration_seconds: int
    max_concurrent_tasks: int = 1
    has_invite_code: bool
    invite_code_rotated_at: datetime | None


class MemberSummary(BaseModel):
    user_id: uuid.UUID
    status: str
    joined_at: datetime | None
    is_owner: bool


class SharingSummary(BaseModel):
    connection_id: uuid.UUID
    state: str
    auto_approve_members: bool
    max_concurrent_tasks: int = 1
    allowed_task_types: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class KindSummary(BaseModel):
    kind: str
    name_zh: str
    name_en: str
    status: str
    required_tools: list[str]
    offered: bool


class TaskSummary(BaseModel):
    id: uuid.UUID
    status: str
    task_type: str
    execution_mode: str
    requester_id: uuid.UUID
    approval_status: str
    approval_source: str | None
    created_at: datetime
    started_at: datetime | None = None
    progress: dict | None = None
    queue_position: int | None = None
    failure: dict | None = None
    max_duration_seconds: int | None = None


class UsageSummary(BaseModel):
    tasks_started_last_24h: int = 0
    execution_minutes_last_24h: float = 0
    running: int = 0
    queued: int = 0


class SharingStatusOut(BaseModel):
    group: GroupSummary
    members: list[MemberSummary]
    sharing: SharingSummary | None
    device_tools: list[str] = Field(default_factory=list)
    kinds: list[KindSummary] = Field(default_factory=list)
    live_ready: bool
    live_blockers: list[str]
    tasks_started_last_24h: int
    tasks: list[TaskSummary]
    usage: UsageSummary = Field(default_factory=UsageSummary)
    dashboard_url: str
    # Only returned when a new code was just issued; only its digest is stored.
    invite_code: str | None = None


class DashboardLoginOut(BaseModel):
    token: str
    expires_at: datetime
    dashboard_url: str


class JoinCodeIn(Strict):
    code: Annotated[StrictStr, Field(min_length=12, max_length=32)]
    terms_version: StrictStr
    acknowledgements: Acks
