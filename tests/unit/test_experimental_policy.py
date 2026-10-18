"""ExperimentalSharingPolicy: every condition is required; LIVE adds the Milestone D gates."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from services.scheduler.policy import ResourceEligibilityPolicy
from services.subscriptions.policy import (
    AuthorizationSnapshot,
    DispatchSnapshot,
    ExperimentalSharingPolicy,
    Mode,
    Reason,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
OWNER, REQUESTER = uuid.uuid4(), uuid.uuid4()
TYPES = frozenset({"CODE_REVIEW", "WRITE_TESTS", "CODE_FIX"})


def snap(**overrides) -> DispatchSnapshot:
    base = DispatchSnapshot(
        now=NOW,
        experiment_enabled=True,
        task_scope="EXPERIMENTAL_SHARED_SUBSCRIPTION",
        task_type="CODE_REVIEW",
        task_max_duration_seconds=300,
        requester_id=REQUESTER,
        owner_id=OWNER,
        group_status="ACTIVE",
        group_sharing_enabled=True,
        group_allowed_task_types=TYPES,
        group_max_task_duration_seconds=900,
        group_max_concurrent_tasks=1,
        group_max_tasks_per_day=5,
        active_member_ids=frozenset({OWNER, REQUESTER}),
        authorization=AuthorizationSnapshot(
            id=uuid.uuid4(),
            verification_status="OPERATOR_VERIFIED",
            integration_route_covered=True,
            expires_at=NOW + timedelta(days=30),
            revoked_at=None,
            integration_route="CODEX_CLI_EXEC",
            max_participants=5,
        ),
        sharing_state="ENABLED",
        consent_accepted=True,
        allow_paid_credits=False,
        owner_allowed_task_types=TYPES,
        owner_max_task_duration_seconds=900,
        owner_max_concurrent_tasks=1,
        owner_max_tasks_per_day=5,
        task_consent_status="APPROVED",
        resource_type="PERSONAL_SUBSCRIPTION",
        resource_owner_id=OWNER,
        resource_public_eligible=False,
        connection_status="CONNECTED",
        connection_auth_state="VALID",
        node_revoked=False,
        node_online=True,
        active_tasks_on_connection=0,
        tasks_started_last_24h=0,
        live_enabled=True,
        group_member_count=2,
        group_max_members=5,
        owner_check_passed=True,
        no_purchased_credits=True,
        node_executor="codex-subscription",
    )
    return dataclasses.replace(base, **overrides)


def auth(**kw) -> AuthorizationSnapshot:
    return dataclasses.replace(snap().authorization, **kw)


def test_fully_consented_simulated_dispatch_is_allowed():
    d = ExperimentalSharingPolicy.evaluate(snap())
    assert d.allowed and d.mode == Mode.SIMULATED and d.reasons == ()


def test_live_allowed_only_when_every_milestone_d_gate_holds():
    d = ExperimentalSharingPolicy.evaluate(snap(), mode=Mode.LIVE)
    assert d.allowed and d.mode == Mode.LIVE


@pytest.mark.parametrize(
    ("overrides", "reason", "transient"),
    [
        ({"live_enabled": False}, Reason.LIVE_EXECUTION_DISABLED, False),
        ({"authorization": "uncovered"}, Reason.INTEGRATION_ROUTE_NOT_COVERED, False),
        ({"authorization": "other_route"}, Reason.INTEGRATION_ROUTE_NOT_COVERED, False),
        ({"authorization": "no_cap"}, Reason.GROUP_EXCEEDS_AUTHORIZED_SIZE, False),
        ({"group_max_members": 6}, Reason.GROUP_EXCEEDS_AUTHORIZED_SIZE, False),
        ({"group_member_count": 6}, Reason.GROUP_EXCEEDS_AUTHORIZED_SIZE, False),
        ({"owner_check_passed": False}, Reason.OWNER_CHECK_MISSING, False),
        ({"no_purchased_credits": False}, Reason.CREDITS_ATTESTATION_MISSING, False),
        ({"node_executor": None}, Reason.NODE_NOT_CODEX_CAPABLE, True),
    ],
)
def test_each_live_gate_is_required(overrides, reason, transient):
    special = overrides.get("authorization")
    if special == "uncovered":
        overrides = {"authorization": auth(integration_route_covered=False)}
    elif special == "other_route":
        overrides = {"authorization": auth(integration_route="APP_SERVER")}
    elif special == "no_cap":
        overrides = {"authorization": auth(max_participants=None)}
    d = ExperimentalSharingPolicy.evaluate(snap(**overrides), mode=Mode.LIVE)
    assert not d.allowed and reason in d.reasons and d.transient is transient
    # The same snapshot is still fine for the simulation: LIVE gates only apply to LIVE.
    assert ExperimentalSharingPolicy.evaluate(snap(**overrides)).allowed


@pytest.mark.parametrize(
    ("overrides", "reason", "transient"),
    [
        ({"experiment_enabled": False}, Reason.EXPERIMENT_DISABLED, False),
        ({"task_scope": "PUBLIC_MARKETPLACE"}, Reason.SCOPE_MISMATCH, False),
        ({"group_status": "DISABLED"}, Reason.GROUP_DISABLED, False),
        ({"group_sharing_enabled": False}, Reason.GROUP_SHARING_DISABLED, True),
        ({"authorization": None}, Reason.AUTHORIZATION_MISSING, False),
        (
            {"authorization": "unverified"},
            Reason.AUTHORIZATION_NOT_VERIFIED,
            False,
        ),
        ({"authorization": "expired"}, Reason.AUTHORIZATION_EXPIRED, False),
        ({"authorization": "revoked"}, Reason.AUTHORIZATION_REVOKED, False),
        ({"active_member_ids": frozenset({OWNER})}, Reason.REQUESTER_NOT_MEMBER, False),
        ({"active_member_ids": frozenset({REQUESTER})}, Reason.OWNER_NOT_MEMBER, False),
        ({"sharing_state": "DISABLED"}, Reason.OWNER_NOT_OPTED_IN, False),
        ({"consent_accepted": False}, Reason.OWNER_NOT_OPTED_IN, False),
        ({"sharing_state": "PAUSED"}, Reason.OWNER_PAUSED, True),
        ({"sharing_state": "REVOKED"}, Reason.OWNER_REVOKED, False),
        ({"allow_paid_credits": True}, Reason.PAID_CREDITS_FORBIDDEN, False),
        ({"task_consent_status": "PENDING"}, Reason.OWNER_APPROVAL_MISSING, True),
        ({"task_consent_status": "REJECTED"}, Reason.OWNER_REJECTED, False),
        ({"resource_type": "MANAGED_API"}, Reason.RUNTIME_NOT_OWNER_CONTROLLED, False),
        ({"resource_owner_id": uuid.uuid4()}, Reason.RUNTIME_NOT_OWNER_CONTROLLED, False),
        ({"resource_public_eligible": True}, Reason.RUNTIME_NOT_OWNER_CONTROLLED, False),
        ({"node_revoked": True}, Reason.RUNTIME_NOT_OWNER_CONTROLLED, False),
        ({"connection_status": "DISCONNECTED"}, Reason.CONNECTION_INACTIVE, False),
        ({"connection_auth_state": "EXPIRED"}, Reason.SUBSCRIPTION_AUTH_INVALID, True),
        ({"node_online": False}, Reason.PROVIDER_OFFLINE, True),
        ({"task_type": "REFACTOR"}, Reason.TASK_TYPE_NOT_ALLOWED, False),
        (
            {"owner_allowed_task_types": frozenset({"WRITE_TESTS"})},
            Reason.TASK_TYPE_NOT_ALLOWED,
            False,
        ),
        ({"task_max_duration_seconds": 1000}, Reason.DURATION_LIMIT, False),
        ({"owner_max_task_duration_seconds": 100}, Reason.DURATION_LIMIT, False),
        ({"active_tasks_on_connection": 1}, Reason.CONCURRENCY_LIMIT, True),
        ({"tasks_started_last_24h": 5}, Reason.DAILY_LIMIT, True),
        ({"owner_max_tasks_per_day": 0}, Reason.DAILY_LIMIT, True),
        ({"rate_limited_until": NOW + timedelta(minutes=5)}, Reason.RATE_LIMITED, True),
    ],
)
def test_each_condition_is_required(overrides, reason, transient):
    special = overrides.get("authorization")
    if special == "unverified":
        overrides = {"authorization": auth(verification_status="UNVERIFIED")}
    elif special == "expired":
        overrides = {"authorization": auth(expires_at=NOW - timedelta(seconds=1))}
    elif special == "revoked":
        overrides = {"authorization": auth(revoked_at=NOW)}
    d = ExperimentalSharingPolicy.evaluate(snap(**overrides))
    assert not d.allowed
    assert reason in d.reasons
    assert d.transient is transient


def test_authorization_scope_can_narrow_task_types():
    d = ExperimentalSharingPolicy.evaluate(
        snap(authorization=auth(scope_task_types=frozenset({"WRITE_TESTS"})))
    )
    assert Reason.TASK_TYPE_NOT_ALLOWED in d.reasons


def test_expired_rate_limit_cooldown_no_longer_blocks():
    assert ExperimentalSharingPolicy.evaluate(
        snap(rate_limited_until=NOW - timedelta(seconds=1))
    ).allowed


class _Res:
    def __init__(self, rtype, status="APPROVED", public=True):
        self.resource_type, self.authorization_status, self.public_eligible = rtype, status, public


def test_public_marketplace_policy_unchanged_for_personal_subscriptions():
    personal = _Res("PERSONAL_SUBSCRIPTION")
    for scope in ("PUBLIC_MARKETPLACE", "OWNER_ONLY", "EXPERIMENTAL_SHARED_SUBSCRIPTION"):
        assert not ResourceEligibilityPolicy.can_serve_task(personal, resource_scope=scope)
    assert not ResourceEligibilityPolicy.is_gateway_routable(personal)
    # The public policy never serves the experimental scope, even for public resources.
    assert not ResourceEligibilityPolicy.can_serve_task(
        _Res("MANAGED_API"), resource_scope="EXPERIMENTAL_SHARED_SUBSCRIPTION"
    )
