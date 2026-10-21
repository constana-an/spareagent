"""Deliverables of generic tasks: document helpers and structural checks (design §2, §4).

Shared by the owner's device (to prepare inputs, render outputs and self-check before
upload) and by the platform verifier (to check what the device returned). Standard
library only, so the worker wheel stays dependency-free. Office files are handled as the
ZIP/XML packages they are; XML is never parsed with entity expansion, DTDs are refused and
archive members are size-limited, so a hostile file cannot exhaust memory.

These checks establish structure (format, completeness, language), never quality.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import struct
import unicodedata
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

MAX_DELIVERABLE_BYTES = 5 * 1024 * 1024
MAX_DELIVERABLES = 6
MAX_TOTAL_DELIVERABLE_BYTES = 12 * 1024 * 1024
MAX_PARAGRAPHS = 3000
MAX_XML_BYTES = 30 * 1024 * 1024
MAX_SHEET_ROWS = 50_000

MEDIA_TYPES = {
    "md": "text/markdown",
    "txt": "text/plain",
    "csv": "text/csv",
    "json": "application/json",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "patch": "text/x-diff",
}
# Source files a general task may hand back (edited copies of what the member uploaded).
# HTML is accepted as input only: a deliverable never carries active content (as for SVG).
CODE_OUTPUTS = frozenset(
    {
        "py",
        "js",
        "ts",
        "tsx",
        "jsx",
        "css",
        "java",
        "go",
        "rs",
        "c",
        "h",
        "cpp",
        "hpp",
        "rb",
        "php",
        "swift",
        "kt",
        "sql",
        "sh",
        "yaml",
        "yml",
        "toml",
        "diff",
    }
)
MEDIA_TYPES.update({ext: "text/plain" for ext in CODE_OUTPUTS})
# File types a device may return per kind (anything else in out/ is dropped and logged).
KIND_OUTPUTS: dict[str, frozenset[str]] = {
    "TRANSLATE_DOCUMENT": frozenset({"docx", "md", "txt"}),
    "ANALYZE_SPREADSHEET": frozenset({"md", "png", "svg", "csv", "xlsx"}),
    "GENERAL": frozenset(
        {"md", "txt", "csv", "json", "docx", "xlsx", "png", "svg", "pdf", "patch"} | CODE_OUTPUTS
    ),
}
GENERAL_ANSWER = "answer.md"
DOCUMENT_KINDS = frozenset(KIND_OUTPUTS)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentError(ValueError):
    """The file is not a well-formed document of its type."""


def safe_name(name: str) -> str:
    """A flat ASCII file name (matches the artifacts.name constraint)."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    flat = _SAFE.sub("-", unicodedata.normalize("NFKD", stem)).strip("-.") or "file"
    ext = _SAFE.sub("", ext.lower())[:8]
    out = f"{flat[:90]}.{ext}" if ext else flat[:99]
    return out if out[0].isalnum() else f"f{out}"[:100]


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


# ------------------------------------------------------------------ zip / xml safety
def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise DocumentError("not a valid ZIP package") from exc
    total = 0
    for info in zf.infolist():
        total += info.file_size
        if info.file_size > MAX_XML_BYTES or total > 4 * MAX_XML_BYTES:
            raise DocumentError("package expands beyond the size limit")
    return zf


def _member(zf: zipfile.ZipFile, name: str) -> str:
    try:
        raw = zf.read(name)
    except KeyError as exc:
        raise DocumentError(f"missing {name}") from exc
    text = raw.decode("utf-8", errors="strict")
    head = text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in text.upper():
        raise DocumentError("XML with DTD/entities is not accepted")
    return text


# ------------------------------------------------------------------ .docx
_DOCX_TAG = re.compile(r"<(/?)w:(p|t)(?=[\s/>])([^>]*?)(/?)>")


