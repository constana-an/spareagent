"""CodeExecutionAdapter + independent VerificationService through the real node API."""

import base64
import json
import uuid

import pytest

from packages.provider_sdk.base import PermanentProviderError
from packages.provider_sdk.types import ExecutionRequest, ExecutionResult
from services.execution.catalog import CATALOG, Edit, Recipe
from services.scheduler.lease_service import LeaseService
from services.verification.service import Outcome
from tests.conftest import (
    PROBE_SHA,
    PROBE_URL,
    PROVIDER,
    PUBLIC_RESOURCE,
    PY_SHA,
    PY_URL,
    TS_SHA,
    TS_URL,
    USER_A,
    bearer,
    code_adapter,
    create_task,
    execute,
    fetch_val,
    headers,
    register_code_node,
    register_node,
    verification_service,
)

pytestmark = pytest.mark.db


SYMLINK_PATCH = (
    b"diff --git a/link b/link\nnew file mode 120000\n--- /dev/null\n+++ b/link\n"
    b"@@ -0,0 +1 @@\n+/etc/passwd\n"
)


def request_for(url, sha, attempt_id="a-1", attempt=1, duration=120):
    return ExecutionRequest(
        task_id=str(uuid.uuid4()),
        attempt_id=attempt_id,
        user_id="redacted",
        resource_scope="PUBLIC_MARKETPLACE",
        prompt="fix",
        repository_url=url,
        commit_sha=sha,
        max_duration_seconds=duration,
        max_budget_cents=500,
        attempt_number=attempt,
        task_type="CODE_FIX",
    )


async def run_adapter(adapter, request):
    items = [i async for i in adapter.execute(request)]
    result = items[-1]
    assert isinstance(result, ExecutionResult)
    return {a.kind: a for a in result.artifacts}, result


def to_payload(arts, **overrides):
    body = {
        "succeeded": True,
        "result_summary": arts["SUMMARY"].content.decode(),
        "artifacts": [
            {
                "kind": a.kind,
                "name": a.name,
                "media_type": a.media_type,
                "content_base64": base64.b64encode(a.content).decode(),
            }
            for a in arts.values()
        ],
    }
    body.update(overrides)
    return body


def replace_artifact(body, kind, content: bytes):
    for item in body["artifacts"]:
        if item["kind"] == kind:
            item["content_base64"] = base64.b64encode(content).decode()
    return body


