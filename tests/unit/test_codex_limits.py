"""Plan usage read from the owner's Codex (`codex app-server`) and the menu-bar fallback,
against the deterministic fake Codex CLI (no network, no credentials)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.worker.codex_limits import parse_rate_limits, quota_status, read_rate_limits

FAKE = str(Path(__file__).resolve().parents[1] / "fakes" / "fake_codex.py")
NOW = 1_792_700_000.0


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-THISMUSTNEVERLEAK0000000000")
    return home


async def test_reads_both_windows_without_credits_or_api_keys(codex_home):
    limits = await read_rate_limits(FAKE)
    assert limits == {
        "windows": [
            {"minutes": 300, "remaining_percent": 83, "resets_at": 1792712254},
            {"minutes": 10080, "remaining_percent": 97, "resets_at": 1793299054},
        ],
        "reached": False,
    }
    call = json.loads((codex_home / "calls.jsonl").read_text().splitlines()[-1])
    assert call["argv"] == ["app-server"] and not call["api_key_seen"]


@pytest.mark.parametrize("mode", ["app_server_error", "app_server_hang"])
async def test_no_answer_means_no_numbers(codex_home, mode):
    (codex_home / "mode").write_text(mode)
    assert await read_rate_limits(FAKE, limit_seconds=2) is None


async def test_a_wrapper_child_holding_stdout_does_not_hang_the_probe(codex_home):
    (codex_home / "mode").write_text("app_server_wrapper")
    limits = await asyncio.wait_for(read_rate_limits(FAKE), 10)
    assert limits is not None and limits["windows"][0]["remaining_percent"] == 83


async def test_missing_binary_means_no_numbers(tmp_path):
    assert await read_rate_limits(str(tmp_path / "no-codex")) is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"rateLimits": None},
        {"rateLimits": {"primary": {"usedPercent": "17", "windowDurationMins": 300}}},
        {"rateLimits": {"primary": {"usedPercent": 17, "windowDurationMins": 0}}},
        {"rateLimits": {"primary": {"usedPercent": True, "windowDurationMins": 300}}},
    ],
)
def test_unusable_results_are_ignored(raw):
    assert parse_rate_limits(raw) is None


def test_percentages_are_clamped_and_windows_sorted_shortest_first():
    limits = parse_rate_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": 140, "windowDurationMins": 10080, "resetsAt": None},
                "secondary": {"usedPercent": -5.4, "windowDurationMins": 300, "resetsAt": 7},
                "rateLimitReachedType": "primary",
            }
        }
    )
    assert limits == {
        "windows": [
            {"minutes": 300, "remaining_percent": 100, "resets_at": 7},
            {"minutes": 10080, "remaining_percent": 0, "resets_at": None},
        ],
        "reached": True,
    }


def reading(*remaining: int, reached: bool = False) -> dict:
    return {
        "windows": [
            {"minutes": 300 * (i + 1), "remaining_percent": r, "resets_at": 1}
            for i, r in enumerate(remaining)
        ],
        "reached": reached,
    }


@pytest.mark.parametrize(
    ("limits", "level"),
    [
        (reading(83, 97), "ok"),
        (reading(9, 97), "low"),
        (reading(60, 0), "exhausted"),
        (reading(40, 50, reached=True), "exhausted"),
    ],
)
def test_fresh_reading_sets_the_level(limits, level):
    status = quota_status(limits, NOW - 30, [], NOW)
    assert status["source"] == "codex" and status["level"] == level
    assert status["windows"] == limits["windows"]


def task(status: str, minutes_ago: float, category: str | None = None) -> dict:
    created = datetime.fromtimestamp(NOW, UTC) - timedelta(minutes=minutes_ago)
    return {
        "status": status,
        "created_at": created.isoformat(),
        "failure": {"category": category} if category else None,
    }


@pytest.mark.parametrize(
    ("tasks", "level"),
    [
        ([], "ok"),
        ([task("FAILED", 30, "USAGE_LIMIT")], "exhausted"),
        ([task("FAILED", 6 * 60, "USAGE_LIMIT")], "ok"),
        ([task("FAILED", 60, "USAGE_LIMIT"), task("SUCCEEDED", 10)], "ok"),
        ([task("FAILED", 60, "USAGE_LIMIT"), task("RUNNING", 5)], "exhausted"),
        ([task("FAILED", 20, "LOGIN")], "ok"),
    ],
)
def test_without_numbers_the_last_finished_task_decides(tasks, level):
    stale = reading(83, 97)
    status = quota_status(stale, NOW - 3600, tasks, NOW)
    assert status == {"source": "fallback", "level": level, "windows": []}


class LimitsCodex:
    codex_bin = "codex"

    def __init__(self, answers):
        self.answers = list(answers)
        self.reads = 0

    def installed(self):
        return True

    async def rate_limits(self):
        self.reads += 1
        return self.answers.pop(0) if self.answers else None


async def test_controller_reads_usage_when_due_and_after_each_task(tmp_path):
    from types import SimpleNamespace

    from packages.worker.app import Controller
    from tests.unit.test_one_click_app import BUNDLE, FakeClient, reachable

    codex = LimitsCodex([reading(83, 97), None])
    c = Controller(
        tmp_path, BUNDLE, codex=codex, probe=reachable, client_factory=lambda s: FakeClient([])
    )
    assert c.snapshot()["quota"] == {"source": "fallback", "level": "ok", "windows": []}
    c.refresh_limits()
    await c._limits_job
    assert c.snapshot()["quota"]["windows"] == reading(83, 97)["windows"]
    c.refresh_limits()  # not due yet
    assert codex.reads == 1
    c._agent = SimpleNamespace(active=1)
    c.refresh_limits()
    c._agent.active = 0  # a task ended: read again, a failed read keeps the last numbers
    c.refresh_limits()
    await c._limits_job
    assert codex.reads == 2
    assert c.snapshot()["quota"]["source"] == "codex"
