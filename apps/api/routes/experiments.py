"""Week 4 experimental scope: invitation-only groups, subscription connections, sharing
consent and experimental tasks. Every route authenticates the caller; ownership and role
checks live in ExperimentalParticipationService. Unknown/foreign ids return 404."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select

from apps.api.dependencies import get_ctx, parse_uuid_or_404
from apps.api.middleware.auth import get_principal
from apps.api.schemas.experiments import (
    AttachmentOut,
    AuditOut,
    AuthorizationCreate,
    AuthorizationOut,
    AuthorizationVerify,
    ConnectionOut,
    Consent,
    DecisionOut,
    DeviceEnrollmentCreate,
    DeviceEnrollmentOut,
    ExperimentalTaskCreate,
    ExperimentalTaskList,
    ExperimentalTaskOut,
    GroupCreate,
    GroupOut,
    GroupSharing,
    Invite,
    KillSwitchOut,
    MemberOut,
    NodeList,
    NodeOut,
    PreferencesIn,
    PreferencesOut,
    SharingEnable,
    SharingTarget,
    SubscriptionAttest,
    SubscriptionConnect,
    SubscriptionStatus,
    SubscriptionStatusItem,
    TaskKindList,
    TaskKindOut,
    TaskKindStatusIn,
    UploadOut,
    UsageList,
    UsageOut,
)
from apps.api.schemas.tasks import IDEMPOTENCY_KEY
from db.models import ExperimentalGroup, ExperimentalGroupMember, VerificationResult
from services.errors import DomainError
from services.experiments.participation import (
    ExperimentalParticipationService,
    ExperimentalTaskView,
)
from services.observability import redact
from services.tasks import kinds as kind_catalog
from services.tasks import repository as repo
from services.tasks.progress import device_wait_reasons, task_details
from services.tasks.service import Principal
from services.tasks.uploads import TaskKindService, UploadService

router = APIRouter(prefix="/v1", tags=["experiments"])


def svc(request: Request) -> ExperimentalParticipationService:
    return ExperimentalParticipationService(get_ctx(request))


def _auth_out(auth, request: Request) -> AuthorizationOut | None:
    if auth is None:
        return None
    out = AuthorizationOut.model_validate(auth, from_attributes=True)
    out.live_execution_enabled = bool(
        get_ctx(request).settings.experiment_live_enabled
        and auth.verification_status == "OPERATOR_VERIFIED"
        and auth.integration_route_covered
        and auth.integration_route == "CODEX_CLI_EXEC"
        and auth.revoked_at is None
        and auth.expires_at > datetime.now(UTC)
    )
    return out


def _prefs_out(prefs) -> PreferencesOut | None:
    return None if prefs is None else PreferencesOut.model_validate(prefs, from_attributes=True)


async def _task_out(request: Request, view: ExperimentalTaskView) -> ExperimentalTaskOut:
    t, c, d = view.task, view.consent, view.last_decision
    async with get_ctx(request).sessionmaker() as session:
        summaries = await repo.result_summaries(session, [t.id])
        uploads = (await UploadService.for_tasks(session, [t.id]))[t.id]
        details = (await task_details(session, [t]))[t.id]
        waiting = (
            await device_wait_reasons(
                session, c.connection_id, get_ctx(request).settings.worker_heartbeat_ttl_seconds
            )
            if t.status == "QUEUED"
            else []
        )
        verification = (
            await session.execute(
                select(VerificationResult.status)
                .where(VerificationResult.task_id == t.id)
                .order_by(VerificationResult.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    decision = (
        None
        if d is None
        else DecisionOut(
            decision=d.decision,
            reasons=d.reasons,
            execution_mode=d.execution_mode,
            created_at=d.created_at,
        )
    )
    if waiting and (decision is None or not set(waiting) <= set(decision.reasons)):
        # The owner's device is paused or offline right now (no newer decision exists).
        decision = DecisionOut(
            decision="DENY",
            reasons=waiting,
            execution_mode="LIVE" if t.execution_profile == "CODEX_LIVE" else "SIMULATED",
            created_at=datetime.now(UTC),
        )
    return ExperimentalTaskOut(
        id=t.id,
        parent_task_id=(t.spec or {}).get("parent_task_id"),
        conversation_id=(t.spec or {}).get("conversation_id", t.id),
        status=t.status,
        task_type=t.task_type,
        kind=t.task_type,
        resource_scope=t.resource_scope,
        execution_mode="LIVE" if t.execution_profile == "CODEX_LIVE" else "SIMULATED",
        group_id=c.group_id,
        connection_id=c.connection_id,
        requester_id=c.requester_id,
        owner_id=c.owner_id,
        repository_url=t.repository_url,
        commit_sha=t.commit_sha.strip() if t.commit_sha else None,
        prompt=redact(t.prompt),
        requirement=redact(t.prompt),
        attachments=[AttachmentOut.model_validate(u, from_attributes=True) for u in uploads],
        parameters=(t.spec or {}).get("parameters") or {},
        deliverable=(t.spec or {}).get("deliverable"),
        network_access=bool((t.spec or {}).get("network_access")),
        max_duration_seconds=t.max_duration_seconds,
        verification_status=verification or ("PENDING" if t.status == "VERIFYING" else "NOT_RUN"),
        approval_status=c.status,
        approval_source=c.consent_source,
        last_decision=decision,
        created_at=t.created_at,
        result_summary=summaries.get(t.id),
        progress=details["progress"],
        queue_position=details["queue_position"],
        started_at=details["started_at"],
        failure=details["failure"],
    )


# ------------------------------------------------------------------ groups (operator/members)
@router.post(
    "/experiments/groups",
    operation_id="createExperimentalGroup",
    status_code=201,
    response_model=GroupOut,
)
async def create_group(
    body: GroupCreate, request: Request, principal: Principal = Depends(get_principal)
) -> GroupOut:
    group = await svc(request).create_group(principal, body.name, body.model_dump())
    return GroupOut.model_validate(group, from_attributes=True)


@router.get(
    "/experiments/groups", operation_id="listExperimentalGroups", response_model=list[GroupOut]
)
async def list_groups(request: Request, principal: Principal = Depends(get_principal)):
    async with get_ctx(request).sessionmaker() as session:
        stmt = select(ExperimentalGroup.id).order_by(ExperimentalGroup.created_at)
        if principal.role != "ADMIN":
            stmt = stmt.join(
                ExperimentalGroupMember, ExperimentalGroupMember.group_id == ExperimentalGroup.id
            ).where(
                ExperimentalGroupMember.user_id == principal.user_id,
                ExperimentalGroupMember.status.in_(("INVITED", "ACTIVE")),
            )
        ids = (await session.execute(stmt)).scalars().all()
    return [await get_group(str(gid), request, principal) for gid in ids]


@router.get(
    "/experiments/groups/{group_id}", operation_id="getExperimentalGroup", response_model=GroupOut
)
async def get_group(
    group_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> GroupOut:
    gid = parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    group, members, auth = await svc(request).get_group(principal, gid)
    out = GroupOut.model_validate(group, from_attributes=True)
    out.members = [MemberOut.model_validate(m, from_attributes=True) for m in members]
    out.authorization = _auth_out(auth, request)
    return out


@router.post(
    "/experiments/groups/{group_id}/invite",
    operation_id="inviteExperimentalMember",
    status_code=201,
    response_model=MemberOut,
)
async def invite(
    group_id: str, body: Invite, request: Request, principal: Principal = Depends(get_principal)
) -> MemberOut:
    gid = parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    member = await svc(request).invite(principal, gid, body.user_id)
    return MemberOut.model_validate(member, from_attributes=True)


@router.post(
    "/experiments/groups/{group_id}/join",
    operation_id="joinExperimentalGroup",
    response_model=MemberOut,
)
async def join(
    group_id: str, body: Consent, request: Request, principal: Principal = Depends(get_principal)
) -> MemberOut:
    gid = parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    member = await svc(request).join(principal, gid, body.acknowledgements, body.terms_version)
    return MemberOut.model_validate(member, from_attributes=True)


@router.post(
    "/experiments/groups/{group_id}/authorizations",
    operation_id="recordExperimentalAuthorization",
    status_code=201,
    response_model=AuthorizationOut,
)
async def record_authorization(
    group_id: str,
    body: AuthorizationCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> AuthorizationOut:
    gid = parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    auth = await svc(request).record_authorization(principal, gid, body.model_dump())
    return _auth_out(auth, request)


@router.post(
    "/experiments/authorizations/{authorization_id}/verify",
    operation_id="verifyExperimentalAuthorization",
    response_model=AuthorizationOut,
)
async def verify_authorization(
    authorization_id: str,
    body: AuthorizationVerify,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> AuthorizationOut:
    aid = parse_uuid_or_404(authorization_id, "AUTHORIZATION_NOT_FOUND")
    auth = await svc(request).verify_authorization(
        principal,
        aid,
        status=body.verification_status,
        integration_route_covered=body.integration_route_covered,
    )
    return _auth_out(auth, request)


@router.post(
    "/experiments/authorizations/{authorization_id}/revoke",
    operation_id="revokeExperimentalAuthorization",
    response_model=AuthorizationOut,
)
async def revoke_authorization(
    authorization_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> AuthorizationOut:
    aid = parse_uuid_or_404(authorization_id, "AUTHORIZATION_NOT_FOUND")
    return _auth_out(await svc(request).revoke_authorization(principal, aid), request)


@router.post(
    "/experiments/groups/{group_id}/sharing",
    operation_id="setGroupSharing",
    response_model=GroupOut,
)
async def set_group_sharing(
    group_id: str,
    body: GroupSharing,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> GroupOut:
    gid = parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    group = await svc(request).set_group_sharing(principal, gid, body.enabled)
    return GroupOut.model_validate(group, from_attributes=True)


@router.post(
    "/experiments/kill-switch", operation_id="experimentKillSwitch", response_model=KillSwitchOut
)
async def kill_switch(
    request: Request, principal: Principal = Depends(get_principal)
) -> KillSwitchOut:
    return KillSwitchOut(**await svc(request).kill_switch(principal))


@router.get(
    "/experiments/groups/{group_id}/audit",
    operation_id="listExperimentalAudit",
    response_model=list[AuditOut],
)
async def audit(
    group_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
) -> list[AuditOut]:
    gid = parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    rows = await svc(request).audit_log(principal, gid, limit)
    return [AuditOut.model_validate(r, from_attributes=True) for r in rows]


# ------------------------------------------------------------------ subscriptions
@router.post(
    "/subscriptions/connect",
    operation_id="connectSubscription",
    status_code=201,
    response_model=ConnectionOut,
)
async def connect(
    body: SubscriptionConnect, request: Request, principal: Principal = Depends(get_principal)
) -> ConnectionOut:
    conn = await svc(request).connect(principal, body.model_dump())
    return ConnectionOut.model_validate(conn, from_attributes=True)


@router.post(
    "/subscriptions/attest",
    operation_id="attestSubscription",
    response_model=ConnectionOut,
)
async def attest(
    body: SubscriptionAttest, request: Request, principal: Principal = Depends(get_principal)
) -> ConnectionOut:
    conn = await svc(request).attest(principal, body.connection_id, body.model_dump())
    return ConnectionOut.model_validate(conn, from_attributes=True)


@router.post(
    "/subscriptions/enrollment-tokens",
    operation_id="createDeviceEnrollmentToken",
    status_code=201,
    response_model=DeviceEnrollmentOut,
)
async def device_enrollment(
    body: DeviceEnrollmentCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> DeviceEnrollmentOut:
    resource_id, issued = await request.app.state.node_auth.create_personal_enrollment_token(
        principal, body.ttl_seconds
    )
    return DeviceEnrollmentOut(
        resource_id=resource_id,
        token_id=issued.id,
        token=issued.secret,
        expires_at=issued.expires_at,
    )


@router.get(
    "/subscriptions/status", operation_id="getSubscriptionStatus", response_model=SubscriptionStatus
)
async def status(
    request: Request, principal: Principal = Depends(get_principal)
) -> SubscriptionStatus:
    items = await svc(request).subscription_status(principal)
    return SubscriptionStatus(
        items=[
            SubscriptionStatusItem(
                connection=ConnectionOut.model_validate(i["connection"], from_attributes=True),
                worker_status=i["worker_status"],
                last_heartbeat=i["last_heartbeat"],
                preferences=_prefs_out(i["preferences"]),
            )
            for i in items
        ]
    )


@router.get("/subscriptions/usage", operation_id="getSubscriptionUsage", response_model=UsageList)
async def usage(request: Request, principal: Principal = Depends(get_principal)) -> UsageList:
    return UsageList(items=[UsageOut(**u) for u in await svc(request).usage(principal)])


# ------------------------------------------------------------------ sharing (owner)
@router.put(
    "/sharing/preferences", operation_id="putSharingPreferences", response_model=PreferencesOut
)
async def put_preferences(
    body: PreferencesIn, request: Request, principal: Principal = Depends(get_principal)
) -> PreferencesOut:
    prefs = await svc(request).set_preferences(
        principal,
        body.connection_id,
        body.group_id,
        body.model_dump(exclude={"connection_id", "group_id"}, exclude_unset=True),
    )
    return _prefs_out(prefs)


@router.post("/sharing/enable", operation_id="enableSharing", response_model=PreferencesOut)
async def enable(
    body: SharingEnable, request: Request, principal: Principal = Depends(get_principal)
) -> PreferencesOut:
    prefs = await svc(request).enable(
        principal, body.connection_id, body.acknowledgements, body.terms_version
    )
    return _prefs_out(prefs)


@router.post("/sharing/pause", operation_id="pauseSharing", response_model=PreferencesOut)
async def pause(
    body: SharingTarget, request: Request, principal: Principal = Depends(get_principal)
) -> PreferencesOut:
    return _prefs_out(await svc(request).pause(principal, body.connection_id))


@router.post("/sharing/revoke", operation_id="revokeSharing", response_model=PreferencesOut)
async def revoke(
    body: SharingTarget, request: Request, principal: Principal = Depends(get_principal)
) -> PreferencesOut:
    return _prefs_out(await svc(request).revoke(principal, body.connection_id))


# ------------------------------------------------------------------ task kinds / uploads
def _kind_out(kind: kind_catalog.Kind, status: str, live_enabled: bool) -> TaskKindOut:
    return TaskKindOut(
        kind=kind.kind,
        category=kind.category,
        name_zh=kind.name_zh,
        name_en=kind.name_en,
        description_zh=kind.description_zh,
        status=status,
        needs_repository=kind.needs_repository,
        min_attachments=kind.min_attachments,
        max_attachments=kind.max_attachments,
        extensions=sorted(kind.extensions),
        parameters=[p.name for p in kind.params],
        required_parameters=[p.name for p in kind.params if p.required],
        deliverable_format=kind.deliverable.format,
        deliverable_formats=sorted({kind.deliverable.format, *kind.deliverable_formats}),
        capabilities=list(kind.capabilities),
        live_available=live_enabled and status == "OPEN",
    )


@router.get("/task-kinds", operation_id="listTaskKinds", response_model=TaskKindList)
async def list_task_kinds(
    request: Request, principal: Principal = Depends(get_principal)
) -> TaskKindList:
    ctx = get_ctx(request)
    rows = await TaskKindService(ctx).list()
    live = ctx.settings.experiment_live_enabled
    return TaskKindList(items=[_kind_out(k, status, live) for k, status in rows])


@router.put(
    "/task-kinds/{kind}/status", operation_id="setTaskKindStatus", response_model=TaskKindOut
)
async def set_task_kind_status(
    kind: str,
    body: TaskKindStatusIn,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> TaskKindOut:
    ctx = get_ctx(request)
    row = await TaskKindService(ctx).set_status(
        principal, kind, body.status, body.acceptance_report
    )
    return _kind_out(kind_catalog.get(row.kind), row.status, ctx.settings.experiment_live_enabled)


@router.post(
    "/experimental-uploads",
    operation_id="uploadTaskAttachment",
    status_code=201,
    response_model=UploadOut,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string"}}},
        },
        "parameters": [
            {
                "name": "X-Filename",
                "in": "header",
                "required": True,
                "description": "UTF-8 file name, percent-encoded",
                "schema": {"type": "string", "maxLength": 600},
            }
        ],
    },
)
async def upload_attachment(
    request: Request, principal: Principal = Depends(get_principal)
) -> UploadOut:
    from urllib.parse import unquote

    raw_name = request.headers.get("x-filename")
    if not raw_name or len(raw_name) > 600:
        raise DomainError(422, "UPLOAD_NAME_INVALID", "X-Filename header is required")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > kind_catalog.MAX_UPLOAD_BYTES:
        raise DomainError(413, "UPLOAD_TOO_LARGE", "The file is larger than 20 MB")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > kind_catalog.MAX_UPLOAD_BYTES:
            raise DomainError(413, "UPLOAD_TOO_LARGE", "The file is larger than 20 MB")
        chunks.append(chunk)
    try:
        filename = unquote(raw_name, errors="strict")
    except UnicodeDecodeError:
        raise DomainError(422, "UPLOAD_NAME_INVALID", "X-Filename must be UTF-8") from None
    row = await UploadService(get_ctx(request)).create(principal, filename, b"".join(chunks))
    return UploadOut.model_validate(row, from_attributes=True)


# ------------------------------------------------------------------ experimental tasks
@router.get(
    "/experimental-tasks/nodes", operation_id="listExperimentalNodes", response_model=NodeList
)
async def nodes(
    request: Request, group_id: str = Query(...), principal: Principal = Depends(get_principal)
) -> NodeList:
    gid = parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    return NodeList(
        items=[NodeOut(**n) for n in await svc(request).available_nodes(principal, gid)]
    )


@router.post(
    "/experimental-tasks",
    operation_id="createExperimentalTask",
    status_code=202,
    response_model=ExperimentalTaskOut,
)
async def create_task(
    body: ExperimentalTaskCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ExperimentalTaskOut:
    if idempotency_key is None:
        raise DomainError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise DomainError(
            400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be 8-128 chars of [A-Za-z0-9._:-]"
        )
    view, _ = await svc(request).create_task(principal, idempotency_key, body.normalized())
    return await _task_out(request, view)


@router.get(
    "/experimental-tasks", operation_id="listExperimentalTasks", response_model=ExperimentalTaskList
)
async def list_tasks(
    request: Request,
    role: str = Query(default="requester", pattern="^(requester|owner)$"),
    principal: Principal = Depends(get_principal),
) -> ExperimentalTaskList:
    views = await svc(request).list_tasks(principal, role)
    return ExperimentalTaskList(items=[await _task_out(request, v) for v in views])


@router.get(
    "/experimental-tasks/{task_id}",
    operation_id="getExperimentalTask",
    response_model=ExperimentalTaskOut,
)
async def get_task(
    task_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> ExperimentalTaskOut:
    tid = parse_uuid_or_404(task_id, "TASK_NOT_FOUND")
    return await _task_out(request, await svc(request).get_task(principal, tid))


@router.get(
    "/experimental-tasks/{task_id}/conversation",
    operation_id="getTaskConversation",
    response_model=ExperimentalTaskList,
)
async def conversation(
    task_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> ExperimentalTaskList:
    tid = parse_uuid_or_404(task_id, "TASK_NOT_FOUND")
    view = await svc(request).get_task(principal, tid)
    root = (view.task.spec or {}).get("conversation_id", str(tid))
    from sqlalchemy import or_

    from db.models import Task

    async with get_ctx(request).sessionmaker() as session:
        rows = list(
            (
                await session.execute(
                    select(Task)
                    .where(
                        Task.consumer_id == view.task.consumer_id,
                        or_(Task.id == root, Task.spec["conversation_id"].astext == root),
                    )
                    .order_by(Task.created_at, Task.id)
                )
            ).scalars()
        )
    items = []
    for task in rows:
        items.append(await _task_out(request, await svc(request).get_task(principal, task.id)))
    return ExperimentalTaskList(items=items)


@router.post(
    "/experimental-tasks/{task_id}/approve",
    operation_id="approveExperimentalTask",
    response_model=ExperimentalTaskOut,
)
async def approve(
    task_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> ExperimentalTaskOut:
    tid = parse_uuid_or_404(task_id, "TASK_NOT_FOUND")
    return await _task_out(request, await svc(request).decide(principal, tid, approve=True))


@router.post(
    "/experimental-tasks/{task_id}/reject",
    operation_id="rejectExperimentalTask",
    response_model=ExperimentalTaskOut,
)
async def reject(
    task_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> ExperimentalTaskOut:
    tid = parse_uuid_or_404(task_id, "TASK_NOT_FOUND")
    return await _task_out(request, await svc(request).decide(principal, tid, approve=False))


@router.get("/experiments/groups/{group_id}/metrics", operation_id="getPilotMetrics")
async def pilot_metrics(
    group_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> dict:
    from services.experiments.pilot import group_metrics

    return await group_metrics(
        get_ctx(request), principal, parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    )


@router.get("/experimental-tasks/{task_id}/pilot-report", operation_id="getPilotReport")
async def export_pilot_report(
    task_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> dict:
    from services.experiments.pilot import pilot_report

    return await pilot_report(
        get_ctx(request), principal, parse_uuid_or_404(task_id, "TASK_NOT_FOUND")
    )
