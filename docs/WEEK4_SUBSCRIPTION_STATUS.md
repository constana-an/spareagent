# Week 4 status: experimental Codex subscription sharing

Branch `feat/week4-codex-subscription-pool`, based on `main` at e59c511.

## Summary

| Milestone | State | Evidence |
|---|---|---|
| A. Local owner-only Codex subscription connector | **Code complete. Live proof NOT run yet.** | 41 unit tests against a deterministic fake Codex CLI. No real ChatGPT login was used: the Codex CLI is not installed on the build host. The owner still has to run `spareagent-worker codex-check` on their own Mac. |
| B. Experimental permission and consent | **Done** | Groups, invitations, join/sharing acknowledgements, authorization records with operator verification, pause, revoke, kill switch. |
| C. Shared scheduler simulation | **Done (simulated)** | `ExperimentalDispatcher` and `ExperimentalSimulation`, 26 integration tests, `scripts/e2e_experimental_demo.py` (PASS). |
| D. Live experimental execution | **Implemented and gated. Off by default. Never run against a real subscription.** | Migration 0008, LIVE policy gates, device enrollment, remote lease routing, owner-device Codex executor, live result verification. 16 integration tests plus device-side unit tests, all against the fake CLI and a simulated device. |
| E. Minimal dashboard | **Not started (deferred)** | `main` has no web app. The Next.js app lives only in the unmerged, uncommitted `feat/week4-web-github` working tree. The API is ready for it. |

Full suite: **446 passed, 2 skipped** (the 2 pre-existing `SPAREAGENT_NETWORK_TESTS` cases). Coverage is 90.5% against an 80% gate. `ruff check`, `ruff format --check` and `scripts/validate_openapi.py` (contract 1.4.0, 47 paths) are all clean. The only edited Week 1–3 test is the expected migration list in `test_migrations.py`, which now includes 0007 and 0008.

## Authorization for live cross-user execution

The first version of this branch stopped at Milestone C (blocker "B-1"). The published Sign in with ChatGPT terms restrict pooling and requests on behalf of other users, and the support excerpts in the brief were unverified and named no technical route. The owner then supplied OpenAI Support's follow-up reply, **ticket 10766658**, and reported that they checked the message headers. The reply confirms the following, which is the scope Milestone D enforces:

| Confirmed by OpenAI Support (ticket 10766658) | Enforced by |
|---|---|
| Execution via the official Codex CLI (`codex exec`), signed in with each participant's own ChatGPT account on their own computer | Authorization `integration_route = CODEX_CLI_EXEC` (policy and DB trigger). The device executor runs only `codex exec` with `forced_login_method="chatgpt"`, an environment with no API keys, and a `codex login status` check before every run. |
| A coordination server may relay task text and results, never handling credentials | No credential fields anywhere on the server. Request models are `extra=forbid`. Prompts are redacted before they leave the API. |
| At most 5 participants | `max_participants = 5` on the record. LIVE is denied (`GROUP_EXCEEDS_AUTHORIZED_SIZE`) if the group's cap or member count exceeds it. |
| Purchased Codex credits must never be consumed by another participant's task; stop when the plan limit is reached | The owner attests "no purchased credits, auto-reload off": through the API (stored, and required by the DB trigger) and on the device (`--attest-no-purchased-credits`). Withdrawing it stops LIVE dispatch immediately. A plan-limit error fails the task without retry and starts a cooldown. |
| No resale, payment or credential sharing; voluntary and revocable | No money fields. Consent acknowledgements. Pause, revoke, local kill switch, operator kill switch. |
| Check back after 6 months, or sooner if the scope changes | Record `expires_at` (6 months from 2026-10-18, i.e. 2027-04-18). LIVE is denied after it, in the policy and in the DB trigger. |

Caveats that stay on record:

- SpareAgent can't verify the email itself. The operator's `verify` call is the attestation that the headers were checked. The authorization record holds only the ticket number.
- SpareAgent can't see credit balances. The guarantee against consuming purchased credits rests on each owner's attestation. (The owner reports that no member has credits and none has auto-reload on.)
- The permission is project-specific, not a general one. Nothing here should be presented as OpenAI endorsing SpareAgent. Any change of scope (commercial use, payment, more than 5 people, shared credentials) needs a new review with OpenAI.

### Turning LIVE on (operator runbook)

1. Set `EXPERIMENT_ENABLED=true` and `EXPERIMENT_LIVE_ENABLED=true`. Both default to off.
2. Record the authorization:

   ```text
   POST /v1/experiments/groups/{id}/authorizations
     provider_name=openai-codex
     authorization_source=VENDOR_SUPPORT_CORRESPONDENCE
     authorization_reference="OpenAI support ticket 10766658"
     integration_route=CODEX_CLI_EXEC
     max_participants=5
     expires_at=2027-04-18T00:00:00Z
   ```

   Then call `POST /v1/experiments/authorizations/{id}/verify` with `OPERATOR_VERIFIED` and `integration_route_covered=true`.
