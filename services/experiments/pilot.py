"""Read-only pilot diagnostics and aggregate observations. Never activates execution."""

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import func, select

from db.models import (
    AuditLog,
    ExperimentalDispatchAudit,
    ExperimentalGroupMember,
    ExperimentalTaskConsent,
    Resource,
    SubscriptionConnection,
    SubscriptionSharingPreference,
    SubscriptionUsageObservation,
    Task,
    TaskAttempt,
    VerificationResult,
    WorkerNode,
)
from services.errors import not_found
from services.experiments.dispatcher import ExperimentalDispatcher
from services.experiments.participation import ExperimentalParticipationService
from services.scheduler.lease_service import utcnow
from services.subscriptions.policy import ExperimentalSharingPolicy, Mode

PILOT_MAX_DURATION_SECONDS = 600


def check_item(name, ok, action, *, source="AUTOMATIC"):
    return {"id": name, "status": "PASS" if ok else "BLOCKED", "source": source, "action": action}


async def node_readiness(ctx, worker_id, task_id=None):
    """Only the authenticated node's own connection; no prompts or other identities."""
    checks = []
    async with ctx.sessionmaker() as session:
        worker = await session.get(WorkerNode, worker_id)
        resource = await session.get(Resource, worker.resource_id)
        conn = (
            await session.execute(
                select(SubscriptionConnection).where(SubscriptionConnection.worker_id == worker_id)
            )
        ).scalar_one_or_none()
        checks.append(
            check_item(
                "personal_registration",
                resource.resource_type == "PERSONAL_SUBSCRIPTION"
                and not resource.public_eligible
                and worker.revoked_at is None,
                "Enroll your own non-public personal subscription device in the dashboard.",
            )
        )
        if conn is None:
            checks.append(
                check_item("connection", False, "Connect this worker ID in your dashboard.")
            )
            return {"checks": checks, "live_enabled": ctx.settings.experiment_live_enabled}
        checks.append(
            check_item(
                "plus_chatgpt_subscription",
                conn.subscription_type == "PLUS" and conn.auth_method == "CHATGPT",
                "Confirm your own ChatGPT Plus account; this server field is owner-reported.",
                source="OWNER_REPORTED_NOT_MACHINE_VERIFIED",
            )
        )
        prefs = await session.get(SubscriptionSharingPreference, conn.id)
        if prefs is None:
            checks.append(
                check_item("sharing_preferences", False, "Choose a group and save sharing limits.")
            )
            return {"checks": checks, "live_enabled": ctx.settings.experiment_live_enabled}
        members = list(
            (
                await session.execute(
                    select(ExperimentalGroupMember).where(
                        ExperimentalGroupMember.group_id == prefs.group_id,
                        ExperimentalGroupMember.status.in_(("ACTIVE", "INVITED")),
                    )
                )
            ).scalars()
        )
        others = [m.user_id for m in members if m.user_id != conn.owner_id and m.status == "ACTIVE"]
        checks.append(
            check_item(
                "two_participants",
                len(members) == 2
                and len(others) == 1
                and all(m.status == "ACTIVE" for m in members),
                "Operator: use exactly Alice and Bob; both must accept current join terms.",
            )
        )
        checks.append(
            check_item(
                "current_join_terms",
                all(m.terms_version == ctx.settings.experiment_terms_version for m in members),
                "Both participants must accept the current group terms.",
            )
        )
        checks.append(
            check_item(
                "pilot_limits",
                prefs.max_concurrent_tasks == 1
                and prefs.max_task_duration_seconds is not None
                and prefs.max_task_duration_seconds <= PILOT_MAX_DURATION_SECONDS
                and prefs.allowed_task_types == ["CODE_REVIEW"],
                "Set owner limits: CODE_REVIEW only, timeout at most 600 seconds, concurrency 1.",
            )
        )
        task = SimpleNamespace(
            resource_scope="EXPERIMENTAL_SHARED_SUBSCRIPTION",
            task_type="CODE_REVIEW",
            max_duration_seconds=min(
                PILOT_MAX_DURATION_SECONDS,
                prefs.max_task_duration_seconds or PILOT_MAX_DURATION_SECONDS,
            ),
            execution_profile="CODEX_LIVE",
        )
        consent = SimpleNamespace(
            group_id=prefs.group_id,
            connection_id=conn.id,
            requester_id=others[0] if others else conn.owner_id,
            status="PENDING",
            consent_source=None,
        )
        if task_id is not None:
            consent = (
                await session.execute(
                    select(ExperimentalTaskConsent).where(
                        ExperimentalTaskConsent.task_id == task_id,
                        ExperimentalTaskConsent.connection_id == conn.id,
                    )
                )
            ).scalar_one_or_none()
            if consent is None:
                raise not_found("TASK_NOT_FOUND", "Task not found")
            task = await session.get(Task, task_id)
            checks.append(
                check_item(
                    "pilot_task",
                    task.execution_profile == "CODEX_LIVE"
                    and task.task_type == "CODE_REVIEW"
                    and task.max_duration_seconds is not None
                    and task.max_duration_seconds <= PILOT_MAX_DURATION_SECONDS,
                    (
                        "Use one LIVE CODE_REVIEW on a pinned public repository with a "
                        "600-second limit."
                    ),
                )
            )
        snap, _, _ = await ExperimentalDispatcher(ctx).snapshot(session, task, consent)
        reasons = ExperimentalSharingPolicy.evaluate(snap, mode=Mode.LIVE).reasons
        for reason in reasons:
            if task_id is None and reason.value == "OWNER_APPROVAL_MISSING":
                continue
            checks.append(
                check_item(
                    reason.value,
                    False,
                    ACTIONS.get(
                        reason.value,
                        "Resolve this policy condition in the dashboard or with the operator.",
                    ),
                )
            )
        checks.append(
            check_item(
                "group_pilot_limits",
                snap.group_max_members == 2
                and snap.group_max_concurrent_tasks == 1
                and snap.group_max_task_duration_seconds is not None
                and snap.group_max_task_duration_seconds <= PILOT_MAX_DURATION_SECONDS
                and snap.group_allowed_task_types == frozenset({"CODE_REVIEW"}),
                "Operator: cap the pilot at 2 members, 1 concurrent CODE_REVIEW, 600 seconds.",
            )
        )
        checks.append(
            check_item(
                "sharing_terms",
                prefs.consent_terms_version == ctx.settings.experiment_terms_version,
                "Accept the current seven sharing statements.",
            )
        )
        auth = snap.authorization
        return {
            "checks": checks,
            "live_enabled": snap.live_enabled,
            "connection_id": str(conn.id),
            "group_id": str(prefs.group_id),
            "authorization": None
            if auth is None
            else {
                "id": str(auth.id),
                "expires_at": auth.expires_at.isoformat(),
                "route": auth.integration_route,
                "participant_cap": auth.max_participants,
            },
            "owner_attestations": {
                "owner_check_recorded": snap.owner_check_passed,
                "no_extra_credits_recorded": snap.no_purchased_credits,
                "source": "OWNER_REPORTED_NOT_MACHINE_VERIFIED",
            },
            "per_task_approval": "REQUIRED_BEFORE_EACH_TASK" if task_id is None else consent.status,
            "physical_two_devices": "OPERATOR_CONFIRMATION_REQUIRED",
        }


