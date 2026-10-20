"""Experimental shared-subscription policy - a SEPARATE authority from
`services.scheduler.policy.ResourceEligibilityPolicy`, which is unchanged: personal
subscriptions remain ineligible for PUBLIC_MARKETPLACE and the gateway, always.

This checker only ever answers for scope EXPERIMENTAL_SHARED_SUBSCRIPTION. It is a pure
function over a snapshot so every reason can be audited and unit-tested. Every condition
must hold; any failure denies.

LIVE mode (a participant's real Codex CLI runs another member's task) is Milestone D. It is
limited to the scope OpenAI Support confirmed in writing (ticket 10766658): the official
Codex CLI (`codex exec`) on the owner's own computer with their own ChatGPT login, a relay
server that never handles credentials, at most `max_participants` (5) members, no
purchased credits consumed by another member's task, and review within 6 months
(`expires_at`). On top of every SIMULATED condition, LIVE additionally requires:

* the operator switch EXPERIMENT_LIVE_ENABLED;
* the authorization to be OPERATOR_VERIFIED, route-covered for CODEX_CLI_EXEC, unexpired;
* the group to fit inside the authorized participant cap;
* the owner's recorded `codex-check` pass and no-purchased-credits attestation;
* a node that advertises the Codex subscription executor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

EXPERIMENTAL_SCOPE = "EXPERIMENTAL_SHARED_SUBSCRIPTION"
PERSONAL_SUBSCRIPTION = "PERSONAL_SUBSCRIPTION"

# The only technical route covered by the written confirmation (ticket 10766658).
AUTHORIZED_ROUTE = "CODEX_CLI_EXEC"
CODEX_EXECUTOR = "codex-subscription"


LEGACY_NODE_TOOLS = frozenset({"code", "git"})  # mirrors services.tasks.kinds


class Mode(StrEnum):
    SIMULATED = "SIMULATED"
    LIVE = "LIVE"


class Reason(StrEnum):
    EXPERIMENT_DISABLED = "EXPERIMENT_DISABLED"
    GROUP_DISABLED = "GROUP_DISABLED"
    GROUP_SHARING_DISABLED = "GROUP_SHARING_DISABLED"
    AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"
    AUTHORIZATION_NOT_VERIFIED = "AUTHORIZATION_NOT_VERIFIED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    INTEGRATION_ROUTE_NOT_COVERED = "INTEGRATION_ROUTE_NOT_COVERED"
    LIVE_EXECUTION_DISABLED = "LIVE_EXECUTION_DISABLED"
    GROUP_EXCEEDS_AUTHORIZED_SIZE = "GROUP_EXCEEDS_AUTHORIZED_SIZE"
    OWNER_CHECK_MISSING = "OWNER_CHECK_MISSING"
    CREDITS_ATTESTATION_MISSING = "CREDITS_ATTESTATION_MISSING"
    NODE_NOT_CODEX_CAPABLE = "NODE_NOT_CODEX_CAPABLE"
    REQUESTER_NOT_MEMBER = "REQUESTER_NOT_MEMBER"
    OWNER_NOT_MEMBER = "OWNER_NOT_MEMBER"
    OWNER_NOT_OPTED_IN = "OWNER_NOT_OPTED_IN"
    OWNER_PAUSED = "OWNER_PAUSED"
    OWNER_REVOKED = "OWNER_REVOKED"
    OWNER_APPROVAL_MISSING = "OWNER_APPROVAL_MISSING"
    OWNER_REJECTED = "OWNER_REJECTED"
    RUNTIME_NOT_OWNER_CONTROLLED = "RUNTIME_NOT_OWNER_CONTROLLED"
    CONNECTION_INACTIVE = "CONNECTION_INACTIVE"
    SUBSCRIPTION_AUTH_INVALID = "SUBSCRIPTION_AUTH_INVALID"
    PROVIDER_OFFLINE = "PROVIDER_OFFLINE"
    CONCURRENCY_LIMIT = "CONCURRENCY_LIMIT"
    DAILY_LIMIT = "DAILY_LIMIT"
    DURATION_LIMIT = "DURATION_LIMIT"
    TASK_TYPE_NOT_ALLOWED = "TASK_TYPE_NOT_ALLOWED"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    PAID_CREDITS_FORBIDDEN = "PAID_CREDITS_FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    TASK_KIND_DISABLED = "TASK_KIND_DISABLED"
    TASK_KIND_NOT_OPEN = "TASK_KIND_NOT_OPEN"
    NODE_LACKS_CAPABILITY = "NODE_LACKS_CAPABILITY"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"


# Reasons that can clear without anyone changing the task (wait, keep QUEUED). Everything
# else is final for this task and fails it with EXPERIMENT_POLICY_DENIED.
TRANSIENT_REASONS = frozenset(
    {
        Reason.OWNER_PAUSED,
        Reason.OWNER_APPROVAL_MISSING,
        Reason.PROVIDER_OFFLINE,
        Reason.CONCURRENCY_LIMIT,
        Reason.DAILY_LIMIT,
        Reason.RATE_LIMITED,
        Reason.GROUP_SHARING_DISABLED,
        Reason.SUBSCRIPTION_AUTH_INVALID,
        Reason.NODE_NOT_CODEX_CAPABLE,  # the owner's device may restart with the executor
        Reason.NODE_LACKS_CAPABILITY,  # e.g. the owner installs the data tools
        Reason.TOOL_NOT_ALLOWED,  # the owner may allow the tool again
    }
)


@dataclass(frozen=True)
class AuthorizationSnapshot:
    id: uuid.UUID
    verification_status: str
    integration_route_covered: bool
    expires_at: datetime
    revoked_at: datetime | None
    scope_task_types: frozenset[str] | None = None  # None = not restricted by the record
    integration_route: str | None = None
    max_participants: int | None = None


@dataclass(frozen=True)
class DispatchSnapshot:
    now: datetime
    experiment_enabled: bool
    task_scope: str
    task_type: str
    task_max_duration_seconds: int
    requester_id: uuid.UUID
    owner_id: uuid.UUID
    # group
    group_status: str
    group_sharing_enabled: bool
    group_allowed_task_types: frozenset[str]
    group_max_task_duration_seconds: int
    group_max_concurrent_tasks: int
    group_max_tasks_per_day: int
    active_member_ids: frozenset[uuid.UUID]
    authorization: AuthorizationSnapshot | None
    # owner preferences / consent
    sharing_state: str
    consent_accepted: bool
    allow_paid_credits: bool
    owner_allowed_task_types: frozenset[str]
    owner_max_task_duration_seconds: int
    owner_max_concurrent_tasks: int
    owner_max_tasks_per_day: int
    task_consent_status: str  # PENDING / APPROVED / REJECTED / WITHDRAWN
    # runtime
    resource_type: str
    resource_owner_id: uuid.UUID
    resource_public_eligible: bool
    connection_status: str
    connection_auth_state: str
    node_revoked: bool
    node_online: bool
    # usage (counted by the platform, never claimed by the node)
    active_tasks_on_connection: int
    tasks_started_last_24h: int
    rate_limited_until: datetime | None = None
    # LIVE-only inputs
    live_enabled: bool = False
    group_member_count: int = 0  # INVITED + ACTIVE
    group_max_members: int = 0
    owner_check_passed: bool = False
    no_purchased_credits: bool = False
    node_executor: str | None = None
    # 0011: the task kind's opening status (only OPEN kinds run LIVE)
    kind_status: str = "OPEN"
    # 0013: tools the kind needs, tools the device declared (None: a pre-0013 node that
    # declares none and is treated as code-capable) and tools the owner allows.
    required_tools: frozenset[str] = frozenset()
    node_tools: frozenset[str] | None = None
    owner_allowed_tools: frozenset[str] = frozenset({"code", "doc", "git", "python-data"})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    mode: Mode
    reasons: tuple[Reason, ...] = field(default_factory=tuple)

    @property
    def transient(self) -> bool:
        return not self.allowed and all(r in TRANSIENT_REASONS for r in self.reasons)


def authorization_reasons(
    auth: AuthorizationSnapshot | None, now: datetime, *, mode: Mode
) -> list[Reason]:
    if auth is None:
        return [Reason.AUTHORIZATION_MISSING]
    reasons = []
    if auth.revoked_at is not None:
        reasons.append(Reason.AUTHORIZATION_REVOKED)
    if auth.verification_status != "OPERATOR_VERIFIED":
        reasons.append(Reason.AUTHORIZATION_NOT_VERIFIED)
    if auth.expires_at <= now:
        reasons.append(Reason.AUTHORIZATION_EXPIRED)
    if mode == Mode.LIVE and not (
        auth.integration_route_covered and auth.integration_route == AUTHORIZED_ROUTE
    ):
        reasons.append(Reason.INTEGRATION_ROUTE_NOT_COVERED)
    return reasons


def live_reasons(s: DispatchSnapshot) -> list[Reason]:
    r = []
    if not s.live_enabled:
        r.append(Reason.LIVE_EXECUTION_DISABLED)
    cap = s.authorization.max_participants if s.authorization else None
    if cap is None or s.group_max_members > cap or s.group_member_count > cap:
        r.append(Reason.GROUP_EXCEEDS_AUTHORIZED_SIZE)
    if not s.owner_check_passed:
        r.append(Reason.OWNER_CHECK_MISSING)
    if not s.no_purchased_credits:
        r.append(Reason.CREDITS_ATTESTATION_MISSING)
    if s.node_executor != CODEX_EXECUTOR:
        r.append(Reason.NODE_NOT_CODEX_CAPABLE)
    return r


class ExperimentalSharingPolicy:
    @staticmethod
    def evaluate(s: DispatchSnapshot, *, mode: Mode = Mode.SIMULATED) -> Decision:
        r: list[Reason] = []
        if mode == Mode.LIVE:
            r += live_reasons(s)
        if not s.experiment_enabled:
            r.append(Reason.EXPERIMENT_DISABLED)
        if s.task_scope != EXPERIMENTAL_SCOPE:
            r.append(Reason.SCOPE_MISMATCH)
        if s.group_status != "ACTIVE":
            r.append(Reason.GROUP_DISABLED)
        elif not s.group_sharing_enabled:
            r.append(Reason.GROUP_SHARING_DISABLED)
        r += authorization_reasons(s.authorization, s.now, mode=mode)
        if s.requester_id not in s.active_member_ids:
            r.append(Reason.REQUESTER_NOT_MEMBER)
        if s.owner_id not in s.active_member_ids:
            r.append(Reason.OWNER_NOT_MEMBER)

        if s.sharing_state == "REVOKED":
            r.append(Reason.OWNER_REVOKED)
        elif s.sharing_state == "PAUSED":
            r.append(Reason.OWNER_PAUSED)
        elif s.sharing_state != "ENABLED" or not s.consent_accepted:
            r.append(Reason.OWNER_NOT_OPTED_IN)
        if s.allow_paid_credits:
            r.append(Reason.PAID_CREDITS_FORBIDDEN)
        if s.task_consent_status == "REJECTED":
            r.append(Reason.OWNER_REJECTED)
        elif s.task_consent_status != "APPROVED":
            r.append(Reason.OWNER_APPROVAL_MISSING)

        if (
            s.resource_type != PERSONAL_SUBSCRIPTION
            or s.resource_owner_id != s.owner_id
            or s.resource_public_eligible
            or s.node_revoked
        ):
            r.append(Reason.RUNTIME_NOT_OWNER_CONTROLLED)
        if s.connection_status != "CONNECTED":
            r.append(Reason.CONNECTION_INACTIVE)
        elif s.connection_auth_state != "VALID":
            r.append(Reason.SUBSCRIPTION_AUTH_INVALID)
        if not s.node_online:
            r.append(Reason.PROVIDER_OFFLINE)

        if s.kind_status == "DISABLED":
            r.append(Reason.TASK_KIND_DISABLED)
        elif mode == Mode.LIVE and s.kind_status != "OPEN":
            r.append(Reason.TASK_KIND_NOT_OPEN)
        if mode == Mode.LIVE:
            available = s.node_tools if s.node_tools is not None else LEGACY_NODE_TOOLS
            if s.required_tools - available:
                r.append(Reason.NODE_LACKS_CAPABILITY)
            if s.required_tools - s.owner_allowed_tools:
                r.append(Reason.TOOL_NOT_ALLOWED)
        allowed_types = s.group_allowed_task_types & s.owner_allowed_task_types
        # The authorization's task types bound subscription use, i.e. LIVE runs; a
        # SIMULATED run consumes no subscription and is bounded by group/owner settings.
        if (
            mode == Mode.LIVE
            and s.authorization is not None
            and s.authorization.scope_task_types is not None
        ):
            allowed_types &= s.authorization.scope_task_types
        if s.task_type not in allowed_types:
            r.append(Reason.TASK_TYPE_NOT_ALLOWED)
        if s.task_max_duration_seconds > min(
            s.group_max_task_duration_seconds, s.owner_max_task_duration_seconds
        ):
            r.append(Reason.DURATION_LIMIT)
        if s.active_tasks_on_connection >= min(
            s.group_max_concurrent_tasks, s.owner_max_concurrent_tasks
        ):
            r.append(Reason.CONCURRENCY_LIMIT)
        if s.tasks_started_last_24h >= min(s.group_max_tasks_per_day, s.owner_max_tasks_per_day):
            r.append(Reason.DAILY_LIMIT)
        if s.rate_limited_until is not None and s.rate_limited_until > s.now:
            r.append(Reason.RATE_LIMITED)
        return Decision(allowed=not r, mode=mode, reasons=tuple(dict.fromkeys(r)))