3. Each owner, on their own Mac:
   1. Run `codex login` (ChatGPT) and `spareagent-worker codex-check`.
   2. `POST /v1/subscriptions/enrollment-tokens`, then `spareagent-worker register --resource-id … --enrollment-token …`.
   3. `POST /v1/subscriptions/connect`, `PUT /v1/sharing/preferences`, `POST /v1/sharing/enable`.
   4. `POST /v1/subscriptions/attest` with the `codex-check` result and the no-credits / no-auto-reload statements.
   5. `spareagent-worker start --executor codex-subscription --accept-shared-tasks --attest-no-purchased-credits`
4. Requesters submit with `"execution_mode": "LIVE"`. The owner approves each task, unless they granted scoped advance consent.

Recommended first real run:
1. `codex-check` on your own Mac.
2. One CODE_REVIEW between two members, on a small public repository.
3. Confirm in each owner's ChatGPT usage page that only plan usage was consumed.
4. Only then widen use.

## What each piece does

### Milestone A: owner-only connector (on the owner's device)

- `packages/worker/subscription_auth.py` runs the official `codex login status` and accepts **only** "Logged in using ChatGPT". An API-key login, no login, an unrecognised output or a missing CLI all fail closed. It never reads credential files.
- The child process gets an allow-listed environment (`PATH, HOME, CODEX_HOME, LANG, LC_ALL, TMPDIR, USER, SHELL`). `OPENAI_API_KEY`, `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN`, base URLs and org/project ids are never passed to it.
- `packages/worker/codex_runtime.py` runs `codex -c forced_login_method="chatgpt" exec --json --ephemeral --skip-git-repo-check --sandbox read-only -` with these properties:
  - The prompt goes in on stdin, never in argv.
  - The process gets its own process group, so cancel and timeout kill the whole tree.
  - The JSONL output is parsed into typed events. Progress messages carry only the item kind (no commands or paths). All text is redacted.
  - Errors are classified as `AUTH`, `USAGE_LIMIT` (with a reset hint) or `RUNTIME`.
- `packages/provider_sdk/codex_subscription.py` (`CodexSubscriptionAdapter`):
  - Owner-only by default: it requires `resource_scope == OWNER_ONLY` and `user_id == owner_ref`. Shared work is possible only through the Milestone D on-device opt-in (see below).
  - Usage is reported as `billable=false` with `estimated_cost_cents=0`.
  - An expired session raises `SubscriptionUnavailable`. A plan limit raises `SubscriptionLimitReached`, which is not retryable and is never retried with other credentials.
- `spareagent-worker codex-check` is the owner-run proof. It prints a JSON report: the login method, which API-billing variables were present in the shell (names only), the fact that none were passed to Codex, and the token counts from one trivial read-only run. Nothing is sent to SpareAgent.
- `spareagent-worker sharing pause|resume|status` is a local kill switch. While it is paused, the worker agent declines every experimental lease before accepting it, whatever the server says.

**To finish Milestone A, the owner runs this on their own Mac after `codex login` (ChatGPT):**

```bash
spareagent-worker codex-check
```

Expected result: `"result": "OWNER_ONLY_SUBSCRIPTION_RUN_OK"`, `"auth_method": "CHATGPT"`, `"api_billing_env_passed_to_codex": []`. Then confirm in ChatGPT's Codex usage settings that the run counted against the plan and not API credits. Codex does not expose that over a supported interface. Record the outcome here.

### Milestones B and C: experimental scope (server)

- **A separate scope and a separate policy.** The scope is `EXPERIMENTAL_SHARED_SUBSCRIPTION`. `ExperimentalSharingPolicy` (`services/subscriptions/policy.py`) is a pure function over a database snapshot, and every one of these conditions is required:
  - the experiment is on, the group is active and sharing is enabled
  - a non-revoked, unexpired, `OPERATOR_VERIFIED` authorization exists
  - the requester and the owner are both active members
  - the owner opted in with recorded consent and is not paused or revoked
  - the task is approved, or the requester is covered by scoped advance consent
  - the runtime is the owner's own non-public `PERSONAL_SUBSCRIPTION` node, not revoked, and online
  - the connection is `CONNECTED` and its auth is `VALID`
  - the task type is allowed by the group, the owner and the authorization scope
  - duration, concurrency and daily limits hold, and the connection is outside its rate-limit cooldown
  - paid credits are not allowed

  `ResourceEligibilityPolicy` is **unchanged**, so personal subscriptions stay ineligible for `PUBLIC_MARKETPLACE` and the gateway.
