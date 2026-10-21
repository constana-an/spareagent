"""Step D (docs/GENERAL_TASKS_DESIGN.md §6): what members and providers see while a task
waits, runs and ends. Protocol-level; no real execution."""

import base64

from packages.provider_sdk.mock import MockProviderAdapter
from packages.provider_sdk.types import ExecutionRequest, ExecutionResult
from tests.conftest import bearer
from tests.integration import test_generic_tasks as gt
from tests.integration import test_one_click_sharing as one_click
from tests.integration.test_experimental_live import poll
from tests.integration.test_experimental_sharing import ok

octx = one_click.octx
oc = one_click.oc


async def view(bob, task) -> dict:
    return await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))


async def start(oc, node) -> dict:
    lease = (await poll(oc, node)).json()
    auth = bearer(node["access_token"])
    assert (
        await oc.post(f"/v1/leases/{lease['lease_id']}/accept", headers=auth)
    ).status_code == 204
    return lease


async def fail(oc, node, lease, **body) -> dict:
    payload = {"finalization_key": f"fin-{lease['attempt_id']}", "succeeded": False, **body}
    return await ok(
        await oc.post(
            f"/v1/leases/{lease['lease_id']}/complete",
            json=payload,
            headers=bearer(node["access_token"]),
        )
    )