async def claim_code_task(client, reg, url=PY_URL, sha=PY_SHA):
    task = (
        await create_task(client, task_type="CODE_FIX", repository_url=url, commit_sha=sha)
    ).json()
    resp = await client.get(
        f"/v1/workers/{reg['worker_id']}/leases/next",
        params={"wait_seconds": 0},
        headers=bearer(reg["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()
    assert a["execution_profile"] == "CODE"
    r = await client.post(f"/v1/leases/{a['lease_id']}/accept", headers=bearer(reg["access_token"]))
    assert r.status_code == 204
    return task["id"], a


async def submit(client, reg, a, body):
    return await client.post(
        f"/v1/leases/{a['lease_id']}/complete",
        json={"finalization_key": f"fin-{a['attempt_id']}", **body},
        headers=bearer(reg["access_token"]),
    )


async def task_status(client, task_id):
    return (await client.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))).json()["status"]


# ------------------------------------------------------------------ executor
@pytest.mark.parametrize(
    ("url", "sha", "framework", "path"),
    [(PY_URL, PY_SHA, "unittest", "calc.py"), (TS_URL, TS_SHA, "node-tap", "src/strings.ts")],
)
async def test_code_adapter_fixes_fixture(fixture_root, sandbox, url, sha, framework, path):
    adapter = code_adapter(fixture_root, sandbox)
    arts, result = await run_adapter(adapter, request_for(url, sha))
    report = json.loads(arts["TEST_REPORT"].content)
    assert report["ok"] and report["failed"] == 0 and report["passed"] == 3
    assert report["framework"] == framework and report["base_commit"] == sha
    patch = arts["PATCH"].content.decode()
    assert patch.startswith(f"diff --git a/{path} b/{path}")
    assert "removed=True" in arts["EXECUTION_LOG"].content.decode()
    assert result.usage.estimated_cost_cents == 25
    assert sandbox.list_containers() == []


async def test_code_adapter_refuses_unlisted_repository(fixture_root, sandbox):
    adapter = code_adapter(fixture_root, sandbox)
    with pytest.raises(PermanentProviderError, match="UNSUPPORTED_REPOSITORY"):
        await run_adapter(adapter, request_for("https://github.com/octocat/Hello-World", "1" * 40))


async def test_code_adapter_timeout_kills_sandbox(fixture_root, sandbox):
    recipe = Recipe(
        "spareagent-fixtures/python-timeout",
        "ba3cac0bae8fab0b8bdc1b41f4b9076b94123781",
        "python",
        "unittest",
        (Edit("slow.py", "    return 41\n", "    return 40\n"),),
        ("python3", "-m", "unittest"),
        ("python3", "-m", "unittest"),
        frozenset({"slow.py"}),
        1,
        "never finishes",
    )
    from services.execution.sandbox import DockerSandbox, SandboxLimits

    short = DockerSandbox(sandbox.image, SandboxLimits(timeout_seconds=3))
    adapter = code_adapter(fixture_root, short, catalog=(recipe,))
    arts, _ = await run_adapter(
        adapter,
        request_for("https://github.com/spareagent-fixtures/python-timeout", recipe.commit_sha),
    )
    report = json.loads(arts["TEST_REPORT"].content)
    assert report["timed_out"] is True and report["ok"] is False
    assert short.list_containers() == []


async def test_isolation_probe_fixture_passes_only_when_isolated(fixture_root, sandbox):
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PROBE_URL, PROBE_SHA)
    )
    report = json.loads(arts["TEST_REPORT"].content)
    assert report["ok"] and report["passed"] == 6, arts["EXECUTION_LOG"].content.decode()[-2000:]


# ------------------------------------------------------------------ dispatch rules
async def test_code_tasks_only_reach_sandbox_capable_allowlisted_nodes(code_client, code_ctx):
    plain = await register_node(code_client, name="plain-node")
    task = (
        await create_task(
            code_client, task_type="CODE_FIX", repository_url=PY_URL, commit_sha=PY_SHA
        )
    ).json()
    poll = await code_client.get(
        f"/v1/workers/{plain['worker_id']}/leases/next",
        params={"wait_seconds": 0},
        headers=bearer(plain["access_token"]),
    )
    assert poll.status_code == 204  # mock-only node never gets a CODE task
    profile = await fetch_val(
        code_ctx, "SELECT execution_profile FROM tasks WHERE id=:t", t=uuid.UUID(task["id"])
    )
    assert profile == "CODE"
    # non-allowlisted repo/sha stays MOCK even with code execution enabled
    other = (await create_task(code_client, task_type="CODE_FIX")).json()
    assert (
        await fetch_val(
            code_ctx, "SELECT execution_profile FROM tasks WHERE id=:t", t=uuid.UUID(other["id"])
        )
        == "MOCK"
    )


async def test_personal_subscription_resource_never_runs_code(code_client, code_ctx):
    personal = uuid.uuid4()
    await execute(
        code_ctx,
        "INSERT INTO resources(id,owner_id,resource_type,authorization_status) "
        "VALUES (:r,:p,'PERSONAL_SUBSCRIPTION','APPROVED')",
        r=personal,
        p=PROVIDER,
    )
    wid = uuid.uuid4()
    await execute(
        code_ctx,
        "INSERT INTO worker_nodes(id,resource_id,name,status,last_heartbeat,capabilities) "
        "VALUES (:w,:r,'personal-code','IDLE',now(),'{\"sandbox\":\"docker\"}')",
        w=wid,
        r=personal,
    )
    from services.registry.node_auth import NodeAuthService

    async with code_ctx.sessionmaker() as session, session.begin():
        issued = await NodeAuthService(code_ctx)._issue_pair(session, wid)
    # even listing the personal resource as code-capable in config would not help
    object.__setattr__(
        code_ctx.settings, "code_execution_resource_ids", f"{PUBLIC_RESOURCE},{personal}"
    )
    code_ctx.settings.__dict__.pop("code_resource_ids", None)
    await create_task(code_client, task_type="CODE_FIX", repository_url=PY_URL, commit_sha=PY_SHA)
    poll = await code_client.get(
        f"/v1/workers/{wid}/leases/next",
        params={"wait_seconds": 0},
        headers=bearer(issued.access_token.secret),
    )
    assert poll.status_code == 204
    assert await fetch_val(code_ctx, "SELECT count(*) FROM task_attempts") == 0


