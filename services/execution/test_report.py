"""Trusted parsing of test-runner output into a TEST_REPORT document (host side).

The report is derived from the exit code and the runner's summary lines; repository code
cannot choose the report format. Schema version 1 fields are validated by `validate_report`.
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = 1
REQUIRED = {
    "schema_version": int,
    "framework": str,
    "base_commit": str,
    "exit_code": int,
    "passed": int,
    "failed": int,
    "total": int,
    "ok": bool,
    "timed_out": bool,
}


class ReportInvalid(Exception):
    pass


def parse_output(framework: str, exit_code: int, stdout: str, stderr: str) -> dict[str, int]:
    text = f"{stdout}\n{stderr}"
    if framework == "unittest":
        ran = re.search(r"^Ran (\d+) tests?", text, re.MULTILINE)
        total = int(ran.group(1)) if ran else 0
        failed = 0
        m = re.search(r"^FAILED \((.*)\)$", text, re.MULTILINE)
        if m:
            for key in ("failures", "errors"):
                k = re.search(rf"{key}=(\d+)", m.group(1))
                failed += int(k.group(1)) if k else 0
        skipped = re.search(r"skipped=(\d+)", text)
        passed = max(0, total - failed - (int(skipped.group(1)) if skipped else 0))
        return {"total": total, "passed": passed, "failed": failed}
    if framework == "node-tap":

        def count(name: str) -> int:
            m = re.search(rf"^# {name} (\d+)$", text, re.MULTILINE)
            return int(m.group(1)) if m else 0

        return {"total": count("tests"), "passed": count("pass"), "failed": count("fail")}
    raise ReportInvalid(f"unknown framework {framework}")


def build_report(
    *,
    framework: str,
    base_commit: str,
    command: list[str],
    exit_code: int,
    stdout: str,
    stderr: str,
    timed_out: bool,
    duration_ms: int,
) -> dict[str, Any]:
    counts = parse_output(framework, exit_code, stdout, stderr)
    return {
        "schema_version": SCHEMA_VERSION,
        "framework": framework,
        "base_commit": base_commit,
        "command": list(command),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        **counts,
        "ok": exit_code == 0 and not timed_out and counts["failed"] == 0 and counts["total"] > 0,
    }


def validate_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ReportInvalid("report must be a JSON object")
    for key, typ in REQUIRED.items():
        value = report.get(key)
        if not isinstance(value, typ) or (typ is int and isinstance(value, bool)):
            raise ReportInvalid(f"field {key} missing or not {typ.__name__}")
    if report["schema_version"] != SCHEMA_VERSION:
        raise ReportInvalid("unsupported schema_version")
    if min(report["passed"], report["failed"], report["total"]) < 0:
        raise ReportInvalid("negative counts")
    if report["passed"] + report["failed"] > report["total"]:
        raise ReportInvalid("inconsistent counts")
    return report
