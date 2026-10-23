import pytest
from pydantic import ValidationError

from apps.api.schemas.experiments import ExperimentalTaskCreate
from apps.api.schemas.tasks import IDEMPOTENCY_KEY, TaskCreate
from services.tasks.service import request_digest

BASE = {
    "task_type": "CODE_FIX",
    "repository_url": "https://github.com/octo/repo",
    "commit_sha": "A" * 40,
    "prompt": "fix it",
    "max_budget_cents": 100,
    "max_duration_seconds": 60,
}


def test_experimental_request_defaults_to_general_and_keeps_legacy_alias():
    body = {
        "requirement": "Write a short fictional notice",
        "max_duration_seconds": 60,
        "group_id": "11111111-1111-4111-8111-111111111111",
        "connection_id": "22222222-2222-4222-8222-222222222222",
    }
    assert ExperimentalTaskCreate(**body).normalized()["kind"] == "GENERAL"
    assert ExperimentalTaskCreate(**body, task_type="CODE_REVIEW").kind == "CODE_REVIEW"
    with pytest.raises(ValidationError, match="not both"):
        ExperimentalTaskCreate(**body, kind="GENERAL", task_type="CODE_REVIEW")


def test_valid_and_normalized():
    model = TaskCreate(**BASE)
    assert model.commit_sha == "a" * 40


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/octo/repo",
        "https://gitlab.com/octo/repo",
        "https://github.com.evil.com/octo/repo",
        "https://user:pw@github.com/octo/repo",
        "https://github.com:8443/octo/repo",
        "https://github.com/octo/repo?x=1",
        "https://github.com/octo/repo#frag",
        "https://github.com/octo",
        "https://github.com/octo/repo/extra",
        "https://github.com/octo/..",
        "https://github.com/octo/repo;rm -rf",
        "file:///etc/passwd",
    ],
)
def test_repository_url_rejected(url):
    with pytest.raises(ValidationError):
        TaskCreate(**{**BASE, "repository_url": url})


@pytest.mark.parametrize("sha", ["abc", "g" * 40, "a" * 39, "a" * 41, ""])
def test_task_002_bad_sha(sha):
    with pytest.raises(ValidationError):
        TaskCreate(**{**BASE, "commit_sha": sha})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_budget_cents", 0),
        ("max_budget_cents", 10_000_001),
        ("max_budget_cents", 1.5),
        ("max_budget_cents", "100"),
        ("max_budget_cents", True),
        ("max_duration_seconds", 9),
        ("max_duration_seconds", 7201),
        ("prompt", ""),
        ("prompt", "   "),
        ("prompt", "x" * 12001),
        ("task_type", "RUN_SHELL"),
    ],
)
def test_field_bounds(field, value):
    with pytest.raises(ValidationError):
        TaskCreate(**{**BASE, field: value})


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        TaskCreate(**{**BASE, "consumer_id": "someone-else"})


def test_digest_is_canonical():
    a = TaskCreate(**BASE).model_dump()
    b = TaskCreate(**dict(reversed(list(BASE.items())))).model_dump()
    assert request_digest(a) == request_digest(b)
    assert request_digest(a) != request_digest({**a, "prompt": "other"})


@pytest.mark.parametrize("key", ["short", "x" * 129, "has space here", "semi;colon-key"])
def test_bad_idempotency_keys(key):
    assert not IDEMPOTENCY_KEY.fullmatch(key)
