import pytest

from services.tasks.state_machine import (
    ALLOWED,
    EVENT_TYPE_FOR_STATUS,
    TERMINAL,
    InvalidTransition,
    TaskStatus,
    can_transition,
    validate_transition,
)

S = TaskStatus


def test_happy_path_is_allowed():
    path = [S.CREATED, S.QUEUED, S.RESERVED, S.RUNNING, S.VERIFYING, S.SUCCEEDED]
    for current, target in zip(path, path[1:], strict=False):
        validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.QUEUED, S.CANCELLED),
        (S.RUNNING, S.CANCELLING),
        (S.CANCELLING, S.CANCELLED),
        (S.RESERVED, S.QUEUED),
        (S.RUNNING, S.RETRY_PENDING),
        (S.RETRY_PENDING, S.QUEUED),
    ],
)
def test_prd_side_transitions(current, target):
    assert can_transition(current, target)


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_terminal_states_have_no_exits(terminal):
    assert ALLOWED[terminal] == frozenset()
    for target in TaskStatus:
        with pytest.raises(InvalidTransition):
            validate_transition(terminal, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.QUEUED, S.SUCCEEDED),
        (S.QUEUED, S.RUNNING),
        (S.CREATED, S.RUNNING),
        (S.RESERVED, S.SUCCEEDED),
        (S.CANCELLING, S.SUCCEEDED),
        (S.RUNNING, S.SUCCEEDED),  # must pass through VERIFYING
    ],
)
def test_illegal_shortcuts_rejected(current, target):
    with pytest.raises(InvalidTransition):
        validate_transition(current, target)


def test_every_status_maps_to_contract_event_type():
    import json
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json").read_text()
    )
    allowed = set(schema["properties"]["type"]["enum"])
    assert set(EVENT_TYPE_FOR_STATUS) == set(TaskStatus)
    assert set(EVENT_TYPE_FOR_STATUS.values()) <= allowed
