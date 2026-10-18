# Product Requirements Document: SpareAgent v1.0

**Date:** 2026-10-17 | **Status:** implementation-ready MVP specification | **Product:** both (Task Marketplace first; AI Gateway second) | **Deployment:** hybrid

## 1. Problem and value proposition
Developers need trustworthy, verifiable autonomous coding work with a transparent price ceiling; operators have heterogeneous legitimately commercializable inference/execution resources. SpareAgent routes tasks to appropriate resources, isolates execution, verifies deliverables, and records usage. Additionally a standardized AI Gateway enables model calls through managed/licensed API resources.

The original concept of selling unused personal ChatGPT/Claude subscription usage is **not part of public marketplace MVP**. OpenAI SIWC allows eligible users to invoke eligible requests for themselves via approved integrations, but prohibits subscription pooling/resale and requests on behalf of other users. Owners' tasks may be supported in a separate user-controlled runtime after partner approval and security review, without charging for plan-use itself. No silent fallback to paid credits. Anthropic subscription entitlements must similarly not be assumed resellable.

References: https://openai.com/policies/sign-in-with-chatgpt-terms/ ; https://help.openai.com/en/articles/20001542-using-your-chatgpt-plan-in-other-apps-and-sites ; https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server

## 2. Actors
- Consumer: creates tasks, sets price ceilings, reviews artifacts, manages their own payments.
- Provider operator: registers an approved public-resource node and sees usage/payout-eligible receipts (no real payouts in W1).
- Admin: approves provider license, suspends resource, resolves disputes, audits policy violations.
- Owner (personal mode, future): invokes ONLY own ChatGPT plan from own controlled runtime.

## 3. P0 user stories
- C1: As consumer, submit a task with repository HTTPS URL, pinned commit SHA, task description, budget cap (USD cents), duration cap and idempotency key.
- C2: As consumer, see deterministic status changes and detailed SSE event stream.
- C3: As consumer, view patch, structured tests, failure reason and execution metadata.
- C4: As consumer, cancel queued or running work; cancelled work cannot publish PR.
- C5: As consumer, call POST /v1/chat/completions (non-stream) on managed model through OpenAI-like protocol, with explicit error response and metered usage.
- O1: As operator, register an approved worker, heartbeats, leases and controlled completion events.
- A1: As admin, enforce authorization eligibility and owner-vs-public isolation regardless of client request.

## 4. Nonfunctional requirements
- Security: no raw API keys or SIWC tokens in logs. Worker never receives unrelated consumer secrets. Only public read-only repositories supported through W1.
- Reliability: idempotent task creation; duplicate completion does not double-bill; lease expiration leads to retry/fail; max attempts=2 W1; exponential backoff.
- Budget: never start attempt without sufficient reserved headroom; stop before hard budget cap (provider accounting may lag, so reserve safety margin); do not promise impossible absolute caps when upstream costs are delayed.
- Observability: correlation IDs, event audit trail, structured logs; OpenTelemetry optional after W1.
- Data retention: task metadata 90 days, raw logs 7 days in beta by default, configurable; users may request removal subject to ledger/legal retention.
- Task throughput W1: at least 10 concurrent queued tasks, 1 running per mock worker; W6: target 3 workers, 20 simultaneous tasks in test lab (not SLA).

## 5. Task marketplace lifecycle
CREATED -> QUEUED -> RESERVED -> RUNNING -> VERIFYING -> SUCCEEDED or FAILED.
Transitions: QUEUED -> CANCELLED; RUNNING -> CANCELLING -> CANCELLED; RESERVED -> QUEUED on lease expiry; RUNNING -> RETRY_PENDING -> QUEUED if retryable and budget/time allow. Terminals: SUCCEEDED, FAILED, CANCELLED, TIMED_OUT.
- W1 mock verification deterministic and artifact-generating.
- W2 real local sandbox for public sample repository; W3 git diff and tests.
- PR creation optional W4 and only via platform-controlled GitHub App with review, never direct worker push.

## 6. Gateway behavior
- POST `/v1/chat/completions`, GET `/v1/models` (W1 non-stream only); `stream: true` -> 422 with clear unsupported-feature error.
- Gateway exclusively uses managed API/licensed resources; any personal subscription resource is rejected.
- Stable public model aliases; actual provider/model mapping server-side; never let caller select arbitrary credentials.
- Separate rate/budget policies from task marketplace; both write usage records.

## 7. Pricing and settlement
W1 uses *simulated* estimated cost and internal accounting. Later: prepaid ledger, distinct escrow/hold, finalized charge, provider payable, refund and chargeback. Never represent simulated prices as provider price list. Gateway bill by provider usage plus platform fee; tasks quote upper bound and charge on pre-agreed terms. Real Stripe and payouts are P2 and require financial/legal review.

## 8. UI pages
- `/`: landing, entry into Tasks and Gateway.
- `/tasks/new`: repo, SHA, text, budget/duration, submit.
- `/tasks/[id]`: status, event stream, artifact links, cancellation.
- `/gateway`: API key management (W4) and request demo.
- `/provider/nodes`: registered nodes, capacity, heartbeat (W4).
- `/admin/resources`: authorization review (W5).
W1 API-only vertical slice is explicitly acceptable; W4 ships UI.

## 9. Out of scope for MVP
- Renting, pooling, reselling individual ChatGPT/Claude plans.
- Multi-account rotation, session-cookie relay, credential scraping, arbitrary consumer shell commands.
- Private repositories running on unknown community hosts.
- Marketplace fiat payouts, tax handling, subscription balance estimation by screen scraping.
- General-purpose SIWC-powered API gateway.

## 10. Release gates and measurable KPIs
G1: API contract tests pass, schema migrates cleanly, mock E2E lifecycle succeeds, idempotency verified.
G2: isolated real execution only for test/public repos; no credential leakage.
G3: worker lease failover, hard resource eligibility, duplicate result idempotence.
G4: gateway contract compatibility for documented subset and explicit capability errors.
G5: benchmark: p50/p95 latency, price per verified success, attempt vs task success, worker utilization, budget-stop events.
G6: public beta with observability and threat model signoff, after separate terms check.

## 11. Open questions tracked, not blockers
Actual tariff model, jurisdiction of funds flow, license verification workflow, GitHub App permission approval, model selection + context limit, commercial SIWC partner acceptance, Anthropic-specific credentials/SDK capabilities. Default to managed API + mock until verified.
