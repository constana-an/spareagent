"""The one-click worker app: button flow and local-server protections (no Codex, no API)."""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest

from packages.worker.app import AppError, Bundle, Controller, load_app_state
from packages.worker.app_server import AppServer
from packages.worker.lease_client import TransportError, WorkerApiError
from packages.worker.local_controls import LocalSharingControl
from packages.worker.state import load_state

BUNDLE = Bundle(
    api_url="https://coordinator.example:18443",
    signup_code="s" * 24,
    terms_version="2026-10-experimental-v1",
    dashboard_url="https://coordinator.example:13443",
)


class FakeCodex:
    def __init__(self, installed=True, logged_in=True, check="OWNER_ONLY_SUBSCRIPTION_RUN_OK"):
        self.codex_bin = "codex"
        self._installed, self._logged_in, self._check = installed, logged_in, check
        self.calls: list[str] = []

    def installed(self):
        return self._installed

    async def install(self):
        self.calls.append("install")
        raise AppError("codex_missing")

    async def logged_in(self):
        return self._logged_in

    async def login(self):
        self.calls.append("login")
        self._logged_in = True

    async def owner_check(self):
        self.calls.append("owner_check")
        return {"result": self._check}


class FakeClient:
    def __init__(self, log):
        self.log = log
        self.api_url = BUNDLE.api_url
        self.node_key = "sa_nk_x"

    async def signup(self, body):
        self.log.append(("signup", body["device_label"]))
        return {
            "user_id": "11111111-1111-4111-8111-111111111111",
            "group_id": "22222222-2222-4222-8222-222222222222",
            "resource_id": "33333333-3333-4333-8333-333333333333",
            "enrollment_token": "sa_enr_x",
            "enrollment_expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        }

    async def register(self, token, **kw):
        self.log.append(("register", kw["capabilities"]["executor"]))
        return {
            "worker_id": "44444444-4444-4444-8444-444444444444",
            "node_key": "sa_nk_x",
            "node_key_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        }

    async def sharing_online(self, body):
        self.log.append(("online", body["auto_approve_members"], body["acknowledgements"]))
        return {"group": {}, "sharing": {"state": "ENABLED"}, "invite_code": "ABCD-EFGH-JKLM"}

    async def sharing_offline(self):
        self.log.append(("offline",))
        return {"group": {}, "sharing": {"state": "PAUSED"}, "invite_code": None}

    async def sharing_status(self):
        return {"group": {}, "sharing": {"state": "PAUSED"}}

    async def aclose(self):
        pass


async def reachable(_url):
    return True


def make_controller(tmp_path, codex=None, bundle=BUNDLE):
    log: list = []
    agent_runs: list = []

    async def agent(stop):
        agent_runs.append("started")
        await stop.wait()
        return True

    c = Controller(
        tmp_path,
        bundle,
        codex=codex or FakeCodex(),
        probe=reachable,
        client_factory=lambda state: FakeClient(log),
        agent_factory=agent,
    )
    return c, log, agent_runs


async def test_one_click_goes_online_then_offline(tmp_path):
    codex = FakeCodex(logged_in=False)
    c, log, agent_runs = make_controller(tmp_path, codex)
    await c.go_online({"device_label": "Alice Mac", "subscription_type": "PLUS"})
    assert c.snap.phase == "ONLINE", c.snap.error
    assert codex.calls == ["login", "owner_check"]
    assert [x[0] for x in log] == ["signup", "register", "online"]
    assert log[1] == ("register", "codex-subscription")
    assert log[2][1] is True and all(log[2][2].values())  # standing approval + every ack
    assert load_state(tmp_path).worker_id.startswith("4444")
    assert not LocalSharingControl(tmp_path).paused
    snap = c.snapshot()
    assert snap["invite_code"] == "ABCD-EFGH-JKLM"
    assert snap["invite_link"] == "https://coordinator.example:13443/#join=ABCD-EFGH-JKLM"
    await asyncio.sleep(0)
    assert agent_runs == ["started"]
    state = load_app_state(tmp_path)
    assert state["owner_check_ok_at"] and "signup" not in state  # no stale enrollment kept

    await c.go_offline()
    assert c.snap.phase == "OFFLINE" and log[-1] == ("offline",)
    assert LocalSharingControl(tmp_path).paused

    # Second time: no signup and no repeated self-check.
    await c.go_online({})
    assert c.snap.phase == "ONLINE"
    assert codex.calls == ["login", "owner_check"]
    assert [x[0] for x in log].count("signup") == 1
    await c.shutdown()
    assert c.snap.phase == "OFFLINE"


@pytest.mark.parametrize("label", ["小安的 Mac", "Alice Mac", "工作电脑 💻"])
async def test_display_name_is_separate_from_registration_identifier(tmp_path, label):
    from apps.api.schemas.workers import WorkerRegistration

    registered = []

    class Client(FakeClient):
        async def register(self, token, **kw):
            registered.append(WorkerRegistration.model_validate(kw))
            return await super().register(token, **kw)

        async def sharing_online(self, body):
            assert body["device_label"] == label
            return await super().sharing_online(body)

    log = []
    c = Controller(
        tmp_path,
        BUNDLE,
        codex=FakeCodex(),
        probe=reachable,
        client_factory=lambda state: Client(log),
        agent_factory=lambda stop: stop.wait(),
        git_check=lambda: True,
    )
    try:
        await c.go_online({"device_label": label})
        assert c.snap.phase == "ONLINE", c.snap.error
        assert log[0] == ("signup", label)
        assert registered[0].name == f"spareagent-{registered[0].resource_id}"
        assert load_state(tmp_path).name == registered[0].name
        assert c.snapshot()["device_label"] == label
    finally:
        await c.shutdown()


@pytest.mark.parametrize("failure_step", ["register", "online"])
async def test_retry_resumes_saved_registration_and_self_check(tmp_path, failure_step):
    log, attempts = [], []

    class Client(FakeClient):
        async def register(self, token, **kw):
            attempts.append(("register", token, kw["name"]))
            if failure_step == "register" and len(attempts) == 1:
                raise TransportError("connection refused before registration")
            return await super().register(token, **kw)

        async def sharing_online(self, body):
            attempts.append(("online",))
            if failure_step == "online" and sum(a[0] == "online" for a in attempts) == 1:
                raise TransportError("coordinator temporarily unavailable")
            return await super().sharing_online(body)

    codex = FakeCodex(logged_in=False)

    def controller():
        return Controller(
            tmp_path,
            BUNDLE,
            codex=codex,
            probe=reachable,
            client_factory=lambda state: Client(log),
            agent_factory=lambda stop: stop.wait(),
            git_check=lambda: True,
        )

    c = controller()
    await c.go_online({"device_label": "小安的 Mac"})
    assert c.snap.phase == "ERROR" and c.snap.error["code"] == "service_unreachable"
    # Restarting the application must also preserve progress, not just clicking retry.
    c = controller()
    try:
        await c.go_online({})
        assert c.snap.phase == "ONLINE", c.snap.error
        assert [a[0] for a in log].count("signup") == 1
        assert codex.calls == ["login", "owner_check"]
        registrations = [a for a in attempts if a[0] == "register"]
        assert len(registrations) == (2 if failure_step == "register" else 1)
        assert len(set(registrations)) == 1  # same valid enrollment and stable name
    finally:
        await c.shutdown()


@pytest.mark.parametrize(
    ("codex", "bundle", "code"),
    [
        (FakeCodex(installed=False), BUNDLE, "codex_missing"),
        (FakeCodex(check="FAILED"), BUNDLE, "owner_check_failed"),
        (FakeCodex(), None, "bundle_missing"),
    ],
)
async def test_failures_are_reported_and_nothing_goes_online(tmp_path, codex, bundle, code):
    c, log, agent_runs = make_controller(tmp_path, codex, bundle)
    await c.go_online({})
    assert c.snap.phase == "ERROR" and c.snap.error["code"] == code
    assert ("online",) not in [x[:1] for x in log] and not agent_runs


async def test_rejected_settings_are_not_remembered_locally(tmp_path):
    class RejectingClient(FakeClient):
        async def sharing_settings(self, body):
            raise WorkerApiError(422, "MEMBER_LIMIT_OUT_OF_RANGE", "max_members out of range")

    c = Controller(
        tmp_path,
        BUNDLE,
        codex=FakeCodex(),
        probe=reachable,
        client_factory=lambda state: RejectingClient([]),
        agent_factory=lambda stop: stop.wait(),
    )
    await c.go_online({"device_label": "Alice Mac", "subscription_type": "PLUS"})
    with pytest.raises(WorkerApiError):
        await c.update_settings({"max_members": 99, "auto_approve_members": False})
    assert load_app_state(tmp_path).get("auto_approve_members", True) is True
    await c.shutdown()


async def test_cancel_aborts_a_slow_start(tmp_path):
    class SlowCodex(FakeCodex):
        async def login(self):
            await asyncio.sleep(3600)

    c, log, _ = make_controller(tmp_path, SlowCodex(logged_in=False))
    c.start_toggle({})
    await asyncio.sleep(0.01)
    assert c.snap.phase == "STARTING" and c.snap.step == "codex_login"
    await c.cancel()
    assert c.snap.phase == "OFFLINE" and not log


# ------------------------------------------------------------------ local server
async def test_member_notes_persist_locally_and_reject_unknown_members(tmp_path):
    c, log, _ = make_controller(tmp_path)
    c.snap.server = {"members": [{"user_id": "member-a", "is_owner": False}]}
    server = AppServer(c, asyncio.get_running_loop())
    try:
        state = await server._call("member-note", {"user_id": "member-a", "note": "  小明  "})
        assert state["member_notes"] == {"member-a": "小明"}
        restarted, _, _ = make_controller(tmp_path)
        assert restarted.snapshot()["member_notes"] == {"member-a": "小明"}
        assert log == []  # Names never leave the owner's device.
        for user, note in [("unknown", "小红"), ("member-a", "a" * 41), ("member-a", 42)]:
            with pytest.raises(AppError, match="invalid_input"):
                c.set_member_note(user, note)
        c.set_member_note("member-a", "")
        assert c.snapshot()["member_notes"] == {}
    finally:
        server.httpd.server_close()


@pytest.fixture
def server(tmp_path):
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    c, _, _ = make_controller(tmp_path)
    srv = AppServer(c, loop)
    srv.serve_in_thread()
    yield srv
    srv.close()
    loop.call_soon_threadsafe(loop.stop)


def request(srv, path, *, key=None, host=None, body=b"{}", method="POST"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}{path}", data=body if method == "POST" else None, method=method
    )
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-App-Key", key)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback test
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_page_is_served_and_api_needs_the_launch_key(server):
    status, page = request(server, "/", method="GET")
    assert status == 200 and "一键提供算力" in page.decode()
    assert request(server, "/api/state")[0] == 403
    assert request(server, "/api/state", key="wrong")[0] == 403
    status, body = request(server, "/api/state", key=server.key)
    assert status == 200 and json.loads(body)["phase"] == "OFFLINE"
    assert server.key in server.url and server.url.startswith("http://127.0.0.1:")


def test_dns_rebinding_host_is_refused(server):
    assert request(server, "/", method="GET", host="evil.example")[0] == 403
    assert (
        request(server, "/api/state", key=server.key, host=f"evil.example:{server.port}")[0] == 403
    )


async def test_state_reports_the_app_version(tmp_path, monkeypatch):
    c, _, _ = make_controller(tmp_path)
    srv = AppServer(c, asyncio.get_running_loop())
    try:
        assert (await srv._call("state", {}))["app_version"] is None
        monkeypatch.setenv("SPAREAGENT_APP_VERSION", "0.17.3")
        assert (await srv._call("state", {}))["app_version"] == "0.17.3"
    finally:
        srv.httpd.server_close()


async def test_desktop_pause_never_starts_an_offline_device(tmp_path):
    codex = FakeCodex()
    c, log, agent_runs = make_controller(tmp_path, codex)
    srv = AppServer(c, asyncio.get_running_loop(), stop=asyncio.Event())
    srv.serve_in_thread()
    try:
        state = await srv._call("pause", {"offline_mode": "now"})
        await srv._desktop_task
        assert state["desktop_protocol"] == 1
        assert c.snap.phase == "OFFLINE"
        assert not codex.calls and not log and not agent_runs
        with pytest.raises(AppError):
            await srv._call("shutdown", {"offline_mode": "invalid"})
    finally:
        await asyncio.to_thread(srv.close)


async def test_desktop_shutdown_waits_for_running_tasks_and_blocks_new_online(tmp_path):
    c, _, _ = make_controller(tmp_path)
    drained, finish = asyncio.Event(), asyncio.Event()

    async def agent(stop):
        await stop.wait()
        drained.set()
        await finish.wait()
        return True

    c._agent_factory = agent
    await c.go_online({})
    stop = asyncio.Event()
    srv = AppServer(c, asyncio.get_running_loop(), stop=stop)
    srv.serve_in_thread()
    try:
        state = await srv._call("shutdown", {"offline_mode": "finish"})
        await drained.wait()
        assert state["phase"] == "STOPPING" and not stop.is_set()
        assert LocalSharingControl(tmp_path).paused
        with pytest.raises(AppError):
            await srv._call("toggle", {})
        finish.set()
        await srv._desktop_task
        assert stop.is_set() and c.snap.phase == "OFFLINE"
    finally:
        finish.set()
        await c.shutdown()
        await asyncio.to_thread(srv.close)


async def test_desktop_shutdown_cancels_a_pending_login(tmp_path):
    class SlowCodex(FakeCodex):
        async def login(self):
            await asyncio.sleep(3600)

    c, log, _ = make_controller(tmp_path, SlowCodex(logged_in=False))
    c.start_toggle({})
    await asyncio.sleep(0.01)
    stop = asyncio.Event()
    srv = AppServer(c, asyncio.get_running_loop(), stop=stop)
    srv.serve_in_thread()
    try:
        await srv._call("shutdown", {})
        await srv._desktop_task
        assert stop.is_set() and c.snap.phase == "OFFLINE" and not log
    finally:
        await c.shutdown()
        await asyncio.to_thread(srv.close)


async def test_capability_settings_and_tool_state_reach_the_page(tmp_path):
    sent = []

    class Client(FakeClient):
        async def sharing_settings(self, body):
            sent.append(body)
            return {"group": {}, "sharing": {"state": "ENABLED"}}

    c = Controller(
        tmp_path,
        BUNDLE,
        codex=FakeCodex(),
        probe=reachable,
        client_factory=lambda state: Client([]),
        agent_factory=lambda stop: stop.wait(),
        git_check=lambda: True,
    )
    await c.go_online({"device_label": "Alice Mac", "subscription_type": "PLUS"})
    await c.update_settings(
        {
            "max_concurrent_tasks": "2",
            "allowed_task_types": ["TRANSLATE_DOCUMENT"],
            "allowed_tools": ["doc", "git"],
        }
    )
    assert sent[-1] == {
        "max_concurrent_tasks": 2,
        "allowed_task_types": ["TRANSLATE_DOCUMENT"],
        "allowed_tools": ["doc", "git"],
    }
    await c.update_settings({"max_tasks_per_day": None, "max_task_minutes": None})
    assert sent[-1] == {"max_tasks_per_day": None, "max_task_duration_seconds": None}
    with pytest.raises(AppError):
        await c.update_settings({"allowed_tools": "doc"})
    snap = c.snapshot()
    assert snap["tools"]["git"] is True
    assert snap["tools"]["python-data"]["state"] == "UNAVAILABLE"  # no pins in this bundle
    caps = c.capabilities()
    assert caps["tools"] == {
        "code": True,
        "doc": True,
        "git": True,
        "python-data": False,
        "network": True,
    }
    assert c.tool_access("python-data") is None
    c.start_tool_install("python-data")
    await asyncio.sleep(0.05)
    assert c.snapshot()["tools"]["python-data"]["state"] == "UNAVAILABLE"
    with pytest.raises(AppError):
        c.start_tool_install("shell")
    await c.shutdown()


@pytest.mark.parametrize(("mode", "grace"), [("finish", 3660.0), ("now", 0.0)])
async def test_offline_choice_sets_how_long_running_tasks_may_continue(tmp_path, mode, grace):
    from types import SimpleNamespace

    c, log, _ = make_controller(tmp_path)
    await c.go_online({"device_label": "Alice Mac", "subscription_type": "PLUS"})
    agent = SimpleNamespace(config=SimpleNamespace(shutdown_grace_seconds=60.0), active=2)
    c._agent = agent
    assert c.snapshot()["running_here"] == 2
    await c.toggle({"offline_mode": mode})
    assert agent.config.shutdown_grace_seconds == grace
    assert c.snap.phase == "OFFLINE" and log[-1] == ("offline",)
    assert c.snapshot()["running_here"] == 0 and c.snapshot()["offline_mode"] is None
    with pytest.raises(AppError):
        await c.go_offline("whenever")


async def test_agent_reports_the_device_failure_code():
    from packages.provider_sdk.base import PermanentProviderError
    from packages.provider_sdk.types import ExecutionRequest
    from packages.worker.agent import WorkerAgent

    class Failing:
        async def execute(self, request):
            raise PermanentProviderError("check failed", detail_code="WRONG_LANGUAGE")
            yield  # pragma: no cover - makes this an async generator

        async def cancel(self, attempt_id):
            pass

    req = ExecutionRequest(
        "t", "a", "u", "EXPERIMENTAL_SHARED_SUBSCRIPTION", "p", None, None, 30, 1
    )
    payload = await WorkerAgent(object())._run_adapter(req, "lease", asyncio.Event(), Failing())
    assert payload["failure_detail"] == "WRONG_LANGUAGE"
    assert payload["error_code"] == "PERMANENT_PROVIDER_ERROR" and not payload["succeeded"]


# ---------------------------------------------------------------- several networks
def two_network_bundle(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "api_url": "https://10.0.0.16:18443",
                "dashboard_url": "https://10.0.0.16:13443",
                "signup_code": "s" * 24,
                "terms_version": "2026-10-experimental-v1",
                "networks": [
                    {
                        "id": "lan",
                        "label": "局域网",
                        "api_url": "https://10.0.0.16:18443",
                        "dashboard_url": "https://10.0.0.16:13443",
                    },
                    {
                        "id": "tailscale",
                        "label": "Tailscale",
                        "api_url": "https://100.87.82.71:18443",
                        "dashboard_url": "https://100.87.82.71:13443",
                    },
                ],
            }
        )
    )
    return Bundle.load(path)