# ------------------------------------------------------------------ verification
async def test_honest_submission_verified_and_published(
    code_client, code_ctx, fixture_root, sandbox
):
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    resp = await submit(code_client, reg, a, to_payload(arts))
    assert resp.json() == {"task_status": "VERIFYING", "replayed": False}
    # worker artifacts are invisible until the platform verifies them
    listing = (
        await code_client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))
    ).json()
    assert listing["items"] == []
    # worker capacity released at submission
    assert (
        await fetch_val(code_ctx, "SELECT active_tasks FROM worker_nodes WHERE name='code-node'")
        == 0
    )
    assert await verification_service(code_ctx, fixture_root, sandbox).run_once() == "SUCCEEDED"
    assert await task_status(code_client, task_id) == "SUCCEEDED"
    listing = (
        await code_client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))
    ).json()
    kinds = {i["kind"]: i["source"] for i in listing["items"]}
    assert kinds == {
        "PATCH": "WORKER",
        "TEST_REPORT": "WORKER",
        "EXECUTION_LOG": "WORKER",
        "SUMMARY": "WORKER",
        "VERIFICATION_REPORT": "PLATFORM",
    }
    vr = await fetch_val(code_ctx, "SELECT status FROM verification_results")
    assert vr == "PASSED"
    report_id = next(i["id"] for i in listing["items"] if i["kind"] == "VERIFICATION_REPORT")
    report = (
        await code_client.get(
            f"/v1/tasks/{task_id}/artifacts/{report_id}/content", headers=headers(USER_A)
        )
    ).json()
    assert report["status"] == "PASSED" and report["verified"]["passed"] == 3
    assert all(c["passed"] for c in report["checks"])
    assert sandbox.list_containers() == []


