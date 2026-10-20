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
