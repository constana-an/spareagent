"""Waiting and execution clocks meet exactly at their own deadlines."""

from datetime import UTC, datetime, timedelta

from db.models import Task, TaskAttempt
from services.scheduler.lease_service import task_deadline_passed


def test_experimental_clocks_and_marketplace_deadline():
    created = datetime(2026, 10, 19, tzinfo=UTC)
    task = Task(
        created_at=created,
        max_duration_seconds=60,
        resource_scope="EXPERIMENTAL_SHARED_SUBSCRIPTION",
    )
    assert not task_deadline_passed(task, created + timedelta(seconds=599))
    assert task_deadline_passed(task, created + timedelta(seconds=600))
    attempt = TaskAttempt(started_at=created + timedelta(seconds=599))
    assert not task_deadline_passed(task, created + timedelta(seconds=658), attempt)
    assert task_deadline_passed(task, created + timedelta(seconds=659), attempt)
    task.resource_scope = "PUBLIC_MARKETPLACE"
    assert not task_deadline_passed(task, created + timedelta(seconds=59), attempt)
    assert task_deadline_passed(task, created + timedelta(seconds=60), attempt)


def test_ten_minute_execution_is_independent_of_submission_age():
    created = datetime(2026, 10, 19, tzinfo=UTC)
    task = Task(
        created_at=created,
        max_duration_seconds=600,
        resource_scope="EXPERIMENTAL_SHARED_SUBSCRIPTION",
    )
    attempt = TaskAttempt(started_at=created + timedelta(seconds=599))
    assert not task_deadline_passed(task, created + timedelta(seconds=1198), attempt)
    assert task_deadline_passed(task, created + timedelta(seconds=1199), attempt)


def test_requeued_task_gets_a_fresh_waiting_window():
    created = datetime(2026, 10, 19, tzinfo=UTC)
    handed_back = created + timedelta(minutes=25)
    task = Task(
        created_at=created,
        updated_at=handed_back,
        max_duration_seconds=600,
        resource_scope="EXPERIMENTAL_SHARED_SUBSCRIPTION",
    )
    assert task_deadline_passed(task, handed_back + timedelta(seconds=1))
    assert not task_deadline_passed(task, handed_back + timedelta(seconds=599), requeued=True)
    assert task_deadline_passed(task, handed_back + timedelta(seconds=600), requeued=True)


def test_experimental_tasks_retry_once_and_only_after_an_interruption():
    from services.scheduler.lease_service import EXPERIMENTAL_MAX_ATTEMPTS, retry_allowed

    now = datetime(2026, 10, 19, tzinfo=UTC)
    task = Task(
        created_at=now,
        max_duration_seconds=600,
        max_attempts=EXPERIMENTAL_MAX_ATTEMPTS,
        resource_scope="EXPERIMENTAL_SHARED_SUBSCRIPTION",
    )
    first = TaskAttempt(attempt_number=1, started_at=now)
    assert retry_allowed(task, first, now, "WORKER_SHUTDOWN")
    assert retry_allowed(task, first, now, "LEASE_EXPIRED")
    assert not retry_allowed(task, first, now, "EXECUTION_ERROR")
    assert not retry_allowed(task, first, now, "VERIFICATION_FAILED")
    second = TaskAttempt(attempt_number=2, started_at=now)
    assert not retry_allowed(task, second, now, "WORKER_SHUTDOWN")
    task.resource_scope = "PUBLIC_MARKETPLACE"
    assert retry_allowed(task, first, now, "EXECUTION_ERROR")
