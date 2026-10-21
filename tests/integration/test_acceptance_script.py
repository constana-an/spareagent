"""The design's acceptance script (docs/GENERAL_TASKS_DESIGN.md §8) in SIMULATED mode.

Unlike the protocol tests, the owner's device here is the real `WorkerAgent` (heartbeat,
long poll, local opt-in, attachment download, finalization) with its in-process mock
executor, talking to the API in-process. No model, no Codex, no Docker socket.

1. The owner's device is online with an invite code; Bob joins.
2-3. Bob submits a .docx translation, an .xlsx analysis, a code change and two general
     tasks (a question, and an uploaded file to change); all of them are
     approved by the standing approval, run on the device, pass the platform checks and
     download by name with matching SHA-256.
4. The owner lowers "tasks per 24 hours" to what is used: the next task waits with
   DAILY_LIMIT; raising the limit lets it run.
5. The owner goes offline: a new task waits with OWNER_PAUSED; online again, it runs.
   "Stop now and hand back" re-queues a running task, which then completes.
6. The device drops off the network mid-run: the lease lapses, the task is re-run once
   and completes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time

import httpx

from apps.api.main import create_app
from packages.provider_sdk.mock import MockProviderAdapter
from packages.worker.agent import AgentConfig, WorkerAgent
from packages.worker.lease_client import NodeClient
from packages.worker.local_controls import LocalSharingControl
from services.scheduler.dispatcher import Dispatcher
from services.scheduler.lease_service import LeaseService
from tests.conftest import execute
from tests.integration import test_generic_tasks as gt
from tests.integration import test_one_click_sharing as one_click
from tests.integration.test_experimental_sharing import ok
from tests.unit.test_deliverables import make_docx, make_xlsx

octx = one_click.octx
oc = one_click.oc
REPO = {
    "url": "https://github.com/example/public-fixture",
    "commit_sha": "0123456789abcdef0123456789abcdef01234567",
}


class Device:
    """The owner's worker process, started and stopped like the Mac app does."""

    def __init__(self, ctx, node, state_dir, *, step_delay: float = 0.0) -> None:
        self.ctx, self.node, self.state_dir, self.step_delay = ctx, node, state_dir, step_delay
        self.stop: asyncio.Event | None = None
        self.task: asyncio.Task | None = None
        self.client: NodeClient | None = None

    async def start(self) -> None:
        self.client = NodeClient(
            "http://localhost",
            worker_id=self.node["worker_id"],
            node_key=self.node["node_key"],
            allow_insecure_http=True,
            transport=httpx.ASGITransport(app=create_app(self.ctx.settings, self.ctx)),
        )
        agent = WorkerAgent(
            self.client,
            AgentConfig(
                poll_wait_seconds=0.2,
                heartbeat_interval_seconds=0.3,
                backoff_base_seconds=0.05,
                backoff_max_seconds=0.2,
                shutdown_grace_seconds=0.5,
                capabilities={"executor": "codex-subscription", "tools": gt.ALL_TOOLS},
            ),
            MockProviderAdapter(step_delay_seconds=self.step_delay),
            sharing_control=LocalSharingControl(self.state_dir),
        )
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(agent.run(self.stop))

    async def shutdown(self) -> None:
        """Graceful stop: a running attempt is handed back (WORKER_SHUTDOWN)."""
        if self.task is None:
            return
        self.stop.set()
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.task
        await self.client.aclose()
        self.task = None

    async def vanish(self) -> None:
        """The network drops: nothing more reaches the server from this device."""
        if self.task is None:
            return

        def unreachable(request):
            raise httpx.ConnectError("network is down", request=request)

        await self.client._http.aclose()
        self.client._http = httpx.AsyncClient(
            base_url="http://localhost", transport=httpx.MockTransport(unreachable)
        )
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.task
        self.task = None


async def view(bob, task) -> dict:
    return await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))


async def wait_for(bob, task, statuses, seconds: float = 20.0, tick=None) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        out = await view(bob, task)
        if out["status"] in statuses:
            return out
        assert time.monotonic() < deadline, f"stuck in {out['status']}: {out}"
        if tick is not None:
            await tick()
        await asyncio.sleep(0.1)


async def deliverables(bob, task) -> dict[str, bytes]:
    items = (await ok(await bob.get(f"/v1/tasks/{task['id']}/artifacts")))["items"]
    out = {}
    for a in items:
        if a["kind"] != "DELIVERABLE":
            continue
        got = await bob.get(f"/v1/tasks/{task['id']}/artifacts/{a['id']}/content")
        assert got.status_code == 200, got.text
        assert hashlib.sha256(got.content).hexdigest() == a["sha256"]
        assert got.headers.get("x-artifact-sha256") in (None, a["sha256"])
        out[a["name"]] = got.content
    return out


async def translation(bob, status, n: int) -> dict:
    """A fresh upload per task: an attachment belongs to exactly one task."""
    doc = await gt.upload(bob, f"合同-{n}.docx", make_docx())
    body = gt.generic(
        status,
        kind="TRANSLATE_DOCUMENT",
        requirement=f"Translate into Chinese ({n})",
        attachments=[doc["id"]],
        parameters={"target_language": "zh"},
    )
    return await gt.submit(bob, body)