ACTIONS = {
    "LIVE_EXECUTION_DISABLED": (
        "LIVE is safely off. Only the operator may manually enable it after all checks."
    ),
    "PROVIDER_OFFLINE": (
        "Start your explicitly opted-in Codex worker; check heartbeat and outbound HTTPS."
    ),
    "NODE_NOT_CODEX_CAPABLE": (
        "Start with --executor codex-subscription and both explicit consent flags."
    ),
    "OWNER_CHECK_MISSING": (
        "Owner: manually run the separate opted-in codex-check before recording its result."
    ),
    "CREDITS_ATTESTATION_MISSING": (
        "Owner: confirm Plus, no purchased/granted Credits, auto-reload off; record attestation."
    ),
    "OWNER_APPROVAL_MISSING": "Alice must inspect and explicitly approve this individual task.",
    "AUTHORIZATION_MISSING": (
        "Operator: validate the project-specific correspondence and record its scoped reference."
    ),
    "AUTHORIZATION_EXPIRED": (
        "Stop. Operator must review authorization; do not extend expiry automatically."
    ),
    "OWNER_PAUSED": "Sharing is paused. Only the owner may explicitly resume.",
    "SUBSCRIPTION_AUTH_INVALID": (
        "Owner: renew your own ChatGPT login and reconnect; no credential substitution."
    ),
    "RATE_LIMITED": (
        "Stop and wait for included usage to reset; do not buy Credits or switch accounts."
    ),
}


