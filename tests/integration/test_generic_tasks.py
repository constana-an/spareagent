"""Generic tasks, step A (docs/GENERAL_TASKS_DESIGN.md): kind catalogue and status,
attachments, the new submission shape and lease inputs. No real execution."""

import hashlib
from urllib.parse import quote

from sqlalchemy import text

from apps.api.schemas.experiments import TaskType
from services.tasks.kinds import CATALOG, MAX_UPLOAD_BYTES, experimental_kinds
from tests.conftest import ADMIN, bearer, headers
from tests.integration import test_one_click_sharing as one_click
from tests.integration.test_experimental_live import poll
from tests.integration.test_experimental_sharing import ok

ORIGIN = one_click.ORIGIN
octx = one_click.octx
oc = one_click.oc

DOCX = b"PK\x03\x04" + b"word/document.xml" + b"\x00" * 64
XLSX = b"PK\x03\x04" + b"xl/workbook.xml" + b"\x00" * 64


ALL_TOOLS = {"code": True, "doc": True, "git": True, "python-data": True}


async def group_with_member(oc, octx):
    """An online step-C device (declares every tool) with one member, Bob."""
    node = await one_click.signup_device(oc)
    status = await one_click.go_online(oc, node)
    resp = await oc.post(
        f"/v1/workers/{node['worker_id']}/heartbeat",
        json={
            "state": "ONLINE",
            "capabilities": {"executor": "codex-subscription", "tools": ALL_TOOLS},
        },
        headers=bearer(node["access_token"]),
    )
    assert resp.status_code == 204, resp.text
    bob, _ = await one_click.join(octx, status["invite_code"])
    return node, status, bob


