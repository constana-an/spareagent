"""Recheck durable LIVE authority while running and before artifact publication."""

from sqlalchemy import select

from db.models import ExperimentalDispatchAudit, ExperimentalTaskConsent
from services.subscriptions.policy import ExperimentalSharingPolicy, Mode, Reason
from services.tasks.state_machine import TaskStatus, transition

# Pause and resource limits govern new work. Revocation/expiry/scope/auth govern all work.
NEW_WORK_ONLY = frozenset(
    {Reason.OWNER_PAUSED, Reason.PROVIDER_OFFLINE, Reason.CONCURRENCY_LIMIT, Reason.DAILY_LIMIT}
)


async def enforce_live_authority(ctx, session, task):
    if task.execution_profile != "CODEX_LIVE" or task.status not in (
        TaskStatus.RUNNING,
        TaskStatus.VERIFYING,
    ):
        return
    # Lazy import avoids the existing dispatcher/lease-service dependency cycle.
    from services.experiments.dispatcher import ExperimentalDispatcher

    consent = (
        await session.execute(
            select(ExperimentalTaskConsent).where(ExperimentalTaskConsent.task_id == task.id)
        )
    ).scalar_one()
    snap, _, _ = await ExperimentalDispatcher(ctx).snapshot(session, task, consent)
    reasons = [
        r.value
        for r in ExperimentalSharingPolicy.evaluate(snap, mode=Mode.LIVE).reasons
        if r not in NEW_WORK_ONLY
    ]
    approved_auth = (
        await session.execute(
            select(ExperimentalDispatchAudit.authorization_id)
            .where(
                ExperimentalDispatchAudit.task_id == task.id,
                ExperimentalDispatchAudit.decision == "ALLOW",
            )
            .order_by(ExperimentalDispatchAudit.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        approved_auth is None
        or snap.authorization is None
        or approved_auth != snap.authorization.id
    ):
        reasons.append("AUTHORIZATION_CHANGED")
    if reasons:
        await transition(
            session,
            task,
            TaskStatus.CANCELLING,
            payload={
                "reason": "LIVE_AUTHORITY_WITHDRAWN",
                "policy_reasons": reasons,
                "result_summary": "Stopped: LIVE authorization or consent is no longer valid",
            },
        )
