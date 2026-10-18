import socket

import pytest

from packages.provider_sdk.base import PermanentProviderError, TransientProviderError
from packages.provider_sdk.mock import MockProviderAdapter
from packages.provider_sdk.types import (
    ArtifactPayload,
    ChatInvocation,
    ChatMessage,
    ExecutionRequest,
    ExecutionResult,
    ProgressEvent,
)
from services.execution import verifier


def req(prompt="do it", attempt=1, task_id="t-1"):
    return ExecutionRequest(
        task_id=task_id,
        attempt_id=f"a-{attempt}",
        user_id="u",
        resource_scope="PUBLIC_MARKETPLACE",
        prompt=prompt,
        repository_url="https://github.com/example/repo",
        commit_sha="0" * 40,
        max_duration_seconds=60,
        max_budget_cents=100,
        attempt_number=attempt,
    )


async def run(adapter, request):
    items = [item async for item in adapter.execute(request)]
    return items


@pytest.fixture
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("mock adapter attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", guard)


async def test_deterministic_output_by_task_id(no_network):
    adapter = MockProviderAdapter()
    a = await run(adapter, req())
    b = await run(adapter, req())
    assert a == b
    c = await run(adapter, req(task_id="t-2"))
    assert a[-1].summary != c[-1].summary
    assert isinstance(a[0], ProgressEvent) and isinstance(a[-1], ExecutionResult)
    kinds = {art.kind for art in a[-1].artifacts}
    assert kinds == {"SUMMARY", "PATCH", "TEST_REPORT", "LOG"}
    assert verifier.verify(a[-1].artifacts).ok


async def test_transient_fault_only_on_first_attempt():
    adapter = MockProviderAdapter()
    with pytest.raises(TransientProviderError) as info:
        await run(adapter, req("x [mock:transient]", attempt=1))
    assert info.value.usage is not None
    items = await run(adapter, req("x [mock:transient]", attempt=2))
    assert isinstance(items[-1], ExecutionResult)


async def test_permanent_fault():
    with pytest.raises(PermanentProviderError):
        await run(MockProviderAdapter(), req("[mock:fail]"))


async def test_mock_log_redacts_secrets():
    items = await run(MockProviderAdapter(), req("use key sk-live-ABCDEFGHIJKLMNOPQRSTUVWX please"))
    log = next(a for a in items[-1].artifacts if a.kind == "LOG").content.decode()
    assert "sk-live-ABCDEFGHIJKLMNOPQRSTUVWX" not in log
    assert "[REDACTED]" in log


async def test_estimate_is_integer_cents():
    cents = await MockProviderAdapter().estimate_cents(req("x" * 2500))
    assert cents == 12 and isinstance(cents, int)


async def test_chat_deterministic_and_non_billable(no_network):
    adapter = MockProviderAdapter()
    inv = ChatInvocation("mock-chat-v1", (ChatMessage("user", "Say hello"),))
    r1, r2 = await adapter.chat(inv), await adapter.chat(inv)
    assert r1.content == r2.content
    assert r1.provider_request_id != r2.provider_request_id
    assert r1.usage.estimated_cost_cents == 0 and r1.usage.billable is False
    short = await adapter.chat(ChatInvocation("mock-chat-v1", inv.messages, max_tokens=2))
    assert short.finish_reason == "length" and short.usage.output_tokens == 2


def test_verifier_rejects_bad_artifacts():
    good_summary = ArtifactPayload("SUMMARY", "s", "text/plain", b"s")
    assert not verifier.verify((good_summary,)).ok
    bad_patch = ArtifactPayload("PATCH", "p", "text/x-diff", b"nope")
    assert not verifier.verify((good_summary, bad_patch)).ok
    patch = ArtifactPayload("PATCH", "p", "text/x-diff", b"diff --git a b")
    bad_report = ArtifactPayload("TEST_REPORT", "t", "application/json", b"{not json")
    assert not verifier.verify((good_summary, patch, bad_report)).ok
    failing = ArtifactPayload("TEST_REPORT", "t", "application/json", b'{"failed": 1}')
    assert not verifier.verify((good_summary, patch, failing)).ok
    assert verifier.verify((good_summary, patch)).ok
