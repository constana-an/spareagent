"""Failure scenarios on the real LIVE protocol with a simulated owner device."""

import pytest

from services.scheduler.lease_service import LeaseService
from tests.conftest import bearer, execute, fetch_val, headers
from tests.integration import test_experimental_live as live
from tests.integration.test_experimental_live import (
    ALICE,
    BOB,
    approve,
    get_task,
    live_world,
    poll,
    submit_live,
)
from tests.integration.test_experimental_sharing import SHARE_ACKS, TERMS

lctx = live.lctx
lclient = live.lclient
pytestmark = pytest.mark.db


async def reserved(c):
    gid, conn, node, auth = await live_world(
        c, require_owner_approval=False, advance_consent_requester_ids=[str(BOB)]
    )
    task = await submit_live(c, gid, conn)
    # LIVE never inherits advance consent, even when configured for simulations.
    assert task["approval_status"] == "PENDING"
    assert (await poll(c, node)).status_code == 204
    await approve(c, task["id"])
    lease = (await poll(c, node)).json()
    return task, lease, conn, node, auth


async def test_pause_after_reservation_blocks_accept_until_explicit_resume(lclient):
    task, lease, conn, node, _ = await reserved(lclient)
    await lclient.post("/v1/sharing/pause", json={"connection_id": conn}, headers=headers(ALICE))
    accept = f"/v1/leases/{lease['lease_id']}/accept"
    r = await lclient.post(accept, headers=bearer(node["access_token"]))
    assert r.status_code == 409 and "OWNER_PAUSED" in r.text
    await lclient.post(
        "/v1/sharing/enable",
        json={"connection_id": conn, "terms_version": TERMS, "acknowledgements": SHARE_ACKS},
        headers=headers(ALICE),
    )
    assert (await lclient.post(accept, headers=bearer(node["access_token"]))).status_code == 204


async def test_expired_authorization_between_reservation_and_accept(lclient, lctx):
    _, lease, _, node, _ = await reserved(lclient)
    await execute(
        lctx,
        "UPDATE experimental_authorizations SET created_at=now()-interval '2 days',"
        "expires_at=now()-interval '1 day'",
    )
    r = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
    )
    assert r.status_code == 409 and "AUTHORIZATION_EXPIRED" in r.text


async def test_owner_revoke_running_task_disallows_success_and_artifacts(lclient, lctx):
    task, lease, conn, node, _ = await reserved(lclient)
    await lclient.post(
        f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
    )
    await lclient.post("/v1/sharing/revoke", json={"connection_id": conn}, headers=headers(ALICE))
    renew = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/renew", headers=bearer(node["access_token"])
    )
    assert renew.json()["cancel_requested"] is True
    r = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/complete",
        json={"finalization_key": f"fin-{lease['attempt_id']}", "succeeded": True},
        headers=bearer(node["access_token"]),
    )
    assert r.json()["task_status"] == "CANCELLED"
    assert await fetch_val(lctx, "SELECT count(*) FROM artifacts") == 0
    assert (await get_task(lclient, task["id"]))["verification_status"] == "NOT_RUN"


async def test_disconnect_lease_expiry_survives_server_restart(lclient, lctx):
    task, lease, _, node, _ = await reserved(lclient)
    await lclient.post(
        f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
    )
    await execute(lctx, "UPDATE task_attempts SET leased_until=now()-interval '1 second'")
    # A new service instance recovers persisted leases; no in-memory execution ownership.
    assert await LeaseService(lctx).recover_expired() == 1
    assert (await get_task(lclient, task["id"]))["status"] == "FAILED"
    r = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/complete",
        json={"finalization_key": f"fin-{lease['attempt_id']}", "succeeded": True},
        headers=bearer(node["access_token"]),
    )
    assert r.status_code == 409
    assert await fetch_val(lctx, "SELECT count(*) FROM artifacts") == 0


async def test_database_blocks_unverified_live_success(lclient, lctx):
    from sqlalchemy.exc import DBAPIError

    task, lease, _, node, _ = await reserved(lclient)
    await lclient.post(
        f"/v1/leases/{lease['lease_id']}/accept", headers=bearer(node["access_token"])
    )
    with pytest.raises(DBAPIError, match="without platform verification"):
        await execute(
            lctx,
            "UPDATE tasks SET status='SUCCEEDED' WHERE id=:t",
            t=__import__("uuid").UUID(task["id"]),
        )