def _docx_scan(xml: str):
    """Yields (paragraph_index, [(start, end) of each w:t content]) for each innermost
    paragraph, in document order. Paragraphs nested in text boxes are their own entries."""
    stack: list[list[tuple[int, int]]] = []
    open_t: int | None = None
    index = 0
    for m in _DOCX_TAG.finditer(xml):
        closing, tag, _attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if tag == "p":
            if selfclose:
                continue
            if not closing:
                stack.append([])
            elif stack:
                spans = stack.pop()
                yield index, spans
                index += 1
        elif tag == "t" and stack:
            if selfclose:
                continue
            if not closing:
                open_t = m.end()
            elif open_t is not None:
                stack[-1].append((open_t, m.start()))
                open_t = None


def docx_paragraphs(data: bytes) -> list[str]:
    """Non-empty paragraph texts of a .docx, in order."""
    xml = _member(_zip(data), "word/document.xml")
    out = []
    for _, spans in _docx_scan(xml):
        text = "".join(html.unescape(xml[a:b]) for a, b in spans)
        if text.strip():
            out.append(text)
    if len(out) > MAX_PARAGRAPHS:
        raise DocumentError("too many paragraphs")
    return out


def docx_replace_paragraphs(data: bytes, texts: list[str]) -> bytes:
    """The same .docx with each non-empty paragraph's text replaced, keeping the package,
    styles and the first run's formatting of every paragraph."""
    zf = _zip(data)
    xml = _member(zf, "word/document.xml")
    edits: list[tuple[int, int, str]] = []
    it = iter(texts)
    count = 0
    for _, spans in _docx_scan(xml):
        if not "".join(html.unescape(xml[a:b]) for a, b in spans).strip():
            continue
        try:
            new = next(it)
        except StopIteration:
            raise DocumentError("fewer translated paragraphs than source paragraphs") from None
        count += 1
        for n, (a, b) in enumerate(spans):
            edits.append((a, b, escape(new) if n == 0 else ""))
    if next(it, None) is not None:
        raise DocumentError("more translated paragraphs than source paragraphs")
    if not count:
        raise DocumentError("document has no text")
    for a, b, text in sorted(edits, reverse=True):
        xml = xml[:a] + text + xml[b:]
    # The first w:t of each paragraph now holds all text; keep its spaces.
    xml = re.sub(r"<w:t(?![^>]*xml:space)(?=[\s>])", '<w:t xml:space="preserve"', xml)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in zf.infolist():
            body = xml.encode() if info.filename == "word/document.xml" else zf.read(info)
            dst.writestr(info, body)
    return out.getvalue()


_MIN_DOCX = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    ),
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    ),
}


def docx_from_paragraphs(texts: list[str]) -> bytes:
    """A minimal valid .docx with one plain paragraph per text."""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>' for t in texts
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in {**_MIN_DOCX, "word/document.xml": document}.items():
            zf.writestr(name, text)
    return out.getvalue()


