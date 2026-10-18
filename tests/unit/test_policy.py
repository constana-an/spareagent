from dataclasses import dataclass

import pytest

from services.scheduler.policy import PolicyViolation, ResourceEligibilityPolicy


@dataclass
class FakeResource:
    resource_type: str
    authorization_status: str = "APPROVED"
    public_eligible: bool = True


@pytest.mark.parametrize("rtype", ["MANAGED_API", "LICENSED_PROVIDER", "SELF_HOSTED"])
def test_approved_public_resource_is_schedulable(rtype):
    assert ResourceEligibilityPolicy.is_publicly_schedulable(FakeResource(rtype))
    assert ResourceEligibilityPolicy.is_gateway_routable(FakeResource(rtype))


def test_policy_002_personal_never_eligible_even_if_flags_tampered():
    # A malicious fixture that bypassed the DB CHECK constraint still cannot be scheduled.
    tampered = FakeResource("PERSONAL_SUBSCRIPTION", "APPROVED", True)
    assert not ResourceEligibilityPolicy.is_publicly_schedulable(tampered)
    assert not ResourceEligibilityPolicy.is_gateway_routable(tampered)
    assert not ResourceEligibilityPolicy.can_serve_task(
        tampered, resource_scope="PUBLIC_MARKETPLACE"
    )


@pytest.mark.parametrize("status", ["PENDING", "REJECTED", "REVOKED"])
def test_unapproved_resources_not_eligible(status):
    assert not ResourceEligibilityPolicy.is_publicly_schedulable(
        FakeResource("MANAGED_API", status)
    )


def test_not_public_eligible_rejected():
    assert not ResourceEligibilityPolicy.is_publicly_schedulable(
        FakeResource("MANAGED_API", public_eligible=False)
    )


def test_unknown_resource_type_rejected():
    assert not ResourceEligibilityPolicy.is_publicly_schedulable(FakeResource("SHARED_COOKIE"))
    with pytest.raises(PolicyViolation):
        ResourceEligibilityPolicy.assert_may_grant_public(FakeResource("SHARED_COOKIE"))


def test_owner_only_tasks_never_use_public_pool():
    assert not ResourceEligibilityPolicy.can_serve_task(
        FakeResource("MANAGED_API"), resource_scope="OWNER_ONLY"
    )


def test_cannot_grant_public_to_personal():
    with pytest.raises(PolicyViolation):
        ResourceEligibilityPolicy.assert_may_grant_public(FakeResource("PERSONAL_SUBSCRIPTION"))