async def test_acceptance_script_in_simulation(oc, octx, tmp_path):
    node, status, bob = await gt.group_with_member(oc, octx)
    device = Device(octx, node, tmp_path / "device")
    await device.start()
    try:
        # ---- 2-3: three kinds, auto-approved, executed and downloaded
        sheet = await gt.upload(bob, "sales.xlsx", make_xlsx())
        tasks = [
            await translation(bob, status, 1),
            await gt.submit(
                bob,
                gt.generic(
                    status,
                    kind="ANALYZE_SPREADSHEET",
                    requirement="Monthly totals and a chart",
                    attachments=[sheet["id"]],
                ),
            ),
            await gt.submit(
                bob,
                gt.generic(
                    status, kind="CODE_CHANGE", requirement="Add a --verbose flag", repository=REPO
                ),
            ),
        ]
        source = await gt.upload(bob, "app.py", b"print('hi')\n")
        tasks += [
            await gt.submit(
                bob, gt.generic(status, kind="GENERAL", requirement="讨论：如何学习 Rust？")
            ),
            await gt.submit(
                bob,
                gt.generic(
                    status,
                    kind="GENERAL",
                    requirement="Add a --verbose flag and draw a diagram",
                    attachments=[source["id"]],
                ),
            ),
        ]
        for task in tasks:
            done = await wait_for(bob, task, ("SUCCEEDED", "FAILED", "TIMED_OUT"))
            assert done["status"] == "SUCCEEDED", done
        assert set(await deliverables(bob, tasks[0])) == {"translation.docx"}
        assert set(await deliverables(bob, tasks[1])) == {"report.md"}
        code_items = (await ok(await bob.get(f"/v1/tasks/{tasks[2]['id']}/artifacts")))["items"]
        assert "PATCH" in {a["kind"] for a in code_items}
        assert set(await deliverables(bob, tasks[3])) == {"answer.md"}
        assert set(await deliverables(bob, tasks[4])) == {"answer.md"}

        # ---- 4: the daily limit holds the next task, raising it releases the task
        used = (await ok(await one_click.owner(oc, node, "GET", "")))["usage"][
            "tasks_started_last_24h"
        ]
        assert used == 5
        await ok(
            await one_click.owner(oc, node, "PUT", "/settings", json={"max_tasks_per_day": used})
        )
        fourth = await translation(bob, status, 4)
        await asyncio.sleep(1.0)
        held = await view(bob, fourth)
        assert held["status"] == "QUEUED"
        assert "DAILY_LIMIT" in held["last_decision"]["reasons"]
        await ok(
            await one_click.owner(oc, node, "PUT", "/settings", json={"max_tasks_per_day": 20})
        )
        assert (await wait_for(bob, fourth, ("SUCCEEDED", "FAILED")))["status"] == "SUCCEEDED"

        # ---- 5a: owner offline -> the task waits and says why; online again -> it runs
        await device.shutdown()
        await ok(await one_click.owner(oc, node, "POST", "/offline"))
        fifth = await translation(bob, status, 5)
        await asyncio.sleep(0.5)
        await Dispatcher(octx).promote_retries()
        assert "OWNER_PAUSED" in (await view(bob, fifth))["last_decision"]["reasons"]
        await one_click.go_online(oc, node)
        await device.start()
        assert (await wait_for(bob, fifth, ("SUCCEEDED", "FAILED")))["status"] == "SUCCEEDED"

        # ---- 5b: "stop now and hand back" mid-run -> re-queued -> completes
        await device.shutdown()
        slow = Device(octx, node, tmp_path / "device", step_delay=3.0)
        await slow.start()
        sixth = await translation(bob, status, 6)
        await wait_for(bob, sixth, ("RUNNING",))
        await slow.shutdown()
        assert (await view(bob, sixth))["status"] == "RETRY_PENDING"
        await device.start()
        promote = Dispatcher(octx).promote_retries
        done = await wait_for(bob, sixth, ("SUCCEEDED", "FAILED"), tick=promote)
        assert done["status"] == "SUCCEEDED", done

        # ---- 6: the network drops mid-run -> lease lapses -> re-run once -> completes
        await device.shutdown()
        slow = Device(octx, node, tmp_path / "device", step_delay=3.0)
        await slow.start()
        seventh = await translation(bob, status, 7)
        await wait_for(bob, seventh, ("RUNNING",))
        await slow.vanish()
        await execute(
            octx,
            "UPDATE task_attempts SET leased_until=now()-interval '1 second' "
            "WHERE status IN ('RESERVED','RUNNING')",
        )
        assert await LeaseService(octx).recover_expired() == 1
        assert (await view(bob, seventh))["status"] == "RETRY_PENDING"
        await slow.client.aclose()
        await device.start()
        done = await wait_for(bob, seventh, ("SUCCEEDED", "FAILED"), tick=promote)
        assert done["status"] == "SUCCEEDED", done
    finally:
        await device.shutdown()
        await bob.aclose()
