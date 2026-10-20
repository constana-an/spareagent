"""Deterministic local MOCK provider.

* No network calls, no subprocesses, no repository clone, no code execution.
* Output is a pure function of (task_id, attempt_number, prompt) so tests are repeatable.
* Prompt markers inject faults for tests:
    `[mock:transient]`  -> attempt 1 raises TransientProviderError, later attempts succeed
    `[mock:fail]`       -> PermanentProviderError on every attempt
    `[mock:bad-patch]`  -> returns an artifact set that fails verification
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator

from packages.common.deliverables import (
    DOCUMENT_KINDS,
    MEDIA_TYPES,
    docx_from_paragraphs,
    extension,
)
from packages.common.estimator import estimate_tokens
from packages.common.observability import redact
from packages.provider_sdk.base import PermanentProviderError, TransientProviderError
from packages.provider_sdk.types import (
    ArtifactPayload,
    ChatInvocation,
    ChatResult,
    ExecutionRequest,
    ExecutionResult,
    ProgressEvent,
    ProviderCapabilities,
    UsageReport,
)

PROVIDER_NAME = "mock"
CODE_MODEL = "mock-code-v1"
BASE_TASK_COST_CENTS = 10


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class MockProviderAdapter:
    name = PROVIDER_NAME

    def __init__(self, *, step_delay_seconds: float = 0.0) -> None:
        self._step_delay = step_delay_seconds
        self.cancelled: set[str] = set()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            resource_type="MANAGED_API",
            supports_code=True,
            supports_chat=True,
            supports_stream=False,
            max_concurrency=1,
        )

    async def estimate_cents(self, request: ExecutionRequest) -> int:
        # Simulated, deterministic, integer cents. Not a provider price list.
        return BASE_TASK_COST_CENTS + len(request.prompt) // 1000

    def _usage(self, request: ExecutionRequest, output_tokens: int) -> UsageReport:
        return UsageReport(
            provider_name=PROVIDER_NAME,
            # One provider request per attempt: repeated callbacks for the same attempt carry
            # the same id and therefore deduplicate in usage_records.
            provider_request_id=f"mock-task-{request.attempt_id}",
            model_name=CODE_MODEL,
            input_tokens=estimate_tokens(request.prompt),
            output_tokens=output_tokens,
            estimated_cost_cents=BASE_TASK_COST_CENTS + len(request.prompt) // 1000,
            billable=False,
        )

    async def execute(
        self, request: ExecutionRequest
    ) -> AsyncIterator[ProgressEvent | ExecutionResult]:
        yield ProgressEvent("Loaded task metadata (mock: repository is not cloned)", 25)
        await asyncio.sleep(self._step_delay)

        if "[mock:fail]" in request.prompt:
            raise PermanentProviderError("Mock permanent failure", usage=self._usage(request, 0))
        if "[mock:transient]" in request.prompt and request.attempt_number == 1:
            raise TransientProviderError(
                "Mock transient failure on first attempt", usage=self._usage(request, 0)
            )

        seed = _digest(request.task_id, request.prompt)
        if request.task_type in DOCUMENT_KINDS:
            yield self._document_result(request, seed[:12])
            return

        yield ProgressEvent("Generated deterministic fixture patch", 75)
        await asyncio.sleep(self._step_delay)

        short = seed[:12]
        if "[mock:bad-patch]" in request.prompt:
            patch = b"this is not a unified diff\n"
        else:
            patch = (
                "diff --git a/SPAREAGENT_MOCK.md b/SPAREAGENT_MOCK.md\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/SPAREAGENT_MOCK.md\n"
                "@@ -0,0 +1,3 @@\n"
                "+# SpareAgent mock output\n"
                f"+fixture: {short}\n"
                f"+task_type: {request.task_type}\n"
            ).encode()
        summary = (
            f"Mock {request.task_type} result {short}: generated a fixture patch for "
            f"{request.repository_url}@{(request.commit_sha or '')[:12]} (no code was executed)."
        )
        test_report = json.dumps(
            {
                "framework": "mock",
                "executed": False,
                "passed": 2,
                "failed": 0,
                "tests": [
                    {"name": f"mock_test_{short}_a", "outcome": "passed"},
                    {"name": f"mock_test_{short}_b", "outcome": "passed"},
                ],
            },
            sort_keys=True,
        ).encode()
        log_lines = [
            f"attempt={request.attempt_number} provider=mock model={CODE_MODEL}",
            f"prompt_excerpt={request.prompt[:200]}",
            "no network calls; no repository checkout; no command execution",
        ]
        log = redact("\n".join(log_lines) + "\n").encode()
        output_tokens = estimate_tokens(summary) + estimate_tokens(patch.decode())
        yield ExecutionResult(
            summary=summary,
            artifacts=(
                ArtifactPayload("SUMMARY", "summary.txt", "text/plain", summary.encode()),
                ArtifactPayload("PATCH", "changes.patch", "text/x-diff", patch),
                ArtifactPayload("TEST_REPORT", "tests.json", "application/json", test_report),
                ArtifactPayload("LOG", "execution.log", "text/plain", log),
            ),
            usage=self._usage(request, output_tokens),
        )

    def _document_result(self, request: ExecutionRequest, short: str) -> ExecutionResult:
        """Kind-shaped placeholder deliverables (no model, no inputs read)."""
        if request.task_type == "TRANSLATE_DOCUMENT":
            spec = request.deliverable or {"format": "docx", "filename": "translation.docx"}
            lang = request.parameters.get("target_language", "?")
            texts = [f"[mock translation to {lang}] {short}", "No document was translated."]
            content = (
                docx_from_paragraphs(texts)
                if spec["format"] == "docx"
                else ("\n\n".join(texts) + "\n").encode()
            )
            files = [(spec["filename"], content)]
        else:
            report = (
                f"# Mock analysis {short}\n\nNo table was read and nothing was computed; "
                "this placeholder only exercises the delivery flow.\n"
            )
            files = [("report.md", report.encode())]
        summary = f"Mock {request.task_type} result {short} (no model was used)."
        return ExecutionResult(
            summary=summary,
            artifacts=(
                ArtifactPayload("SUMMARY", "summary.txt", "text/plain", summary.encode()),
                *(
                    ArtifactPayload("DELIVERABLE", name, MEDIA_TYPES[extension(name)], body)
                    for name, body in files
                ),
            ),
            usage=self._usage(request, estimate_tokens(summary)),
        )

    async def cancel(self, attempt_id: str) -> None:
        self.cancelled.add(attempt_id)

    async def chat(self, invocation: ChatInvocation) -> ChatResult:
        digest = _digest(
            invocation.physical_model,
            *(f"{m.role}:{m.content}" for m in invocation.messages),
        )
        content = (
            f"This is a deterministic mock response from {invocation.physical_model} "
            f"(no model was called). Digest {digest[:16]}."
        )
        finish_reason = "stop"
        completion_tokens = estimate_tokens(content)
        if invocation.max_tokens is not None and completion_tokens > invocation.max_tokens:
            content = content[: invocation.max_tokens * 4]
            completion_tokens = invocation.max_tokens
            finish_reason = "length"
        prompt_tokens = sum(estimate_tokens(m.content) + 4 for m in invocation.messages)
        request_id = f"mock-chat-{uuid.uuid4().hex}"
        return ChatResult(
            provider_request_id=request_id,
            content=content,
            finish_reason=finish_reason,
            usage=UsageReport(
                provider_name=PROVIDER_NAME,
                provider_request_id=request_id,
                model_name=invocation.physical_model,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                estimated_cost_cents=0,  # MOCK estimates are labelled non-billable
                billable=False,
            ),
        )
