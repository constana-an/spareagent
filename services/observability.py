"""Structured JSON logging with mandatory secret redaction and request correlation."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

REDACTED = "[REDACTED]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{8,}=*"),
)
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)(\s*[:=]\s*)([\"']?)[^\s\"',;]{4,}"
)

_RESERVED_LOG_ATTRS = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def redact(text: str) -> str:
    """Replace anything that looks like a credential with a fixed marker."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _KEY_VALUE_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(v) for v in value]
    return value


class RedactingFilter(logging.Filter):
    """Redacts the rendered message and extra fields before any handler sees them."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # noqa: BLE001 - malformed format args must not crash logging
            rendered = str(record.msg)
        record.msg = redact(rendered)
        record.args = None
        for key, value in list(vars(record).items()):
            if key not in _RESERVED_LOG_ATTRS:
                setattr(record, key, redact_value(value))
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for key, value in vars(record).items():
            if key not in _RESERVED_LOG_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        if getattr(handler, "_spareagent", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    handler._spareagent = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
