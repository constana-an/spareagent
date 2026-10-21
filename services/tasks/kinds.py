"""Task kind catalogue (docs/GENERAL_TASKS_DESIGN.md §2).

Every task names a kind. The kind decides which inputs are accepted, whether a repository
is required, the default deliverable, the device capabilities needed and how the member
UI describes it. The catalogue lives in code; the opening status of each kind lives in
the `task_kinds` table (operator-controlled, audited) and is enforced by the dispatcher
and by a database trigger: only OPEN kinds run LIVE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_ATTACHMENTS = 5
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_TASK_INPUT_BYTES = 30 * 1024 * 1024
UPLOAD_TTL_HOURS = 24

# Extension -> (media type, magic-prefix check). Executables, archives and macro-enabled
# Office files are never accepted.
_ZIP = (b"PK\x03\x04",)
UPLOAD_TYPES: dict[str, tuple[str, tuple[bytes, ...] | None]] = {
    "txt": ("text/plain", None),
    "md": ("text/markdown", None),
    "csv": ("text/csv", None),
    "json": ("application/json", None),
    "pdf": ("application/pdf", (b"%PDF-",)),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", _ZIP),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _ZIP),
    "png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}
# Source code and config as UTF-8 text, for general tasks ("change this file").
CODE_TEXT_EXTENSIONS = frozenset(
    {
        "py",
        "js",
        "ts",
        "tsx",
        "jsx",
        "html",
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
        "patch",
        "diff",
    }
)
UPLOAD_TYPES.update({ext: ("text/plain", None) for ext in CODE_TEXT_EXTENSIONS})
TEXT_EXTENSIONS = frozenset({"txt", "md", "csv", "json"}) | CODE_TEXT_EXTENSIONS

# Tools a device can declare (from its own probes) and an owner can allow (design §5).
TOOLS = ("code", "doc", "git", "python-data")
# Nodes registered before step C declare no tools; they have always run code kinds.
LEGACY_NODE_TOOLS = frozenset({"code", "git"})

LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


@dataclass(frozen=True)
class Param:
    name: str
    required: bool
    pattern: re.Pattern[str] | None = None
    max_length: int = 100


@dataclass(frozen=True)
class Deliverable:
    format: str
    filename: str


@dataclass(frozen=True)
class Kind:
    kind: str
    category: str
    name_zh: str
    name_en: str
    description_zh: str
    needs_repository: bool
    capabilities: tuple[str, ...]
    deliverable: Deliverable
    min_attachments: int = 0
    max_attachments: int = 0
    extensions: frozenset[str] = frozenset()
    params: tuple[Param, ...] = ()
    deliverable_formats: frozenset[str] = field(default_factory=frozenset)
    # Kinds offered to one-click sharing groups. Legacy marketplace kinds are not.
    experimental: bool = True


CATALOG: dict[str, Kind] = {
    k.kind: k
    for k in (
        Kind(
            "CODE_REVIEW",
            "CODE",
            "代码审查",
            "Code review",
            "阅读固定版本的公开仓库，给出问题与改进建议。",
            needs_repository=True,
            capabilities=("git", "code"),
            deliverable=Deliverable("md", "review.md"),
        ),
        Kind(
            "CODE_FIX",
            "CODE",
            "修复问题",
            "Fix a bug",
            "在固定版本的仓库上修复描述的问题，交付补丁。",
            needs_repository=True,
            capabilities=("git", "code"),
            deliverable=Deliverable("patch", "changes.patch"),
        ),
        Kind(
            "WRITE_TESTS",
            "CODE",
            "补充测试",
            "Write tests",
            "为固定版本的仓库补充测试，交付补丁。",
            needs_repository=True,
            capabilities=("git", "code"),
            deliverable=Deliverable("patch", "changes.patch"),
        ),
        Kind(
            "REFACTOR",
            "CODE",
            "重构",
            "Refactor",
            "公开市场的重构任务。",
            needs_repository=True,
            capabilities=("git", "code"),
            deliverable=Deliverable("patch", "changes.patch"),
            experimental=False,
        ),
        Kind(
            "CODE_CHANGE",
            "CODE",
            "修改代码",
            "Change code",
            "按需求修改固定版本的仓库：写代码、修问题或补测试，交付补丁和测试输出。",
            needs_repository=True,
            capabilities=("git", "code"),
            deliverable=Deliverable("patch", "changes.patch"),
        ),
        Kind(
            "TRANSLATE_DOCUMENT",
            "WRITING",
            "翻译文档",
            "Translate a document",
            "把一份 Word、Markdown 或纯文本文档翻译成目标语言，尽量保留原有格式。",
            needs_repository=False,
            capabilities=("doc",),
            deliverable=Deliverable("docx", "translation.docx"),
            min_attachments=1,
            max_attachments=1,
            # PDF is not offered: the device has no dependency-free PDF text reader yet.
            extensions=frozenset({"docx", "md", "txt"}),
            params=(Param("target_language", True, LANGUAGE, 16),),
            deliverable_formats=frozenset({"docx", "md", "txt"}),
        ),
        Kind(
            "GENERAL",
            "GENERAL",
            "通用任务",
            "General task",
            "讨论问题、写作、翻译、整理表格、修改代码文件、用代码画图（SVG/PNG 图表、示意图）等。"
            "不联网，只能使用你上传的材料。",
            needs_repository=False,
            capabilities=("doc",),
            deliverable=Deliverable("md", "answer.md"),
            min_attachments=0,
            max_attachments=MAX_ATTACHMENTS,
            extensions=frozenset(UPLOAD_TYPES),
        ),
        Kind(
            "ANALYZE_SPREADSHEET",
            "DATA",
            "分析表格",
            "Analyze spreadsheets",
            "清洗、计算并分析表格，交付分析报告、图表和整理后的表格。",
            needs_repository=False,
            capabilities=("python-data",),
            deliverable=Deliverable("md", "report.md"),
            min_attachments=1,
            max_attachments=5,
            extensions=frozenset({"csv", "xlsx"}),
        ),
    )
}


class KindError(ValueError):
    """Input that does not fit the kind; `code` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def get(kind: str) -> Kind:
    try:
        return CATALOG[kind]
    except KeyError:
        raise KindError("TASK_KIND_UNKNOWN", f"Unknown task kind {kind!r}") from None


