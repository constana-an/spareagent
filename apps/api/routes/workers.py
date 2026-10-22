"""Worker control plane (Week 2): enrollment, node credentials, heartbeats, leases.

Every node endpoint authenticates with a short-lived, node-scoped access token. The worker id
in the path must match the token; lease operations are checked against the lease's worker.
"""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import FileResponse

from apps.api.dependencies import get_ctx, parse_uuid_or_404
from apps.api.middleware.auth import get_principal
from apps.api.middleware.node_auth import get_node_auth, get_node_principal
from apps.api.schemas.workers import (
    AccessTokenOut,
    AppReleaseOut,
    CompletionOut,
    EnrollmentCreate,
    EnrollmentOut,
    HeartbeatIn,
    LeaseAssignment,
    LeaseCompletion,
    LeaseStateOut,
    ProgressIn,
    RegistrationOut,
    WorkerRegistration,
    WorkerRevocationOut,
    WorkerSelf,
)
from packages.provider_sdk.types import ArtifactPayload
from services.app_release import load_release
from services.context import AppContext
from services.errors import DomainError
from services.execution.remote import RemoteWorkerService, assert_path_scope
from services.registry.node_auth import NodeAuthService, NodePrincipal, Registration
from services.scheduler.lease_service import CompletionInput, LeaseService
from services.tasks.service import Principal

router = APIRouter(prefix="/v1", tags=["workers"])

RETRYABLE_CODES = frozenset({"TRANSIENT_PROVIDER_ERROR", "WORKER_SHUTDOWN"})
BILLABLE_FAILURE_CODES = frozenset({"TRANSIENT_PROVIDER_ERROR", "PERMANENT_PROVIDER_ERROR"})


def _remote(ctx: AppContext) -> RemoteWorkerService:
    return RemoteWorkerService(ctx)


def _registration_out(reg: Registration) -> RegistrationOut:
    return RegistrationOut(
        worker_id=reg.worker_id,
        node_key=reg.node_key.secret,
        node_key_expires_at=reg.node_key.expires_at,
        access_token=reg.access_token.secret,
        access_token_expires_at=reg.access_token.expires_at,
    )


def _lease_id(value: str) -> uuid.UUID:
    return parse_uuid_or_404(value, "LEASE_NOT_FOUND")


# ---------------------------------------------------------------- enrollment (user auth)
@router.post(
    "/enrollment-tokens",
    operation_id="createEnrollmentToken",
    status_code=201,
    response_model=EnrollmentOut,
)
async def create_enrollment_token(
    body: EnrollmentCreate,
    principal: Principal = Depends(get_principal),
    service: NodeAuthService = Depends(get_node_auth),
) -> EnrollmentOut:
    issued = await service.create_enrollment_token(principal, body.resource_id, body.ttl_seconds)
    return EnrollmentOut(
        id=issued.id,
        token=issued.secret,
        resource_id=body.resource_id,
        expires_at=issued.expires_at,
    )


@router.post(
    "/admin/workers/{worker_id}/revoke",
    operation_id="revokeWorker",
    response_model=WorkerRevocationOut,
)
async def revoke_worker(
    worker_id: str,
    principal: Principal = Depends(get_principal),
    service: NodeAuthService = Depends(get_node_auth),
    ctx: AppContext = Depends(get_ctx),
) -> WorkerRevocationOut:
    wid = parse_uuid_or_404(worker_id, "WORKER_NOT_FOUND")
    expired = await service.revoke_worker(principal, wid)
    await LeaseService(ctx).recover_expired()
    return WorkerRevocationOut(worker_id=wid, revoked=True, leases_expired=expired)


# ---------------------------------------------------------------- node credentials
@router.post(
    "/workers/register",
    operation_id="registerWorker",
    status_code=201,
    response_model=RegistrationOut,
)
async def register_worker(
    body: WorkerRegistration,
    authorization: str | None = Header(default=None),
    service: NodeAuthService = Depends(get_node_auth),
) -> RegistrationOut:
    reg = await service.register(
        authorization,
        resource_id=body.resource_id,
        name=body.name,
        capabilities=body.capabilities,
        max_concurrency=body.max_concurrency,
    )
    return _registration_out(reg)


