"""Independent platform verification of CODE task submissions (Week 3).

Runs on platform-controlled infrastructure (the `verifier` process), never on the node that
produced the result. A worker's self-reported success is input to be checked, not trusted:

 1. claim one VERIFYING attempt (row lock, SKIP LOCKED; crash-safe claim lease)
 2. load the worker's unpublished artifacts; the store re-checks SHA-256 integrity
 3. validate artifact schema and the TEST_REPORT document, incl. its base commit
 4. statically vet the patch (paths, modes, allowed files, size)
 5. fresh RepositoryManager checkout of the task's pinned commit (HEAD verified)
 6. `git apply --check` + `git apply` on the clean tree
 7. run the catalog's TRUSTED verification command in a fresh sandbox with independent limits
 8. compare the actual result with the recipe's expectations and with the worker's claim
 9. record `verification_results` + a platform-sourced VERIFICATION_REPORT artifact, then
    SUCCEEDED (publish artifacts) / RETRY_PENDING / FAILED / CANCELLED in one transaction.

Infrastructure faults (sandbox/daemon/checkout timeouts) are ERROR and retried up to
`verification_max_errors`; they never count as a pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Artifact, Task, TaskAttempt, VerificationResult
from packages.provider_sdk.types import ArtifactPayload
from services.context import AppContext
from services.execution import catalog as catalog_mod
from services.execution.patch_policy import PatchRejected, inspect_patch
from services.execution.repository_manager import RepositoryError, RepositoryManager
from services.execution.sandbox import DockerSandbox, SandboxError
from services.execution.test_report import ReportInvalid, build_report, validate_report
from services.experiments.safety import enforce_live_authority
from services.scheduler.lease_service import (
    close_attempt,
    store_artifacts,
    task_deadline_passed,
    utcnow,
)
from services.tasks import repository as repo
from services.tasks.state_machine import TaskStatus, transition

log = logging.getLogger(__name__)
VERSION = "spareagent-verifier/2"


@dataclass
class Job:
    task_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_number: int
    repository_url: str | None
    commit_sha: str | None
    execution_profile: str = "CODE"
    task_type: str = "CODE_FIX"


@dataclass
class Outcome:
    status: str  # PASSED | FAILED | ERROR | UNVERIFIED
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    worker_reported: dict[str, Any] = field(default_factory=dict)
    verified: dict[str, Any] = field(default_factory=dict)
    patch_sha256: str | None = None

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"check": name, "passed": ok, "detail": detail})
        return ok


class VerificationService:
    def __init__(
        self,
        ctx: AppContext,
        repositories: RepositoryManager,
        sandbox: DockerSandbox,
        *,
        catalog: tuple[catalog_mod.Recipe, ...] = catalog_mod.CATALOG,
        verifier_id: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.repositories = repositories
        self.sandbox = sandbox
        self.catalog = catalog
        self.verifier_id = verifier_id or f"verifier-{uuid.uuid4().hex[:8]}"

    # ---------------------------------------------------------------- claim
    async def claim(self) -> Job | None:
        s = self.ctx.settings
        now = utcnow()
        async with self.ctx.sessionmaker() as session, session.begin():
            rows = (
                await session.execute(
                    select(TaskAttempt, Task, VerificationResult)
                    .join(Task, Task.id == TaskAttempt.task_id)
                    .outerjoin(VerificationResult, VerificationResult.attempt_id == TaskAttempt.id)
                    .where(
                        TaskAttempt.status == "VERIFYING",
                        Task.execution_profile.in_(("CODE", "CODEX_LIVE")),
                        Task.status.in_(("VERIFYING", "CANCELLING")),
                    )
                    .order_by(TaskAttempt.completed_at, TaskAttempt.id)
                    .limit(20)
                    .with_for_update(of=TaskAttempt, skip_locked=True)
                )
            ).all()
            for attempt, task, result in rows:
                if result is not None and result.status in ("PASSED", "FAILED", "UNVERIFIED"):
                    continue
                if result is not None and result.status == "RUNNING":
                    if result.claimed_until and result.claimed_until > now:
                        continue  # another verifier holds a live claim
                values = {
                    "task_id": task.id,
                    "attempt_id": attempt.id,
                    "status": "RUNNING",
                    "base_commit_sha": task.commit_sha.strip() if task.commit_sha else None,
                    "verifier_id": self.verifier_id,
                    "claimed_until": now + timedelta(seconds=s.verification_claim_seconds),
                    "started_at": now,
                }
                await session.execute(
                    pg_insert(VerificationResult)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["attempt_id"],
                        set_={k: values[k] for k in ("status", "verifier_id", "claimed_until")},
                    )
                )
                return Job(
                    task.id,
                    attempt.id,
                    attempt.attempt_number,
                    task.repository_url,
                    task.commit_sha.strip() if task.commit_sha else None,
                    task.execution_profile,
                    task.task_type,
                )
        return None

    # ---------------------------------------------------------------- verify (no DB tx)
    async def _load_artifacts(self, attempt_id: uuid.UUID) -> dict[str, ArtifactPayload]:
        async with self.ctx.sessionmaker() as session:
            rows = list(
                (
                    await session.execute(
                        select(Artifact).where(
                            Artifact.attempt_id == attempt_id, Artifact.source == "WORKER"
                        )
                    )
                ).scalars()
            )
        out = {}
        for row in rows:
            if row.kind == "DELIVERABLE":
                continue  # several per attempt; see _load_deliverables
            content = await self.ctx.artifacts.get(row.storage_key)  # integrity-checked
            if hashlib.sha256(content).hexdigest() != row.sha256.strip() or row.kind in out:
                raise ValueError("Artifact metadata integrity failure")
            out[row.kind] = ArtifactPayload(row.kind, row.kind.lower(), row.media_type, content)
        return out

    async def _load_deliverables(self, attempt_id: uuid.UUID) -> list[ArtifactPayload]:
        async with self.ctx.sessionmaker() as session:
            rows = list(
                (
                    await session.execute(
                        select(Artifact)
                        .where(
                            Artifact.attempt_id == attempt_id,
                            Artifact.source == "WORKER",
                            Artifact.kind == "DELIVERABLE",
                        )
                        .order_by(Artifact.name)
                    )
                ).scalars()
            )
        out = []
        for row in rows:
            content = await self.ctx.artifacts.get(row.storage_key)  # integrity-checked
            if hashlib.sha256(content).hexdigest() != row.sha256.strip():
                raise ValueError("Artifact metadata integrity failure")
            out.append(ArtifactPayload("DELIVERABLE", row.name, row.media_type, content))
        return out

    async def _verify_document(self, job: Job, out: Outcome) -> Outcome:
        """Translation / analysis: structural checks of the deliverables against the
        task's inputs and parameters. Content quality is never claimed (UNVERIFIED)."""
        from packages.common import deliverables as d
        from services.execution.verifier import verify_codex_live
        from services.tasks.uploads import UploadService

        try:
            singles = await self._load_artifacts(job.attempt_id)
            files = await self._load_deliverables(job.attempt_id)
            async with self.ctx.sessionmaker() as session:
                task = await session.get(Task, job.task_id)
                uploads = (await UploadService.for_tasks(session, [job.task_id]))[job.task_id]
            inputs = [
                d.FileIn(u.filename, await self.ctx.artifacts.get(u.storage_key)) for u in uploads
            ]
        except Exception as exc:  # noqa: BLE001 - integrity failure or store error
            out.check("artifact_integrity", False, type(exc).__name__)
            out.reason = "ARTIFACT_INTEGRITY"
            return out
        out.check("artifact_integrity", True)
        structure = verify_codex_live(job.task_type, (*singles.values(), *files))
        if not out.check("artifact_schema", structure.ok, structure.reason):
            out.reason = "INVALID_LIVE_ARTIFACTS"
            return out
        spec = task.spec or {}
        delivered = [d.FileIn(f.name, f.content) for f in files]
        if job.task_type == "TRANSLATE_DOCUMENT":
            if not out.check("source_present", len(inputs) == 1, len(inputs)):
                out.reason = "SOURCE_MISSING"
                return out
            report = d.check_translation(
                delivered,
                inputs[0],
                (spec.get("parameters") or {}).get("target_language", ""),
                spec.get("deliverable") or {"format": "docx", "filename": "translation.docx"},
            )
        else:
            report = d.check_analysis(delivered, [i.name for i in inputs])
        for c in report.checks:
            out.check(c["name"], c["ok"], c["detail"])
        out.verified = {"deliverables": [f.name for f in files]}
        if report.ok:
            out.status, out.reason = "UNVERIFIED", "STRUCTURE_CHECKED_CONTENT_NOT_REVIEWED"
        else:
            out.reason = report.reason or "DELIVERABLE_CHECK_FAILED"
        return out

    async def verify(self, job: Job) -> Outcome:
        out = Outcome("FAILED", "")
        live = job.execution_profile == "CODEX_LIVE"
        from packages.common.deliverables import DOCUMENT_KINDS

        if job.task_type in DOCUMENT_KINDS:
            return await self._verify_document(job, out)
        recipes = (
            catalog_mod.LIVE_WRITE_TESTS_CATALOG
            if live and job.task_type == "WRITE_TESTS"
            else self.catalog
        )
        recipe = catalog_mod.lookup(job.repository_url, job.commit_sha, recipes)
        out.check("repository_in_allowlist", recipe is not None)
        if recipe is None and not live:
            out.reason = "UNSUPPORTED_REPOSITORY"
            return out
        try:
            arts = await self._load_artifacts(job.attempt_id)
        except Exception as exc:  # noqa: BLE001 - integrity failure or store error
            out.check("artifact_integrity", False, type(exc).__name__)
            out.reason = "ARTIFACT_INTEGRITY"
            return out
        out.check("artifact_integrity", True)
        if live:
            from services.execution.verifier import verify_codex_live

            structure = verify_codex_live(job.task_type, tuple(arts.values()))
            if not out.check("artifact_schema", structure.ok, structure.reason):
                out.reason = "INVALID_LIVE_ARTIFACTS"
                return out
        if live and job.task_type == "CODE_REVIEW":
            out.status, out.reason = "UNVERIFIED", "REVIEW_REQUIRES_HUMAN_ASSESSMENT"
            return out
        required = (
            {"PATCH", "SUMMARY"} if live else {"PATCH", "TEST_REPORT", "SUMMARY", "EXECUTION_LOG"}
        )
        if not out.check("artifact_schema", required <= set(arts), sorted(arts)):
            out.reason = "MISSING_ARTIFACTS"
            return out
        claimed = None
        if not live:
            try:
                claimed = validate_report(json.loads(arts["TEST_REPORT"].content))
                out.worker_reported = {k: claimed[k] for k in ("ok", "passed", "failed", "total")}
                out.check("test_report_schema", True)
            except (ValueError, ReportInvalid) as exc:
                out.check("test_report_schema", False, str(exc)[:200])
                out.reason = "INVALID_TEST_REPORT"
                return out
            if not out.check("base_commit_matches", claimed["base_commit"] == job.commit_sha):
                out.reason = "BASE_COMMIT_MISMATCH"
                return out
        patch = arts["PATCH"].content
        out.patch_sha256 = hashlib.sha256(patch).hexdigest()
        try:
            summary = inspect_patch(patch, recipe.allowed_paths if recipe else None)
            out.check("patch_policy", True, list(summary.paths))
        except PatchRejected as exc:
            out.check("patch_policy", False, exc.reason)
            out.reason = "PATCH_POLICY"
            return out

        try:
            checkout = await asyncio.to_thread(
                self.repositories.checkout, job.repository_url, job.commit_sha
            )
        except RepositoryError as exc:
            out.check("clean_checkout", False, exc.code)
            out.status, out.reason = (
                ("ERROR", f"CHECKOUT_{exc.code}")
                if exc.code in ("TIMEOUT", "FETCH_FAILED")
                else ("FAILED", f"CHECKOUT_{exc.code}")
            )
            return out
        try:
            out.check("clean_checkout", True, checkout.commit_sha)
            with tempfile.NamedTemporaryFile(suffix=".patch") as fh:
                fh.write(patch)
                fh.flush()
                try:
                    await asyncio.to_thread(checkout.git, "apply", "--check", fh.name)
                    await asyncio.to_thread(checkout.git, "apply", fh.name)
                    out.check("patch_applies_cleanly", True)
                except RepositoryError as exc:
                    out.check("patch_applies_cleanly", False, exc.message[:200])
                    out.reason = "PATCH_DOES_NOT_APPLY"
                    return out
            if recipe is None:
                out.status, out.reason = "UNVERIFIED", "NO_TRUSTED_TEST_RECIPE"
                return out
            out.check("trusted_command", True, list(recipe.verify_command))
            try:
                result = await asyncio.to_thread(
                    self.sandbox.execute,
                    checkout.path,
                    list(recipe.verify_command),
                    run_id=f"verify-{job.attempt_id}",
                    timeout=self.ctx.settings.verification_timeout_seconds,
                )
            except SandboxError as exc:
                out.check("sandbox_run", False, exc.code)
                out.status, out.reason = "ERROR", f"SANDBOX_{exc.code}"
                return out
            actual = build_report(
                framework=recipe.framework,
                base_commit=job.commit_sha,
                command=list(recipe.verify_command),
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=result.timed_out,
                duration_ms=result.duration_ms,
            )
            out.verified = {
                **{k: actual[k] for k in ("ok", "passed", "failed", "total", "exit_code")},
                "timed_out": result.timed_out,
                "oom_killed": result.oom_killed,
                "sandbox_removed": result.removed,
                "duration_ms": result.duration_ms,
            }
            out.check("sandbox_cleaned_up", result.removed, result.container)
            tests_ok = (
                actual["ok"]
                and actual["passed"] == recipe.expected_passed
                and actual["failed"] == 0
            )
            out.check(
                "trusted_tests_pass",
                tests_ok,
                {"expected_passed": recipe.expected_passed, "actual": out.verified},
            )
            claim_consistent = claimed is None or (
                claimed["ok"] == actual["ok"] and claimed["passed"] == actual["passed"]
            )
            if claimed is not None:
                out.check("worker_claim_consistent", claim_consistent, out.worker_reported)
            if result.timed_out:
                out.reason = "VERIFICATION_TIMEOUT"
            elif not result.removed or result.oom_killed or result.cancelled:
                out.reason = "SANDBOX_INCOMPLETE"
            elif not tests_ok:
                out.reason = "TESTS_FAILED"
            elif not claim_consistent:
                out.reason = "WORKER_REPORT_MISMATCH"
            else:
                out.status, out.reason = "PASSED", "VERIFIED"
            return out
        finally:
            await asyncio.to_thread(checkout.cleanup)

    # ---------------------------------------------------------------- finalize
    async def finalize(self, job: Job, outcome: Outcome) -> str:
        s = self.ctx.settings
        now = utcnow()
        report = {
            "verifier": VERSION,
            "verifier_id": self.verifier_id,
            "task_id": str(job.task_id),
            "attempt_id": str(job.attempt_id),
            "base_commit": job.commit_sha,
            "execution_profile": job.execution_profile,
            "task_type": job.task_type,
            "semantic_correctness": "TRUSTED_TESTS_ONLY"
            if outcome.status == "PASSED"
            else "NOT_ESTABLISHED",
            "patch_sha256": outcome.patch_sha256,
            "status": outcome.status,
            "reason": outcome.reason,
            "worker_reported": outcome.worker_reported,
            "verified": outcome.verified,
            "checks": outcome.checks,
            "verified_at": now.isoformat(),
        }
        async with self.ctx.sessionmaker() as session, session.begin():
            task = await repo.lock_task(session, job.task_id)
            attempt = await repo.lock_attempt(session, job.attempt_id)
            result = (
                await session.execute(
                    select(VerificationResult)
                    .where(VerificationResult.attempt_id == job.attempt_id)
                    .with_for_update()
                )
            ).scalar_one()
            if (
                task is None
                or attempt is None
                or attempt.status != "VERIFYING"
                or result.verifier_id != self.verifier_id
                or result.status != "RUNNING"
                or result.claimed_until is None
                or result.claimed_until <= now
            ):
                return "STALE"
            await enforce_live_authority(self.ctx, session, task)
            if task.status == TaskStatus.CANCELLING:
                outcome.status, outcome.reason = "FAILED", "CANCELLED_BEFORE_PUBLICATION"
                report.update(
                    status=outcome.status,
                    reason=outcome.reason,
                    semantic_correctness="NOT_ESTABLISHED",
                )
            if outcome.status == "ERROR" and result.error_count + 1 < s.verification_max_errors:
                result.status = "ERROR"
                result.error_count += 1
                result.reason = outcome.reason
                result.claimed_until = now  # immediately reclaimable
                return "ERROR_RETRY"
            if outcome.status == "ERROR":
                outcome.status, outcome.reason = "FAILED", f"VERIFIER_UNAVAILABLE:{outcome.reason}"
                report["status"], report["reason"] = outcome.status, outcome.reason
            result.status = outcome.status
            result.reason = outcome.reason
            result.patch_sha256 = outcome.patch_sha256
            result.worker_reported = outcome.worker_reported
            result.verified_result = {"checks": outcome.checks, **outcome.verified}
            result.completed_at = now
            result.claimed_until = None
            await store_artifacts(
                self.ctx,
                session,
                task.id,
                attempt.id,
                (
                    ArtifactPayload(
                        "VERIFICATION_REPORT",
                        "verification.json",
                        "application/json",
                        json.dumps(report, sort_keys=True, default=str).encode(),
                    ),
                ),
                published=True,
                source="PLATFORM",
            )
            payload = {"verification": outcome.status, "reason": outcome.reason}
            if task.status == TaskStatus.CANCELLING:
                await close_attempt(session, attempt, "CANCELLED", release=False)
                await transition(
                    session,
                    task,
                    TaskStatus.CANCELLED,
                    payload={**payload, "result_summary": "Cancelled by user"},
                    attempt_id=attempt.id,
                )
                return "CANCELLED"
            if outcome.status in ("PASSED", "UNVERIFIED"):
                await session.execute(
                    update(Artifact).where(Artifact.attempt_id == attempt.id).values(published=True)
                )
                await close_attempt(session, attempt, "SUCCEEDED", release=False)
                await transition(
                    session,
                    task,
                    TaskStatus.SUCCEEDED,
                    payload={
                        **payload,
                        "result_summary": (
                            f"UNVERIFIED: {outcome.reason}"
                            if outcome.status == "UNVERIFIED"
                            else f"Verified by platform: {outcome.verified.get('passed')}/"
                            f"{outcome.verified.get('total')} trusted tests passed on "
                            f"{job.commit_sha[:12]}"
                        ),
                    },
                    attempt_id=attempt.id,
                )
                return "SUCCEEDED"
            await close_attempt(session, attempt, "FAILED", release=False)
            if attempt.attempt_number < task.max_attempts and not task_deadline_passed(
                task, now, attempt
            ):
                await transition(
                    session,
                    task,
                    TaskStatus.RETRY_PENDING,
                    payload={**payload, "attempt_number": attempt.attempt_number},
                    attempt_id=attempt.id,
                )
                return "RETRY_PENDING"
            await transition(
                session,
                task,
                TaskStatus.FAILED,
                payload={**payload, "result_summary": f"Verification failed: {outcome.reason}"},
                attempt_id=attempt.id,
            )
            return "FAILED"

    async def run_once(self) -> str | None:
        job = await self.claim()
        if job is None:
            return None
        async with self.ctx.sessionmaker() as session:
            status = (await session.get(Task, job.task_id)).status
        try:
            if status == TaskStatus.CANCELLING:
                outcome = Outcome("FAILED", "CANCELLED_BEFORE_VERIFICATION")
            else:
                outcome = await self.verify(job)
        except Exception as exc:  # noqa: BLE001 - never let a crash count as a pass
            log.exception("verification crashed", extra={"attempt_id": str(job.attempt_id)})
            outcome = Outcome("ERROR", f"VERIFIER_EXCEPTION:{type(exc).__name__}")
        result = await self.finalize(job, outcome)
        log.info(
            "verification finished",
            extra={"attempt_id": str(job.attempt_id), "status": outcome.status, "result": result},
        )
        return result


def from_settings(ctx: AppContext) -> VerificationService:
    s = ctx.settings
    from services.execution.sandbox import SandboxLimits

    return VerificationService(
        ctx,
        RepositoryManager(mirror_root=Path(s.repo_fixture_root) if s.repo_fixture_root else None),
        DockerSandbox(
            image=s.sandbox_image,
            limits=SandboxLimits(
                memory_mb=s.verification_memory_mb,
                timeout_seconds=s.verification_timeout_seconds,
            ),
        ),
    )