def experimental_kinds() -> list[str]:
    return [k.kind for k in CATALOG.values() if k.experimental]


def validate_params(kind: Kind, params: dict[str, str]) -> dict[str, str]:
    known = {p.name: p for p in kind.params}
    unknown = set(params) - set(known)
    if unknown:
        raise KindError("TASK_PARAM_UNKNOWN", f"Unknown parameter(s): {sorted(unknown)}")
    clean: dict[str, str] = {}
    for p in kind.params:
        value = params.get(p.name)
        if value is None:
            if p.required:
                raise KindError("TASK_PARAM_REQUIRED", f"Parameter {p.name!r} is required")
            continue
        if not isinstance(value, str) or not value or len(value) > p.max_length:
            raise KindError("TASK_PARAM_INVALID", f"Parameter {p.name!r} is invalid")
        if p.pattern is not None and not p.pattern.fullmatch(value):
            raise KindError("TASK_PARAM_INVALID", f"Parameter {p.name!r} is invalid")
        clean[p.name] = value
    return clean


def resolve_deliverable(kind: Kind, requested: dict[str, str] | None) -> dict[str, str]:
    """The kind's default deliverable, optionally with another allowed format."""
    fmt = (requested or {}).get("format") or kind.deliverable.format
    if fmt != kind.deliverable.format and fmt not in kind.deliverable_formats:
        raise KindError("DELIVERABLE_FORMAT_INVALID", f"Format {fmt!r} is not offered")
    stem = kind.deliverable.filename.rsplit(".", 1)[0]
    return {"format": fmt, "filename": f"{stem}.{fmt}"}


def check_attachment_count(kind: Kind, count: int) -> None:
    if count < kind.min_attachments or count > kind.max_attachments:
        if kind.max_attachments == 0:
            raise KindError("ATTACHMENTS_NOT_ACCEPTED", "This kind takes no attachments")
        raise KindError(
            "ATTACHMENT_COUNT_INVALID",
            f"This kind needs {kind.min_attachments}-{kind.max_attachments} attachment(s)",
        )


def check_attachment_types(kind: Kind, extensions: list[str]) -> None:
    bad = [e for e in extensions if e not in kind.extensions]
    if bad:
        raise KindError(
            "ATTACHMENT_TYPE_NOT_ACCEPTED",
            f"This kind accepts {sorted(kind.extensions)}, not {bad}",
        )


def sniff_upload(filename: str, content: bytes) -> tuple[str, str]:
    """(extension, media type) for an allowed upload; raises KindError otherwise."""
    name = filename.strip()
    if not name or len(name) > 200 or "/" in name or "\\" in name or "\x00" in name:
        raise KindError("UPLOAD_NAME_INVALID", "Invalid file name")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in UPLOAD_TYPES:
        raise KindError("UPLOAD_TYPE_NOT_ACCEPTED", f"Files of type .{ext} are not accepted")
    if not content:
        raise KindError("UPLOAD_EMPTY", "The file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise KindError("UPLOAD_TOO_LARGE", "The file is larger than 20 MB")
    media, magic = UPLOAD_TYPES[ext]
    if magic is not None and not any(content.startswith(m) for m in magic):
        raise KindError("UPLOAD_CONTENT_MISMATCH", f"The content is not a .{ext} file")
    if ext in TEXT_EXTENSIONS:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise KindError("UPLOAD_CONTENT_MISMATCH", "Text files must be UTF-8") from None
    if ext in ("docx", "xlsx") and b"vbaProject.bin" in content:
        raise KindError("UPLOAD_MACROS_NOT_ACCEPTED", "Files with macros are not accepted")
    return ext, media
