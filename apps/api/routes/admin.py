from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.api.dependencies import get_registry_service, parse_uuid_or_404
from apps.api.middleware.auth import get_principal
from apps.api.schemas.workers import ResourceApproval, ResourceOut
from services.registry.service import RegistryService
from services.tasks.service import Principal

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.post(
    "/resources/{resource_id}/approve",
    operation_id="approveResource",
    response_model=ResourceOut,
)
async def approve_resource(
    resource_id: str,
    body: ResourceApproval | None = None,
    principal: Principal = Depends(get_principal),
    service: RegistryService = Depends(get_registry_service),
) -> ResourceOut:
    resource = await service.approve_resource(
        principal,
        parse_uuid_or_404(resource_id, "RESOURCE_NOT_FOUND"),
        body.license_reference if body else None,
    )
    return ResourceOut(
        id=resource.id,
        resource_type=resource.resource_type,
        authorization_status=resource.authorization_status,
        public_eligible=resource.public_eligible,
    )
