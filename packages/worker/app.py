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
import ssl
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from packages.worker.lease_client import NodeClient, TransportError, WorkerApiError
from packages.worker.runtime import (
    ManagedCodex,
    RuntimeSetupError,
    git_available,
    request_git_install,
)
from packages.worker.state import NodeState, load_state, save_state
from packages.worker.tools import DATA_TOOL, DataToolEnv, probe_capabilities

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
MAX_PARALLEL = 3
# Seconds running tasks get after the owner goes offline: "finish" waits for the longest
# allowed task, "now" stops them at once, "shutdown" (window closed) keeps a short grace.
OFFLINE_GRACE = {"finish": 3600.0 + 60, "now": 0.0, "shutdown": SHUTDOWN_GRACE_SECONDS}
GIT_PROBE_SECONDS = 60.0


class AppError(Exception):
    """A failure with a stable code the page translates; `detail` is shown verbatim."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail[:500]


# ------------------------------------------------------------------ bundle / local state
@dataclass(frozen=True)
class Network:
    """One way to reach the same server (e.g. the LAN address or the Tailscale one)."""

    id: str
    label: str
    api_url: str
    dashboard_url: str | None = None


@dataclass
class Bundle:
    """Operator-supplied connection details shipped with the worker download."""

    api_url: str
    signup_code: str
    terms_version: str
    ca_pem: str | None = None
    dashboard_url: str | None = None
    allow_insecure_http: bool = False
    networks: tuple[Network, ...] = ()

    def __post_init__(self) -> None:
        if not self.networks:
            self.networks = (Network("default", "", self.api_url, self.dashboard_url),)

    @classmethod
    def load(cls, path: Path) -> Bundle:
        data = json.loads(path.read_text(encoding="utf-8"))
        networks = tuple(
            Network(
                id=str(n["id"]),
                label=str(n.get("label") or n["id"]),
                api_url=n["api_url"],
                dashboard_url=n.get("dashboard_url"),
            )
            for n in data.get("networks") or ()
        )
        return cls(
            api_url=data["api_url"],
            signup_code=data["signup_code"],
            terms_version=data["terms_version"],
            ca_pem=data.get("ca_pem"),
            dashboard_url=data.get("dashboard_url"),
            allow_insecure_http=bool(data.get("allow_insecure_http", False)),
            networks=networks,
        )


PROBE_SECONDS = 2.0


def connection_problem(exc: BaseException, url: str) -> str:
    causes = []
    current: BaseException | None = exc
    while current is not None and current not in causes:
        causes.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(e, ssl.SSLCertVerificationError) for e in causes):
        return "certificate_invalid"
    if (urlsplit(url).hostname or "").endswith(".ts.net") and any(
        isinstance(e, socket.gaierror | TimeoutError | httpx.ConnectTimeout) for e in causes
    ):
        return "tailscale_unreachable"
    return "service_unreachable"


async def probe_server(url: str, ca_file: str | None) -> str | None:
    """Check TLS and readiness without signing in or running an inference."""
    try:
        async with httpx.AsyncClient(
            verify=ssl.create_default_context(cafile=ca_file) if ca_file else True,
            timeout=PROBE_SECONDS,
            trust_env=False,
        ) as client:
            response = await client.get(f"{url.rstrip('/')}/readyz")
            response.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        return connection_problem(exc, url)
    return None


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
    offline_mode: str | None = None


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
        probe=None,
    ) -> None:
        self.directory = directory
        self.bundle = bundle
        self._probe = probe
        self._reachable: dict[str, bool] = {}
        self._network_errors: dict[str, str | None] = {}
        self._connection_error: str | None = None
        self._network_id: str | None = None  # resolved choice ("auto" picks one)
        managed = (
            ManagedCodex(directory / "runtime", runtime_pins)
            if runtime_pins and runtime_pins.get("codex")
            else None
        )
        self.codex = codex or CodexTools(
            os.environ.get("SPAREAGENT_CODEX_BIN", "codex"), managed=managed
        )
        self._git_check = git_check
        self._git_cache: tuple[float, bool] | None = None
        self.data_env = DataToolEnv(directory / "runtime", runtime_pins)
        self._tool_job: asyncio.Task | None = None
        self._agent = None
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

    # ---------------------------------------------------------------- networks
    def network_choice(self) -> str:
        choice = self.app_state.get("network") or "auto"
        ids = {n.id for n in self.bundle.networks} if self.bundle else set()
        return choice if choice in ids else "auto"

    def network(self) -> Network | None:
        if self.bundle is None:
            return None
        nets = self.bundle.networks
        want = self.network_choice()
        if want == "auto":
            want = self._network_id or nets[0].id
        return next((n for n in nets if n.id == want), nets[0])

    async def resolve_network(self) -> None:
        """Check which networks reach the server; "auto" uses the first that does (in the
        bundle's order). A changed address drops the cached client so the next call uses
        the new one - same server, so the device's credentials stay valid."""
        if self.bundle is None:
            return
        before = self.network()
        nets = self.bundle.networks
        if self._probe is not None:
            results = await asyncio.gather(*(self._probe(n.api_url) for n in nets))
            self._reachable = {n.id: bool(ok) for n, ok in zip(nets, results, strict=True)}
        else:
            errors = await asyncio.gather(*(probe_server(n.api_url, self._ca_file()) for n in nets))
            self._network_errors = dict(zip((n.id for n in nets), errors, strict=True))
            self._reachable = {n.id: self._network_errors[n.id] is None for n in nets}
        self._network_id = next((n.id for n in nets if self._reachable[n.id]), nets[0].id)
        self._connection_error = self._network_errors.get(self.network().id)
        if self.network() != before and self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None

    async def set_network(self, choice: str) -> None:
        if self.snap.phase in ("ONLINE", "STARTING", "STOPPING"):
            raise AppError("network_locked")
        ids = {n.id for n in self.bundle.networks} if self.bundle else set()
        if choice != "auto" and choice not in ids:
            raise AppError("invalid_input", "network")
        self._save(network=choice)
        await self.resolve_network()
        await self.refresh()

    def _default_client(self, state: NodeState | None) -> NodeClient:
        net = self.network()
        if state is not None:
            return NodeClient(
                net.api_url if net else state.api_url,
                worker_id=state.worker_id,
                node_key=state.node_key,
                ca_file=self._ca_file() if net else state.ca_file,
                allow_insecure_http=(
                    self.bundle.allow_insecure_http if net else state.allow_insecure_http
                ),
            )
        assert self.bundle is not None and net is not None  # noqa: S101 - checked by callers
        return NodeClient(
            net.api_url,
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
        net = self.network()
        nets = self.bundle.networks if self.bundle else ()
        if net and net.dashboard_url:
            dashboard = net.dashboard_url  # the address that works on this network
        else:
            dashboard = (s.server or {}).get("dashboard_url") or (net and net.dashboard_url)
        return {
            "phase": s.phase,
            "step": s.step,
            "offline_mode": s.offline_mode,
            "running_here": self._agent.active if self._agent is not None else 0,
            "error": s.error,
            "notices": s.notices[-3:],
            "bundle_ok": self.bundle is not None,
            "registered": load_state(self.directory) is not None,
            "consented": bool(self.app_state.get("consented_at")),
            "device_label": self.app_state.get("device_label") or default_device_label(),
            "subscription_type": self.app_state.get("subscription_type") or "PLUS",
            "invite_code": code,
            "invite_link": f"{dashboard}/#join={code}" if dashboard and code else None,
            # Members may sit on another network than the owner: one link per network.
            "invite_links": [
                {"id": n.id, "label": n.label, "url": f"{n.dashboard_url}/#join={code}"}
                for n in nets
                if n.dashboard_url and code and len(nets) > 1
            ],
            "network_choice": self.network_choice(),
            "network": net.id if net else None,
            "networks": [
                {"id": n.id, "label": n.label, "reachable": self._reachable.get(n.id)} for n in nets
            ],
            "dashboard_url": dashboard,
            "server": s.server,
            "server_error": s.server_error,
            "connection_error": self._connection_error,
            "tools": {
                "git": self.git_ok(),
                DATA_TOOL: {
                    "state": self.data_env.state,
                    "detail": self.data_env.detail,
                    "size_mb": round(self.data_env.size_bytes() / 1e6),
                },
            },
        }

    async def refresh(self) -> None:
        client = self._node_client()
        if client is None:
            return
        try:
            self.snap.server = await client.sharing_status()
            self.snap.server_error = None
            self._connection_error = None
        except (WorkerApiError, TransportError) as exc:
            self.snap.server_error = str(exc)
            if isinstance(exc, TransportError):
                self._connection_error = connection_problem(exc, client.api_url)

    async def startup(self) -> None:
        """A previous run may have exited without going offline; make the server agree
        that this device is not sharing until the owner clicks again."""
        await self.resolve_network()
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
            await self.go_offline(options.get("offline_mode") or "finish")
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
                await self.resolve_network()
                if self._connection_error:
                    raise AppError(self._connection_error)
                await self._online_steps(options)
            except AppError as exc:
                self._fail(exc.code, exc.detail)
            except WorkerApiError as exc:
                self._fail("server_rejected", exc.message, server_code=exc.code)
            except TransportError as exc:
                net = self.network()
                state = load_state(self.directory)
                self._fail(connection_problem(exc, net.api_url if net else state.api_url), str(exc))
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

        # Only code tasks need git (they clone repositories). Without it the device still
        # goes online: it declares git=false, so the server sends it document and table
        # tasks only, and code tasks open up once Apple's command line tools are installed.
        self.snap.step = "git_check"
        if not self._git_check():
            request_git_install()
            self.snap.notices.append("git missing: code tasks stay off until it is installed")

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
            # Display names can contain Chinese/spaces; enrollment needs an ASCII identifier.
            node_name = f"spareagent-{signup['resource_id']}"
            data = await client.register(
                signup["enrollment_token"],
                resource_id=str(signup["resource_id"]),
                name=node_name,
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
                name=node_name,
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
            runtime=CodexRuntime(
                CodexRuntimeConfig(codex_bin=self.codex.codex_bin, max_parallel=MAX_PARALLEL)
            ),
            shared=SharedWorkConsent(control, no_purchased_credits_attested=True),
            tools=self.tool_access,
        )
        # The server assigns at most the owner's concurrency setting (1-3); the device keeps
        # enough slots for the maximum so a changed setting needs no restart.
        agent = self._agent = WorkerAgent(
            client,
            AgentConfig(
                concurrency=MAX_PARALLEL,
                shutdown_grace_seconds=SHUTDOWN_GRACE_SECONDS,
                capabilities_provider=self.capabilities,
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

    # ---------------------------------------------------------------- device tools
    def git_ok(self) -> bool:
        now = time.monotonic()
        if self._git_cache is None or now - self._git_cache[0] > GIT_PROBE_SECONDS:
            self._git_cache = (now, bool(self._git_check()))
        return self._git_cache[1]

    def capabilities(self) -> dict[str, Any]:
        return probe_capabilities(self.data_env, self.git_ok)

    def tool_access(self, name: str):
        return self.data_env.access() if name == DATA_TOOL else None

    def start_tool_install(self, name: str) -> None:
        if name != DATA_TOOL:
            raise AppError("invalid_input", name)
        if self._tool_job is None or self._tool_job.done():
            self._tool_job = asyncio.create_task(self._install_data_tools())

    async def _install_data_tools(self) -> None:
        try:
            await self.data_env.install()
        except Exception:  # noqa: BLE001 - state/detail carry the reason to the page
            log.exception("data tools install failed")

    def remove_tool(self, name: str) -> None:
        if name != DATA_TOOL or (self._tool_job and not self._tool_job.done()):
            raise AppError("invalid_input", name)
        if self.data_env.home and self.data_env.home.exists():
            shutil.rmtree(self.data_env.home)
        self.data_env.state = "MISSING" if self.data_env.spec else "UNAVAILABLE"

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

    async def go_offline(self, mode: str = "shutdown") -> None:
        """Stop sharing. No new task is accepted from the first moment on; running tasks
        either finish (up to their own time limit), stop now (the platform hands them back
        to the queue when another attempt is allowed), or get the short shutdown grace."""
        if mode not in OFFLINE_GRACE:
            raise AppError("invalid_input", mode)
        async with self._busy:
            self.snap.phase, self.snap.step, self.snap.error = "STOPPING", "server_offline", None
            self.snap.offline_mode = mode
            from packages.worker.local_controls import LocalSharingControl

            LocalSharingControl(self.directory).pause("owner went offline in the app")
            client = self._node_client()
            if client is not None:
                try:
                    self.snap.server = await client.sharing_offline()
                except (WorkerApiError, TransportError) as exc:
                    self.snap.notices.append(f"server pause failed: {exc}")
            self.snap.step = "draining" if mode == "finish" else "worker_stop"
            grace = OFFLINE_GRACE[mode]
            if self._agent is not None:
                self._agent.config.shutdown_grace_seconds = grace
            if self._stop is not None:
                self._stop.set()
            if self._agent_task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._agent_task, grace + 15)
            self._stop = self._agent_task = self._agent = None
            self.snap.phase, self.snap.step, self.snap.offline_mode = "OFFLINE", None, None

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
        if body.get("max_concurrent_tasks") is not None:
            clean["max_concurrent_tasks"] = int(body["max_concurrent_tasks"])
        for key in ("allowed_task_types", "allowed_tools"):
            if body.get(key) is not None:
                if not isinstance(body[key], list):
                    raise AppError("invalid_input", key)
                clean[key] = [str(x) for x in body[key]]
        await self._owner_call("sharing_settings", clean)
        # Only remember the choice locally once the server has accepted it.
        if "auto_approve_members" in clean:
            self._save(auto_approve_members=clean["auto_approve_members"])

    async def rotate_invite(self) -> None:
        await self._owner_call("rotate_invite_code")

    async def set_member_limits(self, user_id: str, limits: dict[str, Any]) -> None:
        clean: dict[str, int | None] = {}
        for key in ("max_tasks_per_day", "max_minutes_per_day"):
            value = limits.get(key)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise AppError("invalid_input", key)
            clean[key] = value
        await self._owner_call("set_member_limits", user_id, clean)

    async def remove_member(self, user_id: str) -> None:
        await self._owner_call("remove_member", user_id)

    async def dashboard_link(self) -> str:
        data = await self._owner_call("dashboard_login")
        net = self.network()
        base = net.dashboard_url if net and net.dashboard_url else data["dashboard_url"]
        return f"{base}/#login={data['token']}"

    async def shutdown(self) -> None:
        if self.snap.phase in ("ONLINE", "STARTING"):
            await self.go_offline()
        if self._client is not None:
            await self._client.aclose()
