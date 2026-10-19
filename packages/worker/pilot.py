"""No-inference owner-device preflight. Never polls/accepts leases or changes sharing."""

import asyncio
import platform
import re
import shutil
import subprocess

from packages.worker.lease_client import TransportError, WorkerApiError
from packages.worker.local_controls import LocalSharingControl
from packages.worker.sandbox_check import check as sandbox_check
from packages.worker.state import load_state, state_dir
from packages.worker.subscription_auth import forbidden_env_present, probe_login, sanitized_env


def item(name, ok, action, *, source="AUTOMATIC"):
    return {"id": name, "status": "PASS" if ok else "BLOCKED", "source": source, "action": action}


def cli_compatibility(codex_bin):
    """Only --version and exec --help. Never a model turn; version is an observation."""
    try:
        outputs = []
        for args in (("--version",), ("exec", "--help")):
            run = subprocess.run(  # noqa: S603 - owner-selected local executable, fixed arguments
                [codex_bin, *args],
                env=sanitized_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if run.returncode != 0:
                return None, False
            outputs.append(run.stdout + run.stderr)
        match = re.search(r"codex-cli [0-9][A-Za-z0-9.+-]{0,64}", outputs[0])
        compatible = bool(match) and all(
            flag in outputs[1]
            for flag in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules")
        )
        return match.group(0) if match else None, compatible
    except (OSError, subprocess.SubprocessError):
        return None, False


async def run_check(args, client_factory):
    checks = []
    directory = state_dir(args.state_dir)
    state = None
    registration_action = "Enroll this Mac using its owner’s one-time enrollment token."
    try:
        state = load_state(directory)
    except (OSError, ValueError, TypeError):
        registration_action = (
            "Fix node.json: valid JSON, owned by you, chmod 600, no symlink; do not share its key."
        )
    checks.append(
        item(
            "local_registration",
            state is not None,
            registration_action,
        )
    )
    binary = shutil.which(args.codex_bin)
    checks.append(
        item("codex_available", bool(binary), "Install the official Codex CLI or set --codex-bin.")
    )
    version, compatible = (
        await asyncio.to_thread(cli_compatibility, binary) if binary else (None, False)
    )
    checks.append(
        item(
            "cli_compatibility",
            compatible,
            (
                "Use an official CLI supporting ephemeral JSON execution and "
                "ignored user config/rules."
            ),
        )
    )
    auth = await probe_login(binary) if binary else None
    checks.append(
        item(
            "chatgpt_login",
            bool(auth and auth.eligible),
            (
                "Run codex login with your own ChatGPT Plus account; "
                "API-key/unknown logins are refused."
            ),
        )
    )
    sandbox = None
    if compatible:
        try:
            sandbox = await asyncio.to_thread(sandbox_check, binary)
        except (OSError, subprocess.SubprocessError):
            sandbox = {"passed": False}
    checks.append(
        item(
            "local_sandbox",
            bool(sandbox and sandbox["passed"]),
            (
                "The no-inference OS sandbox probe must pass on this Mac/CLI "
                "version; do not weaken isolation."
            ),
        )
    )
    checks.append(
        item(
            "local_sharing",
            not LocalSharingControl(directory).paused,
            "Local sharing is paused. Resume only by the owner's explicit choice.",
        )
    )
    checks.append(
        item(
            "local_concurrency",
            bool(state and state.max_concurrency == 1),
            "This pilot requires one local execution slot.",
        )
    )
    server = None
    if state:
        client = None
        try:
            client = client_factory(state)
            params = {"task_id": args.task_id} if args.task_id else {}
            server = (
                await client.call("GET", "/v1/workers/me/pilot-readiness", params=params)
            ).json()
            checks.append(
                item(
                    "secure_api",
                    state.api_url.startswith("https://"),
                    (
                        "Use the operator's HTTPS endpoint and trusted CA. Loopback HTTP "
                        "is development only."
                    ),
                )
            )
            checks.extend(server["checks"])
        except (WorkerApiError, TransportError, OSError, ValueError, KeyError, TypeError):
            checks.append(
                item(
                    "secure_api",
                    False,
                    (
                        "Check HTTPS, CA, coordinator version, node credential "
                        "expiry/revocation and network access."
                    ),
                )
            )
        finally:
            if client:
                await client.aclose()
    manual = [
        {
            "id": "official_install_source",
            "status": "REQUIRED",
            "source": "OWNER_OPERATOR_ATTESTATION",
            "action": (
                "Verify this executable came from OpenAI's official CLI "
                "distribution; a version string is not provenance."
            ),
        },
        {
            "id": "funding_source",
            "status": "REQUIRED",
            "source": "OWNER_ATTESTATION",
            "action": (
                "Before any real task: confirm Plus, no purchased or granted "
                "Credits, auto-reload off; check usage again afterward."
            ),
        },
        {
            "id": "authorization_scope",
            "status": "REQUIRED",
            "source": "OPERATOR_ATTESTATION",
            "action": (
                "Validate project-specific Support ticket 10766658 and actual "
                "scope/expiry. A database record is not vendor endorsement."
            ),
        },
        {
            "id": "two_physical_devices",
            "status": "REQUIRED",
            "source": "OPERATOR_ATTESTATION",
            "action": (
                "Confirm Alice and Bob use two distinct owner-controlled devices. "
                "This command cannot establish that."
            ),
        },
        {
            "id": "task_approval",
            "status": "REQUIRED",
            "source": "OWNER_ATTESTATION",
            "action": (
                "Alice must read the public pinned repository, complete prompt "
                "and configured execution limit (up to 600 seconds) and approve each task."
            ),
        },
    ]
    blocked = any(c["status"] == "BLOCKED" for c in checks)
    return {
        "schema_version": 1,
        "kind": "PILOT_READINESS_NO_INFERENCE",
        "inference_executed": False,
        "result": "BLOCKED" if blocked else "ATTESTATION_REQUIRED",
        "checks": checks,
        "manual_checks": manual,
        "codex_version": version,
        "auth_method": auth.method.value if auth else "UNAVAILABLE",
        "runtime": {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
        },
        "sandbox": sandbox,
        "server": server,
        "api_billing_env_in_shell": forbidden_env_present(),
        "api_billing_env_passed_to_codex": [],
        "real_two_person_pilot": "NOT_RUN_BY_THIS_COMMAND",
    }, 1 if blocked else 2
