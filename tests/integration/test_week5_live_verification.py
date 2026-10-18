"""LIVE protocol with a simulated device; real Git patching and Docker verification.
No Codex executable or subscription-consuming call appears in these tests.
"""

import pytest

from services.execution.catalog import LIVE_WRITE_TESTS_CATALOG
from services.verification.service import from_settings
from tests.conftest import PY_SHA, PY_URL, bearer, execute, fetch_val, headers, verification_service
from tests.integration import test_experimental_live as live
from tests.integration.test_experimental_live import (  # noqa: F401
    ALICE,
    BOB,
    approve,
    get_task,
    live_world,
    run_on_device,
    submit_live,
)
from tests.integration.test_experimental_sharing import CAROL

lclient = live.lclient
lctx = live.lctx

pytestmark = pytest.mark.db
FIX = (
    b"diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n"
    b"@@ -1,6 +1,6 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
    b" \n \n def mul(a, b):\n     return a * b\n"
)
TEST_PATCH = (
    b"diff --git a/test_added.py b/test_added.py\nnew file mode 100644\n"
    b"--- /dev/null\n+++ b/test_added.py\n@@ -0,0 +1,5 @@\n"
    b"+import unittest\n+from calc import add\n+class Added(unittest.TestCase):\n"
    b"+    def test_zero(self):\n+        self.assertEqual(add(0, 0), 0)\n"
)


async def submitted(c, *, kind="CODE_FIX", patch=FIX, url=PY_URL, sha=PY_SHA):
    gid, conn, node, _ = await live_world(c)
    task = await submit_live(c, gid, conn, task_type=kind, repository_url=url, commit_sha=sha)
    await approve(c, task["id"])
    lease, done = await run_on_device(
        c, node, artifacts=(("SUMMARY", b"Device claims success"), ("PATCH", patch))
    )
    assert done["task_status"] == "VERIFYING"
    return task, lease, node


@pytest.mark.parametrize("incorrect", [False, True])
async def test_code_fix_independent_tests_reject_forged_success(
    lclient, lctx, fixture_root, sandbox, incorrect
):
    patch = FIX.replace(b"+    return a + b", b"+    return a * b") if incorrect else FIX
    task, lease, _ = await submitted(lclient, patch=patch)
    assert (await lclient.get(f"/v1/tasks/{task['id']}/artifacts", headers=headers(BOB))).json()[
        "items"
    ] == []
    assert await verification_service(lctx, fixture_root, sandbox).run_once() == (
        "FAILED" if incorrect else "SUCCEEDED"
    )
    view = await get_task(lclient, task["id"])
    assert view["verification_status"] == ("FAILED" if incorrect else "PASSED")
    assert await fetch_val(lctx, "SELECT reason FROM verification_results") == (
        "TESTS_FAILED" if incorrect else "VERIFIED"
    )
    listing = (await lclient.get(f"/v1/tasks/{task['id']}/artifacts", headers=headers(BOB))).json()[
        "items"
    ]
    report = next(x for x in listing if x["kind"] == "VERIFICATION_REPORT")
    assert report["source"] == "PLATFORM"
    provenance = (
        await lclient.get(
            f"/v1/tasks/{task['id']}/artifacts/{report['id']}/content", headers=headers(BOB)
        )
    ).json()
    assert provenance["execution_profile"] == "CODEX_LIVE"
    assert provenance["patch_sha256"] and provenance["base_commit"] == PY_SHA
    assert any(c["check"] == "patch_applies_cleanly" and c["passed"] for c in provenance["checks"])
    assert ("PATCH" in {x["kind"] for x in listing}) is not incorrect


async def test_write_tests_uses_supported_fixture(lclient, lctx, fixture_root, sandbox):
    recipe = LIVE_WRITE_TESTS_CATALOG[0]
    task, _, _ = await submitted(
        lclient,
        kind="WRITE_TESTS",
        patch=TEST_PATCH,
        url=f"https://github.com/{recipe.repository}",
        sha=recipe.commit_sha,
    )
    assert await verification_service(lctx, fixture_root, sandbox).run_once() == "SUCCEEDED"
    assert (await get_task(lclient, task["id"]))["verification_status"] == "PASSED"
    assert (
        await fetch_val(lctx, "SELECT verified_result->>'passed' FROM verification_results") == "4"
    )


async def test_bad_patch_application_fails(lclient, lctx, fixture_root):
    task, _, _ = await submitted(
        lclient, patch=FIX.replace(b"-    return a - b", b"-    return nonexistent")
    )
    assert await verification_service(lctx, fixture_root, None).run_once() == "FAILED"
    assert (
        await fetch_val(lctx, "SELECT reason FROM verification_results") == "PATCH_DOES_NOT_APPLY"
    )
    assert (await get_task(lclient, task["id"]))["verification_status"] == "FAILED"


