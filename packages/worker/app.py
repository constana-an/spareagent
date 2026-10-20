"""`spareagent-worker app` - one-click "provide spare capacity" control panel.

Opens a local page (127.0.0.1 only, random port, per-launch key) with one button. The
first click, after the owner reads the statements shown next to it, does everything:

1. installs the official Codex CLI if missing (the pinned build from the package's
   `runtime.json`, otherwise npm or Homebrew) and opens `codex login`
   so the owner signs in with their own ChatGPT account (credentials stay with Codex);
2. signs up with the operator's bundle and registers this device;
3. runs the owner-only check once (one tiny prompt on the owner's own plan);
4. connects, attests, accepts the sharing terms and enables sharing with automatic
   approval for the owner's group, and issues a group invite code;
5. starts the worker loop with the owner's own Codex CLI.

Clicking again pauses sharing on the server, sets the local pause and stops the worker.
Member and task limits can be changed at any time; the operator's caps still apply.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import platform
import shutil
import socket
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.worker.lease_client import NodeClient, TransportError, WorkerApiError
from packages.worker.runtime import (
    ManagedCodex,
    RuntimeSetupError,
    git_available,
    request_git_install,
)
from packages.worker.state import NodeState, load_state, save_state

log = logging.getLogger(__name__)

APP_FILE = "app.json"
BUNDLE_FILE = "bundle.json"
CA_FILE = "ca.pem"
CHECK_PROMPT = "Reply with exactly: SPAREAGENT_OWNER_ONLY_OK. Do not run any commands."
SHARING_ACKNOWLEDGEMENTS = (
    "non_commercial_experimental_use",
    "no_payment_or_compensation",
    "subscription_usage_may_be_consumed_within_confirmed_scope",
    "device_executes_opted_in_workloads",
    "participation_revocable_any_time",
    "owner_controls_local_credentials",
    "owner_may_add_restrictions",
)
LOGIN_TIMEOUT_SECONDS = 900
INSTALL_TIMEOUT_SECONDS = 900
SHUTDOWN_GRACE_SECONDS = 60.0


class AppError(Exception):
    """A failure with a stable code the page translates; `detail` is shown verbatim."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail[:500]


# ------------------------------------------------------------------ bundle / local state
@dataclass
class Bundle:
    """Operator-supplied connection details shipped with the worker download."""

    api_url: str
    signup_code: str
    terms_version: str
    ca_pem: str | None = None
    dashboard_url: str | None = None
    allow_insecure_http: bool = False

    @classmethod
    def load(cls, path: Path) -> Bundle:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            api_url=data["api_url"],
            signup_code=data["signup_code"],
            terms_version=data["terms_version"],
            ca_pem=data.get("ca_pem"),
            dashboard_url=data.get("dashboard_url"),
            allow_insecure_http=bool(data.get("allow_insecure_http", False)),
        )


def find_bundle(directory: Path, explicit: str | None = None) -> Path | None:
    candidates = [
        explicit,
        os.environ.get("SPAREAGENT_BUNDLE"),
        str(directory / BUNDLE_FILE),
    ]
    for raw in candidates:
        if raw and Path(raw).expanduser().is_file():
            return Path(raw).expanduser()
    return None


