import hashlib
import json
import uuid

import pytest
from jsonschema import Draft202012Validator

from tests.conftest import USER_A, USER_B, create_task, drain_worker, execute, fetch_val, headers

pytestmark = pytest.mark.db

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
EVENT_SCHEMA = json.loads((ROOT / "contracts" / "events.schema.json").read_text())


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":") or ":" not in line:
                continue
            name, _, value = line.partition(":")
            fields[name] = value.lstrip(" ")
        if "data" in fields:
            events.append(
                {
                    "id": int(fields["id"]),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return events


async def completed_task(client, ctx):
    task_id = (await create_task(client)).json()["id"]
    await drain_worker(ctx)
    return task_id


async def test_sse_full_stream_ordered_and_schema_valid(client, ctx):
    task_id = await completed_task(client, ctx)
    resp = await client.get(f"/v1/tasks/{task_id}/events", headers=headers(USER_A))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    seqs = [e["id"] for e in events]
    assert seqs == list(range(1, len(events) + 1))
    types = [e["event"] for e in events]
    assert types == [
        "task.created",
        "task.queued",
        "task.reserved",
        "task.started",
        "task.progress",
        "task.progress",
        "task.verifying",
        "task.succeeded",
    ]
    validator = Draft202012Validator(EVENT_SCHEMA)
    for e in events:
        validator.validate(e["data"])
        assert e["data"]["seq"] == e["id"] and e["data"]["task_id"] == task_id
    statuses = [
        e["data"]["payload"].get("status") for e in events if "status" in e["data"]["payload"]
    ]
    assert statuses == ["CREATED", "QUEUED", "RESERVED", "RUNNING", "VERIFYING", "SUCCEEDED"]


async def test_sse_last_event_id_replay(client, ctx):
    task_id = await completed_task(client, ctx)
    full = parse_sse(
        (await client.get(f"/v1/tasks/{task_id}/events", headers=headers(USER_A))).text
    )
    resp = await client.get(
        f"/v1/tasks/{task_id}/events", headers={**headers(USER_A), "Last-Event-ID": "3"}
    )
    replay = parse_sse(resp.text)
    assert [e["id"] for e in replay] == [e["id"] for e in full if e["id"] > 3]
    assert replay == [e for e in full if e["id"] > 3]
    bad = await client.get(
        f"/v1/tasks/{task_id}/events", headers={**headers(USER_A), "Last-Event-ID": "abc"}
    )
    assert bad.status_code == 400


async def test_sse_unauthorized_is_error(client, ctx):
    task_id = await completed_task(client, ctx)
    resp = await client.get(f"/v1/tasks/{task_id}/events", headers=headers(USER_B))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"
    assert (await client.get(f"/v1/tasks/{task_id}/events")).status_code == 401


async def test_task_001_success_has_result_summary_and_artifacts(client, ctx):
    task_id = await completed_task(client, ctx)
    task = (await client.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))).json()
    assert task["status"] == "SUCCEEDED"
    assert task["result_summary"].startswith("Mock WRITE_TESTS result")
    arts = (await client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))).json()
    assert sorted(a["kind"] for a in arts["items"]) == ["LOG", "PATCH", "SUMMARY", "TEST_REPORT"]
    for art in arts["items"]:
        assert set(art) == {"id", "kind", "sha256", "media_type", "source"}
        content = await client.get(
            f"/v1/tasks/{task_id}/artifacts/{art['id']}/content", headers=headers(USER_A)
        )
        assert content.status_code == 200
        assert hashlib.sha256(content.content).hexdigest() == art["sha256"]
        assert content.headers["x-content-type-options"] == "nosniff"
        assert content.headers["content-disposition"].startswith("attachment")


async def test_cross_user_artifact_denied(client, ctx):
    task_id = await completed_task(client, ctx)
    arts = (await client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))).json()
    art_id = arts["items"][0]["id"]
    resp = await client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_B))
    assert resp.status_code == 404
    resp = await client.get(
        f"/v1/tasks/{task_id}/artifacts/{art_id}/content", headers=headers(USER_B)
    )
    assert resp.status_code == 404
    # B's own task cannot be used to reach A's artifact id
    b_task = (await create_task(client, user=USER_B)).json()["id"]
    resp = await client.get(
        f"/v1/tasks/{b_task}/artifacts/{art_id}/content", headers=headers(USER_B)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"


async def test_sec_002_tampered_storage_key_rejected_via_api(client, ctx):
    task_id = await completed_task(client, ctx)
    art_id = (await client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))).json()[
        "items"
    ][0]["id"]
    await execute(
        ctx,
        "UPDATE artifacts SET storage_key='../../../../etc/passwd' WHERE id=:a",
        a=uuid.UUID(art_id),
    )
    resp = await client.get(
        f"/v1/tasks/{task_id}/artifacts/{art_id}/content", headers=headers(USER_A)
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "ARTIFACT_UNAVAILABLE"
    assert "root:" not in resp.text


async def test_sec_001_secret_in_prompt_redacted_in_log_artifact(client, ctx):
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"
    task_id = (await create_task(client, prompt=f"use {secret} to call the api")).json()["id"]
    await drain_worker(ctx)
    arts = (await client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))).json()
    log = next(a for a in arts["items"] if a["kind"] == "LOG")
    content = (
        await client.get(
            f"/v1/tasks/{task_id}/artifacts/{log['id']}/content", headers=headers(USER_A)
        )
    ).text
    assert secret not in content and "[REDACTED]" in content
    payloads = await fetch_val(
        ctx,
        "SELECT string_agg(payload::text, ' ') FROM execution_events WHERE task_id=:t",
        t=uuid.UUID(task_id),
    )
    assert secret not in payloads
