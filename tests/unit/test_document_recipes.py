"""Device-side execution of document kinds (translation, analysis) against the fake
Codex CLI: workspace layout, rendering, collection rules and device checks."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from packages.common import deliverables as d
from packages.provider_sdk.base import PermanentProviderError
from packages.provider_sdk.codex_subscription import (
    CodexSubscriptionAdapter,
    SharedWorkConsent,
    SharedWorkRefused,
)
from packages.provider_sdk.types import ExecutionRequest, ExecutionResult, InputFile
from packages.worker.codex_runtime import CodexRuntime, CodexRuntimeConfig
from packages.worker.local_controls import LocalSharingControl
from tests.unit.test_codex_subscription import FAKE, codex_home, set_mode  # noqa: F401
from tests.unit.test_deliverables import make_docx, make_xlsx


def adapter(tmp_path) -> CodexSubscriptionAdapter:
    return CodexSubscriptionAdapter(
        owner_ref="owner",
        runtime=CodexRuntime(CodexRuntimeConfig(codex_bin=FAKE)),
        shared=SharedWorkConsent(
            LocalSharingControl(tmp_path / "state"), no_purchased_credits_attested=True
        ),
    )


def req(kind, inputs, **extra) -> ExecutionRequest:
    return ExecutionRequest(
        task_id=str(uuid.uuid4()),
        attempt_id=str(uuid.uuid4()),
        user_id="redacted",
        resource_scope="EXPERIMENTAL_SHARED_SUBSCRIPTION",
        prompt="Please keep the tone formal.",
        repository_url=None,
        commit_sha=None,
        max_duration_seconds=30,
        max_budget_cents=1,
        task_type=kind,
        authorization_ref=str(uuid.uuid4()),
        inputs=tuple(InputFile(n, c, "x") for n, c in inputs),
        **extra,
    )


def translate(fmt="docx", source=("paper.docx", None)):
    name, content = source
    return req(
        "TRANSLATE_DOCUMENT",
        [(name, content if content is not None else make_docx())],
        parameters={"target_language": "ja"},
        deliverable={"format": fmt, "filename": f"translation.{fmt}"},
    )


async def last(a, r):
    return [item async for item in a.execute(r)][-1]


async def test_translation_renders_a_checked_docx(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "chatgpt")
    result = await last(adapter(tmp_path), translate())
    assert isinstance(result, ExecutionResult)
    files = {a.name: a for a in result.artifacts if a.kind == "DELIVERABLE"}
    assert list(files) == ["translation.docx"]
    doc = files["translation.docx"].content
    assert d.docx_paragraphs(doc) == [f"これは第{i}段落の翻訳です。" for i in (1, 2, 3)]
    assert zipfile.ZipFile(io.BytesIO(doc)).read("word/styles.xml") == b"<w:styles/>"
    prompt = (codex_home / "last_prompt.txt").read_text()
    assert "exactly 3 strings" in prompt and "<<<MEMBER REQUEST" in prompt
    assert "keep the tone formal" in prompt


async def test_translation_of_markdown_to_markdown(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "chatgpt")
    r = translate("md", ("notes.md", b"# Title\n\nBody text here.\n"))
    result = await last(adapter(tmp_path), r)
    (md,) = [a for a in result.artifacts if a.kind == "DELIVERABLE"]
    assert md.name == "translation.md" and md.media_type == "text/markdown"
    assert md.content.decode().count("翻訳") == 2


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("doc_missing", "MISSING_OUTPUT"),
        ("doc_wrong_count", "MALFORMED_OUTPUT"),
        ("doc_untranslated", "WRONG_LANGUAGE"),
    ],
)
async def test_translation_failures_are_reported(codex_home, tmp_path, mode, code):  # noqa: F811
    set_mode(codex_home, mode)
    with pytest.raises(PermanentProviderError, match=code):
        await last(adapter(tmp_path), translate())


async def test_analysis_collects_allowed_outputs_only(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "chatgpt")
    r = req("ANALYZE_SPREADSHEET", [("sales.xlsx", make_xlsx()), ("extra.csv", b"a,b\n1,2\n")])
    result = await last(adapter(tmp_path), r)
    names = sorted(a.name for a in result.artifacts if a.kind == "DELIVERABLE")
    assert names == ["chart-1.svg", "report.md"]  # tool.exe and the symlink are dropped
    prompt = (codex_home / "last_prompt.txt").read_text()
    assert "in/sales__Sales.csv" in prompt and "in/extra.csv" in prompt


async def test_analysis_with_active_svg_fails(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "doc_active_svg")
    r = req("ANALYZE_SPREADSHEET", [("sales.csv", b"a,b\n1,2\n")])
    with pytest.raises(PermanentProviderError, match="MALFORMED_OUTPUT"):
        await last(adapter(tmp_path), r)


async def test_document_tasks_never_take_a_repository_and_need_inputs(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "chatgpt")
    with pytest.raises(SharedWorkRefused):
        await last(adapter(tmp_path), req("ANALYZE_SPREADSHEET", []))
    bad = req("ANALYZE_SPREADSHEET", [("a.csv", b"x")])
    object.__setattr__(bad, "repository_url", "https://github.com/a/b")
    object.__setattr__(bad, "commit_sha", "0" * 40)
    with pytest.raises(SharedWorkRefused):
        await last(adapter(tmp_path), bad)


async def test_unreadable_source_is_rejected_before_codex_runs(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "chatgpt")
    with pytest.raises(PermanentProviderError, match="INPUT_INVALID"):
        await last(adapter(tmp_path), translate(source=("paper.docx", b"PK\x03\x04broken")))
    assert not (codex_home / "last_prompt.txt").exists()


async def test_mock_runs_produce_kind_shaped_deliverables_that_pass_the_gate():
    from packages.provider_sdk.mock import MockProviderAdapter
    from services.execution.verifier import verify

    mock = MockProviderAdapter()
    for kind, extra, expected in (
        (
            "TRANSLATE_DOCUMENT",
            {
                "parameters": {"target_language": "fr"},
                "deliverable": {"format": "md", "filename": "translation.md"},
            },
            "translation.md",
        ),
        ("ANALYZE_SPREADSHEET", {}, "report.md"),
    ):
        r = req(kind, [], **extra)
        result = [x async for x in mock.execute(r)][-1]
        names = [a.name for a in result.artifacts if a.kind == "DELIVERABLE"]
        assert names == [expected]
        assert verify(result.artifacts, kind).ok
        assert not verify(result.artifacts, "CODE_FIX").ok  # code kinds still need a patch


def test_analysis_keeps_distinct_uploads_that_share_a_name():
    from packages.worker.recipes import Workspace, prepare_analysis

    request = req(
        "ANALYZE_SPREADSHEET",
        [
            ("sales.csv", b"x\n1\n"),
            ("sales.csv", b"x\n2\n"),
            ("销售.csv", b"x\n3\n"),
            ("成本.csv", b"x\n4\n"),
        ],
    )
    ws = Workspace.create()
    try:
        prompt = prepare_analysis(request, ws)
        names = sorted(p.name for p in ws.inputs.iterdir())
        assert names == ["file-2.csv", "file.csv", "sales-2.csv", "sales.csv"]
        assert sorted(p.read_bytes() for p in ws.inputs.iterdir()) == [
            b"x\n1\n",
            b"x\n2\n",
            b"x\n3\n",
            b"x\n4\n",
        ]
        for name in names:
            assert f"- in/{name}" in prompt
    finally:
        ws.cleanup()


async def test_a_clean_run_that_writes_nothing_is_reported_as_declined(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "doc_missing")
    with pytest.raises(PermanentProviderError) as exc:
        await last(adapter(tmp_path), translate())
    assert exc.value.detail_code == "MODEL_DECLINED"
    from services.tasks.progress import failure_category

    assert failure_category("MODEL_DECLINED") == "REFUSED"


# ------------------------------------------------------------------ general tasks
async def test_general_task_without_attachments_answers_and_draws(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "chatgpt")
    result = await last(adapter(tmp_path), req("GENERAL", []))
    names = sorted(a.name for a in result.artifacts if a.kind == "DELIVERABLE")
    assert names == ["answer.md", "diagram.svg"]  # page.html is never delivered
    prompt = (codex_home / "last_prompt.txt").read_text()
    assert "No attachments." in prompt and "Never install packages or use the network" in prompt


async def test_general_task_reads_office_files_and_returns_edited_code(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "chatgpt")
    r = req(
        "GENERAL",
        [("app.py", b"print('hi')\n"), ("notes.docx", make_docx()), ("t.xlsx", make_xlsx())],
    )
    result = await last(adapter(tmp_path), r)
    files = {a.name: a for a in result.artifacts if a.kind == "DELIVERABLE"}
    assert set(files) == {"answer.md", "diagram.svg", "app.py"}
    assert files["app.py"].content.endswith(b"print('verbose')\n")
    assert files["app.py"].media_type == "text/plain"
    prompt = (codex_home / "last_prompt.txt").read_text()
    assert "in/notes__text.txt (text of notes.docx)" in prompt
    assert "in/t__Sales.csv (sheet 'Sales' of t.xlsx)" in prompt


@pytest.mark.parametrize(
    ("mode", "code"), [("doc_missing", "MODEL_DECLINED"), ("doc_active_svg", "MALFORMED_OUTPUT")]
)
async def test_general_task_failures(codex_home, tmp_path, mode, code):  # noqa: F811
    set_mode(codex_home, mode)
    with pytest.raises(PermanentProviderError) as exc:
        await last(adapter(tmp_path), req("GENERAL", []))
    assert exc.value.detail_code == code


def test_general_check_needs_an_answer_and_well_formed_files():
    ok = d.check_general(
        [
            d.FileIn("answer.md", "这是一个足够长的回答，用来说明结果。".encode() * 2),
            d.FileIn("chart.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
            d.FileIn("data.json", b'{"a": 1}'),
            d.FileIn("main.go", b"package main\n"),
        ]
    )
    assert ok.ok, ok.checks
    assert d.check_general([d.FileIn("chart.svg", b"<svg/>")]).reason == "MISSING_OUTPUT"
    assert d.check_general([d.FileIn("answer.md", b"ok")]).reason == "EMPTY_OUTPUT"
    bad_json = [d.FileIn("answer.md", b"x" * 40), d.FileIn("a.json", b"{")]
    assert d.check_general(bad_json).reason == "MALFORMED_OUTPUT"
    html = [d.FileIn("answer.md", b"x" * 40), d.FileIn("page.html", b"<p>")]
    assert d.check_general(html).reason == "FILE_TYPE_NOT_ALLOWED"