async def group_metrics(ctx, principal, group_id):
    _, members, _ = await ExperimentalParticipationService(ctx).get_group(principal, group_id)
    if principal.role != "ADMIN" and not any(
        m.user_id == principal.user_id and m.status == "ACTIVE" for m in members
    ):
        raise not_found("GROUP_NOT_FOUND", "Group not found")
    now = utcnow()
    async with ctx.sessionmaker() as session:
        rows = (
            await session.execute(
                select(WorkerNode, SubscriptionConnection, SubscriptionSharingPreference)
                .join(SubscriptionConnection, SubscriptionConnection.worker_id == WorkerNode.id)
                .join(
                    SubscriptionSharingPreference,
                    SubscriptionSharingPreference.connection_id == SubscriptionConnection.id,
                )
                .where(SubscriptionSharingPreference.group_id == group_id)
            )
        ).all()
        active_ids = {m.user_id for m in members if m.status == "ACTIVE"}
        rows = [r for r in rows if r[1].owner_id in active_ids]
        fresh = now - timedelta(seconds=ctx.settings.worker_heartbeat_ttl_seconds)

        def online(w):
            return (
                w.revoked_at is None
                and w.status in ("IDLE", "RESERVED", "RUNNING")
                and w.last_heartbeat is not None
                and w.last_heartbeat >= fresh
            )

        counts = (
            await session.execute(
                select(Task.execution_profile, Task.status, func.count())
                .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
                .where(ExperimentalTaskConsent.group_id == group_id)
                .group_by(Task.execution_profile, Task.status)
            )
        ).all()
        modes = {}
        for profile, mode in (("CODEX_LIVE", "LIVE"), ("MOCK", "SIMULATED")):
            by_status = {status: n for p, status, n in counts if p == profile}
            completed = by_status.get("SUCCEEDED", 0)
            failed = sum(by_status.get(s, 0) for s in ("FAILED", "TIMED_OUT"))
            cancelled = by_status.get("CANCELLED", 0)
            terminal = completed + failed + cancelled
            obs = (
                await session.execute(
                    select(
                        func.count(SubscriptionUsageObservation.id),
                        func.sum(SubscriptionUsageObservation.duration_ms),
                        func.sum(SubscriptionUsageObservation.input_tokens),
                        func.sum(SubscriptionUsageObservation.output_tokens),
                        func.count().filter(SubscriptionUsageObservation.event == "RATE_LIMITED"),
                    )
                    .join(
                        TaskAttempt, TaskAttempt.id == SubscriptionUsageObservation.task_attempt_id
                    )
                    .join(Task, Task.id == TaskAttempt.task_id)
                    .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
                    .where(
                        ExperimentalTaskConsent.group_id == group_id,
                        Task.execution_profile == profile,
                    )
                )
            ).one()
            latency = (
                await session.execute(
                    select(
                        func.avg(func.extract("epoch", Task.updated_at - Task.created_at) * 1000)
                    )
                    .join(ExperimentalTaskConsent, ExperimentalTaskConsent.task_id == Task.id)
                    .where(
                        ExperimentalTaskConsent.group_id == group_id,
                        Task.execution_profile == profile,
                        Task.status == "SUCCEEDED",
                    )
                )
            ).scalar_one()
            modes[mode] = {
                "submitted": sum(by_status.values()),
                "completed": completed,
                "failed": failed,
                "cancelled": cancelled,
                "success_rate": completed / terminal if terminal else None,
                "success_rate_denominator": terminal,
                "total_execution_ms": int(obs[1]) if obs[1] is not None else None,
                "observed_input_tokens": int(obs[2]) if obs[2] is not None else None,
                "observed_output_tokens": int(obs[3]) if obs[3] is not None else None,
                "usage_limit_errors": obs[4],
                "observations": obs[0],
                "average_completed_latency_ms": round(float(latency))
                if latency is not None
                else None,
            }
        return {
            "observed_at": now.isoformat(),
            "participating_devices": len(rows),
            "online_devices": sum(online(w) for w, _, _ in rows),
            "available_owner_devices": sum(
                online(w)
                and w.active_tasks == 0
                and c.status == "CONNECTED"
                and c.auth_state == "VALID"
                and p.sharing_state == "ENABLED"
                for w, c, p in rows
            ),
            "availability_meaning": (
                "Fresh heartbeat, idle, connected, valid reported auth, owner "
                "sharing enabled; not a dispatch guarantee."
            ),
            "token_source": "DEVICE_REPORTED_NOT_BILLING_PROOF",
            "plan_remaining": "NOT_AVAILABLE",
            "modes": modes,
        }