@router.post("/workers/token", operation_id="exchangeNodeKey", response_model=AccessTokenOut)
async def exchange_node_key(
    authorization: str | None = Header(default=None),
    service: NodeAuthService = Depends(get_node_auth),
) -> AccessTokenOut:
    worker_id, access = await service.exchange_node_key(authorization)
    return AccessTokenOut(
        worker_id=worker_id, access_token=access.secret, expires_at=access.expires_at
    )


@router.get("/workers/me", operation_id="getWorkerSelf", response_model=WorkerSelf)
async def worker_self(
    node: NodePrincipal = Depends(get_node_principal), ctx: AppContext = Depends(get_ctx)
) -> WorkerSelf:
    return WorkerSelf(**await _remote(ctx).describe(node))


ARCH = Query(pattern="^(arm64|x86_64)$")


def _release(ctx: AppContext, arch: str) -> dict:
    release = load_release(ctx.settings.app_release_dir, arch)
    if release is None:
        raise DomainError(404, "NO_APP_RELEASE", "No App release is published for this CPU")
    return release


@router.get("/workers/me/app-release", operation_id="getAppRelease", response_model=AppReleaseOut)
async def app_release(
    arch: str = ARCH,
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> AppReleaseOut:
    """The newest App the operator published (D-103); the App compares the build."""
    release = _release(ctx, arch)
    return AppReleaseOut(**{k: release[k] for k in ("version", "build", "sha256", "size")})


@router.get(
    "/workers/me/app-release/download",
    operation_id="downloadAppRelease",
    response_class=FileResponse,
)
async def download_app_release(
    arch: str = ARCH,
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> FileResponse:
    release = _release(ctx, arch)
    return FileResponse(
        release["path"],
        media_type="application/zip",
        headers={"X-Content-SHA256": release["sha256"], "Cache-Control": "no-store"},
    )


@router.get("/workers/me/pilot-readiness", operation_id="getWorkerPilotReadiness")
async def worker_pilot_readiness(
    task_id: str | None = Query(default=None),
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> dict:
    from services.experiments.pilot import node_readiness

    tid = parse_uuid_or_404(task_id, "TASK_NOT_FOUND") if task_id else None
    return await node_readiness(ctx, node.worker_id, tid)


@router.post(
    "/workers/{worker_id}/credentials/rotate",
    operation_id="rotateNodeCredentials",
    response_model=RegistrationOut,
)
async def rotate_credentials(
    worker_id: str,
    node: NodePrincipal = Depends(get_node_principal),
    service: NodeAuthService = Depends(get_node_auth),
) -> RegistrationOut:
    assert_path_scope(node, worker_id)
    return _registration_out(await service.rotate(node))


@router.post(
    "/workers/{worker_id}/heartbeat",
    operation_id="workerHeartbeat",
    status_code=204,
    response_class=Response,
    response_model=None,
)
async def worker_heartbeat(
    worker_id: str,
    body: HeartbeatIn | None = None,
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> Response:
    assert_path_scope(node, worker_id)
    body = body or HeartbeatIn()
    await _remote(ctx).heartbeat(node, body.state, body.capabilities)
    return Response(status_code=204)


@router.get(
    "/workers/{worker_id}/leases/next",
    operation_id="pollLease",
    response_model=LeaseAssignment,
    responses={204: {"description": "no lease within the wait window"}},
)
async def poll_lease(
    worker_id: str,
    request: Request,
    wait_seconds: float = Query(default=20, ge=0, le=60),
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
):
    assert_path_scope(node, worker_id)
    found = await _remote(ctx).poll(node, wait_seconds, request.is_disconnected)
    if found is None:
        return Response(status_code=204)
    return LeaseAssignment(**found)


# ---------------------------------------------------------------- leases
@router.post(
    "/leases/{lease_id}/accept",
    operation_id="acceptLease",
    status_code=204,
    response_class=Response,
    response_model=None,
)
async def accept_lease(
    lease_id: str,
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> Response:
    await LeaseService(ctx).accept(_lease_id(lease_id), node.worker_id)
    return Response(status_code=204)


@router.get(
    "/leases/{lease_id}/inputs/{input_id}",
    operation_id="getLeaseInput",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def lease_input(
    lease_id: str,
    input_id: str,
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> Response:
    upload, content = await _remote(ctx).read_input(
        node, _lease_id(lease_id), parse_uuid_or_404(input_id, "INPUT_NOT_FOUND")
    )
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"X-Content-SHA256": upload.sha256, "Cache-Control": "no-store"},
    )


@router.post(
    "/leases/{lease_id}/progress", operation_id="reportLeaseProgress", response_model=LeaseStateOut
)
async def lease_progress(
    lease_id: str,
    body: ProgressIn,
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> LeaseStateOut:
    leases = LeaseService(ctx)
    lid = _lease_id(lease_id)
    keep_going = await leases.progress(lid, node.worker_id, body.message, body.percent)
    if not keep_going:
        return LeaseStateOut(leased_until=None, cancel_requested=True)
    leased_until, cancel = await leases.renew(lid, node.worker_id)
    return LeaseStateOut(leased_until=leased_until, cancel_requested=cancel)


@router.post("/leases/{lease_id}/renew", operation_id="renewLease", response_model=LeaseStateOut)
async def renew_lease(
    lease_id: str,
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> LeaseStateOut:
    leased_until, cancel = await LeaseService(ctx).renew(_lease_id(lease_id), node.worker_id)
    return LeaseStateOut(leased_until=leased_until, cancel_requested=cancel)


def _decode_artifacts(body: LeaseCompletion, limit: int) -> tuple[ArtifactPayload, ...]:
    out, total = [], 0
    for item in body.artifacts:
        try:
            content = base64.b64decode(item.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DomainError(422, "VALIDATION_ERROR", "artifact content is not base64") from exc
        total += len(content)
        if total > limit:
            raise DomainError(413, "PAYLOAD_TOO_LARGE", "artifacts exceed the size limit")
        out.append(ArtifactPayload(item.kind, item.name, item.media_type, content))
    return tuple(out)


@router.post(
    "/leases/{lease_id}/complete", operation_id="completeLease", response_model=CompletionOut
)
async def complete_lease(
    lease_id: str,
    body: LeaseCompletion,
    node: NodePrincipal = Depends(get_node_principal),
    ctx: AppContext = Depends(get_ctx),
) -> CompletionOut:
    lid = _lease_id(lease_id)
    artifacts = _decode_artifacts(body, ctx.settings.max_completion_artifact_bytes)
    usage = None
    if body.succeeded or body.error_code in BILLABLE_FAILURE_CODES:
        usage = await _remote(ctx).server_usage(
            lid, node.worker_id, artifacts, produced_output=body.succeeded
        )
    outcome = await LeaseService(ctx).complete(
        lid,
        node.worker_id,
        CompletionInput(
            finalization_key=body.finalization_key,
            succeeded=body.succeeded,
            result_summary=body.result_summary,
            # The server, not the node, decides which failures are retryable.
            retryable=body.retryable and body.error_code in RETRYABLE_CODES,
            error_code=body.error_code,
            timed_out=body.timed_out,
            artifacts=artifacts if body.succeeded else (),
            usage=usage,
            observed_usage=body.observed_usage.model_dump() if body.observed_usage else None,
            reset_hint=body.reset_hint,
            failure_detail=body.failure_detail,
        ),
    )
    return CompletionOut(task_status=outcome.task_status, replayed=outcome.replayed)
