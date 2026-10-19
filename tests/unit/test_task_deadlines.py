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
