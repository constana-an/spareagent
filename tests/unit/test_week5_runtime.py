"""Process and parser regression tests; no network/auth/subscription."""

import asyncio
import json
import sys

import pytest

from packages.worker.cli import main
from packages.worker.codex_runtime import (
    CodexRunResult,
    CodexRuntime,
    CodexRuntimeConfig,
    parse_event,
)
from tests.unit.test_codex_subscription import FAKE, calls, set_mode


def program(tmp_path, source):
    p = tmp_path / "codex-test"
    p.write_text(f"#!{sys.executable}\n" + source)
    p.chmod(0o700)
    return CodexRuntime(CodexRuntimeConfig(codex_bin=str(p)))


async def collect(runtime, tmp_path, timeout=2):
    return [x async for x in runtime.run("check", "test", tmp_path, timeout_seconds=timeout)]


async def test_zero_exit_without_completed_turn_is_not_success(tmp_path):
    runtime = program(tmp_path, 'print(\'{"type":"thread.started","thread_id":"test"}\')\n')
    result = (await collect(runtime, tmp_path))[-1]
    assert isinstance(result, CodexRunResult) and not result.succeeded


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "item.completed", "item": []},
        {"type": "turn.completed", "usage": []},
        {"type": "turn.completed", "usage": {"input_tokens": True, "output_tokens": -1}},
    ],
)
def test_malformed_events_never_inflate_tokens(raw):
    event = parse_event(raw)
    assert event is None or event.usage is None or event.usage.input_tokens == 0


async def test_oversized_stream_fails_and_reaps_process(tmp_path):
    runtime = program(
        tmp_path, "print('x' * (2*1024*1024), flush=True)\nimport time\ntime.sleep(30)\n"
    )
    result = (await collect(runtime, tmp_path))[-1]
    assert result.error_class == "RUNTIME" and not result.succeeded
    assert runtime._procs == {}


@pytest.mark.parametrize("timeout", [2, None])
async def test_runtime_allows_only_one_process(tmp_path, timeout):
    runtime = program(
        tmp_path, 'import time\nprint(\'{"type":"turn.started"}\',flush=True)\ntime.sleep(30)\n'
    )
    first = asyncio.create_task(collect(runtime, tmp_path, timeout=timeout))
    for _ in range(100):
        if runtime._procs:
            break
        await asyncio.sleep(0.01)
    other = (await collect(runtime, tmp_path))[-1]
    assert not other.succeeded and "busy" in other.error_message
    await runtime.cancel("check")
    assert (await first)[-1].cancelled


async def test_normal_leader_exit_cannot_leave_background_child(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    loop_code = "while True:\n f.write('x');time.sleep(.01)"
    child_code = (
        "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"f=open({str(heartbeat)!r},'a',buffering=1);"
        f"exec({loop_code!r})"
    )
    runtime = program(
        tmp_path,
        "import subprocess,sys,time,os\n"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"while not os.path.exists({str(heartbeat)!r}):time.sleep(.01)\n"
        'print(\'{"type":"turn.completed","usage":{"input_tokens":0,"output_tokens":0}}\',flush=True)\n',
    )
    assert (await collect(runtime, tmp_path))[-1].succeeded
    stopped = heartbeat.read_bytes()
    await asyncio.sleep(0.15)
    assert heartbeat.read_bytes() == stopped


def test_cli_refuses_unattested_inference(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    set_mode(tmp_path, "chatgpt")
    assert main(["codex-check", "--codex-bin", FAKE]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["result"] == "CREDITS_ATTESTATION_REQUIRED"
    assert all("exec" not in c["argv"] for c in calls(tmp_path))


async def test_worker_disconnect_cancels_when_lease_can_no_longer_be_renewed():
    from packages.worker.agent import WorkerAgent
    from packages.worker.lease_client import TransportError

    class Client:
        async def renew(self, lease_id):
            raise TransportError("offline")

    class Adapter:
        cancelled = False

        async def cancel(self, attempt_id):
            self.cancelled = True

    agent = WorkerAgent(Client())
    adapter = Adapter()
    cancel = asyncio.Event()
    await asyncio.wait_for(agent._renew_loop("lease", 0.1, cancel, adapter, "attempt"), 1)
    assert cancel.is_set() and adapter.cancelled