async def upload(client, name, content, status=201) -> dict:
    resp = await client.post(
        "/v1/experimental-uploads",
        content=content,
        headers={**ORIGIN, "X-Filename": quote(name), "Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == status, resp.text
    return resp.json()


def generic(status, **fields) -> dict:
    return {
        "group_id": status["group"]["id"],
        "connection_id": status["sharing"]["connection_id"],
        "max_duration_seconds": 300,
        **fields,
    }


def digest(body: dict) -> str:
    return hashlib.sha256(repr(sorted(body.items())).encode()).hexdigest()[:32]


async def submit(client, body, status=202, key=None) -> dict:
    resp = await client.post(
        "/v1/experimental-tasks",
        json=body,
        headers={**ORIGIN, "Idempotency-Key": key or f"gt-{digest(body)}"},
    )
    assert resp.status_code == status, resp.text
    return resp.json()


async def set_kind(oc, kind, status, report=None, expect=200, who=ADMIN):
    body = {"status": status} | ({"acceptance_report": report} if report else {})
    resp = await oc.put(f"/v1/task-kinds/{kind}/status", json=body, headers=headers(who))
    assert resp.status_code == expect, resp.text
    return resp.json()


async def test_catalogue_matches_seeded_kinds(octx):
    async with octx.engine.connect() as conn:
        rows = (
            await conn.execute(text("SELECT kind, category, needs_repository FROM task_kinds"))
        ).all()
    seeded = {kind: (category, needs) for kind, category, needs in rows}
    assert seeded == {k.kind: (k.category, k.needs_repository) for k in CATALOG.values()}
    assert set(TaskType.__args__) == set(experimental_kinds())


async def test_kinds_are_listed_with_their_status(oc, octx):
    _, _, bob = await group_with_member(oc, octx)
    try:
        items = {k["kind"]: k for k in (await ok(await bob.get("/v1/task-kinds")))["items"]}
        assert "REFACTOR" not in items  # marketplace-only kind
        assert items["CODE_REVIEW"]["status"] == "OPEN" and items["CODE_REVIEW"]["live_available"]
        tr = items["TRANSLATE_DOCUMENT"]
        assert tr["status"] == "DEVELOPING" and not tr["live_available"]
        assert tr["required_parameters"] == ["target_language"] and tr["max_attachments"] == 1
        assert tr["name_zh"] == "翻译文档" and not tr["needs_repository"]
    finally:
        await bob.aclose()


async def test_only_the_operator_changes_kind_status(oc, octx):
    _, _, bob = await group_with_member(oc, octx)
    try:
        resp = await bob.put(
            "/v1/task-kinds/TRANSLATE_DOCUMENT/status", json={"status": "OPEN"}, headers=ORIGIN
        )
        assert resp.status_code == 403
        err = await set_kind(oc, "TRANSLATE_DOCUMENT", "OPEN", expect=422)
        assert err["error"]["code"] == "ACCEPTANCE_REPORT_REQUIRED"
        out = await set_kind(oc, "TRANSLATE_DOCUMENT", "SIMULATED_OK")
        assert out["status"] == "SIMULATED_OK"
        await set_kind(oc, "NOPE", "OPEN", report="r", expect=404)
    finally:
        await bob.aclose()


async def test_upload_checks_type_content_and_size(oc, octx):
    _, _, bob = await group_with_member(oc, octx)
    try:
        good = await upload(bob, "报告 v1.docx", DOCX)
        assert good["filename"] == "报告 v1.docx" and good["size_bytes"] == len(DOCX)
        assert good["sha256"] == hashlib.sha256(DOCX).hexdigest()
        cases = [
            ("tool.exe", b"MZ", 422, "UPLOAD_TYPE_NOT_ACCEPTED"),
            ("fake.docx", b"not a zip", 422, "UPLOAD_CONTENT_MISMATCH"),
            ("macro.xlsx", XLSX + b"xl/vbaProject.bin", 422, "UPLOAD_MACROS_NOT_ACCEPTED"),
            ("notes.txt", b"\xff\xfe\x00bad", 422, "UPLOAD_CONTENT_MISMATCH"),
            ("empty.txt", b"", 422, "UPLOAD_EMPTY"),
            ("big.txt", b"a" * (MAX_UPLOAD_BYTES + 1), 413, "UPLOAD_TOO_LARGE"),
            ("../escape.txt", b"hi", 422, "UPLOAD_NAME_INVALID"),
        ]
        for name, content, code, error in cases:
            assert (await upload(bob, name, content, code))["error"]["code"] == error, name
        resp = await oc.post("/v1/experimental-uploads", content=b"x")
        assert resp.status_code == 401
    finally:
        await bob.aclose()


async def test_translation_task_with_attachment_and_rules(oc, octx):
    node, status, bob = await group_with_member(oc, octx)
    try:
        doc = await upload(bob, "paper.docx", DOCX)
        sheet = await upload(bob, "data.xlsx", XLSX)
        base = generic(status, kind="TRANSLATE_DOCUMENT", requirement="Translate into Japanese")

        async def refused(code, http=422, **fields):
            out = await submit(bob, {**base, **fields}, status=http)
            assert out["error"]["code"] == code, out

        await refused("ATTACHMENT_COUNT_INVALID", parameters={"target_language": "ja"})
        await refused("TASK_PARAM_REQUIRED", attachments=[doc["id"]])
        await refused(
            "TASK_PARAM_INVALID", attachments=[doc["id"]], parameters={"target_language": "日本"}
        )
        await refused(
            "TASK_PARAM_UNKNOWN",
            attachments=[doc["id"]],
            parameters={"target_language": "ja", "tone": "x"},
        )
        await refused(
            "ATTACHMENT_TYPE_NOT_ACCEPTED",
            attachments=[sheet["id"]],
            parameters={"target_language": "ja"},
        )
        await refused(
            "REPOSITORY_NOT_ACCEPTED",
            attachments=[doc["id"]],
            parameters={"target_language": "ja"},
            repository={
                "url": "https://github.com/example/public-fixture",
                "commit_sha": "0123456789abcdef0123456789abcdef01234567",
            },
        )
        await refused(
            "DELIVERABLE_FORMAT_INVALID",
            attachments=[doc["id"]],
            parameters={"target_language": "ja"},
            deliverable={"format": "pdf"},
        )
        await refused(
            "TASK_KIND_NOT_OPEN",
            http=409,
            attachments=[doc["id"]],
            parameters={"target_language": "ja"},
            execution_mode="LIVE",
        )

        task = await submit(
            bob,
            {
                **base,
                "attachments": [doc["id"]],
                "parameters": {"target_language": "ja"},
                "deliverable": {"format": "md"},
            },
        )
        assert task["kind"] == task["task_type"] == "TRANSLATE_DOCUMENT"
        assert task["repository_url"] is None and task["commit_sha"] is None
        assert task["requirement"] == "Translate into Japanese"
        assert [a["id"] for a in task["attachments"]] == [doc["id"]]
        assert task["parameters"] == {"target_language": "ja"}
        assert task["deliverable"] == {"format": "md", "filename": "translation.md"}

        again = await submit(
            bob,
            {**base, "attachments": [doc["id"]], "parameters": {"target_language": "fr"}},
            status=409,
        )
        assert again["error"]["code"] == "UPLOAD_ALREADY_USED"
        fetched = await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))
        assert fetched["attachments"][0]["filename"] == "paper.docx"
    finally:
        await bob.aclose()