async def test_queue_position_waiting_reason_progress_and_run_panel(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        conn = status["sharing"]["connection_id"]
        first = await one_click.submit_as(bob, status, conn)
        second = await one_click.submit_as(bob, status, conn)
        assert (await view(bob, first))["queue_position"] == 1
        assert (await view(bob, second))["queue_position"] == 2

        lease = await start(oc, node)  # takes the first task
        await ok(
            await oc.post(
                f"/v1/leases/{lease['lease_id']}/progress",
                json={"message": "Reading the code", "percent": 40},
                headers=bearer(node["access_token"]),
            )
        )
        running = await view(bob, first)
        assert running["status"] == "RUNNING" and running["queue_position"] is None
        assert running["progress"]["percent"] == 40
        assert running["progress"]["message"] == "Reading the code"
        assert running["started_at"]

        # Concurrency 1: the second task waits with a concrete reason.
        assert (await poll(oc, node)).status_code == 204
        waiting = await view(bob, second)
        assert waiting["queue_position"] == 1
        assert waiting["last_decision"]["reasons"] == ["CONCURRENCY_LIMIT"]

        panel = await ok(await one_click.owner(oc, node, "GET", ""))
        assert panel["usage"]["running"] == 1 and panel["usage"]["queued"] == 1
        assert panel["usage"]["execution_minutes_last_24h"] >= 0
        run = next(t for t in panel["tasks"] if t["id"] == first["id"])
        assert run["progress"]["percent"] == 40 and run["started_at"]
        assert run["max_duration_seconds"] == 300

        # The owner goes offline: the queued task now waits for that reason.
        await ok(await one_click.owner(oc, node, "POST", "/offline"))
        assert (await poll(oc, node)).status_code == 204
        assert "OWNER_PAUSED" in (await view(bob, second))["last_decision"]["reasons"]
    finally:
        await bob.aclose()


async def test_device_failure_detail_reaches_the_member(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        task = await one_click.submit_as(bob, status, status["sharing"]["connection_id"])
        lease = await start(oc, node)
        await fail(
            oc,
            node,
            lease,
            error_code="PERMANENT_PROVIDER_ERROR",
            result_summary="Result did not pass the device check",
            failure_detail="WRONG_LANGUAGE",
        )
        out = await view(bob, task)
        assert out["status"] == "FAILED"
        assert out["failure"] == {"code": "WRONG_LANGUAGE", "category": "CHECK_FAILED"}
    finally:
        await bob.aclose()


async def test_shutdown_and_verification_failures_are_categorised(oc, octx):
    from packages.common.deliverables import docx_replace_paragraphs
    from services.verification.service import VerificationService
    from tests.unit.test_deliverables import make_docx

    node, bob, task = await gt.live_translation(oc, octx, make_docx())
    try:
        english = docx_replace_paragraphs(make_docx(), ["Hello", "Cell", "End"])
        await gt.complete_with(oc, node, [("translation.docx", gt.DOCX_MT, english)])
        await VerificationService(octx, None, None).run_once()
        assert (await view(bob, task))["failure"] == {
            "code": "WRONG_LANGUAGE",
            "category": "CHECK_FAILED",
        }
    finally:
        await bob.aclose()

    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        task = await one_click.submit_as(bob, status, status["sharing"]["connection_id"])
        lease = await start(oc, node)
        await fail(oc, node, lease, error_code="WORKER_SHUTDOWN", retryable=True)
        out = await view(bob, task)
        # Handed back, not failed: the final interruption is covered below.
        assert out["status"] == "RETRY_PENDING" and out["failure"] is None
    finally:
        await bob.aclose()


async def test_member_cancels_a_queued_generic_task(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        doc = await gt.upload(bob, "a.md", b"# Hi\n\nThere\n")
        task = await gt.submit(
            bob,
            gt.generic(
                status,
                kind="TRANSLATE_DOCUMENT",
                requirement="Translate",
                attachments=[doc["id"]],
                parameters={"target_language": "fr"},
            ),
        )
        resp = await bob.post(f"/v1/tasks/{task['id']}/cancel", headers=gt.ORIGIN)
        assert resp.status_code == 202, resp.text
        out = await view(bob, task)
        assert out["status"] == "CANCELLED"
        assert out["failure"]["category"] == "CANCELLED"
    finally:
        await bob.aclose()


async def test_progress_payload_is_redacted_and_bounded(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        task = await one_click.submit_as(bob, status, status["sharing"]["connection_id"])
        lease = await start(oc, node)
        secret = "sk-proj-" + base64.b32encode(b"x" * 30).decode()
        await ok(
            await oc.post(
                f"/v1/leases/{lease['lease_id']}/progress",
                json={"message": f"token {secret}", "percent": 10},
                headers=bearer(node["access_token"]),
            )
        )
        assert secret not in (await view(bob, task))["progress"]["message"]
    finally:
        await bob.aclose()


async def test_stop_and_hand_back_requeues_the_task_once(oc, octx):
    """§6: "stop now and hand back" re-runs the task once on the owner's device; a second
    interruption ends it, so one task never re-runs without limit."""
    from services.scheduler.dispatcher import Dispatcher

    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        task = await one_click.submit_as(bob, status, status["sharing"]["connection_id"])
        first = await start(oc, node)
        await fail(oc, node, first, error_code="WORKER_SHUTDOWN", retryable=True)
        assert (await view(bob, task))["status"] == "RETRY_PENDING"
        assert await Dispatcher(octx).promote_retries() == 1
        assert (await view(bob, task))["status"] == "QUEUED"
        second = await start(oc, node)
        assert second["attempt_id"] != first["attempt_id"]
        await fail(oc, node, second, error_code="WORKER_SHUTDOWN", retryable=True)
        out = await view(bob, task)
        assert out["status"] == "FAILED"
        assert out["failure"]["category"] == "DEVICE_INTERRUPTED"
    finally:
        await bob.aclose()


async def test_a_failed_run_is_not_repeated(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        task = await one_click.submit_as(bob, status, status["sharing"]["connection_id"])
        lease = await start(oc, node)
        await fail(oc, node, lease, error_code="EXECUTION_ERROR", retryable=True)
        assert (await view(bob, task))["status"] == "FAILED"
    finally:
        await bob.aclose()


async def test_owner_device_picks_up_simulated_tasks(oc, octx):
    """The web form's default SIMULATED mode runs on the owner's normal worker (its
    in-process mock executor) without any operator step."""
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        task = await one_click.submit_as(
            bob, status, status["sharing"]["connection_id"], mode="SIMULATED"
        )
        resp = await poll(oc, node)
        assert resp.status_code == 200, resp.text
        lease = resp.json()
        assert lease["task"]["task_id"] == task["id"]
        assert lease["execution_profile"] == "MOCK"
        auth = bearer(node["access_token"])
        assert (
            await oc.post(f"/v1/leases/{lease['lease_id']}/accept", headers=auth)
        ).status_code == 204
        # What the worker's MOCK executor returns for this lease, finalized the same way.
        t = lease["task"]
        request = ExecutionRequest(
            task_id=t["task_id"],
            attempt_id=lease["attempt_id"],
            user_id="redacted",
            resource_scope=lease["resource_scope"],
            prompt=t["prompt"],
            repository_url=t["repository_url"],
            commit_sha=t["commit_sha"],
            max_duration_seconds=t["max_duration_seconds"],
            max_budget_cents=t["max_budget_cents"],
            task_type=t["task_type"],
        )
        result = [i async for i in MockProviderAdapter().execute(request)][-1]
        assert isinstance(result, ExecutionResult)
        await ok(
            await oc.post(
                f"/v1/leases/{lease['lease_id']}/complete",
                json={
                    "finalization_key": f"fin-{lease['attempt_id']}",
                    "succeeded": True,
                    "result_summary": result.summary,
                    "artifacts": [
                        {
                            "kind": a.kind,
                            "name": a.name,
                            "media_type": a.media_type,
                            "content_base64": base64.b64encode(a.content).decode(),
                        }
                        for a in result.artifacts
                    ],
                },
                headers=auth,
            )
        )
        assert (await view(bob, task))["status"] == "SUCCEEDED"
    finally:
        await bob.aclose()


async def test_reservation_that_lapses_before_starting_does_not_use_the_rerun(oc, octx):
    """§6: a task the device never started goes back to the queue for free; it still gets
    its one re-run after a real interruption later."""
    from services.scheduler.lease_service import LeaseService
    from tests.conftest import execute

    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        task = await one_click.submit_as(bob, status, status["sharing"]["connection_id"])
        reserved = (await poll(oc, node)).json()  # reserved, never accepted
        await execute(octx, "UPDATE task_attempts SET leased_until=now()-interval '1 second'")
        assert await LeaseService(octx).recover_expired() == 1
        assert (await view(bob, task))["status"] == "QUEUED"
        first = await start(oc, node)
        assert first["attempt_id"] != reserved["attempt_id"]
        await fail(oc, node, first, error_code="WORKER_SHUTDOWN", retryable=True)
        assert (await view(bob, task))["status"] == "RETRY_PENDING"
    finally:
        await bob.aclose()


async def test_per_member_usage_and_limits(oc, octx):
    """D-98: the owner sees each member's 24 h use and can cap a member's tasks/minutes;
    the member sees their own use and caps; a capped member's task waits with the reason."""
    from tests.conftest import execute

    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        conn = status["sharing"]["connection_id"]
        first = await one_click.submit_as(bob, status, conn, mode="SIMULATED")
        lease = await start(oc, node)
        assert lease["task"]["task_id"] == first["id"]
        await fail(oc, node, lease, error_code="EXECUTION_ERROR", retryable=False)
        # Pretend the run took 30 minutes.
        await execute(
            octx,
            "UPDATE task_attempts SET started_at=completed_at-interval '30 minutes'",
        )
        owner_view = await ok(await one_click.owner(oc, node, "GET", ""))
        (member,) = [m for m in owner_view["members"] if not m["is_owner"]]
        assert member["tasks_last_24h"] == 1
        assert 29.5 <= member["minutes_last_24h"] <= 30.5
        assert member["max_tasks_per_day"] is None and member["max_minutes_per_day"] is None

        limits = f"/members/{member['user_id']}/limits"
        await ok(await one_click.owner(oc, node, "PUT", limits, json={"max_tasks_per_day": 1}))
        nodes = (
            await ok(
                await bob.get(f"/v1/experimental-tasks/nodes?group_id={status['group']['id']}")
            )
        )["items"]
        (mine,) = nodes
        assert mine["my_tasks_last_24h"] == 1 and mine["my_max_tasks_per_day"] == 1
        assert mine["my_max_minutes_per_day"] is None

        second = await one_click.submit_as(bob, status, conn, mode="SIMULATED")
        assert (await poll(oc, node)).status_code == 204
        assert "MEMBER_DAILY_LIMIT" in (await view(bob, second))["last_decision"]["reasons"]

        # Clearing the task cap but capping minutes below what was used still holds it.
        await ok(
            await one_click.owner(
                oc, node, "PUT", limits, json={"max_tasks_per_day": None, "max_minutes_per_day": 10}
            )
        )
        assert (await poll(oc, node)).status_code == 204
        assert "MEMBER_MINUTES_LIMIT" in (await view(bob, second))["last_decision"]["reasons"]
        owner_view = await ok(await one_click.owner(oc, node, "GET", ""))
        (member,) = [m for m in owner_view["members"] if not m["is_owner"]]
        assert member["max_tasks_per_day"] is None and member["max_minutes_per_day"] == 10

        # Raising it lets the queued task run.
        await ok(await one_click.owner(oc, node, "PUT", limits, json={"max_minutes_per_day": 60}))
        assert (await poll(oc, node)).json()["task"]["task_id"] == second["id"]

        bad = await one_click.owner(oc, node, "PUT", limits, json={"max_tasks_per_day": 0})
        assert bad.status_code == 422
        owner_id = next(m["user_id"] for m in owner_view["members"] if m["is_owner"])
        own = await one_click.owner(
            oc, node, "PUT", f"/members/{owner_id}/limits", json={"max_tasks_per_day": 3}
        )
        assert own.status_code == 422
    finally:
        await bob.aclose()
