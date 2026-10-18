"""Week 1 deterministic verifier for mock artifacts (real verification is Week 3)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from packages.provider_sdk.types import ArtifactPayload

MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
REQUIRED_KINDS = frozenset({"SUMMARY", "PATCH"})


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    reason: str | None = None


def verify(artifacts: tuple[ArtifactPayload, ...]) -> VerificationReport:
    kinds = {a.kind for a in artifacts}
    missing = REQUIRED_KINDS - kinds
    if missing:
        return VerificationReport(False, f"missing artifacts: {sorted(missing)}")
    for artifact in artifacts:
        if len(artifact.content) > MAX_ARTIFACT_BYTES:
            return VerificationReport(False, f"{artifact.kind} exceeds size limit")
        if artifact.kind == "PATCH" and not artifact.content.startswith(b"diff --git "):
            return VerificationReport(False, "PATCH is not a unified git diff")
        if artifact.kind == "TEST_REPORT":
            try:
                report = json.loads(artifact.content)
            except ValueError:
                return VerificationReport(False, "TEST_REPORT is not valid JSON")
            if report.get("failed", 0) != 0:
                return VerificationReport(False, "TEST_REPORT contains failures")
    return VerificationReport(True)


CODE_REQUIRED_KINDS = frozenset({"SUMMARY", "PATCH", "TEST_REPORT", "EXECUTION_LOG"})
WORKER_KINDS = frozenset({"SUMMARY", "PATCH", "TEST_REPORT", "EXECUTION_LOG", "LOG"})


def verify_code_submission(artifacts: tuple[ArtifactPayload, ...]) -> VerificationReport:
    """Cheap structural gate at submission time. NOT verification: the platform verifier
    re-runs everything in a fresh sandbox before a CODE task can succeed."""
    kinds = [a.kind for a in artifacts]
    if len(kinds) != len(set(kinds)):
        return VerificationReport(False, "duplicate artifact kinds")
    if not set(kinds) <= WORKER_KINDS:
        return VerificationReport(
            False, f"artifact kinds not allowed: {sorted(set(kinds) - WORKER_KINDS)}"
        )
    missing = CODE_REQUIRED_KINDS - set(kinds)
    if missing:
        return VerificationReport(False, f"missing artifacts: {sorted(missing)}")
    # Storage is content-addressed per task: two kinds with identical bytes would share one
    # row, so such submissions are refused explicitly instead of being merged silently.
    if len({a.content for a in artifacts}) != len(artifacts):
        return VerificationReport(False, "duplicate artifact content")
    for artifact in artifacts:
        if len(artifact.content) > MAX_ARTIFACT_BYTES:
            return VerificationReport(False, f"{artifact.kind} exceeds size limit")
    return VerificationReport(True)
