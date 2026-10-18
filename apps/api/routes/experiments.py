"""Week 4 experimental scope: invitation-only groups, subscription connections, sharing
consent and experimental tasks. Every route authenticates the caller; ownership and role
checks live in ExperimentalParticipationService. Unknown/foreign ids return 404."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request

from apps.api.dependencies import get_ctx, parse_uuid_or_404
from apps.api.middleware.auth import get_principal
from apps.api.schemas.experiments import (
    AuditOut,
    AuthorizationCreate,
    AuthorizationOut,
    AuthorizationVerify,
    ConnectionOut,
    Consent,
    DecisionOut,
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
    SubscriptionConnect,
    SubscriptionStatus,
    SubscriptionStatusItem,
    UsageList,
    UsageOut,
)
from apps.api.schemas.tasks import IDEMPOTENCY_KEY
from services.errors import DomainError
from services.experiments.participation import (
    ExperimentalParticipationService,
    ExperimentalTaskView,
)
from services.tasks import repository as repo
from services.tasks.service import Principal

router = APIRouter(prefix="/v1", tags=["experiments"])


def svc(request: Request) -> ExperimentalParticipationService:
    return ExperimentalParticipationService(get_ctx(request))


def _auth_out(auth) -> AuthorizationOut | None:
    if auth is None:
        return None
    return AuthorizationOut.model_validate(auth, from_attributes=True)


def _prefs_out(prefs) -> PreferencesOut | None:
    return None if prefs is None else PreferencesOut.model_validate(prefs, from_attributes=True)


async def _task_out(request: Request, view: ExperimentalTaskView) -> ExperimentalTaskOut:
    t, c, d = view.task, view.consent, view.last_decision
    async with get_ctx(request).sessionmaker() as session:
        summaries = await repo.result_summaries(session, [t.id])
    return ExperimentalTaskOut(
        id=t.id,
        status=t.status,
        task_type=t.task_type,
        resource_scope=t.resource_scope,
        group_id=c.group_id,
        connection_id=c.connection_id,
        requester_id=c.requester_id,
        owner_id=c.owner_id,
        approval_status=c.status,
        approval_source=c.consent_source,
        last_decision=None
        if d is None
        else DecisionOut(
            decision=d.decision,
            reasons=d.reasons,
            execution_mode=d.execution_mode,
            created_at=d.created_at,
        ),
        created_at=t.created_at,
        result_summary=summaries.get(t.id),
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
    "/experiments/groups/{group_id}", operation_id="getExperimentalGroup", response_model=GroupOut
)
async def get_group(
    group_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> GroupOut:
    gid = parse_uuid_or_404(group_id, "GROUP_NOT_FOUND")
    group, members, auth = await svc(request).get_group(principal, gid)
    out = GroupOut.model_validate(group, from_attributes=True)
    out.members = [MemberOut.model_validate(m, from_attributes=True) for m in members]
    out.authorization = _auth_out(auth)
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
    return _auth_out(auth)


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
    return _auth_out(auth)


@router.post(
    "/experiments/authorizations/{authorization_id}/revoke",
    operation_id="revokeExperimentalAuthorization",
    response_model=AuthorizationOut,
)
async def revoke_authorization(
    authorization_id: str, request: Request, principal: Principal = Depends(get_principal)
) -> AuthorizationOut:
    aid = parse_uuid_or_404(authorization_id, "AUTHORIZATION_NOT_FOUND")
    return _auth_out(await svc(request).revoke_authorization(principal, aid))


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
        body.model_dump(exclude={"connection_id", "group_id"}),
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
    view, _ = await svc(request).create_task(
        principal, idempotency_key, body.model_dump(exclude={"max_budget_cents"})
    )
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