async def pilot_report(ctx, principal, task_id):
    view = await ExperimentalParticipationService(ctx).get_task(principal, task_id)
    task, consent = view.task, view.consent
    async with ctx.sessionmaker() as session:
        attempts = list(
            (
                await session.execute(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_id == task_id)
                    .order_by(TaskAttempt.attempt_number)
                )
            ).scalars()
        )
        audits = list(
            (
                await session.execute(
                    select(ExperimentalDispatchAudit).where(
                        ExperimentalDispatchAudit.task_id == task_id
                    )
                )
            ).scalars()
        )
        evidence = []
        for attempt in attempts:
            observations = list(
                (
                    await session.execute(
                        select(SubscriptionUsageObservation).where(
                            SubscriptionUsageObservation.task_attempt_id == attempt.id
                        )
                    )
                ).scalars()
            )
            verification = (
                await session.execute(
                    select(VerificationResult).where(VerificationResult.attempt_id == attempt.id)
                )
            ).scalar_one_or_none()
            evidence.append(
                {
                    "attempt_id": str(attempt.id),
                    "worker_id": str(attempt.worker_id),
                    "status": attempt.status,
                    "started_at": attempt.started_at,
                    "completed_at": attempt.completed_at,
                    "duration_ms": int(
                        (attempt.completed_at - attempt.started_at).total_seconds() * 1000
                    )
                    if attempt.completed_at and attempt.started_at
                    else None,
                    "observations": [
                        {
                            "id": o.id,
                            "event": o.event,
                            "source": o.source,
                            "input_tokens": o.input_tokens,
                            "output_tokens": o.output_tokens,
                        }
                        for o in observations
                    ],
                    "verification": None
                    if verification is None
                    else {
                        "status": verification.status,
                        "id": str(verification.id),
                        "verifier_id": verification.verifier_id,
                    },
                }
            )
        consent_audit_ids = list(
            (
                await session.execute(
                    select(AuditLog.id).where(
                        AuditLog.target_id.in_((str(consent.connection_id), str(task_id))),
                    )
                )
            ).scalars()
        )
    return {
        "schema_version": 1,
        "kind": "PILOT_PROTOCOL_EVIDENCE_NOT_PHYSICAL_DEVICE_PROOF",
        "result": "NOT_RUN"
        if task.execution_profile != "CODEX_LIVE" or not attempts
        else "UNCONFIRMED",
        "task_id": str(task_id),
        "execution_mode": "LIVE" if task.execution_profile == "CODEX_LIVE" else "SIMULATED",
        "task_status": task.status,
        "owner_ref": str(consent.owner_id),
        "requester_ref": str(consent.requester_id),
        "authorization_refs": list({str(a.authorization_id) for a in audits if a.authorization_id}),
        "dispatch_audit_refs": [a.id for a in audits],
        "consent_audit_refs": consent_audit_ids,
        "approval": {
            "status": consent.status,
            "source": consent.consent_source,
            "decided_at": consent.decided_at,
        },
        "attempts": evidence,
        "cli_version": None,
        "auth_method": "MANUAL_REQUIRED",
        "auth_version": None,
        "device_runtime": None,
        "two_distinct_devices_confirmed": False,
        "owner_billing_confirmation": "MANUAL_REQUIRED",
        "cancellation_observation": "MANUAL_REQUIRED",
        "retry_observation": "MANUAL_REQUIRED",
        "note": (
            "Fill private operator/owner observations separately. Server "
            "protocol records, including simulated-device tests, cannot "
            "certify a real two-person pilot. No prompts, results or "
            "credentials exported."
        ),
    }