async def test_auto_picks_the_first_reachable_network_and_owner_can_pin_one(tmp_path):
    up = {"https://10.0.0.16:18443": False, "https://100.87.82.71:18443": True}

    async def probe(url):
        return up[url]

    c = Controller(
        tmp_path,
        two_network_bundle(tmp_path),
        codex=FakeCodex(),
        probe=probe,
        client_factory=lambda state: FakeClient([]),
        agent_factory=lambda stop: stop.wait(),
    )
    await c.resolve_network()
    assert c.network().id == "tailscale"  # off the home Wi-Fi: LAN is unreachable
    assert c._default_client(None).api_url == "https://100.87.82.71:18443"
    snap = c.snapshot()
    assert snap["network_choice"] == "auto" and snap["network"] == "tailscale"
    assert [(n["id"], n["reachable"]) for n in snap["networks"]] == [
        ("lan", False),
        ("tailscale", True),
    ]
    c._save(invite_code="ABCD-EFGH-JKLM")
    snap = c.snapshot()
    assert snap["invite_link"] == "https://100.87.82.71:13443/#join=ABCD-EFGH-JKLM"
    assert [x["url"] for x in snap["invite_links"]] == [
        "https://10.0.0.16:13443/#join=ABCD-EFGH-JKLM",
        "https://100.87.82.71:13443/#join=ABCD-EFGH-JKLM",
    ]

    up["https://10.0.0.16:18443"] = True
    await c.resolve_network()
    assert c.network().id == "lan"  # back home: LAN first
    await c.set_network("tailscale")
    assert c.network().id == "tailscale" and load_app_state(c.directory)["network"] == "tailscale"
    with pytest.raises(AppError):
        await c.set_network("elsewhere")
    await c.go_online({"device_label": "Alice Mac", "subscription_type": "PLUS"})
    assert c.snap.phase == "ONLINE", c.snap.error
    with pytest.raises(AppError) as exc:
        await c.set_network("lan")
    assert exc.value.code == "network_locked"
    await c.go_offline()


