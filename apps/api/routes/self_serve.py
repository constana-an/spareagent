"""One-click sharing: provider signup and owner controls authenticated by the owner's node.

The signup code comes from the operator's worker bundle; all other routes accept only the
node access token of the owner's own device (never a dashboard session or dev header).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from apps.api.dependencies import get_ctx, parse_uuid_or_404
from apps.api.middleware.node_auth import get_node_principal
from apps.api.schemas.self_serve import (
    DashboardLoginOut,
    MemberLimitsIn,
    OnlineIn,
    SettingsIn,
    SharingStatusOut,
    SignupIn,
    SignupOut,
)
from services.experiments.self_serve import SelfServeSharingService
from services.registry.node_auth import NodePrincipal

router = APIRouter(prefix="/v1", tags=["self-serve"])


def svc(request: Request) -> SelfServeSharingService:
    return SelfServeSharingService(get_ctx(request))


def _out(data: dict, response: Response | None = None) -> SharingStatusOut:
    if response is not None and data.get("invite_code"):
        response.headers["Cache-Control"] = "no-store"
    return SharingStatusOut.model_validate(data)


@router.post(
    "/self-serve/signup", operation_id="selfServeSignup", status_code=201, response_model=SignupOut
)
async def signup(body: SignupIn, request: Request, response: Response) -> SignupOut:
    data = await svc(request).signup(
        body.signup_code, body.device_label, body.acknowledgements, body.terms_version
    )
    response.headers["Cache-Control"] = "no-store"
    return SignupOut.model_validate(data)


@router.get(
    "/workers/me/sharing", operation_id="getOneClickSharing", response_model=SharingStatusOut
)
async def sharing_status(
    request: Request, node: NodePrincipal = Depends(get_node_principal)
) -> SharingStatusOut:
    return _out(await svc(request).status(node.worker_id))


@router.post(
    "/workers/me/sharing/online", operation_id="goOnlineOneClick", response_model=SharingStatusOut
)
async def go_online(
    body: OnlineIn,
    request: Request,
    response: Response,
    node: NodePrincipal = Depends(get_node_principal),
) -> SharingStatusOut:
    return _out(await svc(request).go_online(node.worker_id, body.model_dump()), response)


@router.post(
    "/workers/me/sharing/offline",
    operation_id="goOfflineOneClick",
    response_model=SharingStatusOut,
)
async def go_offline(
    request: Request, node: NodePrincipal = Depends(get_node_principal)
) -> SharingStatusOut:
    return _out(await svc(request).go_offline(node.worker_id))


@router.put(
    "/workers/me/sharing/settings",
    operation_id="putOneClickSettings",
    response_model=SharingStatusOut,
)
async def put_settings(
    body: SettingsIn, request: Request, node: NodePrincipal = Depends(get_node_principal)
) -> SharingStatusOut:
    return _out(await svc(request).update_settings(node.worker_id, body.model_dump()))


@router.post(
    "/workers/me/sharing/invite-code",
    operation_id="rotateOneClickInviteCode",
    response_model=SharingStatusOut,
)
async def rotate_invite_code(
    request: Request, response: Response, node: NodePrincipal = Depends(get_node_principal)
) -> SharingStatusOut:
    return _out(await svc(request).rotate_invite_code(node.worker_id), response)


@router.delete(
    "/workers/me/sharing/members/{user_id}",
    operation_id="removeOneClickMember",
    response_model=SharingStatusOut,
)
async def remove_member(
    user_id: str,
    request: Request,
    response: Response,
    node: NodePrincipal = Depends(get_node_principal),
) -> SharingStatusOut:
    uid = parse_uuid_or_404(user_id, "MEMBER_NOT_FOUND")
    return _out(await svc(request).remove_member(node.worker_id, uid), response)


@router.put(
    "/workers/me/sharing/members/{user_id}/limits",
    operation_id="setOneClickMemberLimits",
    response_model=SharingStatusOut,
)
async def set_member_limits(
    user_id: str,
    body: MemberLimitsIn,
    request: Request,
    node: NodePrincipal = Depends(get_node_principal),
) -> SharingStatusOut:
    uid = parse_uuid_or_404(user_id, "MEMBER_NOT_FOUND")
    limits = body.model_dump(include=body.model_fields_set)
    return _out(await svc(request).set_member_limits(node.worker_id, uid, limits))


@router.post(
    "/workers/me/sharing/dashboard-login",
    operation_id="createOneClickDashboardLogin",
    response_model=DashboardLoginOut,
)
async def dashboard_login(
    request: Request, response: Response, node: NodePrincipal = Depends(get_node_principal)
) -> DashboardLoginOut:
    response.headers["Cache-Control"] = "no-store"
    return DashboardLoginOut.model_validate(await svc(request).dashboard_login(node.worker_id))
