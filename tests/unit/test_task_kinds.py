"""Task kind catalogue: parameters, deliverables, attachment rules and upload sniffing."""

import pytest

from services.tasks import kinds
from services.tasks.kinds import KindError


def code(fn, *args) -> str:
    with pytest.raises(KindError) as exc:
        fn(*args)
    return exc.value.code


def test_every_kind_is_consistent():
    for k in kinds.CATALOG.values():
        assert k.needs_repository == (k.category == "CODE")
        assert k.min_attachments <= k.max_attachments <= kinds.MAX_ATTACHMENTS
        assert bool(k.extensions) == (k.max_attachments > 0)
        assert k.extensions <= set(kinds.UPLOAD_TYPES)
        assert kinds.resolve_deliverable(k, None)["filename"] == k.deliverable.filename


def test_parameters_are_validated_per_kind():
    tr = kinds.get("TRANSLATE_DOCUMENT")
    assert kinds.validate_params(tr, {"target_language": "zh-Hans"}) == {
        "target_language": "zh-Hans"
    }
    assert code(kinds.validate_params, tr, {}) == "TASK_PARAM_REQUIRED"
    assert code(kinds.validate_params, tr, {"target_language": "Japanese!"}) == (
        "TASK_PARAM_INVALID"
    )
    assert code(kinds.validate_params, kinds.get("CODE_REVIEW"), {"x": "y"}) == (
        "TASK_PARAM_UNKNOWN"
    )
    assert code(kinds.get, "NOPE") == "TASK_KIND_UNKNOWN"


def test_deliverable_formats_and_attachment_rules():
    tr = kinds.get("TRANSLATE_DOCUMENT")
    assert kinds.resolve_deliverable(tr, {"format": "txt"}) == {
        "format": "txt",
        "filename": "translation.txt",
    }
    assert code(kinds.resolve_deliverable, tr, {"format": "exe"}) == "DELIVERABLE_FORMAT_INVALID"
    assert code(kinds.check_attachment_count, tr, 0) == "ATTACHMENT_COUNT_INVALID"
    assert code(kinds.check_attachment_count, kinds.get("CODE_REVIEW"), 1) == (
        "ATTACHMENTS_NOT_ACCEPTED"
    )
    kinds.check_attachment_count(kinds.get("ANALYZE_SPREADSHEET"), 5)
    assert code(kinds.check_attachment_types, tr, ["xlsx"]) == "ATTACHMENT_TYPE_NOT_ACCEPTED"


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("a.PDF", b"%PDF-1.7 ...", ("pdf", "application/pdf")),
        ("表.csv", "名称,值\n".encode(), ("csv", "text/csv")),
        ("x.png", b"\x89PNG\r\n\x1a\n...", ("png", "image/png")),
    ],
)
def test_allowed_uploads(name, content, expected):
    assert kinds.sniff_upload(name, content) == expected


@pytest.mark.parametrize(
    ("name", "content", "error"),
    [
        ("noext", b"x", "UPLOAD_TYPE_NOT_ACCEPTED"),
        ("a.zip", b"PK\x03\x04", "UPLOAD_TYPE_NOT_ACCEPTED"),
        ("a.docm", b"PK\x03\x04", "UPLOAD_TYPE_NOT_ACCEPTED"),
        ("a.pdf", b"<html>", "UPLOAD_CONTENT_MISMATCH"),
        ("a/b.txt", b"x", "UPLOAD_NAME_INVALID"),
        ("a.txt\x00.pdf", b"x", "UPLOAD_NAME_INVALID"),
        ("m.docx", b"PK\x03\x04 word/vbaProject.bin", "UPLOAD_MACROS_NOT_ACCEPTED"),
    ],
)
def test_refused_uploads(name, content, error):
    assert code(kinds.sniff_upload, name, content) == error
