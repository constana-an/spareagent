"""Week 4 Milestone A: owner-only Codex subscription adapter against a deterministic fake
Codex CLI (no live credentials in CI)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from packages.provider_sdk.base import PermanentProviderError
from packages.provider_sdk.codex_subscription import (
    CodexSubscriptionAdapter,
    OwnerOnlyViolation,
    SubscriptionLimitReached,
    SubscriptionUnavailable,
)
from packages.provider_sdk.types import ExecutionRequest, ExecutionResult, ProgressEvent
from packages.worker.codex_runtime import CodexRuntime, CodexRuntimeConfig, classify_error
from packages.worker.subscription_auth import (
    FORBIDDEN_ENV,
    AuthMethod,
    classify_login_status,
    forbidden_env_present,
    probe_login,
    sanitized_env,
)

FAKE = str(Path(__file__).resolve().parents[1] / "fakes" / "fake_codex.py")
OWNER = "owner-ref-1"


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    # A careless owner shell: API credentials present. They must never reach Codex.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-THISMUSTNEVERLEAK0000000000")
    monkeypatch.setenv("CODEX_API_KEY", "sk-THISMUSTNEVERLEAK1111111111111")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://paid.example")
    return home


def set_mode(home: Path, mode: str) -> None:
    (home / "mode").write_text(mode)


def calls(home: Path) -> list[dict]:
    path = home / "calls.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def request(**overrides) -> ExecutionRequest:
    values = {
        "task_id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "user_id": OWNER,
        "resource_scope": "OWNER_ONLY",
        "prompt": "Review utils.py for off-by-one errors",
        "repository_url": None,
        "commit_sha": None,
        "max_duration_seconds": 20,
        "max_budget_cents": 1,
        "task_type": "CODE_REVIEW",
    }
    values.update(overrides)
    return ExecutionRequest(**values)


def adapter(**cfg) -> CodexSubscriptionAdapter:
    return CodexSubscriptionAdapter(
        owner_ref=OWNER, runtime=CodexRuntime(CodexRuntimeConfig(codex_bin=FAKE, **cfg))
    )


async def run_all(a: CodexSubscriptionAdapter, req: ExecutionRequest) -> list:
    return [item async for item in a.execute(req)]


# ------------------------------------------------------------------ auth classification
@pytest.mark.parametrize(
    ("output", "code", "method"),
    [
        ("Logged in using ChatGPT", 0, AuthMethod.CHATGPT),
        ("Logged in using an API key - sk-proj-***", 0, AuthMethod.API_KEY),
        ("Not logged in", 1, AuthMethod.NOT_LOGGED_IN),
        ("", 1, AuthMethod.NOT_LOGGED_IN),
        ("something new", 0, AuthMethod.UNKNOWN),
        ("Logged in using ChatGPT", 2, AuthMethod.NOT_LOGGED_IN),
    ],
)
def test_login_status_classification_fails_closed(output, code, method):
    assert classify_login_status(output, code).method == method


def test_sanitized_env_drops_every_api_billing_variable():
    base = {k: "x" for k in FORBIDDEN_ENV} | {"PATH": "/bin", "CODEX_HOME": "/h", "FOO": "1"}
    env = sanitized_env(base)
    assert env == {"PATH": "/bin", "CODEX_HOME": "/h"}
    assert forbidden_env_present(base) == sorted(FORBIDDEN_ENV)


@pytest.mark.parametrize(
    ("message", "cls"),
    [
        ("401 Unauthorized", "AUTH"),
        ("Your access token has expired", "AUTH"),
        ("You've hit your usage limit. Try again in 3 hours.", "USAGE_LIMIT"),
        ("429 Too Many Requests", "USAGE_LIMIT"),
        ("segfault", "RUNTIME"),
    ],
)
def test_error_classification(message, cls):
    assert classify_error(message)[0] == cls


# ------------------------------------------------------------------ acceptance 1, 2, 13
async def test_owner_only_execution_works_without_api_key(codex_home):
    set_mode(codex_home, "chatgpt")
    items = await run_all(adapter(), request())
    result = items[-1]
    assert isinstance(result, ExecutionResult)
    assert any(isinstance(i, ProgressEvent) for i in items)
    assert result.usage.billable is False and result.usage.estimated_cost_cents == 0
    assert result.usage.input_tokens == 120 and result.usage.output_tokens == 48
    kinds = {a.kind for a in result.artifacts}
    assert kinds == {"SUMMARY", "LOG"}
    log = next(a for a in result.artifacts if a.kind == "LOG").content.decode()
    assert "api_key_used=false" in log and "auth=CHATGPT" in log
    # Every child process saw neither API key nor base URL, and ran with forced ChatGPT login.
    seen = calls(codex_home)
    assert len(seen) == 2  # login status + exec
    for call in seen:
        assert call["api_key_seen"] is False
        assert not (set(call["env_names"]) & FORBIDDEN_ENV)
    exec_call = seen[1]["argv"]
    assert 'forced_login_method="chatgpt"' in exec_call and "--json" in exec_call
    assert exec_call[-1] == "-"  # prompt never in argv


async def test_outputs_are_redacted_and_carry_no_credential(codex_home):
    set_mode(codex_home, "chatgpt")
    items = await run_all(adapter(), request())
    blob = json.dumps([str(i) for i in items])
    assert "THISMUSTNEVERLEAK" not in blob
    summary = items[-1].artifacts[0].content.decode()
    assert "abcd1234secret" not in summary and "[REDACTED]" in summary


@pytest.mark.parametrize("mode", ["apikey", "logged_out", "garbled"])
async def test_non_chatgpt_login_fails_closed_without_running(codex_home, mode):
    set_mode(codex_home, mode)
    with pytest.raises(SubscriptionUnavailable):
        await run_all(adapter(), request())
    assert all("exec" not in c["argv"] for c in calls(codex_home))


async def test_missing_codex_binary_fails_closed(codex_home):
    status = await probe_login("/nonexistent/codex")
    assert status.method == AuthMethod.UNAVAILABLE and not status.eligible


async def test_expired_session_detected(codex_home):
    set_mode(codex_home, "expired")
    with pytest.raises(SubscriptionUnavailable):
        await run_all(adapter(), request())


# ------------------------------------------------------------------ owner-only guard
@pytest.mark.parametrize(
    "overrides",
    [
        {"user_id": "someone-else"},
        {"resource_scope": "PUBLIC_MARKETPLACE"},
        {"resource_scope": "EXPERIMENTAL_SHARED_SUBSCRIPTION"},
        {"task_type": "REFACTOR"},
    ],
)
async def test_cross_user_or_out_of_scope_requests_are_refused(codex_home, overrides):
    set_mode(codex_home, "chatgpt")
    with pytest.raises(PermanentProviderError) as exc:
        await run_all(adapter(), request(**overrides))
    if "task_type" not in overrides:
        assert isinstance(exc.value, OwnerOnlyViolation)
    assert calls(codex_home) == []  # refused before Codex was even asked


# ------------------------------------------------------------------ acceptance 11, 12
async def test_usage_limit_is_reported_not_worked_around(codex_home):
    set_mode(codex_home, "limit")
    with pytest.raises(SubscriptionLimitReached) as exc:
        await run_all(adapter(), request())
    assert exc.value.retryable is False
    assert exc.value.reset_hint and "2 hours" in exc.value.reset_hint
    assert len([c for c in calls(codex_home) if "exec" in c["argv"]]) == 1  # no rotation/retry


async def test_crash_is_a_failure_not_a_success(codex_home):
    set_mode(codex_home, "crash")
    with pytest.raises(PermanentProviderError, match="Codex run failed"):
        await run_all(adapter(), request())


async def test_timeout_kills_the_run(codex_home):
    set_mode(codex_home, "slow")
    with pytest.raises(TimeoutError):
        await run_all(adapter(), request(max_duration_seconds=1))


async def test_cancellation_kills_the_process_group(codex_home):
    set_mode(codex_home, "slow")
    a = adapter()
    req = request()

    async def consume():
        return await run_all(a, req)

    job = asyncio.create_task(consume())
    for _ in range(100):
        await asyncio.sleep(0.05)
        if req.attempt_id in a.runtime._procs:
            break
    await a.cancel(req.attempt_id)
    with pytest.raises(PermanentProviderError, match="cancelled"):
        await asyncio.wait_for(job, 10)


async def test_estimate_is_zero_and_capabilities_personal(codex_home):
    a = adapter()
    assert await a.estimate_cents(request()) == 0
    assert a.capabilities().resource_type == "PERSONAL_SUBSCRIPTION"


# ------------------------------------------------------------------ owner CLI + local controls
def test_codex_check_cli_reports_subscription_run(codex_home, tmp_path, capsys):
    from packages.worker.cli import main

    set_mode(codex_home, "chatgpt")
    assert main(["--state-dir", str(tmp_path / "s"), "codex-check", "--codex-bin", FAKE]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["result"] == "OWNER_ONLY_SUBSCRIPTION_RUN_OK"
    assert report["auth_method"] == "CHATGPT"
    assert report["api_billing_env_passed_to_codex"] == []
    assert "OPENAI_API_KEY" in report["api_billing_env_in_shell"]  # names only, never values
    assert "THISMUSTNEVERLEAK" not in json.dumps(report)
    assert report["inference"]["billable"] is False


def test_codex_check_cli_fails_closed_on_api_key_login(codex_home, tmp_path, capsys):
    from packages.worker.cli import main

    set_mode(codex_home, "apikey")
    assert main(["--state-dir", str(tmp_path / "s"), "codex-check", "--codex-bin", FAKE]) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "FAILED_CLOSED"


def test_local_kill_switch_roundtrip(tmp_path, capsys):
    from packages.worker.cli import main
    from packages.worker.local_controls import LocalSharingControl

    state = tmp_path / "s"
    control = LocalSharingControl(state)
    assert control.allows_shared_work()
    main(["--state-dir", str(state), "sharing", "pause"])
    assert not control.allows_shared_work()
    assert (control.path.stat().st_mode & 0o777) == 0o600
    main(["--state-dir", str(state), "sharing", "resume"])
    assert control.allows_shared_work()
    capsys.readouterr()


async def test_agent_declines_experimental_lease_when_locally_paused(tmp_path):
    from packages.worker.agent import WorkerAgent
    from packages.worker.local_controls import LocalSharingControl

    class Client:
        accepted: list[str] = []

        async def accept(self, lease_id):  # pragma: no cover - must not be reached
            self.accepted.append(lease_id)
            raise AssertionError("experimental lease must not be accepted while paused")

    control = LocalSharingControl(tmp_path)
    control.pause()
    lease = {
        "lease_id": "l1",
        "attempt_id": "a1",
        "resource_scope": "EXPERIMENTAL_SHARED_SUBSCRIPTION",
        "task": {},
    }
    for ctl in (control, None):  # paused, and never opted in locally
        agent = WorkerAgent(Client(), sharing_control=ctl)
        assert await agent.execute(lease) is None
    assert Client.accepted == []
