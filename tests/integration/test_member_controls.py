"""Owner member controls and unlimited execution, using simulated devices only."""

import uuid

from tests.conftest import bearer
from tests.integration import test_generic_tasks as gt
from tests.integration import test_one_click_sharing as sharing
from tests.integration.test_experimental_live import poll
from tests.integration.test_experimental_sharing import ok

octx = sharing.octx
oc = sharing.oc


async def test_disconnect_cancels_work_and_reconnect_keeps_membership(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        await gt.set_kind(oc, "GENERAL", "OPEN", report="reports/test-acceptance.json")
        uid = (await ok(await bob.get("/v1/friends/me")))["user_id"]
        body = gt.generic(status, requirement="Write a fictional notice", execution_mode="LIVE")
        running = await gt.submit(bob, body)
        lease = (await poll(oc, node)).json()
        accepted = await oc.post(
            f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
        )
        assert accepted.status_code == 204
        queued = await gt.submit(bob, {**body, "requirement": "Write another fictional notice"})
        paused = await ok(
            await sharing.owner(oc, node, "PUT", f"/members/{uid}/access", json={"paused": True})
        )
        member = next(m for m in paused["members"] if m["user_id"] == uid)
        assert member["status"] == "ACTIVE" and member["access_paused"]
        assert paused["invite_code"] is None
        assert (
            paused["group"]["invite_code_rotated_at"] == status["group"]["invite_code_rotated_at"]
        )
        for task, expected in ((running, "CANCELLING"), (queued, "CANCELLED")):
            view = await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))
            assert view["status"] == expected
        renewed = await ok(
            await oc.post(
                f"/v1/leases/{lease['lease_id']}/renew", headers=bearer(node["access_token"])
            )
        )
        assert renewed["cancel_requested"]
        await ok(
            await oc.post(
                f"/v1/leases/{lease['lease_id']}/complete",
                json={
                    "finalization_key": "member-disconnected-final",
                    "succeeded": False,
                    "error_code": "CANCELLED",
                },
                headers=bearer(node["access_token"]),
            )
        )
        view = await ok(await bob.get(f"/v1/experimental-tasks/{running['id']}"))
        assert view["status"] == "CANCELLED"
        group = await ok(await bob.get(f"/v1/experiments/groups/{status['group']['id']}"))
        assert next(m for m in group["members"] if m["user_id"] == uid)["access_paused"]
        denied = await gt.submit(
            bob, {**body, "requirement": "Denied while disconnected"}, status=403
        )
        assert denied["error"]["code"] == "MEMBER_ACCESS_PAUSED"
        # Reusing an invite while signed in cannot bypass the owner's pause.
        await ok(
            await bob.post(
                "/v1/friends/join-code",
                json={
                    "code": status["invite_code"],
                    "terms_version": sharing.TERMS,
                    "acknowledgements": sharing.JOIN_ACKS,
                },
                headers=sharing.ORIGIN,
            )
        )
        await gt.submit(bob, {**body, "requirement": "Still denied"}, status=403)
        restored = await ok(
            await sharing.owner(oc, node, "PUT", f"/members/{uid}/access", json={"paused": False})
        )
        assert not next(m for m in restored["members"] if m["user_id"] == uid)["access_paused"]
        await gt.submit(bob, {**body, "requirement": "Allowed after reconnecting"})
        # Kicking is distinct: it removes membership and changes the invite code.
        kicked = await ok(await sharing.owner(oc, node, "DELETE", f"/members/{uid}"))
        assert all(m["user_id"] != uid for m in kicked["members"])
        assert kicked["invite_code"] != status["invite_code"]
        await sharing.join(octx, status["invite_code"], status=404)
        denied = await sharing.owner(
            oc, node, "PUT", f"/members/{uid}/access", json={"paused": False}
        )
        assert denied.status_code == 404
        await ok(
            await bob.post(
                "/v1/friends/join-code",
                json={
                    "code": kicked["invite_code"],
                    "terms_version": sharing.TERMS,
                    "acknowledgements": sharing.JOIN_ACKS,
                },
                headers=sharing.ORIGIN,
            )
        )
        group = await ok(await bob.get(f"/v1/experiments/groups/{status['group']['id']}"))
        assert not next(m for m in group["members"] if m["user_id"] == uid)["access_paused"]
    finally:
        await bob.aclose()


async def test_member_access_is_owner_scoped_and_cannot_disconnect_owner(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    other = await sharing.signup_device(oc)
    try:
        uid = (await ok(await bob.get("/v1/friends/me")))["user_id"]
        for actor, target, code in [(other, uid, 404), (node, node["user_id"], 422)]:
            response = await sharing.owner(
                oc, actor, "PUT", f"/members/{target}/access", json={"paused": True}
            )
            assert response.status_code == code
        response = await bob.put(
            f"/v1/workers/me/sharing/members/{uid}/access",
            json={"paused": False},
            headers=sharing.ORIGIN,
        )
        assert response.status_code == 401
    finally:
        await bob.aclose()


async def test_unlimited_limits_survive_partial_settings_and_online_cycle(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        await gt.set_kind(oc, "GENERAL", "OPEN", report="reports/test-acceptance.json")
        body = gt.generic(
            status,
            requirement="An unlimited fictional task",
            max_duration_seconds=None,
            execution_mode="LIVE",
        )
        denied = await gt.submit(bob, body, status=422)
        assert denied["error"]["code"] == "DURATION_LIMIT"
        updated = await ok(
            await sharing.owner(
                oc,
                node,
                "PUT",
                "/settings",
                json={
                    "max_tasks_per_day": None,
                    "max_task_duration_seconds": None,
                },
            )
        )
        assert updated["group"]["max_tasks_per_day"] is None
        assert updated["group"]["max_task_duration_seconds"] is None
        # Saving unrelated settings must neither reset nor invent a limit.
        await ok(
            await sharing.owner(oc, node, "PUT", "/settings", json={"auto_approve_members": True})
        )
        await ok(await sharing.owner(oc, node, "POST", "/offline"))
        again = await sharing.go_online(oc, node)
        assert again["group"]["max_tasks_per_day"] is None
        await gt.submit(bob, body, key="unlimited-" + uuid.uuid4().hex)
        assignment = await poll(oc, node)
        assert assignment.status_code == 200, assignment.text
        assert assignment.json()["task"]["max_duration_seconds"] is None
    finally:
        await bob.aclose()