async def test_forged_success_fails_verification_then_retry_succeeds(
    code_client, code_ctx, fixture_root, sandbox
):
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    # forge: a patch that does NOT fix the bug, with a test report claiming success
    bogus_patch = arts["PATCH"].content.replace(b"+    return a + b", b"+    return a * b")
    body = replace_artifact(to_payload(arts), "PATCH", bogus_patch)
    assert (await submit(code_client, reg, a, body)).json()["task_status"] == "VERIFYING"
    verifier = verification_service(code_ctx, fixture_root, sandbox)
    assert await verifier.run_once() == "RETRY_PENDING"
    vr = await fetch_val(code_ctx, "SELECT reason FROM verification_results")
    assert vr == "TESTS_FAILED"
    worker_claim = await fetch_val(
        code_ctx, "SELECT worker_reported->>'ok' FROM verification_results"
    )
    assert worker_claim == "true"  # recorded separately from the verified result
    # forged artifacts are never published
    listing = (
        await code_client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))
    ).json()
    assert [i["kind"] for i in listing["items"]] == ["VERIFICATION_REPORT"]

    from services.scheduler.dispatcher import Dispatcher

    await Dispatcher(code_ctx).promote_retries()
    poll = await code_client.get(
        f"/v1/workers/{reg['worker_id']}/leases/next",
        params={"wait_seconds": 0},
        headers=bearer(reg["access_token"]),
    )
    a2 = poll.json()
    assert a2["attempt_number"] == 2
    await code_client.post(
        f"/v1/leases/{a2['lease_id']}/accept", headers=bearer(reg["access_token"])
    )
    arts2, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a2["attempt_id"], 2)
    )
    await submit(code_client, reg, a2, to_payload(arts2))
    assert await verifier.run_once() == "SUCCEEDED"
    assert await task_status(code_client, task_id) == "SUCCEEDED"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda b, arts: replace_artifact(b, "TEST_REPORT", b"{not json"), "INVALID_TEST_REPORT"),
        (
            lambda b, arts: replace_artifact(b, "TEST_REPORT", json.dumps({"ok": True}).encode()),
            "INVALID_TEST_REPORT",
        ),
        (
            lambda b, arts: replace_artifact(
                b,
                "PATCH",
                arts["PATCH"].content.replace(
                    b"a/calc.py b/calc.py", b"a/../../etc/cron.d/x b/../../etc/cron.d/x"
                ),
            ),
            "PATCH_POLICY",
        ),
        (
            lambda b, arts: replace_artifact(
                b,
                "PATCH",
                SYMLINK_PATCH,
            ),
            "PATCH_POLICY",
        ),
        (
            lambda b, arts: replace_artifact(
                b, "PATCH", arts["PATCH"].content.replace(b"calc.py", b"test_calc.py")
            ),
            "PATCH_POLICY",
        ),
        (
            lambda b, arts: replace_artifact(
                b,
                "PATCH",
                arts["PATCH"].content.replace(b"-    return a - b", b"-    return a ^ b"),
            ),
            "PATCH_DOES_NOT_APPLY",
        ),
        (
            lambda b, arts: replace_artifact(
                b,
                "TEST_REPORT",
                json.dumps(
                    {**json.loads(arts["TEST_REPORT"].content), "base_commit": "f" * 40}
                ).encode(),
            ),
            "BASE_COMMIT_MISMATCH",
        ),
        (
            lambda b, arts: replace_artifact(
                b,
                "TEST_REPORT",
                json.dumps(
                    {
                        **json.loads(arts["TEST_REPORT"].content),
                        "ok": False,
                        "passed": 0,
                        "failed": 3,
                    }
                ).encode(),
            ),
            "WORKER_REPORT_MISMATCH",
        ),
    ],
)
async def test_invalid_submissions_fail_verification(
    code_client, code_ctx, fixture_root, sandbox, mutate, reason
):
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    await execute(code_ctx, "UPDATE tasks SET max_attempts = 1")
    assert (await submit(code_client, reg, a, mutate(to_payload(arts), arts))).json()[
        "task_status"
    ] == "VERIFYING"
    assert await verification_service(code_ctx, fixture_root, sandbox).run_once() == "FAILED"
    assert await fetch_val(code_ctx, "SELECT reason FROM verification_results") == reason
    assert await task_status(code_client, task_id) == "FAILED"
    assert (
        await fetch_val(
            code_ctx, "SELECT count(*) FROM artifacts WHERE published AND source='WORKER'"
        )
        == 0
    )
    assert sandbox.list_containers() == []


async def test_missing_artifacts_rejected_at_submission(
    code_client, code_ctx, fixture_root, sandbox
):
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    body = to_payload(arts)
    body["artifacts"] = [x for x in body["artifacts"] if x["kind"] != "EXECUTION_LOG"]
    resp = await submit(code_client, reg, a, body)
    assert resp.json()["task_status"] == "FAILED"
    same = to_payload(arts)
    replace_artifact(same, "EXECUTION_LOG", arts["SUMMARY"].content)
    _, a_dup = await claim_code_task(code_client, reg)
    dup = await submit(code_client, reg, a_dup, same)
    assert dup.json()["task_status"] == "FAILED"  # identical content for two kinds refused
    forged_report = to_payload(arts)
    forged_report["artifacts"].append(
        {
            "kind": "VERIFICATION_REPORT",
            "name": "v",
            "media_type": "application/json",
            "content_base64": "",
        }
    )
    assert (await submit(code_client, reg, a, forged_report)).status_code in (409, 422)


async def test_duplicate_completion_verifies_once(code_client, code_ctx, fixture_root, sandbox):
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    first = await submit(code_client, reg, a, to_payload(arts))
    again = await submit(code_client, reg, a, to_payload(arts))
    assert first.json()["replayed"] is False and again.json()["replayed"] is True
    verifier = verification_service(code_ctx, fixture_root, sandbox)
    assert await verifier.run_once() == "SUCCEEDED"
    assert await verifier.run_once() is None
    assert await fetch_val(code_ctx, "SELECT count(*) FROM verification_results") == 1
    assert await fetch_val(code_ctx, "SELECT count(*) FROM usage_records") == 1
    assert await fetch_val(code_ctx, "SELECT count(*) FROM artifacts") == 5
    assert (
        await fetch_val(
            code_ctx, "SELECT count(*) FROM execution_events WHERE event_type='task.succeeded'"
        )
        == 1
    )


