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