async def test_uploads_belong_to_their_uploader(oc, octx):
    node, status, bob = await group_with_member(oc, octx)
    code = (await ok(await one_click.owner(oc, node, "POST", "/invite-code")))["invite_code"]
    carol, _ = await one_click.join(octx, code)
    try:
        doc = await upload(carol, "carol.docx", DOCX)
        body = generic(
            status,
            kind="TRANSLATE_DOCUMENT",
            requirement="Translate",
            attachments=[doc["id"]],
            parameters={"target_language": "en"},
        )
        assert (await submit(bob, body, status=404))["error"]["code"] == "UPLOAD_NOT_FOUND"
    finally:
        await bob.aclose()
        await carol.aclose()


async def test_code_kinds_need_a_repository_and_legacy_shape_still_works(oc, octx):
    _, status, bob = await group_with_member(oc, octx)
    try:
        out = await submit(
            bob, generic(status, kind="CODE_REVIEW", requirement="Review it"), status=422
        )
        assert out["error"]["code"] == "REPOSITORY_REQUIRED"
        both = generic(status, kind="CODE_REVIEW", prompt="x", requirement="y")
        assert (await submit(bob, both, status=422))["error"]["code"] == "VALIDATION_ERROR"
        new_shape = await submit(
            bob,
            generic(
                status,
                kind="CODE_REVIEW",
                requirement="Review the parser",
                repository={
                    "url": "https://github.com/example/public-fixture",
                    "commit_sha": "0123456789ABCDEF0123456789abcdef01234567",
                },
            ),
        )
        assert new_shape["commit_sha"] == "0123456789abcdef0123456789abcdef01234567"
        assert new_shape["deliverable"] == {"format": "md", "filename": "review.md"}
        legacy = await submit(
            bob,
            generic(
                status,
                task_type="CODE_REVIEW",
                prompt="Review the parser",
                repository_url="https://github.com/example/public-fixture",
                commit_sha="0123456789abcdef0123456789abcdef01234567",
            ),
        )
        assert legacy["kind"] == "CODE_REVIEW" and legacy["prompt"] == "Review the parser"
    finally:
        await bob.aclose()


async def test_disabled_kind_is_refused(oc, octx):
    _, status, bob = await group_with_member(oc, octx)
    try:
        await set_kind(oc, "CODE_REVIEW", "DISABLED")
        out = await submit(
            bob,
            generic(
                status,
                kind="CODE_REVIEW",
                requirement="Review",
                repository={
                    "url": "https://github.com/example/public-fixture",
                    "commit_sha": "0123456789abcdef0123456789abcdef01234567",
                },
            ),
            status=409,
        )
        assert out["error"]["code"] == "TASK_KIND_DISABLED"
    finally:
        await bob.aclose()


async def test_live_lease_carries_inputs_only_for_the_lease_holder(oc, octx):
    node, status, bob = await group_with_member(oc, octx)
    try:
        await set_kind(oc, "TRANSLATE_DOCUMENT", "OPEN", report="reports/test-acceptance.json")
        doc = await upload(bob, "paper.docx", DOCX)
        await submit(
            bob,
            generic(
                status,
                kind="TRANSLATE_DOCUMENT",
                requirement="Translate into English",
                attachments=[doc["id"]],
                parameters={"target_language": "en"},
                execution_mode="LIVE",
            ),
        )
        lease = await poll(oc, node)
        assert lease.status_code == 200, lease.text
        lease = lease.json()
        task = lease["task"]
        assert task["kind"] == "TRANSLATE_DOCUMENT" and task["repository_url"] is None
        assert task["parameters"] == {"target_language": "en"}
        assert task["deliverable"] == {"format": "docx", "filename": "translation.docx"}
        (inp,) = task["inputs"]
        assert inp["input_id"] == doc["id"] and inp["sha256"] == doc["sha256"]

        url = f"/v1/leases/{lease['lease_id']}/inputs/{inp['input_id']}"
        got = await oc.get(url, headers=bearer(node["access_token"]))
        assert got.status_code == 200 and got.content == DOCX
        assert got.headers["x-content-sha256"] == doc["sha256"]
        other = await upload(bob, "other.docx", DOCX + b"x")
        missing = await oc.get(
            f"/v1/leases/{lease['lease_id']}/inputs/{other['id']}",
            headers=bearer(node["access_token"]),
        )
        assert missing.status_code == 404
        assert (await bob.get(url, headers=ORIGIN)).status_code == 401
    finally:
        await bob.aclose()


