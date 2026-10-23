"""Conversation persistence, old-Worker delivery and member isolation."""

import hashlib
import json

from tests.conftest import bearer
from tests.integration import test_one_click_sharing as one_click
from tests.integration.test_experimental_live import poll
from tests.integration.test_experimental_sharing import ok
from tests.integration.test_generic_tasks import (
    complete_with,
    generic,
    group_with_member,
    set_kind,
    submit,
    upload,
)

octx = one_click.octx
oc = one_click.oc


async def test_conversation_persists_and_reuses_authorized_materials(oc, octx):
    from services.verification.service import VerificationService

    node, status, bob = await group_with_member(oc, octx)
    await set_kind(oc, "GENERAL", "OPEN", report="reports/test-acceptance.json")
    outsider = None
    try:
        source = await upload(bob, "material.txt", b"source material")
        root = await submit(
            bob,
            generic(
                status,
                requirement="请根据材料写一份说明",
                attachments=[source["id"]],
                execution_mode="LIVE",
            ),
        )
        assert root["kind"] == "GENERAL" and root["conversation_id"] == root["id"]
        assert root["requirement"]
        busy = await submit(
            bob, generic(status, parent_task_id=root["id"], requirement="再简短一点"), status=409
        )
        assert busy["error"]["code"] == "CONVERSATION_BUSY"
        answer = "# 回答\n\n这是对上传材料的说明，下一步可以继续追问并修改这份说明。\n".encode()
        await complete_with(
            oc,
            node,
            [
                ("answer.md", "text/markdown", answer),
                ("notes.txt", "text/plain", b"original generated notes"),
            ],
        )
        assert await VerificationService(octx, None, None).run_once() == "SUCCEEDED"
        child = await submit(
            bob,
            generic(
                status,
                requirement="把上次的说明改成三句话",
                parent_task_id=root["id"],
                execution_mode="LIVE",
            ),
        )
        assert child["parent_task_id"] == root["id"]
        assert child["conversation_id"] == root["id"]
        thread = await ok(await bob.get(f"/v1/experimental-tasks/{child['id']}/conversation"))
        assert [t["id"] for t in thread["items"]] == [root["id"], child["id"]]
        lease = (await poll(oc, node)).json()
        assert "conversation-" in lease["task"]["prompt"]
        assert len(lease["task"]["prompt"]) < 12000
        inputs = {item["filename"]: item for item in lease["task"]["inputs"]}
        context_name = f"conversation-{child['id']}.md"
        assert set(inputs) == {"material.txt", "answer.md", "notes.txt", context_name}
        for item in inputs.values():
            response = await oc.get(
                f"/v1/leases/{lease['lease_id']}/inputs/{item['input_id']}",
                headers=bearer(node["access_token"]),
            )
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == item["sha256"]
            if item["filename"] == context_name:
                messages = json.loads(response.text.split("\n\n", 2)[-1])
                assert messages[-1]["text"] == "把上次的说明改成三句话"
                assert messages[-2]["text"] == answer.decode()
                assert messages[0]["text"] == root["requirement"]
        # Finish a second turn and confirm a third one retains the original context.
        answer2 = (
            "# 第二轮\n\n这是沿用上轮材料重新整理的三句话说明，第三轮仍能继续修改。\n".encode()
        )
        await complete_with(oc, node, [("answer.md", "text/markdown", answer2)])
        assert await VerificationService(octx, None, None).run_once() == "SUCCEEDED"
        third = await submit(
            bob,
            generic(
                status, requirement="再改成英文", parent_task_id=child["id"], execution_mode="LIVE"
            ),
        )
        restored = await ok(await bob.get(f"/v1/experimental-tasks/{root['id']}/conversation"))
        assert [task["id"] for task in restored["items"]] == [root["id"], child["id"], third["id"]]
        third_lease = (await poll(oc, node)).json()
        context_input = next(
            item for item in third_lease["task"]["inputs"] if item["input_id"] == third["id"]
        )
        response = await oc.get(
            f"/v1/leases/{third_lease['lease_id']}/inputs/{context_input['input_id']}",
            headers=bearer(node["access_token"]),
        )
        assert response.status_code == 200
        messages = json.loads(response.text.split("\n\n", 2)[-1])
        assert [message["text"] for message in messages] == [
            root["requirement"],
            answer.decode(),
            "把上次的说明改成三句话",
            answer2.decode(),
            "再改成英文",
        ]
        outsider, _ = await one_click.join(octx, status["invite_code"])
        assert (
            await outsider.get(f"/v1/experimental-tasks/{child['id']}/conversation")
        ).status_code == 404
        denied = await submit(
            outsider,
            generic(status, requirement="read another member's context", parent_task_id=root["id"]),
            status=404,
        )
        assert denied["error"]["code"] == "TASK_NOT_FOUND"
    finally:
        await bob.aclose()
        if outsider:
            await outsider.aclose()
