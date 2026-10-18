"""`spareagent-worker` - outbound remote worker CLI.

    spareagent-worker register --api-url https://api.example --resource-id <uuid> --name n1
                               [--enrollment-token sa_enr_... | env SPAREAGENT_ENROLLMENT_TOKEN]
    spareagent-worker start    [--mock-step-delay 0] [--poll-wait 20]
    spareagent-worker status
    spareagent-worker stop
    spareagent-worker codex-check [--prompt "..."] [--codex-bin codex]
    spareagent-worker sharing {pause,resume,status}

State (node id + node key, mode 0600) lives in $SPAREAGENT_WORKER_HOME (default
~/.spareagent-worker). The worker needs no database or Redis credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time

from packages.provider_sdk.mock import MockProviderAdapter
from packages.worker.agent import AgentConfig, WorkerAgent
from packages.worker.lease_client import NodeClient, TransportError, WorkerApiError
from packages.worker.state import (
    NodeState,
    clear_pid,
    load_state,
    pid_alive,
    read_pid,
    save_state,
    state_dir,
    write_pid,
)
from services.observability import configure_logging

DEFAULT_CAPABILITIES = {"code": True, "chat": False, "provider": "mock", "languages": ["python"]}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _client(state: NodeState) -> NodeClient:
    return NodeClient(
        state.api_url,
        worker_id=state.worker_id,
        node_key=state.node_key,
        ca_file=state.ca_file,
        allow_insecure_http=state.allow_insecure_http,
    )


def _print(obj: dict) -> None:
    print(json.dumps(obj, indent=2, default=str))


async def cmd_register(args: argparse.Namespace) -> int:
    directory = state_dir(args.state_dir)
    existing = load_state(directory)
    if existing is not None:
        if args.if_needed:
            _print({"registered": True, "already": True, **existing.public_view()})
            return 0
        print("error: node already registered in this state directory", file=sys.stderr)
        return 2
    token = args.enrollment_token or os.environ.get("SPAREAGENT_ENROLLMENT_TOKEN")
    if not token:
        print(
            "error: --enrollment-token or SPAREAGENT_ENROLLMENT_TOKEN is required", file=sys.stderr
        )
        return 2
    capabilities = dict(DEFAULT_CAPABILITIES)
    for item in args.capability or []:
        key, _, value = item.partition("=")
        capabilities[key] = value
    client = NodeClient(
        args.api_url, ca_file=args.ca_file, allow_insecure_http=args.allow_insecure_http
    )
    try:
        data = await client.register(
            token,
            resource_id=args.resource_id,
            name=args.name,
            capabilities=capabilities,
            max_concurrency=args.max_concurrency,
        )
    except (WorkerApiError, TransportError) as exc:
        print(f"error: registration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await client.aclose()
    state = NodeState(
        api_url=client.api_url,
        worker_id=data["worker_id"],
        node_key=data["node_key"],
        node_key_expires_at=data["node_key_expires_at"],
        resource_id=args.resource_id,
        name=args.name,
        max_concurrency=args.max_concurrency,
        ca_file=args.ca_file,
        allow_insecure_http=args.allow_insecure_http,
    )
    save_state(directory, state)
    _print({"registered": True, **state.public_view()})
    return 0


async def _codex_shared_adapter(args: argparse.Namespace, directory):
    """Owner's device only. Requires both explicit flags and a ChatGPT login."""
    from packages.provider_sdk.codex_subscription import (
        CodexSubscriptionAdapter,
        SharedWorkConsent,
    )
    from packages.worker.codex_runtime import CodexRuntime, CodexRuntimeConfig
    from packages.worker.local_controls import LocalSharingControl

    if not (args.accept_shared_tasks and args.attest_no_purchased_credits):
        print(
            "error: --executor codex-subscription requires --accept-shared-tasks and "
            "--attest-no-purchased-credits (no purchased Codex credits, auto-reload off)",
            file=sys.stderr,
        )
        return None
    control = LocalSharingControl(directory)
    adapter = CodexSubscriptionAdapter(
        owner_ref="local-owner",
        runtime=CodexRuntime(CodexRuntimeConfig(codex_bin=args.codex_bin)),
        shared=SharedWorkConsent(control, no_purchased_credits_attested=True),
    )
    auth = await adapter.check_auth()
    if not auth.eligible:
        print(f"error: Codex is not logged in with ChatGPT ({auth.method.value})", file=sys.stderr)
        return None
    return adapter, control


