# Week 6 status — pilot readiness and beta hardening

Based on merged `main` at `7012489` (Week 5 PR #4), branch
`feat/week6-real-sharing-pilot`. Recorded 2026-10-18. No public deployment or release published.
**LIVE stays off by default. The real two-person experiment remains NOT_RUN.**

## Private Mac deployment follow-up

Week 6 was merged in PR #5 (`5068ead`). A separate seven-service private Mac stack was
deployed and checked with verified HTTPS, production invite authentication, restart/data
persistence and the Verifier's no-inference sandbox probe. Device #1 is the service host;
#2 and #3 are the two users. Friends install no backend. Tailscale Serve is documented as
an optional private-network fallback; no SpareAgent Tailscale ingress has been configured.

At the owner's request, all local Docker containers are now stopped. The seven pilot
containers and three pilot data volumes were retained. Old main-environment volumes
were retained, but two Week 4 temporary containers automatically deleted their anonymous
volumes on stop after their AutoRemove setting was missed; those two test databases have
not been restored. See the [deployment and shutdown record](MAC_PRIVATE_DEPLOYMENT.md).
No second-device connectivity or real cross-user subscription run is claimed.

## Readiness gaps found and addressed

Week 5 already supplied authorization, owner consent, isolated Codex execution, private
sessions/dashboard, persisted leases, independent verification and the real one-owner
funding confirmation. Those components were reused. Actual first-pilot gaps were:

1. Friends' install path required the backend/development package. A separate small
   worker wheel now includes only Worker, SDK and shared stdlib helpers plus httpx.
   `setup-worker-macos.sh` installs it into a user venv after explicit checksum validation;
   no backend, Docker or server credentials, no login/registration/start/LIVE/inference.
2. No consolidated no-inference readiness report existed. `pilot-check` now checks local
   CLI/help/login, actual harmless OS isolation, private local identity/concurrency/pause,
   secure API and node-scoped policy readiness. The server reuses the existing policy,
   checks exactly two participants/current terms/60 seconds/CODE_REVIEW/single concurrency,
   and separates owner-reported subscription fields and manual physical-device/funding
   attestations. `--task-id` checks the selected connection's individual approval.
3. Authority could expire during execution or independent verification. New progress,
   renewal, completion and publication checks stop on expiry/withdrawal/current-terms
   mismatch; a new authorization cannot silently replace the original dispatch record.
   Late LIVE success after the task deadline becomes timeout. Cancellation never records
   a completed execution event or publishes successful worker artifacts/pass reports.
4. Device renewal rejected with auth/ownership errors could leave execution running too
   long. It now cancels locally on invalid authority or an unrenewable lease window,
   including server HTTP errors. Existing short-outage recovery/idempotency are retained.
5. UI lacked a readable persisted timeline, completed owner history and bounded metrics.
   Both participants can reopen their task history; reconnect deduplicates sequence IDs.
   VERIFIED/UNVERIFIED/FAILED/CANCELLED are separate from execution status. Onboarding,
   metrics and redacted protocol evidence export explain manual evidence boundaries.

## Actual evidence and tests

- `reports/week6_local_readiness.json`: **real no-inference** check on this arm64 Mac,
  official bundled CLI `0.163.0-alpha.10.2`; compatible help, ChatGPT login and real OS
  sandbox markers pass. Overall **BLOCKED** because the isolated state is unregistered;
  no coordinator/remote-device readiness is claimed. No model turn was run.
- Final worker wheel is installed outside the checkout in an empty venv; imports and CLI
  work with **no apps/services/db/FastAPI/SQLAlchemy/asyncpg/Redis packages**. The actual
  macOS installer is exercised in another new venv against a fake CLI sentinel; it never
  invokes that sentinel. CI repeats wheel checks on Linux and macOS and uploads bundles.
- Automated Python regression, coverage, Ruff, OpenAPI (1.6.0, **56 paths**), Next production
  build, TypeScript and **7 Node relay tests** are the gates. See the PR's final CI checks
  for the complete authoritative result. All automated inference uses a fake CLI or a
  simulated node; CI keeps LIVE disabled.
- Browser QA uses a separate disposable database, browser-session auth, LIVE false and
  deterministic SIMULATED execution. It checks Bob submit, Alice prompt/SHA/timeout
  inspection and approval, timeline/results, export, installation instructions, metrics,
  desktop/mobile and sign-out. `reports/week6_browser_qa.json` records the actual checks.

Final local full regression: **508 passed in 173.61 seconds, 90.70% coverage** (80%
required), with hardened sandbox and real public-GitHub checkout tests enabled. Ruff,
format check, OpenAPI validation, Next production build/typecheck, 7 Node tests, shell
syntax, empty-venv wheel install and actual macOS installer checks pass. The earlier
contract snapshot and transient Redis setup issue were resolved/rechecked before this
successful final full run. No extra real model turn was run.

## Deterministic reliability coverage

| Scenario | Evidence |
|---|---|
| Short disconnect/reconnect; sustained lease loss | `test_week6_pilot.py::test_short_disconnect_recovers_without_cancelling`; existing Week 5 lease-window cancellation |
| Lease expiry / stale completion / server restart | Week 5 reliability expiry/recovery; existing remote-worker restart test; new LIVE duplicate completion across API restart |
| Cancel during Codex / timeout / process tree | Existing fake-CLI real-subprocess cancellation, hard-timeout, descendant cleanup and bounded-output tests |
| Pause before acceptance; revoke running | Existing Week 5 pause/resume acceptance and running revoke/artifact rejection tests |
| Expiry during execution or verification | New Week 6 authorization-expiry regressions; authorization replacement cannot continue an old attempt |
| Repeated completion | LIVE restart replay test proves one observation/artifact and preserved original token counts |
| Concurrent submission | Two LIVE protocol submissions can reserve only one slot (re-poll may return same lease) |
| Expired login; usage limit | Existing fake-CLI expiry/limit + server cooldown tests; new ineligible preflight login and rejected renewal tests |
| Oversized/malicious prompt; prompt injection | API size/redaction tests, on-device prompt bounds, immutable process permission/argv regression; actual OS sandbox probe. No claim of real-model injection resistance |
| Unauthorized inspection | Existing worker/task/session isolation plus new node-readiness task isolation, active-group metrics and owner/requester-only export |
| Cancelled/stale results never publish success | Existing verifier/DB publication guard; new expiry/deadline tests with worker artifacts withheld and cancelled report labelled failed |

File references in this table are under `tests/unit`, `tests/integration`; inherited
Week 1–5 tests remain in the full regression. No additional real cancellation/timeout,
limit exhaustion or cross-user subscription turn was performed.

## Operator work still required

Follow [the Week 6 runbook](WEEK6_PILOT_RUNBOOK.md). Operator must supply reviewed bundle
bytes/checksum, private HTTPS coordinator/CA, valid invitations and actually reviewed
project-specific authorization. Alice must install/login on her own Mac, establish her
own check/consent/zero-extra-Credits/no-reload conditions and approve each task. Confirm
Bob's second physical device. Only then manually enable LIVE for the single 60-second
public pinned CODE_REVIEW, verify results and manually confirm funding afterward, then
switch LIVE off again. Keep the two-person pilot record **NOT_RUN** until this happens.

Read [security review](WEEK6_SECURITY_REVIEW.md) and [metric definitions](WEEK6_METRICS.md).
Software cannot query/guarantee upstream funding, prove physical devices from protocol,
provide exact remaining quota or establish semantic correctness of a review. Report
exports deliberately leave these fields manual/UNCONFIRMED, including in fake LIVE tests.