# ------------------------------------------------------------------ text documents
def text_paragraphs(data: bytes) -> list[str]:
    """Blocks separated by blank lines (Markdown/plain text paragraphs)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentError("text is not UTF-8") from exc
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n"))]
    out = [b for b in blocks if b.strip()]
    if len(out) > MAX_PARAGRAPHS:
        raise DocumentError("too many paragraphs")
    return out


def source_paragraphs(filename: str, data: bytes) -> list[str]:
    ext = extension(filename)
    if ext == "docx":
        return docx_paragraphs(data)
    if ext in ("md", "txt"):
        return text_paragraphs(data)
    raise DocumentError(f"cannot read .{ext} documents")


def render_translation(source_name: str, source: bytes, texts: list[str], fmt: str) -> bytes:
    """The translated document in the requested format."""
    if fmt == "docx":
        if extension(source_name) == "docx":
            return docx_replace_paragraphs(source, texts)
        return docx_from_paragraphs(texts)
    if fmt in ("md", "txt"):
        return ("\n\n".join(t.strip("\n") for t in texts) + "\n").encode()
    raise DocumentError(f"cannot write .{fmt} documents")


# ------------------------------------------------------------------ .xlsx -> rows
_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return max(n - 1, 0)


def xlsx_sheets(data: bytes) -> dict[str, list[list[str]]]:
    """Cell values (as displayed text, formulas' cached values) of every worksheet."""
    import xml.etree.ElementTree as ET  # noqa: S405 - DTD/entities refused by _member

    zf = _zip(data)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        root = ET.fromstring(_member(zf, "xl/sharedStrings.xml"))  # noqa: S314
        for si in root.iter(f"{_NS_MAIN}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_NS_MAIN}t")))
    book = ET.fromstring(_member(zf, "xl/workbook.xml"))  # noqa: S314
    rels = ET.fromstring(_member(zf, "xl/_rels/workbook.xml.rels"))  # noqa: S314
    targets = {r.get("Id"): r.get("Target", "") for r in rels}
    rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    sheets: dict[str, list[list[str]]] = {}
    for sheet in book.iter(f"{_NS_MAIN}sheet"):
        target = targets.get(sheet.get(rel_attr), "").lstrip("/")
        path = target if target.startswith("xl/") else f"xl/{target}"
        root = ET.fromstring(_member(zf, path))  # noqa: S314
        rows: list[list[str]] = []
        for row in root.iter(f"{_NS_MAIN}row"):
            values: list[str] = []
            for c in row.iter(f"{_NS_MAIN}c"):
                col = _col_index(c.get("r", ""))
                while len(values) < col:
                    values.append("")
                kind = c.get("t")
                v = c.find(f"{_NS_MAIN}v")
                if kind == "s" and v is not None and v.text and v.text.isdigit():
                    idx = int(v.text)
                    values.append(shared[idx] if idx < len(shared) else "")
                elif kind == "inlineStr":
                    values.append("".join(t.text or "" for t in c.iter(f"{_NS_MAIN}t")))
                else:
                    values.append(v.text if v is not None and v.text else "")
            rows.append(values)
            if len(rows) > MAX_SHEET_ROWS:
                raise DocumentError("sheet has too many rows")
        sheets[sheet.get("name") or f"Sheet{len(sheets) + 1}"] = rows
    if not sheets:
        raise DocumentError("workbook has no sheets")
    return sheets


def rows_to_csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode()


# ------------------------------------------------------------------ language / script
_SCRIPTS = {
    "zh": ("CJK",),
    "ja": ("CJK", "HIRAGANA", "KATAKANA"),
    "ko": ("HANGUL",),
    "ru": ("CYRILLIC",),
    "uk": ("CYRILLIC",),
    "ar": ("ARABIC",),
    "he": ("HEBREW",),
    "el": ("GREEK",),
    "th": ("THAI",),
    "hi": ("DEVANAGARI",),
}
_LATIN = {
    "en",
    "fr",
    "de",
    "es",
    "it",
    "pt",
    "nl",
    "sv",
    "da",
    "no",
    "fi",
    "pl",
    "cs",
    "tr",
    "vi",
    "id",
    "ms",
    "ro",
    "hu",
}


def script_ratio(text: str, language: str) -> float | None:
    """Share of letters written in the target language's script; None if unknown."""
    base = language.split("-")[0].lower()
    if base in _LATIN:
        wanted: tuple[str, ...] = ("LATIN",)
    elif base in _SCRIPTS:
        wanted = _SCRIPTS[base]
    else:
        return None
    letters = hits = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        name = unicodedata.name(ch, "")
        if any(name.startswith(w) for w in wanted):
            hits += 1
    return hits / letters if letters else None


