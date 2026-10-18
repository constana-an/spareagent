"""Scheduler, policy, leases, retries, budget and metering invariants."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from packages.provider_sdk.types import ArtifactPayload, UsageReport
from services.execution.orchestrator import Orchestrator
from services.metering.service import record_usage
from services.scheduler.dispatcher import Dispatcher
from services.scheduler.lease_service import CompletionInput, LeaseService
from tests.conftest import (
    PROVIDER,
    PUBLIC_RESOURCE,
    PUBLIC_WORKER,
    create_task,
    drain_worker,
    execute,
    fetch_val,
)

pytestmark = pytest.mark.db

GOOD_ARTIFACTS = (
    ArtifactPayload("SUMMARY", "s.txt", "text/plain", b"summary"),
    ArtifactPayload("PATCH", "p.diff", "text/x-diff", b"diff --git a/x b/x\n"),
)


def usage(attempt_id, req_id=None):
    return UsageReport("mock", req_id or f"mock-task-{attempt_id}", "mock-code-v1", 3, 4, 10)


async def reserve(client, ctx, **task_overrides):
    resp = await create_task(client, **task_overrides)
    task_id = uuid.UUID(resp.json()["id"])
    attempt_id = await Dispatcher(ctx).dispatch_once()
    lease_id = await fetch_val(ctx, "SELECT lease_id FROM task_attempts WHERE id=:a", a=attempt_id)
    return task_id, attempt_id, lease_id


async def status_of(ctx, task_id):
    return await fetch_val(ctx, "SELECT status FROM tasks WHERE id=:t", t=task_id)


async def disable_public_worker(ctx):
    await execute(ctx, "UPDATE worker_nodes SET status='OFFLINE', last_heartbeat=NULL")


async def add_personal_worker(ctx, *, approved=True):
    rid, wid = uuid.uuid4(), uuid.uuid4()
    await execute(
        ctx,
        "INSERT INTO resources(id,owner_id,resource_type,authorization_status,public_eligible) "
        "VALUES (:r,:p,'PERSONAL_SUBSCRIPTION',:s,false)",
        r=rid,
        p=PROVIDER,
        s="APPROVED" if approved else "PENDING",
    )
    await execute(
        ctx,
        "INSERT INTO worker_nodes(id,resource_id,name,status,last_heartbeat) "
        "VALUES (:w,:r,'personal','IDLE',now())",
        w=wid,
        r=rid,
    )
    return rid, wid


# ------------------------------------------------------------------ policy
async def test_policy_001_personal_public_eligible_violates_db_constraint(ctx):
    with pytest.raises(IntegrityError, match="no_personal_public"):
        await execute(
            ctx,
            "INSERT INTO resources(owner_id,resource_type,authorization_status,public_eligible) "
            "VALUES (:p,'PERSONAL_SUBSCRIPTION','APPROVED',true)",
            p=PROVIDER,
        )
    with pytest.raises(IntegrityError, match="public_requires_approval"):
        await execute(
            ctx,
            "INSERT INTO resources(owner_id,resource_type,public_eligible) "
            "VALUES (:p,'MANAGED_API',true)",
            p=PROVIDER,
        )


async def test_policy_002_only_personal_worker_task_stays_queued(client, ctx):
    await disable_public_worker(ctx)
    _, personal_worker = await add_personal_worker(ctx)
    task_id = uuid.UUID((await create_task(client)).json()["id"])
    # The worker loop drives (and heartbeats) only the personal node: it is live, approved
    # for its owner, idle - and must still never receive a marketplace task.
    await drain_worker(ctx, nodes=(personal_worker,))
    assert await status_of(ctx, task_id) == "QUEUED"
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 0
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 0


async def test_policy_002_scheduler_rejects_personal_even_if_constraint_bypassed(client, ctx):
    """Malicious fixture: drop the CHECK constraint, flag a personal resource public."""
    await disable_public_worker(ctx)
    rid, _ = await add_personal_worker(ctx)
    async with ctx.engine.connect() as conn:
        trans = await conn.begin()
        from sqlalchemy import text

        await conn.execute(text("ALTER TABLE resources DROP CONSTRAINT no_personal_public"))
        await conn.execute(
            text("UPDATE resources SET public_eligible=true WHERE id=:r"), {"r": rid}
        )
        session_ctx = ctx
        # run the scheduler's worker query inside this tampered transaction
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(bind=conn) as session:
            eligible = await Dispatcher(session_ctx).eligible_workers(session)
        await trans.rollback()  # restore the constraint
    assert eligible == []


async def test_no_approved_workers_means_queued(client, ctx):
    await disable_public_worker(ctx)
    task_id = uuid.UUID((await create_task(client)).json()["id"])
    assert await Dispatcher(ctx).dispatch_once() is None
    assert await status_of(ctx, task_id) == "QUEUED"


async def test_stale_heartbeat_worker_not_selected(client, ctx):
    await execute(ctx, "UPDATE worker_nodes SET last_heartbeat = now() - interval '10 minutes'")
    task_id = uuid.UUID((await create_task(client)).json()["id"])
    assert await Dispatcher(ctx).dispatch_once() is None
    assert await status_of(ctx, task_id) == "QUEUED"


async def test_revoked_resource_not_selected(client, ctx):
    await execute(
        ctx,
        "UPDATE resources SET public_eligible=false, authorization_status='REVOKED' WHERE id=:r",
        r=PUBLIC_RESOURCE,
    )
    await create_task(client)
    assert await Dispatcher(ctx).dispatch_once() is None


async def test_capacity_respected_and_deterministic_tiebreak(client, ctx):
    second = uuid.UUID("00000000-0000-4000-8000-000000000001")  # sorts before PUBLIC_WORKER
    await execute(
        ctx,
        "INSERT INTO worker_nodes(id,resource_id,name,status,last_heartbeat) "
        "VALUES (:w,:r,'second','IDLE',now())",
        w=second,
        r=PUBLIC_RESOURCE,
    )
    for _ in range(3):
        await create_task(client)
    dispatcher = Dispatcher(ctx)
    a1 = await dispatcher.dispatch_once()
    a2 = await dispatcher.dispatch_once()
    a3 = await dispatcher.dispatch_once()
    assert a1 and a2 and a3 is None  # two workers x capacity 1
    w1 = await fetch_val(ctx, "SELECT worker_id FROM task_attempts WHERE id=:a", a=a1)
    assert w1 == second
    assert await fetch_val(ctx, "SELECT max(active_tasks) FROM worker_nodes") == 1


# ------------------------------------------------------------------ budget
async def test_bill_002_insufficient_budget_never_starts_attempt(client, ctx):
    task_id = uuid.UUID((await create_task(client, max_budget_cents=1)).json()["id"])
    await drain_worker(ctx)
    assert await status_of(ctx, task_id) == "FAILED"
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 0
    reason = await fetch_val(
        ctx,
        "SELECT payload->>'reason' FROM execution_events WHERE event_type='task.failed'",
    )
    assert reason == "BUDGET_INSUFFICIENT"


async def test_retry_blocked_when_budget_spent(client, ctx):
    # budget 12 covers exactly one attempt (10 + 10% margin = 11); the retry cannot start.
    task_id = uuid.UUID(
        (await create_task(client, max_budget_cents=12, prompt="x [mock:transient]")).json()["id"]
    )
    await drain_worker(ctx)
    assert await status_of(ctx, task_id) == "FAILED"
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 1


# ------------------------------------------------------------------ leases
async def test_lease_001_expiration_allows_bounded_retry(client, ctx):
    task_id, attempt1, _ = await reserve(client, ctx)
    leases = LeaseService(ctx)
    await execute(ctx, "UPDATE task_attempts SET leased_until = now() - interval '1 second'")
    assert await leases.recover_expired() == 1
    assert await status_of(ctx, task_id) == "QUEUED"
    assert await fetch_val(ctx, "SELECT status FROM task_attempts WHERE id=:a", a=attempt1) == (
        "EXPIRED"
    )
    assert await fetch_val(ctx, "SELECT active_tasks FROM worker_nodes") == 0

    attempt2 = await Dispatcher(ctx).dispatch_once()
    assert (
        await fetch_val(ctx, "SELECT attempt_number FROM task_attempts WHERE id=:a", a=attempt2)
        == 2
    )
    lease2 = await fetch_val(ctx, "SELECT lease_id FROM task_attempts WHERE id=:a", a=attempt2)
    await leases.accept(lease2, PUBLIC_WORKER)  # crash after start: RUNNING lease lapses
    await execute(ctx, "UPDATE task_attempts SET leased_until = now() - interval '1 second'")
    assert await leases.recover_expired() == 1
    assert await status_of(ctx, task_id) == "FAILED"
    assert await Dispatcher(ctx).dispatch_once() is None
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 2


async def test_running_lease_expiry_goes_through_retry_pending(client, ctx):
    task_id, _, lease = await reserve(client, ctx)
    leases = LeaseService(ctx)
    await leases.accept(lease, PUBLIC_WORKER)
    await execute(ctx, "UPDATE task_attempts SET leased_until = now() - interval '1 second'")
    await leases.recover_expired()
    assert await status_of(ctx, task_id) == "RETRY_PENDING"
    await drain_worker(ctx)
    assert await status_of(ctx, task_id) == "SUCCEEDED"


async def test_lease_002_duplicate_completion_finalizes_once(client, ctx):
    task_id, attempt, lease = await reserve(client, ctx)
    leases = LeaseService(ctx)
    await leases.accept(lease, PUBLIC_WORKER)
    done = CompletionInput(
        "final-key-0001", True, "ok", artifacts=GOOD_ARTIFACTS, usage=usage(attempt)
    )
    first = await leases.complete(lease, PUBLIC_WORKER, done)
    second = await leases.complete(lease, PUBLIC_WORKER, done)
    assert first.task_status == second.task_status == "SUCCEEDED"
    assert not first.replayed and second.replayed
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 1
    assert (
        await fetch_val(
            ctx, "SELECT count(*) FROM execution_events WHERE event_type='task.succeeded'"
        )
        == 1
    )
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 2
    from services.errors import DomainError

    with pytest.raises(DomainError) as info:
        await leases.complete(lease, PUBLIC_WORKER, CompletionInput("other-key-0001", False))
    assert info.value.status_code == 409 and info.value.code == "ALREADY_FINALIZED"
    assert await fetch_val(ctx, "SELECT active_tasks FROM worker_nodes") == 0


async def test_lease_003_expired_lease_completion_is_409(client, ctx):
    from services.errors import DomainError

    task_id, attempt, lease = await reserve(client, ctx)
    leases = LeaseService(ctx)
    await leases.accept(lease, PUBLIC_WORKER)
    await execute(ctx, "UPDATE task_attempts SET leased_until = now() - interval '1 second'")
    with pytest.raises(DomainError) as info:
        await leases.complete(lease, PUBLIC_WORKER, CompletionInput("late-key-0001", True))
    assert info.value.status_code == 409 and info.value.code == "LEASE_EXPIRED"
    await leases.recover_expired()
    with pytest.raises(DomainError) as info:  # revoked (EXPIRED) lease stays rejected
        await leases.complete(lease, PUBLIC_WORKER, CompletionInput("late-key-0002", True))
    assert info.value.status_code == 409
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 0


async def test_worker_cannot_act_on_foreign_lease(client, ctx):
    from services.errors import DomainError

    _, _, lease = await reserve(client, ctx)
    with pytest.raises(DomainError) as info:
        await LeaseService(ctx).accept(lease, uuid.uuid4())
    assert info.value.status_code == 403
    with pytest.raises(DomainError) as info:
        await LeaseService(ctx).accept(uuid.uuid4(), PUBLIC_WORKER)
    assert info.value.status_code == 404


async def test_expired_reserved_lease_cannot_be_accepted(client, ctx):
    from services.errors import DomainError

    _, _, lease = await reserve(client, ctx)
    await execute(ctx, "UPDATE task_attempts SET leased_until = now() - interval '1 second'")
    with pytest.raises(DomainError) as info:
        await LeaseService(ctx).accept(lease, PUBLIC_WORKER)
    assert info.value.code == "LEASE_EXPIRED"


# ------------------------------------------------------------------ retries / failures
async def test_transient_error_retries_then_succeeds(client, ctx):
    task_id = uuid.UUID((await create_task(client, prompt="x [mock:transient]")).json()["id"])
    await drain_worker(ctx)
    assert await status_of(ctx, task_id) == "SUCCEEDED"
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 2
    types = await fetch_val(
        ctx, "SELECT string_agg(event_type, ',' ORDER BY seq) FROM execution_events"
    )
    assert "task.retrying" in types
    # each attempt has exactly one usage row
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 2


async def test_permanent_error_fails_without_retry(client, ctx):
    task_id = uuid.UUID((await create_task(client, prompt="[mock:fail]")).json()["id"])
    await drain_worker(ctx)
    assert await status_of(ctx, task_id) == "FAILED"
    assert await fetch_val(ctx, "SELECT count(*) FROM task_attempts") == 1


async def test_verification_failure_fails_task_without_artifacts(client, ctx):
    task_id = uuid.UUID((await create_task(client, prompt="[mock:bad-patch]")).json()["id"])
    await drain_worker(ctx)
    assert await status_of(ctx, task_id) == "FAILED"
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 0


async def test_task_003_cancel_while_running_publishes_nothing(client, ctx):
    from tests.conftest import USER_A, headers

    task_id, attempt, lease = await reserve(client, ctx)
    leases = LeaseService(ctx)
    await leases.accept(lease, PUBLIC_WORKER)
    resp = await client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_A))
    assert resp.json()["status"] == "CANCELLING"
    assert await leases.progress(lease, PUBLIC_WORKER, "still working", 50) is False
    outcome = await leases.complete(
        lease,
        PUBLIC_WORKER,
        CompletionInput(
            "cancel-key-0001", True, "ok", artifacts=GOOD_ARTIFACTS, usage=usage(attempt)
        ),
    )
    assert outcome.task_status == "CANCELLED"
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 0
    assert await fetch_val(ctx, "SELECT active_tasks FROM worker_nodes") == 0


async def test_orchestrator_observes_cancellation(client, ctx):
    from tests.conftest import USER_A, headers

    task_id, attempt, lease = await reserve(client, ctx)

    class CancelOnFirstProgress(LeaseService):
        async def progress(self, *args, **kwargs):
            await client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_A))
            return await super().progress(*args, **kwargs)

    orchestrator = Orchestrator(ctx)
    orchestrator.leases = CancelOnFirstProgress(ctx)
    assert await orchestrator.run_attempt(attempt) == "CANCELLED"
    assert await fetch_val(ctx, "SELECT count(*) FROM artifacts") == 0


async def test_task_timeout_while_queued(client, ctx):
    await disable_public_worker(ctx)
    task_id = uuid.UUID((await create_task(client, max_duration_seconds=10)).json()["id"])
    await execute(ctx, "UPDATE tasks SET created_at = now() - interval '1 minute'")
    await execute(
        ctx, "UPDATE worker_nodes SET status='IDLE', last_heartbeat=now()"
    )  # worker back, but too late
    await Dispatcher(ctx).dispatch_once()
    assert await status_of(ctx, task_id) == "TIMED_OUT"


# ------------------------------------------------------------------ metering
async def test_bill_001_duplicate_provider_response_one_usage_row(client, ctx):
    _, attempt, _ = await reserve(client, ctx)
    report = usage(attempt, "upstream-response-42")
    async with ctx.sessionmaker() as session, session.begin():
        assert await record_usage(session, report, task_attempt_id=attempt) is True
    async with ctx.sessionmaker() as session, session.begin():
        assert await record_usage(session, report, task_attempt_id=attempt) is False
    assert await fetch_val(ctx, "SELECT count(*) FROM usage_records") == 1


async def test_usage_requires_exactly_one_owner(ctx):
    async with ctx.sessionmaker() as session:
        with pytest.raises(ValueError):
            await record_usage(session, usage("x"))
