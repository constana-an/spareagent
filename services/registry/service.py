"""Resource registry. Public eligibility is granted ONLY by an ADMIN (trusted process)."""

from __future__ import annotations

import uuid

from db.models import AuditLog, Resource
from services.context import AppContext
from services.errors import DomainError, forbidden, not_found
from services.scheduler.policy import PolicyViolation, ResourceEligibilityPolicy
from services.tasks.service import Principal


class RegistryService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def approve_resource(
        self, principal: Principal, resource_id: uuid.UUID, license_reference: str | None
    ) -> Resource:
        if principal.role != "ADMIN":
            # Providers (including the resource owner) can never self-approve.
            raise forbidden("ADMIN_REQUIRED", "Only an administrator can approve resources")
        denial: PolicyViolation | None = None
        async with self.ctx.sessionmaker() as session, session.begin():
            resource = await session.get(Resource, resource_id, with_for_update=True)
            if resource is None:
                raise not_found("RESOURCE_NOT_FOUND", "Resource not found")
            try:
                ResourceEligibilityPolicy.assert_may_grant_public(resource)
            except PolicyViolation as exc:
                denial = exc
            else:
                resource.authorization_status = "APPROVED"
                resource.public_eligible = True
                if license_reference:
                    resource.license_reference = license_reference
            session.add(
                AuditLog(
                    actor_id=principal.user_id,
                    action="resource.approve.denied" if denial else "resource.approve",
                    target_type="resource",
                    target_id=str(resource.id),
                    metadata_={"reason": str(denial)} if denial else {"license": license_reference},
                )
            )
        if denial is not None:
            raise DomainError(422, "POLICY_VIOLATION", str(denial)) from denial
        return resource