async def test_stored_patch_tampering_fails_closed(lclient, lctx, fixture_root):
    task, _, _ = await submitted(lclient)
    key = await fetch_val(lctx, "SELECT storage_key FROM artifacts WHERE kind='PATCH'")
    lctx.artifacts._path(key).write_bytes(b"tampered")
    assert await verification_service(lctx, fixture_root, None).run_once() == "FAILED"
    assert await fetch_val(lctx, "SELECT reason FROM verification_results") == "ARTIFACT_INTEGRITY"


async def test_unsupported_fixture_is_explicitly_unverified(lclient, lctx, fixture_root):
    task, _, _ = await submitted(lclient)
    # Same safe checkout, but no operator-trusted recipe: never run repo-supplied commands.
    verifier = verification_service(lctx, fixture_root, None, catalog=())
    assert await verifier.run_once() == "SUCCEEDED"
    view = await get_task(lclient, task["id"])
    assert view["verification_status"] == "UNVERIFIED"
    assert view["result_summary"].startswith("UNVERIFIED:")
    assert (
        await fetch_val(lctx, "SELECT reason FROM verification_results") == "NO_TRUSTED_TEST_RECIPE"
    )


async def test_reclaimed_verifier_and_duplicate_completion(lclient, lctx):
    gid, conn, node, _ = await live_world(lclient)
    task = await submit_live(lclient, gid, conn)
    await approve(lclient, task["id"])
    lease, _ = await run_on_device(
        lclient,
        node,
        observed_usage={
            "input_tokens": 120,
            "cached_input_tokens": 20,
            "output_tokens": 40,
            "reasoning_output_tokens": 8,
        },
    )
    first = from_settings(lctx)
    job = await first.claim()
    await execute(lctx, "UPDATE verification_results SET claimed_until=now()-interval '1 second'")
    second = from_settings(lctx)
    reclaimed = await second.claim()
    outcome = await first.verify(job)
    assert await first.finalize(job, outcome) == "STALE"
    assert await second.finalize(reclaimed, await second.verify(reclaimed)) == "SUCCEEDED"
    assert await second.finalize(reclaimed, outcome) == "STALE"
    replay = await lclient.post(
        f"/v1/leases/{lease['lease_id']}/complete",
        json={"finalization_key": f"fin-{lease['attempt_id']}", "succeeded": True},
        headers=bearer(node["access_token"]),
    )
    assert replay.json() == {"task_status": "SUCCEEDED", "replayed": True}
    assert await fetch_val(lctx, "SELECT count(*) FROM subscription_usage_observations") == 1
    usage = (await lclient.get("/v1/subscriptions/usage", headers=headers(ALICE))).json()["items"][
        0
    ]
    assert usage["observed_input_tokens"] == 120 and usage["observed_output_tokens"] == 40
    assert (
        usage["observed_cached_input_tokens"] == 20
        and usage["observed_reasoning_output_tokens"] == 8
    )
    assert usage["plan_remaining"] == "NOT_AVAILABLE"


async def test_owner_can_inspect_and_resume_events_strangers_cannot(lclient, lctx):
    gid, conn, node, _ = await live_world(lclient)
    task = await submit_live(lclient, gid, conn, prompt="Review this exact text")
    owned = (
        await lclient.get(f"/v1/experimental-tasks/{task['id']}", headers=headers(ALICE))
    ).json()
    assert owned["prompt"] == "Review this exact text" and owned["repository_url"]
    await approve(lclient, task["id"])
    await run_on_device(lclient, node)
    assert await from_settings(lctx).run_once() == "SUCCEEDED"
    url = f"/v1/tasks/{task['id']}/events"
    events = await lclient.get(url, headers=headers(ALICE))
    assert events.status_code == 200 and "task.succeeded" in events.text
    import re

    ids = [int(x) for x in re.findall(r"^id: (\d+)", events.text, re.M)]
    replay = await lclient.get(url, headers={**headers(ALICE), "Last-Event-ID": str(ids[-2])})
    assert f"id: {ids[-1]}\n" in replay.text and f"id: {ids[-2]}\n" not in replay.text
    assert (await lclient.get(url, headers=headers(CAROL))).status_code == 404
    assert (
        await lclient.get(f"/v1/tasks/{task['id']}/artifacts", headers=headers(CAROL))
    ).status_code == 404


async def test_friends_audit_only_exposes_participant_tasks(lclient, lctx):
    from tests.integration.test_experimental_sharing import invite_join

    gid, conn, node, _ = await live_world(lclient)
    await invite_join(lclient, gid, CAROL)
    task = await submit_live(lclient, gid, conn)
    await approve(lclient, task["id"])
    await run_on_device(lclient, node)
    object.__setattr__(lctx.settings, "friends_auth_enabled", True)
    for user in (ALICE, BOB):
        rows = (await lclient.get(f"/v1/friends/groups/{gid}/audit", headers=headers(user))).json()
        assert [row["task_id"] for row in rows] == [task["id"]]
    assert (
        await lclient.get(f"/v1/friends/groups/{gid}/audit", headers=headers(CAROL))
    ).json() == []