- **`ExperimentalParticipationService`** (`services/experiments/participation.py`):
  - Groups are invitation-only. Membership is capped by `EXPERIMENT_MAX_GROUP_MEMBERS` (default 5, maximum 20).
  - Joining requires the join acknowledgements. Enabling sharing requires seven sharing acknowledgements and the current terms version.
  - Pause stops new assignment. Revoke stops new assignment and cancels unfinished tasks on the connection. The operator kill switch disables every group and cancels every unfinished experimental task.
- **`ExperimentalDispatcher`** writes every decision to the append-only `experimental_dispatch_audit` table. Identical repeated denials are recorded once. A final denial fails the task with `EXPERIMENT_POLICY_DENIED`; a transient one leaves it `QUEUED`. An allowed task gets an attempt and lease on the owner's node and goes through the **existing** `LeaseService` and verifier lifecycle.
- **`SubscriptionUsageMonitor`** records observations only. `plan_remaining` and `plan_limits` are always `NOT_AVAILABLE`, and capacity is `UNKNOWN` unless observed `LIMITED`. A `RATE_LIMITED` observation starts a fixed one-hour cooldown. A failure with `AUTH_EXPIRED` marks the connection `EXPIRED` until the owner reconnects.
- **Migration `0007_experimental_subscriptions.sql`** adds the eight suggested tables. Invariants enforced in the database:
  - no credential columns
  - experimental tasks are MOCK-only
  - `CODE_REVIEW` exists only in the experimental scope
  - a `LIVE` ALLOW cannot be recorded
  - `allow_paid_credits` is always false
  - `ENABLED` requires recorded consent
  - a connection must sit on the owner's own non-public personal runtime (trigger)
  - the audit table is append-only (trigger)

  It is numbered 0007 so it can't collide with the unmerged `0006_web_github`.
- **The authorization record** stores only an opaque reference such as `support-ticket #12345`. The API rejects free text in that field (pattern-restricted), and the correspondence itself is never stored. The API always returns `live_execution_enabled: false`.

### API (contract 1.3.0)

The proposed paths are implemented as specified, plus four operator routes:

- `POST /v1/experiments/groups/{id}/authorizations`
- `POST /v1/experiments/authorizations/{id}/verify|revoke`
- `POST /v1/experiments/groups/{id}/sharing`
- `POST /v1/experiments/kill-switch`
- `GET /v1/experiments/groups/{id}/audit`

There is also `GET /v1/experimental-tasks/nodes` for the requester's node list. Results download through the existing `/v1/tasks/{id}/artifacts`. Every request model is `extra=forbid`, so a body containing `access_token`, `refresh_token`, `api_key` and so on is rejected with 422. Foreign ids return 404.

## Acceptance tests

| # | Requirement | Test |
|---|---|---|
| 1 | Owner-only execution works without an API key | `tests/unit/test_codex_subscription.py::test_owner_only_execution_works_without_api_key` (fake CLI). **Live: pending the owner's `codex-check`.** |
| 2 | Credentials never leave the owner's environment | `test_owner_only_execution_works_without_api_key` (child env), `test_outputs_are_redacted_and_carry_no_credential`, `test_server_never_accepts_credentials_or_api_key_logins` |
| 3 | Sharing can't be activated without consent | `test_sharing_cannot_be_enabled_without_consent` |
| 4 | Non-members can't submit | `test_non_members_cannot_submit` |
| 5 | Provider can reject | `test_owner_can_reject` |
| 6 | Paused providers get no new tasks | `test_paused_provider_receives_no_new_tasks`, `test_agent_declines_experimental_lease_when_locally_paused` |
| 7 | Revocation prevents assignment | `test_revocation_cancels_and_prevents_assignment`, `test_revocation_stops_a_running_task`, `test_operator_kill_switch` |
| 8 | Expired or unauthorized experiments can't run | `test_unauthorized_or_expired_experiment_cannot_run[...]`, `test_experiment_disabled_globally` |
| 9 | No personal routing through `PUBLIC_MARKETPLACE` | `test_personal_subscription_never_routed_through_public_marketplace`, `test_public_marketplace_policy_unchanged_for_personal_subscriptions` |
| 10 | A confirmed task completes through verification | `test_consented_task_completes_through_existing_verification` (SIMULATED); `test_experimental_live.py::test_live_task_runs_on_owner_device_end_to_end` and `test_live_results_are_verified[...]` (LIVE protocol, simulated device) |
| 11 | Failed or interrupted tasks never verify | `test_failed_task_never_reports_verified_success`, `test_interrupted_task_does_not_succeed`, `test_crash_is_a_failure_not_a_success`, `test_timeout_kills_the_run` |
| 12 | Usage limits and rate limits | `test_rate_limit_is_recorded_and_pauses_assignment`, `test_daily_and_duration_limits`, `test_usage_limit_is_reported_not_worked_around`, `test_expired_owner_login_blocks_assignment` |
| 13 | No paid fallback or credit purchase | `test_paid_credits_can_never_be_enabled`, `test_non_chatgpt_login_fails_closed_without_running`, `test_sanitized_env_drops_every_api_billing_variable` |
| 14 | Week 1–3 tests still pass | Full suite: 446 passed, 2 skipped |