def test_old_single_address_bundles_still_load(tmp_path):
    assert [n.api_url for n in BUNDLE.networks] == [BUNDLE.api_url]
    c, _, _ = make_controller(tmp_path)
    assert c.snapshot()["networks"] == [{"id": "default", "label": "", "reachable": None}]
    assert c.snapshot()["invite_links"] == []


async def test_registered_device_uses_updated_bundle_trust_and_dashboard(tmp_path, monkeypatch):
    import packages.worker.app as app_mod

    c, _, _ = make_controller(tmp_path)
    await c.go_online({"device_label": "My Mac", "subscription_type": "PLUS"})
    await c.go_offline()
    state = load_state(tmp_path)
    state.ca_file = str(tmp_path / "obsolete-private-ca.pem")
    c.bundle = Bundle(
        api_url="https://server.example.ts.net:8444",
        dashboard_url="https://server.example.ts.net:8443",
        signup_code="s" * 24,
        terms_version=BUNDLE.terms_version,
    )
    sent = {}

    def client(url, **kwargs):
        sent.update(api_url=url, **kwargs)

    monkeypatch.setattr(app_mod, "NodeClient", client)
    c._default_client(state)
    assert sent["api_url"] == c.bundle.api_url
    assert sent["ca_file"] is None and sent["node_key"] == state.node_key
    assert c.snapshot()["dashboard_url"] == c.bundle.dashboard_url

    async def dashboard_login(_name):
        return {"dashboard_url": "https://old.example:13443", "token": "test-token"}

    monkeypatch.setattr(c, "_owner_call", dashboard_login)
    assert (await c.dashboard_link()).startswith(c.bundle.dashboard_url + "/#login=")


