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
