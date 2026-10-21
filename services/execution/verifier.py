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


def verify(
    artifacts: tuple[ArtifactPayload, ...], task_type: str = "CODE_FIX"
) -> VerificationReport:
    from packages.common.deliverables import DOCUMENT_KINDS

    if task_type in DOCUMENT_KINDS:
        return _verify_document_submission(task_type, artifacts)
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


def verify_codex_live(task_type: str, artifacts: tuple[ArtifactPayload, ...]) -> VerificationReport:
    """W4 Milestone D: platform checks for a LIVE experimental result from an owner's Codex
    CLI. The repository is not re-run by the platform (only catalog repositories can be),
    so this is a structural gate: a SUMMARY always, and for WRITE_TESTS/CODE_FIX a PATCH
    that passes the same patch policy used for sandboxed CODE tasks."""
    from packages.common import deliverables as d
    from services.execution.patch_policy import PatchRejected, inspect_patch

    if task_type in d.DOCUMENT_KINDS:
        return _verify_document_submission(task_type, artifacts)
    kinds = [a.kind for a in artifacts]
    if len(kinds) != len(set(kinds)):
        return VerificationReport(False, "duplicate artifact kinds")
    if not set(kinds) <= {"SUMMARY", "PATCH", "LOG"}:
        return VerificationReport(False, "unexpected artifact kinds")
    if "SUMMARY" not in kinds:
        return VerificationReport(False, "missing artifacts: ['SUMMARY']")
    patch = next((a for a in artifacts if a.kind == "PATCH"), None)
    if task_type in ("WRITE_TESTS", "CODE_FIX", "CODE_CHANGE") and patch is None:
        return VerificationReport(False, "missing artifacts: ['PATCH']")
    if task_type == "CODE_REVIEW" and patch is not None:
        return VerificationReport(False, "a review must not modify the repository")
    for artifact in artifacts:
        if len(artifact.content) > MAX_ARTIFACT_BYTES:
            return VerificationReport(False, f"{artifact.kind} exceeds size limit")
    if len({a.content for a in artifacts}) != len(artifacts):
        return VerificationReport(False, "duplicate artifact content")
    if patch is not None:
        try:
            inspect_patch(patch.content)
        except PatchRejected as exc:
            return VerificationReport(False, f"patch rejected: {exc.reason}")
    return VerificationReport(True)


def _verify_document_submission(
    task_type: str, artifacts: tuple[ArtifactPayload, ...]
) -> VerificationReport:
    """Structural gate at submission for document kinds (translation, analysis); the
    platform verifier then checks the deliverables' content structure."""
    from packages.common import deliverables as d

    singles = [a.kind for a in artifacts if a.kind != "DELIVERABLE"]
    if len(singles) != len(set(singles)):
        return VerificationReport(False, "duplicate artifact kinds")
    if not set(singles) <= {"SUMMARY", "LOG"}:
        return VerificationReport(False, "unexpected artifact kinds")
    if "SUMMARY" not in singles:
        return VerificationReport(False, "missing artifacts: ['SUMMARY']")
    files = [a for a in artifacts if a.kind == "DELIVERABLE"]
    if not files:
        return VerificationReport(False, "missing artifacts: ['DELIVERABLE']")
    if len(files) > d.MAX_DELIVERABLES:
        return VerificationReport(False, "too many deliverables")
    names = [a.name for a in files]
    if len(set(names)) != len(names):
        return VerificationReport(False, "duplicate deliverable names")
    for a in files:
        ext = d.extension(a.name)
        if (
            d.safe_name(a.name) != a.name
            or ext not in d.KIND_OUTPUTS[task_type]
            or a.media_type != d.MEDIA_TYPES[ext]
        ):
            return VerificationReport(False, f"deliverable {a.name!r} has a disallowed type")
    for a in artifacts:
        if len(a.content) > MAX_ARTIFACT_BYTES:
            return VerificationReport(False, f"{a.kind} exceeds size limit")
    if len({a.content for a in artifacts}) != len(artifacts):
        return VerificationReport(False, "duplicate artifact content")
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
