from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from apps.api.dependencies import get_gateway_service
from apps.api.middleware.auth import get_principal
from apps.api.schemas.gateway import ChatRequest, ModelList, ModelOut
from services.errors import DomainError
from services.gateway.service import GatewayService
from services.tasks.service import Principal

router = APIRouter(prefix="/v1", tags=["gateway"])

UNSUPPORTED_STREAMING = DomainError(
    422, "UNSUPPORTED_STREAMING", "stream=true is not supported yet; send stream=false"
)


@router.get("/models", operation_id="listModels", response_model=ModelList)
async def list_models(
    _principal: Principal = Depends(get_principal),
    service: GatewayService = Depends(get_gateway_service),
) -> ModelList:
    return ModelList(data=[ModelOut(**m) for m in await service.list_models()])


@router.post("/chat/completions", operation_id="createChatCompletion")
async def create_chat_completion(
    body: ChatRequest,
    principal: Principal = Depends(get_principal),
    service: GatewayService = Depends(get_gateway_service),
) -> JSONResponse:
    if body.stream:
        raise UNSUPPORTED_STREAMING
    outcome = await service.chat(principal, body.model_dump(exclude_none=True))
    return JSONResponse(
        content=outcome.body,
        # Week 1 usage counters are deterministic MOCK estimates and never billed.
        headers={"X-SpareAgent-Billable": "true" if outcome.billable else "false"},
    )