async def test_artifacts_after_lease_expiry_rejected(code_client, code_ctx, fixture_root, sandbox):
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    await execute(code_ctx, "UPDATE task_attempts SET leased_until = now() - interval '1 second'")
    resp = await submit(code_client, reg, a, to_payload(arts))
    assert resp.status_code == 409
    assert await fetch_val(code_ctx, "SELECT count(*) FROM artifacts") == 0
    await LeaseService(code_ctx).recover_expired()
    assert await task_status(code_client, task_id) == "RETRY_PENDING"


async def test_verifier_infrastructure_errors_retry_then_fail(
    code_client, code_ctx, fixture_root, sandbox
):
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    await submit(code_client, reg, a, to_payload(arts))
    verifier = verification_service(code_ctx, fixture_root, sandbox)

    async def broken(job):
        return Outcome("ERROR", "SANDBOX_DOCKER_UNAVAILABLE")

    verifier.verify = broken
    results = [await verifier.run_once() for _ in range(code_ctx.settings.verification_max_errors)]
    assert results[:-1] == ["ERROR_RETRY"] * (len(results) - 1)
    assert results[-1] == "RETRY_PENDING"  # attempts remain -> rescheduled, never passed
    assert (await fetch_val(code_ctx, "SELECT reason FROM verification_results")).startswith(
        "VERIFIER_UNAVAILABLE"
    )


async def test_cancellation_during_verification(code_client, code_ctx, fixture_root, sandbox):
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    await submit(code_client, reg, a, to_payload(arts))
    resp = await code_client.post(f"/v1/tasks/{task_id}/cancel", headers=headers(USER_A))
    assert resp.json()["status"] == "CANCELLING"
    assert await verification_service(code_ctx, fixture_root, sandbox).run_once() == "CANCELLED"
    assert await task_status(code_client, task_id) == "CANCELLED"
    assert (
        await fetch_val(
            code_ctx, "SELECT count(*) FROM artifacts WHERE published AND source='WORKER'"
        )
        == 0
    )


async def test_profile_cannot_be_forged_by_catalog_drift(
    code_ctx, fixture_root, sandbox, code_client
):
    """A verifier whose catalog lacks the repo refuses to pass it."""
    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    await submit(code_client, reg, a, to_payload(arts))
    await execute(code_ctx, "UPDATE tasks SET max_attempts = 1")
    verifier = verification_service(code_ctx, fixture_root, sandbox, catalog=CATALOG[1:])
    assert await verifier.run_once() == "FAILED"
    assert (
        await fetch_val(code_ctx, "SELECT reason FROM verification_results")
        == "UNSUPPORTED_REPOSITORY"
    )


async def test_database_refuses_unverified_code_success(
    code_client, code_ctx, fixture_root, sandbox
):
    """Version-skew / compromised-process guard: even a direct DB write cannot mark a CODE
    task SUCCEEDED or publish its worker artifacts without a PASSED verification row."""
    from sqlalchemy.exc import DBAPIError

    reg = await register_code_node(code_client)
    task_id, a = await claim_code_task(code_client, reg)
    arts, _ = await run_adapter(
        code_adapter(fixture_root, sandbox), request_for(PY_URL, PY_SHA, a["attempt_id"])
    )
    await submit(code_client, reg, a, to_payload(arts))
    with pytest.raises(DBAPIError, match="without platform verification"):
        await execute(
            code_ctx, "UPDATE tasks SET status='SUCCEEDED' WHERE id=:t", t=uuid.UUID(task_id)
        )
    with pytest.raises(DBAPIError, match="published unverified"):
        await execute(code_ctx, "UPDATE artifacts SET published=true WHERE source='WORKER'")
    # MOCK tasks are unaffected by the trigger
    mock_id = (await create_task(code_client)).json()["id"]
    assert (
        await fetch_val(
            code_ctx, "SELECT execution_profile FROM tasks WHERE id=:t", t=uuid.UUID(mock_id)
        )
        == "MOCK"
    )