async def test_database_enforces_repository_rule_and_open_kind_for_live(oc, octx):
    from sqlalchemy.exc import DBAPIError

    from tests.conftest import USER_A

    insert = text(
        "INSERT INTO tasks (consumer_id, idempotency_key, request_digest, task_type, "
        "repository_url, commit_sha, prompt, resource_scope, max_budget_cents, "
        "max_duration_seconds) VALUES (:u, :k, repeat('0', 64), :kind, :url, :sha, 'p', "
        "'EXPERIMENTAL_SHARED_SUBSCRIPTION', 1, 60)"
    )
    url, sha = "https://github.com/example/public-fixture", "0" * 40
    for kind, repo_url, repo_sha in (
        ("CODE_REVIEW", None, None),
        ("TRANSLATE_DOCUMENT", url, sha),
        ("TRANSLATE_DOCUMENT", url, None),
    ):
        try:
            async with octx.engine.begin() as conn:
                await conn.execute(
                    insert,
                    {
                        "u": USER_A,
                        "k": f"db-{kind}",
                        "kind": kind,
                        "url": repo_url,
                        "sha": repo_sha,
                    },
                )
        except DBAPIError as exc:
            assert "repository" in str(exc) or "repository_fields_together" in str(exc)
        else:
            raise AssertionError(f"{kind} with repository={repo_url!r} was accepted")

    # A LIVE allow for a kind that is not OPEN is refused by the database itself.
    node, status, bob = await group_with_member(oc, octx)
    try:
        doc = await upload(bob, "paper.docx", DOCX)
        task = await submit(
            bob,
            generic(
                status,
                kind="TRANSLATE_DOCUMENT",
                requirement="Translate",
                attachments=[doc["id"]],
                parameters={"target_language": "en"},
            ),
        )
        try:
            async with octx.engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO experimental_dispatch_audit (task_id, group_id, "
                        "connection_id, authorization_id, decision, reasons, execution_mode) "
                        "SELECT :t, c.group_id, c.connection_id, a.id, 'ALLOW', '[]', 'LIVE' "
                        "FROM experimental_task_consents c JOIN experimental_authorizations a "
                        "ON a.group_id = c.group_id WHERE c.task_id = :t"
                    ),
                    {"t": task["id"]},
                )
        except DBAPIError as exc:
            assert "OPEN task kind" in str(exc)
        else:
            raise AssertionError("LIVE allow accepted for a DEVELOPING kind")
    finally:
        await bob.aclose()


async def test_scheduler_forgets_expired_unclaimed_uploads(oc, octx):
    from services.scheduler.runner import tick

    _, status, bob = await group_with_member(oc, octx)
    try:
        stale = await upload(bob, "stale.docx", DOCX)
        used = await upload(bob, "used.docx", DOCX + b"u")
        await submit(
            bob,
            generic(
                status,
                kind="TRANSLATE_DOCUMENT",
                requirement="Translate",
                attachments=[used["id"]],
                parameters={"target_language": "en"},
            ),
        )
        async with octx.engine.begin() as conn:
            await conn.execute(
                text("UPDATE task_uploads SET expires_at = now() - interval '1 minute'")
            )
        assert (await tick(octx))["uploads_purged"] == 1
        async with octx.engine.connect() as conn:
            left = (await conn.execute(text("SELECT id::text FROM task_uploads"))).scalars().all()
        assert left == [used["id"]] and stale["id"] not in left
    finally:
        await bob.aclose()


