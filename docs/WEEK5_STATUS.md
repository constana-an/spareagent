# Week 5 status — private friends beta

Branch `feat/week5-friends-beta`, based on merged Week 4 `main` at `ee20671`.
Recorded on 2026-10-18. LIVE remains off by default. No deployment was published.

## Actual validation

| Area | Evidence | Status |
|---|---|---|
| Real local ChatGPT subscription execution | `reports/week5_owner_codex_validation.json`: OpenAI-signed arm64 bundled Codex `0.163.0-alpha.10.2`; actual read-only owner-authorized task returned `SPAREAGENT_OWNER_ONLY_OK`; 16,264 input / 11 output tokens; ChatGPT login, no API-billing env passed. Owner confirmed Plus, no extra purchased/granted Credits and no reload before execution; afterward confirmed subscription-only usage and unchanged Credits. | **Real owner-only run verified, including owner-confirmed funding source.** Not a mocked CLI result. |
| Real shared-tool filesystem isolation | `reports/week5_codex_sandbox_validation.json`: official Codex OS sandbox, empty Codex home, harmless markers; outside-checkout reads denied, review writes denied, workspace writes allowed. No inference used. | **Passed on this Mac.** Repeat on every owner device/CLI upgrade. |
| Real timeout/cancellation/limit during model execution | Process-tree, timeout, cancellation, malformed-output and usage-limit tests use fake CLI/real subprocesses. No extra real turn or limit exhaustion was triggered. | **Still manual for a real model turn.** |
| Two-participant subscription experiment | Operator-controlled reproducible public-fixture runbook; actual Alice/Bob owner-device subscription task not executed. | **NOT RUN.** Explicit operator activation still required. |
| LIVE independent verification | Protocol with simulated device; real Git checkout/apply and trusted Docker test execution for CODE_FIX and supported WRITE_TESTS fixture; forged success, bad apply and altered stored bytes rejected. | **Implemented and tested without subscription consumption.** |
| Friends dashboard | Next.js production build, TypeScript and six relay/security tests passed. Real browser login, simulated task submission/approval/events/download, pause/revoke and desktop/mobile layouts checked. `reports/week5_browser_qa.json`. | **Working locally with real API/database and simulated execution.** No claim of a real cross-user run. |

Full local regression: **477 passed**, **90.73%** coverage (80% required), including
`SPAREAGENT_NETWORK_TESTS=1` and `SPAREAGENT_REQUIRE_SANDBOX=1`. A subsequently added
participant-audit regression is validated separately. Ruff checks/format, OpenAPI 3.1
(contract 1.5.0, **53 paths**), Next production build, TypeScript, six Node relay tests and
`git diff --check` pass. CI runs these gates without a real Codex login; the PR checks are
the authoritative remote result. No subscription-consuming CI job was added.

## What changed

- One-time, expiring operator invitations; hashed opaque revocable sessions; secure cookies
  outside local; strict browser Origin checks. Setup creates a two-person group without
  authorization, consent, sharing or LIVE activation. It never sends messages.
- API identity and task inspection fields for a usable owner approval. The selected owner
  and requester can read task events/artifacts; unrelated members cannot. The existing
  ADMIN audit route stays ADMIN-only; a new member route returns only participant tasks.
- Next.js dashboard with group acceptance, enrollment/connect, device freshness, consent,
  limits, attestations, sharing/pause/revoke, CODE_REVIEW submission, approvals, SSE,
  cancellation, integrity-checked downloads and audit. No precise remaining quota shown.
- LIVE always requires per-task OWNER_APPROVAL; advance consent is simulation-only.
  Policy is rechecked at acceptance. Personal devices remain single-concurrency.
- Shared Codex tools use minimal-system/checkout-only permissions, disabled tool networking
  and escalation, no inherited user config/rules, MCP, apps, search or subagents. Patch
  generation uses hardened Git. Potential credential-bearing patches are withheld.
- Completed-turn/exit checks, bounded streams, process-group/descendant cleanup and bounded
  renewal loss cancellation. Token details are not added twice to totals. Runtime token
  observations are marked owner-reported and committed atomically with finalization.
- LIVE artifacts remain unpublished until independent platform verification. Verification
  records include repository pin, patch digest, checks, trusted command/result and identity;
  stale verifier claims cannot finalize. Incremental migration 0009 extends the DB guard.

## Verification meaning

`Task.status=SUCCEEDED` means execution/result handling completed. Read the separate
`verification_status` and platform `VERIFICATION_REPORT`:

- **PASSED**: integrity, clean pinned checkout, patch policy/application and the catalog's
  trusted tests passed. This establishes those tests, not full semantic correctness or
  test quality. The independent verifier never trusts the worker's success declaration.
- **UNVERIFIED**: no trusted test recipe (after clean application for a patch), or a review
  needing human assessment. Published artifacts are integrity checked, explicitly unverified.
- **FAILED / ERROR**: invalid result or bounded infrastructure failure, never a pass. Worker
  artifacts are withheld. Cancellation prevents publication, including late success.

CODE_FIX uses the existing Python/TypeScript fixture catalog. WRITE_TESTS supports the new
local deterministic `python-test-target` pin `0c1bded2a0a0461d1f96d20b5c5df0c6dd68c6cb`:
correct immutable source/baseline tests, only `test_added.py` editable, four expected tests.
Fixture URLs in that catalog map to local mirrors in development; they are not asserted
to be published public repositories. The first real review uses the actual public
`octocat/Hello-World` pin documented in the experiment runbook. Unsupported repository
commands are never run on the host or accepted as trusted test commands.

## Remaining operator work and limits

1. Repeat owner installation/login and the no-inference sandbox probe on Alice's actual
   execution device if different from this Mac. Confirm no extra Credits/no reload again.
2. Validate the owner-supplied project-specific Support correspondence, ticket 10766658;
   preserve official CLI/device ownership, five-person ceiling, non-commercial/no-payment
   conditions and review no later than 2027-04-18, sooner on scope change.
3. Follow `WEEK5_FRIENDS_BETA_RUNBOOK.md`: activate LIVE manually, run one Bob-to-Alice public
   CODE_REVIEW only after Alice's explicit approval, inspect results, manually confirm
   usage/Credits, then turn LIVE off and record the real task evidence.
4. Only by additional owner choice, run real-turn cancellation/timeout/disconnect drills.
   CI and browser simulation do not satisfy those real-world checks.

SpareAgent cannot observe upstream balances or force Codex not to spend existing extra
Credits. Eligibility rests on owner confirmations and stop-on-limit handling; this is
not a machine-verified billing guarantee. Browser/device login attestations are also
owner statements. Containers remain the existing trusted-operator testing boundary, not
an approved hostile multi-tenant service. Local simulated tasks require an explicitly
run simulation harness; the owner Codex worker accepts LIVE tasks only. Browser QA's
mock result and four mock artifacts are not evidence of live independent code correctness.

Runbooks: [local owner](WEEK5_LOCAL_CODEX_RUNBOOK.md),
[two-participant experiment](WEEK5_FRIENDS_BETA_RUNBOOK.md).