async def cmd_start(args: argparse.Namespace) -> int:
    directory = state_dir(args.state_dir)
    state = load_state(directory)
    if state is None:
        print("error: not registered; run `spareagent-worker register` first", file=sys.stderr)
        return 2
    if pid_alive(read_pid(directory)) and read_pid(directory) != os.getpid():
        print("error: worker already running", file=sys.stderr)
        return 2
    write_pid(directory)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    client = _client(state)
    code_adapter = None
    capabilities = dict(DEFAULT_CAPABILITIES)
    if args.executor == "code":
        from services.execution.code_adapter import from_env

        code_adapter = from_env()
        try:
            health = await asyncio.to_thread(code_adapter.sandbox.health)
        except Exception as exc:  # noqa: BLE001
            print(f"error: sandbox health check failed: {exc}", file=sys.stderr)
            clear_pid(directory)
            await client.aclose()
            return 4
        print(json.dumps({"sandbox": health}), file=sys.stderr)
        capabilities.update({"sandbox": "docker", "languages": ["python", "typescript"]})
    live_adapter = sharing_control = None
    if args.executor == "codex-subscription":
        live = await _codex_shared_adapter(args, directory)
        if live is None:
            clear_pid(directory)
            await client.aclose()
            return 4
        live_adapter, sharing_control = live
        capabilities.update({"executor": "codex-subscription", "code": True})
    agent = WorkerAgent(
        client,
        AgentConfig(
            concurrency=state.max_concurrency,
            poll_wait_seconds=args.poll_wait,
            heartbeat_interval_seconds=args.heartbeat_interval,
            shutdown_grace_seconds=args.shutdown_grace,
            capabilities=capabilities,
        ),
        adapter=MockProviderAdapter(step_delay_seconds=args.mock_step_delay),
        code_adapter=code_adapter,
        sharing_control=sharing_control,
        live_adapter=live_adapter,
    )
    try:
        await agent.run(stop, state.node_key_expires_at)
    finally:
        if client.node_key and client.node_key != state.node_key:  # rotated
            state.node_key = client.node_key
            save_state(directory, state)
        await client.aclose()
        clear_pid(directory)
    return 3 if agent.fatal else 0


async def cmd_status(args: argparse.Namespace) -> int:
    directory = state_dir(args.state_dir)
    state = load_state(directory)
    if state is None:
        _print({"registered": False})
        return 1
    pid = read_pid(directory)
    report: dict = {
        "registered": True,
        **state.public_view(),
        "running": pid_alive(pid),
        "pid": pid,
    }
    client = _client(state)
    try:
        report["server"] = await client.me()
    except (WorkerApiError, TransportError) as exc:
        report["server_error"] = str(exc)
    finally:
        await client.aclose()
    _print(report)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    directory = state_dir(args.state_dir)
    pid = read_pid(directory)
    if not pid_alive(pid):
        print("worker is not running")
        return 0
    os.kill(pid, signal.SIGTERM)  # graceful: finish/hand back leases, then exit
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            print("worker stopped")
            return 0
        time.sleep(0.2)
    print("error: worker did not stop in time", file=sys.stderr)
    return 1


async def cmd_codex_check(args: argparse.Namespace) -> int:
    """Milestone A proof, run by the owner on their own device with their own login.

    Verifies the official Codex CLI is logged in with ChatGPT (not an API key), runs one
    owner-only read-only prompt with API-billing variables stripped, and prints a report.
    Nothing is sent to SpareAgent; credentials are never read."""
    from packages.provider_sdk.codex_subscription import CodexSubscriptionAdapter
    from packages.provider_sdk.types import ExecutionRequest, ExecutionResult
    from packages.worker.codex_runtime import CodexRuntime, CodexRuntimeConfig
    from packages.worker.subscription_auth import forbidden_env_present

    owner = "local-owner"
    adapter = CodexSubscriptionAdapter(
        owner_ref=owner,
        runtime=CodexRuntime(CodexRuntimeConfig(codex_bin=args.codex_bin, model=args.model)),
    )
    auth = await adapter.check_auth()
    report: dict = {
        "auth_method": auth.method.value,
        "auth_detail": auth.detail,
        "api_billing_env_in_shell": forbidden_env_present(),
        "api_billing_env_passed_to_codex": [],
        "forced_login_method": "chatgpt",
        "inference": None,
    }
    if not auth.eligible:
        report["result"] = "FAILED_CLOSED"
        _print(report)
        return 1
    if args.skip_inference:
        report["result"] = "AUTH_OK_INFERENCE_SKIPPED"
        _print(report)
        return 0
    request = ExecutionRequest(
        task_id="codex-check",
        attempt_id=f"codex-check-{int(time.time())}",
        user_id=owner,
        resource_scope="OWNER_ONLY",
        prompt=args.prompt,
        repository_url=None,
        commit_sha=None,
        max_duration_seconds=args.timeout,
        max_budget_cents=1,
        task_type="CODE_REVIEW",
    )
    try:
        result = None
        async for item in adapter.execute(request):
            if isinstance(item, ExecutionResult):
                result = item
    except Exception as exc:  # noqa: BLE001 - report every failure mode, fail closed
        report["result"] = "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        _print(report)
        return 1
    assert result is not None  # noqa: S101
    report["inference"] = {
        "completed": True,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "billable": result.usage.billable,
        "summary_excerpt": result.summary[:200],
    }
    report["result"] = "OWNER_ONLY_SUBSCRIPTION_RUN_OK"
    report["note"] = (
        "Confirms a ChatGPT-login Codex run with no API key in the child environment. "
        "Codex does not expose plan-quota consumption; confirm in ChatGPT usage settings."
    )
    _print(report)
    return 0