# ------------------------------------------------------------------ images / svg
def png_size(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        raise DocumentError("not a PNG image")
    width, height = struct.unpack(">II", data[16:24])
    if not (0 < width <= 10000 and 0 < height <= 10000):
        raise DocumentError("PNG has implausible dimensions")
    return width, height


def check_svg(data: bytes) -> None:
    import xml.etree.ElementTree as ET  # noqa: S405 - DTD/entities refused below

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentError("SVG is not UTF-8") from exc
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise DocumentError("SVG with DTD/entities is not accepted")
    try:
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError as exc:
        raise DocumentError("SVG is not well-formed") from exc
    if not root.tag.endswith("svg"):
        raise DocumentError("not an SVG document")
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in ("script", "foreignobject") or any(
            a.lower().startswith("on") or "javascript:" in (v or "").lower()
            for a, v in el.attrib.items()
        ):
            raise DocumentError("SVG contains active content")


# ------------------------------------------------------------------ structural checks
@dataclass
class CheckReport:
    checks: list[dict] = field(default_factory=list)
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None

    def check(self, name: str, ok: bool, detail=None, reason: str | None = None) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok and self.reason is None:
            self.reason = reason or name.upper()
        return bool(ok)


@dataclass(frozen=True)
class FileIn:
    name: str
    content: bytes


def _common(kind: str, files: list[FileIn], report: CheckReport) -> None:
    allowed = KIND_OUTPUTS[kind]
    names = [f.name for f in files]
    report.check("deliverables_present", bool(files), names, "NO_DELIVERABLE")
    report.check("deliverable_count", len(files) <= MAX_DELIVERABLES, len(files), "TOO_MANY_FILES")
    report.check("unique_names", len(set(names)) == len(names), names, "DUPLICATE_NAMES")
    bad = [n for n in names if extension(n) not in allowed or safe_name(n) != n]
    report.check("allowed_types", not bad, bad, "FILE_TYPE_NOT_ALLOWED")
    big = [f.name for f in files if len(f.content) > MAX_DELIVERABLE_BYTES]
    report.check("size_limit", not big, big, "FILE_TOO_LARGE")
    total = sum(len(f.content) for f in files)
    report.check("total_size_limit", total <= MAX_TOTAL_DELIVERABLE_BYTES, total, "FILES_TOO_LARGE")


def check_translation(
    files: list[FileIn],
    source: FileIn,
    target_language: str,
    deliverable: dict[str, str],
) -> CheckReport:
    report = CheckReport()
    _common("TRANSLATE_DOCUMENT", files, report)
    if not report.ok:
        return report
    main = next((f for f in files if f.name == deliverable["filename"]), None)
    if not report.check(
        "expected_file", main is not None, deliverable["filename"], "MISSING_OUTPUT"
    ):
        return report
    try:
        src = source_paragraphs(source.name, source.content)
        out = (
            docx_paragraphs(main.content)
            if deliverable["format"] == "docx"
            else text_paragraphs(main.content)
        )
        report.check("well_formed", True)
    except DocumentError as exc:
        report.check("well_formed", False, str(exc), "MALFORMED_OUTPUT")
        return report
    if not report.check("has_text", bool(out), len(out), "EMPTY_OUTPUT"):
        return report
    ratio = len(out) / max(len(src), 1)
    report.check(
        "paragraph_count",
        0.8 <= ratio <= 1.2,
        {"source": len(src), "output": len(out)},
        "PARAGRAPH_COUNT_MISMATCH",
    )
    text = "\n".join(out)
    share = script_ratio(text, target_language)
    report.check(
        "target_script",
        share is None or share >= 0.5,
        {"language": target_language, "share": None if share is None else round(share, 3)},
        "WRONG_LANGUAGE",
    )
    same = sum(
        1
        for a, b in zip(src, out, strict=False)
        if a.strip() == b.strip() and re.search(r"[^\W\d_]{3,}", a)
    )
    report.check(
        "translated",
        same <= max(1, len(src) // 2),
        {"unchanged_paragraphs": same},
        "NOT_TRANSLATED",
    )
    return report


def check_analysis(files: list[FileIn], input_names: list[str]) -> CheckReport:
    report = CheckReport()
    _common("ANALYZE_SPREADSHEET", files, report)
    if not report.ok:
        return report
    main = next((f for f in files if f.name == "report.md"), None)
    if not report.check("expected_file", main is not None, "report.md", "MISSING_OUTPUT"):
        return report
    try:
        text = main.content.decode("utf-8")
    except UnicodeDecodeError:
        report.check("well_formed", False, "report.md is not UTF-8", "MALFORMED_OUTPUT")
        return report
    report.check("report_length", len(text.strip()) >= 200, len(text.strip()), "REPORT_TOO_SHORT")
    stems = {n.rsplit(".", 1)[0].lower() for n in input_names} | {n.lower() for n in input_names}
    report.check(
        "cites_inputs",
        any(s and s in text.lower() for s in stems),
        sorted(stems),
        "REPORT_DOES_NOT_CITE_INPUTS",
    )
    for f in files:
        if f is main:
            continue
        try:
            ext = extension(f.name)
            if ext == "png":
                png_size(f.content)
            elif ext == "svg":
                check_svg(f.content)
            elif ext == "csv":
                rows = list(csv.reader(io.StringIO(f.content.decode("utf-8"))))
                if not rows:
                    raise DocumentError("empty CSV")
            elif ext == "xlsx":
                xlsx_sheets(f.content)
            elif ext == "md":
                f.content.decode("utf-8")
            report.check(f"valid:{f.name}", True)
        except (DocumentError, UnicodeDecodeError, csv.Error) as exc:
            report.check(f"valid:{f.name}", False, str(exc)[:200], "MALFORMED_OUTPUT")
    return report


def _check_file(f: FileIn) -> None:
    """Raise DocumentError/UnicodeDecodeError/csv.Error unless `f` is well-formed."""
    ext = extension(f.name)
    if ext == "png":
        png_size(f.content)
    elif ext == "svg":
        check_svg(f.content)
    elif ext == "csv":
        if not list(csv.reader(io.StringIO(f.content.decode("utf-8")))):
            raise DocumentError("empty CSV")
    elif ext == "xlsx":
        xlsx_sheets(f.content)
    elif ext == "docx":
        docx_paragraphs(f.content)
    elif ext == "json":
        try:
            json.loads(f.content.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DocumentError("JSON is not well-formed") from exc
    elif ext == "pdf":
        if not f.content.startswith(b"%PDF-") or b"%%EOF" not in f.content[-2048:]:
            raise DocumentError("not a complete PDF")
    else:  # md, txt, patch and source files are UTF-8 text
        f.content.decode("utf-8")


def check_general(files: list[FileIn]) -> CheckReport:
    """A general task always answers in answer.md; any other file must be well-formed.
    Nothing here judges whether the answer is right."""
    report = CheckReport()
    _common("GENERAL", files, report)
    if not report.ok:
        return report
    main = next((f for f in files if f.name == GENERAL_ANSWER), None)
    if not report.check("expected_file", main is not None, GENERAL_ANSWER, "MISSING_OUTPUT"):
        return report
    try:
        text = main.content.decode("utf-8")
    except UnicodeDecodeError:
        report.check("well_formed", False, "answer.md is not UTF-8", "MALFORMED_OUTPUT")
        return report
    report.check("answer_length", len(text.strip()) >= 20, len(text.strip()), "EMPTY_OUTPUT")
    for f in files:
        if f is main:
            continue
        try:
            _check_file(f)
            report.check(f"valid:{f.name}", True)
        except (DocumentError, UnicodeDecodeError, csv.Error) as exc:
            report.check(f"valid:{f.name}", False, str(exc)[:200], "MALFORMED_OUTPUT")
    return report


def parse_translation_json(data: bytes, expected: int) -> list[str]:
    """The device's out/translation.json: a JSON array of strings, one per paragraph."""
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DocumentError("translation.json is not valid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DocumentError("translation.json must be an array of strings")
    if len(value) != expected:
        raise DocumentError(f"expected {expected} paragraphs, got {len(value)}")
    return value
