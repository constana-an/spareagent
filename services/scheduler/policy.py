"""Resource eligibility policy - the single authority for who may serve what.

Invariant: PERSONAL_SUBSCRIPTION resources (a person's ChatGPT/Claude plan) are never
eligible for public scheduling or the API gateway, regardless of any flag value, client
input, or fixture. The database enforces the same rule with CHECK constraints
(`no_personal_public`, `public_requires_approval`); this module re-checks it in the
application so a bypassed or relaxed constraint still cannot route personal resources.
"""

from __future__ import annotations

from typing import Protocol

PERSONAL_SUBSCRIPTION = "PERSONAL_SUBSCRIPTION"
PUBLIC_RESOURCE_TYPES: frozenset[str] = frozenset(
    {"MANAGED_API", "LICENSED_PROVIDER", "SELF_HOSTED"}
)
APPROVED = "APPROVED"
PUBLIC_MARKETPLACE = "PUBLIC_MARKETPLACE"


class ResourceLike(Protocol):
    resource_type: str
    authorization_status: str
    public_eligible: bool


class PolicyViolation(Exception):
    pass


class ResourceEligibilityPolicy:
    @staticmethod
    def is_publicly_schedulable(resource: ResourceLike) -> bool:
        return (
            resource.resource_type != PERSONAL_SUBSCRIPTION
            and resource.resource_type in PUBLIC_RESOURCE_TYPES
            and resource.authorization_status == APPROVED
            and resource.public_eligible is True
        )

    @classmethod
    def can_serve_task(cls, resource: ResourceLike, *, resource_scope: str) -> bool:
        # OWNER_ONLY (personal runtime) execution is not implemented; such tasks are never
        # dispatched to the public pool.
        return resource_scope == PUBLIC_MARKETPLACE and cls.is_publicly_schedulable(resource)

    @classmethod
    def is_gateway_routable(cls, resource: ResourceLike) -> bool:
        return cls.is_publicly_schedulable(resource)

    @staticmethod
    def assert_may_grant_public(resource: ResourceLike) -> None:
        if resource.resource_type == PERSONAL_SUBSCRIPTION:
            raise PolicyViolation(
                "PERSONAL_SUBSCRIPTION resources can never be made publicly eligible"
            )
        if resource.resource_type not in PUBLIC_RESOURCE_TYPES:
            raise PolicyViolation(f"Unknown resource type {resource.resource_type}")