def cmd_sharing(args: argparse.Namespace) -> int:
    from packages.worker.local_controls import LocalSharingControl

    control = LocalSharingControl(state_dir(args.state_dir))
    if args.action == "pause":
        control.pause()
    elif args.action == "resume":
        control.resume()
    _print({"sharing_paused": control.paused})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spareagent-worker", description=__doc__.split("\n")[0])
    parser.add_argument("--state-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="enroll this node with a single-use enrollment token")
    reg.add_argument("--api-url", default=os.environ.get("SPAREAGENT_API_URL"), required=False)
    reg.add_argument("--resource-id", default=os.environ.get("SPAREAGENT_RESOURCE_ID"))
    reg.add_argument("--name", default=os.environ.get("SPAREAGENT_WORKER_NAME"))
    reg.add_argument("--enrollment-token", default=None)
    reg.add_argument("--max-concurrency", type=int, default=1)
    reg.add_argument("--capability", action="append", help="extra capability key=value")
    reg.add_argument("--ca-file", default=os.environ.get("SPAREAGENT_CA_FILE"))
    reg.add_argument(
        "--allow-insecure-http",
        action="store_true",
        default=_env_flag("SPAREAGENT_ALLOW_INSECURE_HTTP"),
        help="permit http:// to a loopback API (development only)",
    )
    reg.add_argument("--if-needed", action="store_true", help="no-op if already registered")

    start = sub.add_parser("start", help="run the worker in the foreground")
    start.add_argument("--poll-wait", type=float, default=20.0)
    start.add_argument("--heartbeat-interval", type=float, default=10.0)
    start.add_argument("--shutdown-grace", type=float, default=20.0)
    start.add_argument(
        "--executor",
        choices=("mock", "code", "codex-subscription"),
        default=os.environ.get("SPAREAGENT_EXECUTOR", "mock"),
        help="code = sandboxed deterministic code executor (requires Docker on this host); "
        "codex-subscription = your own Codex CLI for authorized experimental tasks",
    )
    start.add_argument("--codex-bin", default=os.environ.get("SPAREAGENT_CODEX_BIN", "codex"))
    start.add_argument(
        "--accept-shared-tasks",
        action="store_true",
        help="opt this device in to experimental tasks from your group (revocable)",
    )
    start.add_argument(
        "--attest-no-purchased-credits",
        action="store_true",
        help="confirm this ChatGPT account has no purchased Codex credits and no auto-reload",
    )
    start.add_argument(
        "--mock-step-delay",
        type=float,
        default=float(os.environ.get("SPAREAGENT_MOCK_STEP_DELAY", "0")),
    )

    sub.add_parser("status", help="show local identity and server-side node state")

    stop = sub.add_parser("stop", help="gracefully stop a running worker")
    stop.add_argument("--timeout", type=float, default=30.0)

    check = sub.add_parser(
        "codex-check", help="owner-only proof: ChatGPT-login Codex run with no API key"
    )
    check.add_argument("--codex-bin", default=os.environ.get("SPAREAGENT_CODEX_BIN", "codex"))
    check.add_argument("--model", default=None)
    check.add_argument("--timeout", type=int, default=300)
    check.add_argument("--skip-inference", action="store_true")
    check.add_argument(
        "--prompt",
        default="Reply with exactly: SPAREAGENT_OWNER_ONLY_OK. Do not run any commands.",
    )

    sharing = sub.add_parser("sharing", help="local kill switch for experimental sharing")
    sharing.add_argument("action", choices=("pause", "resume", "status"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Logs go to stderr so command output (JSON) on stdout stays machine-readable.
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"), stream=sys.stderr)
    if args.command == "register":
        missing = [n for n in ("api_url", "resource_id", "name") if not getattr(args, n)]
        if missing:
            print(f"error: missing {', '.join('--' + m.replace('_', '-') for m in missing)}")
            return 2
        return asyncio.run(cmd_register(args))
    if args.command == "start":
        return asyncio.run(cmd_start(args))
    if args.command == "status":
        return asyncio.run(cmd_status(args))
    if args.command == "codex-check":
        return asyncio.run(cmd_codex_check(args))
    if args.command == "sharing":
        return cmd_sharing(args)
    return cmd_stop(args)


if __name__ == "__main__":
    sys.exit(main())
