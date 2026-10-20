"""Step D (docs/GENERAL_TASKS_DESIGN.md §6): what members and providers see while a task
waits, runs and ends. Protocol-level; no real execution."""

import base64

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
        assert out["failure"]["category"] == "DEVICE_INTERRUPTED"
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