# ------------------------------------------------------------------ step B: deliverables
async def live_translation(oc, octx, source: bytes):
    node, status, bob = await group_with_member(oc, octx)
    await set_kind(oc, "TRANSLATE_DOCUMENT", "OPEN", report="reports/test-acceptance.json")
    doc = await upload(bob, "paper.docx", source)
    task = await submit(
        bob,
        generic(
            status,
            kind="TRANSLATE_DOCUMENT",
            requirement="Translate into Japanese",
            attachments=[doc["id"]],
            parameters={"target_language": "ja"},
            execution_mode="LIVE",
        ),
    )
    return node, bob, task


async def complete_with(oc, node, files: list[tuple[str, str, bytes]]) -> dict:
    import base64

    lease = (await poll(oc, node)).json()
    auth = bearer(node["access_token"])
    assert (
        await oc.post(f"/v1/leases/{lease['lease_id']}/accept", headers=auth)
    ).status_code == 204
    arts = [("SUMMARY", "summary.md", "text/plain", b"Translated.")] + [
        ("DELIVERABLE", n, m, c) for n, m, c in files
    ]
    body = {
        "finalization_key": f"fin-{lease['attempt_id']}",
        "succeeded": True,
        "result_summary": "done",
        "artifacts": [
            {"kind": k, "name": n, "media_type": m, "content_base64": base64.b64encode(c).decode()}
            for k, n, m, c in arts
        ],
    }
    return await ok(
        await oc.post(f"/v1/leases/{lease['lease_id']}/complete", json=body, headers=auth)
    )


DOCX_MT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def test_live_translation_is_checked_and_downloadable_by_name(oc, octx):
    from packages.common.deliverables import docx_paragraphs, docx_replace_paragraphs
    from services.verification.service import VerificationService
    from tests.unit.test_deliverables import make_docx

    source = make_docx()
    node, bob, task = await live_translation(oc, octx, source)
    try:
        translated = docx_replace_paragraphs(source, ["こんにちは", "セル", "最後の段落"])
        done = await complete_with(oc, node, [("translation.docx", DOCX_MT, translated)])
        assert done["task_status"] == "VERIFYING"
        assert await VerificationService(octx, None, None).run_once() == "SUCCEEDED"
        view = await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))
        assert view["status"] == "SUCCEEDED" and view["verification_status"] == "UNVERIFIED"
        assert view["result_summary"] == "UNVERIFIED: STRUCTURE_CHECKED_CONTENT_NOT_REVIEWED"
        arts = (await ok(await bob.get(f"/v1/tasks/{task['id']}/artifacts")))["items"]
        (deliv,) = [a for a in arts if a["kind"] == "DELIVERABLE"]
        assert deliv["name"] == "translation.docx" and deliv["media_type"] == DOCX_MT
        got = await bob.get(f"/v1/tasks/{task['id']}/artifacts/{deliv['id']}/content")
        assert got.status_code == 200
        assert got.headers["content-disposition"] == 'attachment; filename="translation.docx"'
        assert docx_paragraphs(got.content) == ["こんにちは", "セル", "最後の段落"]
        report = [a for a in arts if a["kind"] == "VERIFICATION_REPORT"]
        assert report, arts
    finally:
        await bob.aclose()


async def test_live_translation_in_the_wrong_language_fails_verification(oc, octx):
    from packages.common.deliverables import docx_replace_paragraphs
    from services.verification.service import VerificationService
    from tests.unit.test_deliverables import make_docx

    source = make_docx()
    node, bob, task = await live_translation(oc, octx, source)
    try:
        english = docx_replace_paragraphs(source, ["Hello there", "A cell", "The end"])
        await complete_with(oc, node, [("translation.docx", DOCX_MT, english)])
        assert await VerificationService(octx, None, None).run_once() == "FAILED"
        view = await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))
        assert view["status"] == "FAILED" and view["verification_status"] == "FAILED"
        async with octx.engine.connect() as conn:
            reason = (
                await conn.execute(text("SELECT reason FROM verification_results"))
            ).scalar_one()
        assert reason == "WRONG_LANGUAGE"
        arts = (await ok(await bob.get(f"/v1/tasks/{task['id']}/artifacts")))["items"]
        assert not [a for a in arts if a["kind"] == "DELIVERABLE"]  # never published
    finally:
        await bob.aclose()