def write_private(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_app_state(directory: Path) -> dict[str, Any]:
    try:
        return json.loads((directory / APP_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_app_state(directory: Path, data: dict[str, Any]) -> None:
    write_private(directory / APP_FILE, json.dumps(data, indent=2, default=str))


def default_device_label() -> str:
    name = platform.node() or socket.gethostname() or "My Mac"
    return name.removesuffix(".local")[:80] or "My Mac"


# ------------------------------------------------------------------ Codex on this device
class CodexTools:
    """The owner's own Codex CLI. SpareAgent never reads its credentials."""

    def __init__(self, codex_bin: str = "codex", managed: ManagedCodex | None = None) -> None:
        self.codex_bin = codex_bin
        self.managed = managed

    def installed(self) -> bool:
        if shutil.which(self.codex_bin):
            return True
        if self.codex_bin == "codex" and self.managed is not None and self.managed.installed():
            self.codex_bin = self.managed.activate()
            return True
        return False

    async def _run(self, argv: list[str], limit_seconds: float) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), limit_seconds)
        except (TimeoutError, asyncio.CancelledError) as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return -1, "timeout"
        return proc.returncode or 0, out.decode(errors="replace")[-2000:]

    async def install(self) -> None:
        if self.codex_bin != "codex":
            # An explicitly configured binary is the owner's choice; never install over it.
            raise AppError("codex_missing", self.codex_bin)
        if self.managed is not None:
            try:
                self.codex_bin = await self.managed.install()
            except (RuntimeSetupError, OSError) as exc:
                raise AppError("codex_install_failed", str(exc)) from exc
            except Exception as exc:  # noqa: BLE001 - network errors from httpx
                raise AppError("codex_install_failed", type(exc).__name__) from exc
            return
        npm, brew = shutil.which("npm"), shutil.which("brew")
        if npm:
            argv = [npm, "install", "-g", "@openai/codex"]
        elif brew:
            argv = [brew, "install", "codex"]
        else:
            raise AppError("codex_missing")
        code, out = await self._run(argv, INSTALL_TIMEOUT_SECONDS)
        found = shutil.which("codex")
        if code != 0 or not found:
            raise AppError("codex_install_failed", out)
        self.codex_bin = found

    async def logged_in(self) -> bool:
        from packages.worker.subscription_auth import probe_login

        return (await probe_login(self.codex_bin)).eligible

    async def login(self) -> None:
        """`codex login` opens the browser for the owner's own ChatGPT sign-in."""
        code, out = await self._run([self.codex_bin, "login"], LOGIN_TIMEOUT_SECONDS)
        if code != 0:
            raise AppError("codex_login_failed", out)

    async def owner_check(self) -> dict[str, Any]:
        from packages.worker.cli import owner_check

        report, _ = await owner_check(
            codex_bin=self.codex_bin,
            model=None,
            prompt=CHECK_PROMPT,
            timeout_seconds=180,
            skip_inference=False,
            attest_no_purchased_credits=True,
        )
        return report


# ------------------------------------------------------------------ controller
@dataclass
class Snapshot:
    phase: str = "OFFLINE"  # OFFLINE / STARTING / ONLINE / STOPPING / ERROR
    step: str | None = None
    error: dict[str, str] | None = None
    server: dict[str, Any] | None = None
    server_error: str | None = None
    notices: list[str] = field(default_factory=list)


class Controller:
    def __init__(
        self,
        directory: Path,
        bundle: Bundle | None,
        *,
        codex: CodexTools | None = None,
        client_factory=None,
        agent_factory=None,
        runtime_pins: dict[str, Any] | None = None,
        git_check=git_available,
    ) -> None:
        self.directory = directory
        self.bundle = bundle
        managed = (
            ManagedCodex(directory / "runtime", runtime_pins)
            if runtime_pins and runtime_pins.get("codex")
            else None
        )
        self.codex = codex or CodexTools(
            os.environ.get("SPAREAGENT_CODEX_BIN", "codex"), managed=managed
        )
        self._git_check = git_check
        self._client_factory = client_factory or self._default_client
        self._agent_factory = agent_factory or self._default_agent
        self.snap = Snapshot()
        self.app_state = load_app_state(directory)
        self._busy = asyncio.Lock()
        self._stop: asyncio.Event | None = None
        self._agent_task: asyncio.Task | None = None
        self._client: NodeClient | None = None
        self._job: asyncio.Task | None = None

    # ---------------------------------------------------------------- plumbing
    def _save(self, **values: Any) -> None:
        self.app_state.update(values)
        save_app_state(self.directory, self.app_state)

    def _ca_file(self) -> str | None:
        if not (self.bundle and self.bundle.ca_pem):
            return None
        path = self.directory / CA_FILE
        if not path.exists() or path.read_text(encoding="utf-8") != self.bundle.ca_pem:
            write_private(path, self.bundle.ca_pem)
        return str(path)

    def _default_client(self, state: NodeState | None) -> NodeClient:
        if state is not None:
            return NodeClient(
                state.api_url,
                worker_id=state.worker_id,
                node_key=state.node_key,
                ca_file=state.ca_file,
                allow_insecure_http=state.allow_insecure_http,
            )
        assert self.bundle is not None  # noqa: S101 - checked by callers
        return NodeClient(
            self.bundle.api_url,
            ca_file=self._ca_file(),
            allow_insecure_http=self.bundle.allow_insecure_http,
        )

    def _node_client(self) -> NodeClient | None:
        if self._client is None:
            state = load_state(self.directory)
            if state is None:
                return None
            self._client = self._client_factory(state)
        return self._client

    def snapshot(self) -> dict[str, Any]:
        s = self.snap
        code = self.app_state.get("invite_code")
        dashboard = (s.server or {}).get("dashboard_url") or (
            self.bundle.dashboard_url if self.bundle else None
        )
        return {
            "phase": s.phase,
            "step": s.step,
            "error": s.error,
            "notices": s.notices[-3:],
            "bundle_ok": self.bundle is not None,
            "registered": load_state(self.directory) is not None,
            "consented": bool(self.app_state.get("consented_at")),
            "device_label": self.app_state.get("device_label") or default_device_label(),
            "subscription_type": self.app_state.get("subscription_type") or "PLUS",
            "invite_code": code,
            "invite_link": f"{dashboard}/#join={code}" if dashboard and code else None,
            "dashboard_url": dashboard,
            "server": s.server,
            "server_error": s.server_error,
        }

    async def refresh(self) -> None:
        client = self._node_client()
        if client is None:
            return
        try:
            self.snap.server = await client.sharing_status()
            self.snap.server_error = None
        except (WorkerApiError, TransportError) as exc:
            self.snap.server_error = str(exc)

    async def startup(self) -> None:
        """A previous run may have exited without going offline; make the server agree
        that this device is not sharing until the owner clicks again."""
        await self.refresh()
        sharing = (self.snap.server or {}).get("sharing") or {}
        client = self._node_client()
        if client is not None and sharing.get("state") == "ENABLED":
            with contextlib.suppress(WorkerApiError, TransportError):
                self.snap.server = await client.sharing_offline()

    # ---------------------------------------------------------------- the button
    async def toggle(self, options: dict[str, Any]) -> None:
        if self.snap.phase in ("STARTING", "STOPPING"):
            return
        if self.snap.phase == "ONLINE":
            await self.go_offline()
        else:
            await self.go_online(options)

    def start_toggle(self, options: dict[str, Any]) -> None:
        """Run the toggle in the background; the page polls the snapshot."""
        if self._job is None or self._job.done():
            self._job = asyncio.create_task(self.toggle(options))

    async def cancel(self) -> None:
        """Abort a slow start (e.g. the owner closed the ChatGPT sign-in page)."""
        if self.snap.phase == "STARTING" and self._job is not None and not self._job.done():
            self._job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._job
            self.snap.phase, self.snap.step, self.snap.error = "OFFLINE", None, None

    async def go_online(self, options: dict[str, Any]) -> None:
        async with self._busy:
            self.snap.phase, self.snap.error = "STARTING", None
            try:
                await self._online_steps(options)
            except AppError as exc:
                self._fail(exc.code, exc.detail)
            except WorkerApiError as exc:
                self._fail("server_rejected", exc.message, server_code=exc.code)
            except TransportError as exc:
                self._fail("server_unreachable", str(exc))
            except Exception as exc:  # noqa: BLE001 - surface every failure on the page
                log.exception("going online failed")
                self._fail("unexpected", type(exc).__name__)

    def _fail(self, code: str, detail: str = "", server_code: str | None = None) -> None:
        self.snap.phase, self.snap.step = "ERROR", None
        self.snap.error = {"code": code, "detail": detail}
        if server_code:
            self.snap.error["server_code"] = server_code

    async def _online_steps(self, options: dict[str, Any]) -> None:
        if self.bundle is None:
            raise AppError("bundle_missing")
        label = str(options.get("device_label") or self.snapshot()["device_label"]).strip()[:80]
        plan = options.get("subscription_type") or self.snapshot()["subscription_type"]
        if plan not in ("PLUS", "PRO"):
            raise AppError("plan_invalid")
        self._save(
            device_label=label or default_device_label(),
            subscription_type=plan,
            consented_at=datetime.now(UTC).isoformat(),
            terms_version=self.bundle.terms_version,
        )

        self.snap.step = "codex_install"
        if not self.codex.installed():
            await self.codex.install()

        # Code tasks clone repositories; a fresh Mac needs Apple's command line tools.
        self.snap.step = "git_check"
        if not self._git_check():
            request_git_install()
            raise AppError("git_missing")

        self.snap.step = "codex_login"
        if not await self.codex.logged_in():
            await self.codex.login()
            if not await self.codex.logged_in():
                raise AppError("codex_login_required")

        self.snap.step = "signup"
        await self._ensure_registered()

        self.snap.step = "owner_check"
        if not self.app_state.get("owner_check_ok_at"):
            report = await self.codex.owner_check()
            if report.get("result") != "OWNER_ONLY_SUBSCRIPTION_RUN_OK":
                raise AppError("owner_check_failed", str(report.get("result")))
            self._save(owner_check_ok_at=datetime.now(UTC).isoformat())

        self.snap.step = "server_online"
        client = self._node_client()
        status = await client.sharing_online(
            {
                "device_label": self.app_state["device_label"],
                "subscription_type": plan,
                "terms_version": self.bundle.terms_version,
                "acknowledgements": {k: True for k in SHARING_ACKNOWLEDGEMENTS},
                "codex_check_result": "OWNER_ONLY_SUBSCRIPTION_RUN_OK",
                "no_purchased_credits": True,
                "no_auto_reload": True,
                "auto_approve_members": self.app_state.get("auto_approve_members", True),
            }
        )
        if status.get("invite_code"):
            self._save(invite_code=status["invite_code"])
        self.snap.server = status

        self.snap.step = "worker_start"
        from packages.worker.local_controls import LocalSharingControl

        LocalSharingControl(self.directory).resume()
        self._stop = asyncio.Event()
        self._agent_task = asyncio.create_task(self._run_agent(self._stop))
        self.snap.phase, self.snap.step = "ONLINE", None

    async def _ensure_registered(self) -> None:
        if load_state(self.directory) is not None:
            return
        assert self.bundle is not None  # noqa: S101
        signup = self.app_state.get("signup")
        expired = signup is None or datetime.fromisoformat(
            signup["enrollment_expires_at"]
        ) <= datetime.now(UTC)
        client = self._client_factory(None)
        try:
            if expired:
                signup = await client.signup(
                    {
                        "signup_code": self.bundle.signup_code,
                        "device_label": self.app_state["device_label"],
                        "terms_version": self.bundle.terms_version,
                        "acknowledgements": {k: True for k in SHARING_ACKNOWLEDGEMENTS},
                    }
                )
                # Kept until registration succeeds so a retry does not sign up twice.
                self._save(signup=signup)
            data = await client.register(
                signup["enrollment_token"],
                resource_id=str(signup["resource_id"]),
                name=self.app_state["device_label"],
                capabilities={"code": True, "chat": False, "executor": "codex-subscription"},
                max_concurrency=1,
            )
        finally:
            await client.aclose()
        save_state(
            self.directory,
            NodeState(
                api_url=client.api_url,
                worker_id=data["worker_id"],
                node_key=data["node_key"],
                node_key_expires_at=data["node_key_expires_at"],
                resource_id=str(signup["resource_id"]),
                name=self.app_state["device_label"],
                max_concurrency=1,
                ca_file=self._ca_file(),
                allow_insecure_http=self.bundle.allow_insecure_http,
            ),
        )
        self.app_state.pop("signup", None)
        self._save(group_id=str(signup["group_id"]), owner_user_id=str(signup["user_id"]))

    async def _default_agent(self, stop: asyncio.Event) -> bool:
        """Run the worker loop with the owner's Codex CLI. Returns False on a fatal error."""
        from packages.provider_sdk.codex_subscription import (
            CodexSubscriptionAdapter,
            SharedWorkConsent,
        )
        from packages.provider_sdk.mock import MockProviderAdapter
        from packages.worker.agent import AgentConfig, WorkerAgent
        from packages.worker.codex_runtime import CodexRuntime, CodexRuntimeConfig
        from packages.worker.local_controls import LocalSharingControl

        state = load_state(self.directory)
        client = self._client_factory(state)
        control = LocalSharingControl(self.directory)
        adapter = CodexSubscriptionAdapter(
            owner_ref="local-owner",
            runtime=CodexRuntime(CodexRuntimeConfig(codex_bin=self.codex.codex_bin)),
            shared=SharedWorkConsent(control, no_purchased_credits_attested=True),
        )
        agent = WorkerAgent(
            client,
            AgentConfig(
                concurrency=1,
                shutdown_grace_seconds=SHUTDOWN_GRACE_SECONDS,
                capabilities={
                    "code": True,
                    "chat": False,
                    "provider": "mock",
                    "languages": ["python"],
                    "executor": "codex-subscription",
                },
            ),
            adapter=MockProviderAdapter(),
            sharing_control=control,
            live_adapter=adapter,
        )
        try:
            await agent.run(stop, state.node_key_expires_at)
        finally:
            if client.node_key and client.node_key != state.node_key:  # rotated
                state.node_key = client.node_key
                save_state(self.directory, state)
            await client.aclose()
        return agent.fatal is None

    async def _run_agent(self, stop: asyncio.Event) -> None:
        try:
            ok = await self._agent_factory(stop)
        except Exception as exc:  # noqa: BLE001
            log.exception("worker loop crashed")
            ok, detail = False, type(exc).__name__
        else:
            detail = ""
        if self.snap.phase == "ONLINE":  # stopped without the owner clicking
            if ok and stop.is_set():
                self.snap.phase = "OFFLINE"
            else:
                self._fail("worker_stopped", detail)

    async def go_offline(self) -> None:
        async with self._busy:
            self.snap.phase, self.snap.step, self.snap.error = "STOPPING", "server_offline", None
            from packages.worker.local_controls import LocalSharingControl

            LocalSharingControl(self.directory).pause("owner went offline in the app")
            client = self._node_client()
            if client is not None:
                try:
                    self.snap.server = await client.sharing_offline()
                except (WorkerApiError, TransportError) as exc:
                    self.snap.notices.append(f"server pause failed: {exc}")
            self.snap.step = "worker_stop"
            if self._stop is not None:
                self._stop.set()
            if self._agent_task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._agent_task, SHUTDOWN_GRACE_SECONDS + 15)
            self._stop = self._agent_task = None
            self.snap.phase, self.snap.step = "OFFLINE", None

    # ---------------------------------------------------------------- owner settings
    async def _owner_call(self, name: str, *args: Any) -> dict[str, Any]:
        client = self._node_client()
        if client is None:
            raise AppError("not_registered")
        status = await getattr(client, name)(*args)
        if isinstance(status, dict) and "group" in status:
            if status.get("invite_code"):
                self._save(invite_code=status["invite_code"])
            self.snap.server = status
        return status

    async def update_settings(self, body: dict[str, Any]) -> None:
        clean: dict[str, Any] = {}
        for key in ("max_members", "max_tasks_per_day"):
            if body.get(key) is not None:
                clean[key] = int(body[key])
        if body.get("max_task_minutes") is not None:
            clean["max_task_duration_seconds"] = int(body["max_task_minutes"]) * 60
        if body.get("auto_approve_members") is not None:
            clean["auto_approve_members"] = bool(body["auto_approve_members"])
        await self._owner_call("sharing_settings", clean)
        # Only remember the choice locally once the server has accepted it.
        if "auto_approve_members" in clean:
            self._save(auto_approve_members=clean["auto_approve_members"])

    async def rotate_invite(self) -> None:
        await self._owner_call("rotate_invite_code")

    async def remove_member(self, user_id: str) -> None:
        await self._owner_call("remove_member", user_id)

    async def dashboard_link(self) -> str:
        data = await self._owner_call("dashboard_login")
        return f"{data['dashboard_url']}/#login={data['token']}"

    async def shutdown(self) -> None:
        if self.snap.phase in ("ONLINE", "STARTING"):
            await self.go_offline()
        if self._client is not None:
            await self._client.aclose()