## Reproducible local demonstration (simulated)

Use a disposable database: the demo inserts users, and the kill switch disables every group in that database.

```bash
DATABASE_URL=postgresql+asyncpg://spareagent:spareagent@localhost:55432/spareagent REDIS_URL=redis://localhost:56379/0 python scripts/e2e_experimental_demo.py
```

A verified run on 2026-10-18 printed `result=PASS`. The steps were:

1. Sharing without consent was refused with 422.
2. Two tasks waited on `OWNER_APPROVAL_MISSING`.
3. The rejected task ended `CANCELLED`.
4. The approved task reached `SUCCEEDED` in `SIMULATED` mode with SUMMARY, PATCH, TEST_REPORT and LOG artifacts.
5. The simulated rate limit left the task `FAILED`, with `plan_remaining=NOT_AVAILABLE` and `capacity=LIMITED`.
6. The paused node refused new tasks (404).
7. Revoke and the kill switch both worked.
8. The audit showed only `SIMULATED` decisions.

The run writes its report to `reports/week4_experimental_demo.json`.

### Milestone D: gated live execution

- **Migration `0008_experimental_live_execution.sql`:**
  - adds `integration_route` and `max_participants` to the authorization record, and `owner_check_passed_at` and `no_purchased_credits_at` to the connection;
  - adds the `CODEX_LIVE` profile, allowed only in the experimental scope;
  - replaces `live_never_allowed` with a trigger: a LIVE ALLOW must cite a verified, route-covered (`CODEX_CLI_EXEC`), unexpired, unrevoked authorization for the same group, plus both owner attestations;
  - narrows D-22. A personal resource can enroll a node only through a token issued by its own owner, and only while that owner is an active member of an active group. Marketplace enrollment of personal resources is still refused, and personal resources still can't be public.
- **Policy (LIVE mode)** requires every SIMULATED condition plus: the operator switch, the covered route, the group fitting the authorized cap, both owner attestations, and a node advertising `executor=codex-subscription`.
- **Routing.** A personal device's long poll goes to `ExperimentalDispatcher` and claims only `CODEX_LIVE` tasks. Every other node keeps the public `Dispatcher`, which still never selects personal resources. The lease carries `resource_scope` and `authorization_ref`.
- **Device executor.** `CodexSubscriptionAdapter` runs shared work only when all of these hold:
  - it was built with `SharedWorkConsent` (both CLI flags);
  - the local `sharing pause` switch is off;
  - the lease names an authorization;
  - the task pins a repository commit.

  It checks the commit out with the hardened `RepositoryManager`. Reviews use Codex's `read-only` sandbox; other tasks use `workspace-write`. A preamble tells Codex to stay in the checkout. The patch includes new files.
- **Verification** (`verify_codex_live`):
  - a SUMMARY is always required;
  - WRITE_TESTS and CODE_FIX need a PATCH that passes the existing patch policy;
  - a review must not contain a patch.
- **Usage.** Server-computed at 0 cents and non-billable. A device-reported `SUBSCRIPTION_USAGE_LIMIT` becomes a `RATE_LIMITED` observation and a one-hour cooldown. `SUBSCRIPTION_AUTH_UNAVAILABLE` marks the connection EXPIRED until the owner reconnects.

## Known limitations and residual risks

- **`auth_method` on connect is device-reported.** The server can't verify a ChatGPT login remotely (by design, it never sees credentials). The guarantee comes from the owner's own `codex-check` and the adapter's per-run check.
- **LIVE has never run against a real subscription.** All Milestone D tests use the fake CLI and a simulated device. Follow the recommended first-run order above.
- **Purchased credits are invisible.** SpareAgent can't see whether Codex draws on purchased credits. The guarantee rests on owner attestations, which can be withdrawn at any time.
- **A friend's prompt runs Codex on the owner's computer.** Codex's own sandbox (read-only for reviews, workspace-write otherwise, never full access) and per-task owner approval are the controls. Owners should read each prompt before approving.
- **Live results aren't re-executed by the platform.** Only structure and patch-policy checks apply. Requesters should treat patches as untrusted code.
- **The rate-limit cooldown is a fixed hour.** The reset hint is shown but not parsed into a time.
- **The UI (Milestone E) is not built** (see the table above).