@pytest.mark.parametrize("failure", [None, "certificate", "dns", "unready"])
async def test_https_probe_checks_tls_and_readiness_without_inference(
    tmp_path, monkeypatch, failure
):
    import socket
    import ssl

    import httpx

    import packages.worker.app as app_mod

    requests = []

    def handle(request):
        requests.append(request)
        if failure == "certificate":
            raise httpx.ConnectError("TLS") from ssl.SSLCertVerificationError(1, "wrong host")
        if failure == "dns":
            raise httpx.ConnectError("DNS") from socket.gaierror("offline")
        return httpx.Response(503 if failure == "unready" else 200)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        app_mod.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    c = Controller(tmp_path, Bundle("https://server.example.ts.net:8444", "s" * 24, "v1"))
    await c.resolve_network()
    expected = {
        None: None,
        "certificate": "certificate_invalid",
        "dns": "tailscale_unreachable",
        "unready": "service_unreachable",
    }[failure]
    assert c.snapshot()["connection_error"] == expected
    assert c.snapshot()["networks"][0]["reachable"] is (failure is None)
    assert [(r.method, r.url.path) for r in requests] == [("GET", "/readyz")]
    if failure:
        await c.go_online({})
        assert c.snap.phase == "ERROR" and c.snap.error["code"] == expected
        assert load_state(tmp_path) is None  # no registration or owner self-check


async def test_owner_sets_member_limits_from_the_app(tmp_path):
    sent = []

    class Client(FakeClient):
        async def set_member_limits(self, user_id, limits):
            sent.append((user_id, limits))
            return {"group": {}, "sharing": {"state": "ENABLED"}}

    c = Controller(
        tmp_path,
        BUNDLE,
        codex=FakeCodex(),
        probe=reachable,
        client_factory=lambda state: Client([]),
        agent_factory=lambda stop: stop.wait(),
        git_check=lambda: True,
    )
    await c.go_online({"device_label": "Alice Mac", "subscription_type": "PLUS"})
    await c.set_member_limits("u-1", {"max_tasks_per_day": 3, "max_minutes_per_day": None})
    assert sent == [("u-1", {"max_tasks_per_day": 3, "max_minutes_per_day": None})]
    with pytest.raises(AppError):
        await c.set_member_limits("u-1", {"max_tasks_per_day": "3"})
    await c.go_offline()
