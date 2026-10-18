# Week 4 status: experimental Codex subscription sharing

Branch `feat/week4-codex-subscription-pool`, based on `main` at e59c511.

## Summary

| Milestone | State | Evidence |
|---|---|---|
| A. Local owner-only Codex subscription connector | **Code complete. Live proof NOT run.** | 32 unit tests against a deterministic fake Codex CLI. No live ChatGPT login was used: the Codex CLI is not installed on the build host, and nobody ran the owner-only check on the owner's device. |
| B. Experimental permission and consent | **Done** | Groups, invitations, join/sharing acknowledgements, authorization records with operator verification, pause, revoke, kill switch. Integration tests. |
| C. Shared scheduler simulation | **Done (simulated)** | `ExperimentalDispatcher` and `ExperimentalSimulation` cover consented routing, revocation, limits and failures. 26 integration tests plus `scripts/e2e_experimental_demo.py` (result PASS). |
| D. Live experimental execution | **Blocked (B-1). Not implemented, not enabled.** | `LIVE_CROSS_USER_SUPPORTED = False` is a code constant. Database CHECKs make experimental tasks MOCK-only and make a `LIVE` ALLOW impossible to record. The adapter refuses all non-owner requests. |
| E. Minimal dashboard | **Not started (deferred)** | `main` has no web app. The Next.js app lives only in the unmerged `feat/week4-web-github` working tree (uncommitted). The API is ready for it. |

Full suite: **414 passed, 2 skipped** (the 2 pre-existing `SPAREAGENT_NETWORK_TESTS` cases). Coverage is 90.5% against an 80% gate. `ruff check`, `ruff format --check` and `scripts/validate_openapi.py` are all clean. Week 1–3 tests still pass; the only edited Week 1 test is the expected migration list in `test_migrations.py`, which now includes 0007.

## Blocker B-1: live cross-user execution

The brief says to stop at the last verified milestone if the official integration cannot support the exact experiment. That is the case here, for four reasons:

1. **The published terms forbid it.** The repository's own PRD (`docs/PRD.md`, with references) records that Sign in with ChatGPT "prohibits subscription pooling/resale and requests on behalf of other users". The Week 4 brief says the same. Routing Bob's task to Alice's plan is a request on behalf of another user.
2. **The integration route is not covered.** OpenAI's developer page for ChatGPT plan usage (developers.openai.com/siwc/token-sharing-open-source) offers it to "open-source and locally hosted apps". It sends paid or *remotely hosted* apps to an interest form. SpareAgent's central scheduler is remotely hosted. Each client_id is "bound to the authenticated user". The Codex auth docs say to treat `~/.codex/auth.json` "like a password" and "Don't expose Codex execution in untrusted or public environments".
3. **The claimed exception is unverified.** The support excerpts in the brief were supplied by the user and are not authenticated. Neither names a technical route (Codex CLI, app-server, or SIWC client) or the cross-user trigger model. They also limit scope to a "small voluntary group".
4. **Plan accounting can't be observed.** Codex exposes no supported way to read remaining plan quota, and no way to check whether a run drew on purchased credits. SpareAgent therefore cannot technically guarantee "no extra-credit purchase" during a live cross-user run. It can only strip API keys and stop on a limit error.

What would unblock it, all needed before any code change:

- Written confirmation from OpenAI, through a channel you can verify, that names the technical route and the cross-user trigger. "Through a channel you can verify" means an agreement or an approved SIWC client registration, not a forwarded support excerpt.
- A recorded `OPERATOR_VERIFIED` authorization with `integration_route_covered = true`.
- A reviewed change that flips `LIVE_CROSS_USER_SUPPORTED`, plus a new migration that relaxes `experimental_tasks_simulated_only` and `live_never_allowed`.

Until then the experimental scope runs only the deterministic simulation, and nothing here can enable live execution by configuration.

**Decision needed from the project owner:** do you want to pursue the verifiable written route above? Or should SpareAgent stay owner-only for real Codex usage, with the friends group kept as a simulation?

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
  - Owner-only: it requires `resource_scope == OWNER_ONLY` and `user_id == owner_ref`. There is no flag that relaxes this.
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
| 10 | A confirmed task completes through verification | `test_consented_task_completes_through_existing_verification` (SIMULATED, MOCK verifier) |
| 11 | Failed or interrupted tasks never verify | `test_failed_task_never_reports_verified_success`, `test_interrupted_task_does_not_succeed`, `test_crash_is_a_failure_not_a_success`, `test_timeout_kills_the_run` |
| 12 | Usage limits and rate limits | `test_rate_limit_is_recorded_and_pauses_assignment`, `test_daily_and_duration_limits`, `test_usage_limit_is_reported_not_worked_around`, `test_expired_owner_login_blocks_assignment` |
| 13 | No paid fallback or credit purchase | `test_paid_credits_can_never_be_enabled`, `test_non_chatgpt_login_fails_closed_without_running`, `test_sanitized_env_drops_every_api_billing_variable` |
| 14 | Week 1–3 tests still pass | Full suite: 414 passed, 2 skipped |

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

## Known limitations and residual risks

- **`auth_method` on connect is device-reported.** The server can't verify a ChatGPT login remotely (by design, it never sees credentials). The guarantee comes from the owner's own `codex-check` and the adapter's per-run check.
- **Personal nodes can't be enrolled over the API.** D-22 still blocks it in the database. Simulated nodes are local fixtures. A real enrollment path for personal nodes belongs to Milestone D and is blocked with it.
- **Purchased credits are invisible.** SpareAgent can't see whether Codex draws on purchased ChatGPT credits once plan limits are hit. Owners must keep any credit auto-top-up off. This is one more reason live sharing stays off (B-1, item 4).
- **The rate-limit cooldown is a fixed hour.** The reset hint is shown but not parsed into a time.
- **The owner-only adapter isn't wired into a server-dispatched lease.** It runs today only through `codex-check`. Putting it behind a node's lease protocol would also need OWNER_ONLY task dispatch, which `main` doesn't implement (the PRD marks it "future").
- **The UI (Milestone E) is not built** (see the table above).
