"""Step C (docs/GENERAL_TASKS_DESIGN.md §5): device capabilities, owner settings for
concurrency, kinds and tools, and capability-based LIVE dispatch. No real execution."""

from sqlalchemy import text

from tests.conftest import bearer
from tests.integration import test_generic_tasks as gt
from tests.integration import test_one_click_sharing as one_click
from tests.integration.test_experimental_live import poll
from tests.integration.test_experimental_sharing import ok

octx = one_click.octx
oc = one_click.oc


async def heartbeat(oc, node, tools: dict | None) -> None:
    caps = {"executor": "codex-subscription"} | ({"tools": tools} if tools is not None else {})
    resp = await oc.post(
        f"/v1/workers/{node['worker_id']}/heartbeat",
        json={"state": "ONLINE", "capabilities": caps},
        headers=bearer(node["access_token"]),
    )
    assert resp.status_code == 204, resp.text


async def settings(oc, node, status=200, **body) -> dict:
    resp = await one_click.owner(oc, node, "PUT", "/settings", json=body)
    assert resp.status_code == status, resp.text
    return resp.json()


async def last_reasons(octx) -> list[str]:
    async with octx.engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT reasons FROM experimental_dispatch_audit ORDER BY id DESC LIMIT 1")
            )
        ).scalar_one()


async def submit_analysis(oc, octx, bob, status, name="sales.csv"):
    await gt.set_kind(oc, "ANALYZE_SPREADSHEET", "OPEN", report="reports/test-acceptance.json")
    up = await gt.upload(bob, name, b"region,value\nN,1\nS,2\n")
    return await gt.submit(
        bob,
        gt.generic(
            status,
            kind="ANALYZE_SPREADSHEET",
            requirement=f"Average value in {name}",
            attachments=[up["id"]],
            execution_mode="LIVE",
        ),
    )


async def test_owner_settings_for_concurrency_kinds_and_tools(oc, octx):
    node = await one_click.signup_device(oc)
    await one_click.go_online(oc, node)
    out = await settings(
        oc,
        node,
        max_concurrent_tasks=2,
        allowed_task_types=["TRANSLATE_DOCUMENT", "CODE_REVIEW"],
        allowed_tools=["doc", "git", "code"],
    )
    sharing = out["sharing"]
    assert sharing["max_concurrent_tasks"] == 2
    assert sharing["allowed_task_types"] == ["CODE_REVIEW", "TRANSLATE_DOCUMENT"]
    assert sharing["allowed_tools"] == ["code", "doc", "git"]
    kinds = {k["kind"]: k for k in out["kinds"]}
    assert kinds["ANALYZE_SPREADSHEET"]["required_tools"] == ["python-data"]
    assert kinds["TRANSLATE_DOCUMENT"]["name_zh"] == "翻译文档" and kinds["CODE_CHANGE"]["offered"]
    async with octx.engine.connect() as conn:
        assert (
            await conn.execute(
                text("SELECT max_concurrency FROM worker_nodes WHERE id = :w"),
                {"w": node["worker_id"]},
            )
        ).scalar_one() == 2

    await settings(oc, node, 422, max_concurrent_tasks=4)
    await settings(oc, node, 422, allowed_tools=["shell"])
    await settings(oc, node, 422, allowed_task_types=[])
    await settings(oc, node, 422, allowed_task_types=["REFACTOR"])

    # New requests use GENERAL; legacy kinds, tool permissions and limits are kept.
    await ok(await one_click.owner(oc, node, "POST", "/offline"))
    again = await one_click.go_online(oc, node)
    assert again["sharing"]["max_concurrent_tasks"] == 2
    assert again["sharing"]["allowed_task_types"] == [
        "CODE_REVIEW",
        "GENERAL",
        "TRANSLATE_DOCUMENT",
    ]
    assert again["sharing"]["allowed_tools"] == ["code", "doc", "git"]


async def test_live_task_waits_for_a_device_with_the_needed_tool(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        await heartbeat(oc, node, {"code": True, "doc": True, "git": True, "python-data": False})
        await submit_analysis(oc, octx, bob, status)
        assert (await poll(oc, node)).status_code == 204
        assert await last_reasons(octx) == ["NODE_LACKS_CAPABILITY"]
        # The owner installs the data tools: the next heartbeat declares them.
        await heartbeat(oc, node, {"code": True, "doc": True, "git": True, "python-data": True})
        lease = await poll(oc, node)
        assert lease.status_code == 200, lease.text
        assert lease.json()["task"]["kind"] == "ANALYZE_SPREADSHEET"
    finally:
        await bob.aclose()


async def test_owner_can_withhold_a_tool(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        await heartbeat(oc, node, {"code": True, "doc": True, "git": True, "python-data": True})
        await settings(oc, node, allowed_tools=["code", "doc", "git"])
        task = await submit_analysis(oc, octx, bob, status)
        assert (await poll(oc, node)).status_code == 204
        assert await last_reasons(octx) == ["TOOL_NOT_ALLOWED"]
        view = await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))
        assert view["status"] == "QUEUED"  # waits; the owner may allow the tool again
        await settings(oc, node, allowed_tools=["code", "doc", "git", "python-data"])
        assert (await poll(oc, node)).status_code == 200
    finally:
        await bob.aclose()


async def test_legacy_node_without_declared_tools_still_runs_code_review(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        await heartbeat(oc, node, None)
        await one_click.submit_as(bob, status, status["sharing"]["connection_id"])
        assert (await poll(oc, node)).status_code == 200
    finally:
        await bob.aclose()


async def test_concurrency_setting_bounds_parallel_leases(oc, octx):
    node, status, bob = await gt.group_with_member(oc, octx)
    try:
        await settings(oc, node, max_concurrent_tasks=2, max_tasks_per_day=10)
        await heartbeat(oc, node, {"code": True, "doc": True, "git": True, "python-data": True})
        for _ in range(3):
            await one_click.submit_as(bob, status, status["sharing"]["connection_id"])
        auth = bearer(node["access_token"])
        for _ in range(2):
            lease = await poll(oc, node)
            assert lease.status_code == 200, lease.text
            lid = lease.json()["lease_id"]
            assert (await oc.post(f"/v1/leases/{lid}/accept", headers=auth)).status_code == 204
        assert (await poll(oc, node)).status_code == 204
        assert "CONCURRENCY_LIMIT" in await last_reasons(octx)
    finally:
        await bob.aclose()