async def test_submission_gate_refuses_disallowed_deliverable_types(oc, octx):
    from tests.unit.test_deliverables import make_docx

    node, bob, task = await live_translation(oc, octx, make_docx())
    try:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30
        done = await complete_with(oc, node, [("translation.png", "image/png", png)])
        assert done["task_status"] == "FAILED"
        view = await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))
        assert "disallowed type" in (view["result_summary"] or "")
    finally:
        await bob.aclose()


async def test_live_general_task_is_checked_and_downloadable(oc, octx):
    """A general task with no attachments: answer.md plus a picture, structure-checked."""
    from services.verification.service import VerificationService

    node, status, bob = await group_with_member(oc, octx)
    await set_kind(oc, "GENERAL", "OPEN", report="reports/test-acceptance.json")
    try:
        task = await submit(
            bob,
            generic(
                status,
                kind="GENERAL",
                requirement="解释一下 TCP 三次握手，并画一张示意图",
                execution_mode="LIVE",
            ),
        )
        assert task["deliverable"] == {"format": "md", "filename": "answer.md"}
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>'
        answer = "# TCP 三次握手\n\nSYN、SYN-ACK、ACK。见 handshake.svg。\n".encode()
        await complete_with(
            oc,
            node,
            [("answer.md", "text/markdown", answer), ("handshake.svg", "image/svg+xml", svg)],
        )
        assert await VerificationService(octx, None, None).run_once() == "SUCCEEDED"
        arts = (await ok(await bob.get(f"/v1/tasks/{task['id']}/artifacts")))["items"]
        names = sorted(a["name"] for a in arts if a["kind"] == "DELIVERABLE")
        assert names == ["answer.md", "handshake.svg"]
    finally:
        await bob.aclose()


async def test_live_general_task_without_an_answer_fails(oc, octx):
    from services.verification.service import VerificationService

    node, status, bob = await group_with_member(oc, octx)
    await set_kind(oc, "GENERAL", "OPEN", report="reports/test-acceptance.json")
    try:
        code = await upload(bob, "app.py", b"print('hi')\n")
        task = await submit(
            bob,
            generic(
                status,
                kind="GENERAL",
                requirement="Add a --verbose flag",
                attachments=[code["id"]],
                execution_mode="LIVE",
            ),
        )
        await complete_with(oc, node, [("app.py", "text/plain", b"print('hi')\n")])
        assert await VerificationService(octx, None, None).run_once() == "FAILED"
        view = await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))
        assert view["failure"]["category"] == "CHECK_FAILED"
    finally:
        await bob.aclose()


async def test_internet_tasks_wait_for_an_owner_who_allows_internet(oc, octx):
    node, status, bob = await group_with_member(oc, octx)
    await set_kind(oc, "GENERAL", "OPEN", report="reports/test-acceptance.json")
    try:
        resp = await oc.post(
            f"/v1/workers/{node['worker_id']}/heartbeat",
            json={
                "state": "ONLINE",
                "capabilities": {
                    "executor": "codex-subscription",
                    "tools": {**ALL_TOOLS, "network": True},
                },
            },
            headers=bearer(node["access_token"]),
        )
        assert resp.status_code == 204, resp.text
        task = await submit(
            bob,
            generic(
                status,
                kind="GENERAL",
                requirement="今天的东京天气如何？",
                network_access=True,
                execution_mode="LIVE",
            ),
        )
        assert task["network_access"] is True
        # Off by default: the owner has not allowed internet on this device.
        assert (await poll(oc, node)).status_code == 204
        view = await ok(await bob.get(f"/v1/experimental-tasks/{task['id']}"))
        assert view["status"] == "QUEUED"
        assert "TOOL_NOT_ALLOWED" in view["last_decision"]["reasons"]

        prefs = (await ok(await one_click.owner(oc, node, "GET", "")))["sharing"]
        assert "network" not in prefs["allowed_tools"]
        await ok(
            await one_click.owner(
                oc,
                node,
                "PUT",
                "/settings",
                json={"allowed_tools": [*prefs["allowed_tools"], "network"]},
            )
        )
        lease = (await poll(oc, node)).json()
        assert lease["task"]["task_id"] == task["id"]
        assert lease["task"]["network_access"] is True

        plain = await submit(bob, generic(status, kind="GENERAL", requirement="Say hi"))
        assert plain["network_access"] is False
    finally:
        await bob.aclose()
