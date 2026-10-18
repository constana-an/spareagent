# SpareAgent MVP — Weeks 2–6 Roadmap

| Week | Focus | Deliverables | Ship gate |
|---|---|---|---|
| 2 | Hybrid worker protocol | provider CLI, outbound-only worker, registration, signed node auth, lease renewal/expiry, capacity reservation | 3 local/remote mock workers; disconnect one; bounded retry, no lost/double finalization |
| 3 | Safe code execution | hardened sandbox, public repo pinned checkout, patch generation, offline unit tests, independent verifier, artifact retrieval | No host secret access in red team fixtures; deterministic PR-ready patch on demo repo |
| 4 | User interface + GitHub | Next.js task submission/status/SSE, GitHub sign-in or verified GitHub App auth, read-only repo integration, PR draft via restricted platform GitHub App | Unprivileged worker can't write to origin repo; users can trace completed task |
| 5 | Gateway + provider quality | real managed OpenAI adapter, Anthropic commercial API adapter, provider limits, cost rate snapshots, scheduler score, metrics and load tests; sandbox egress restrictions | Cost/attempt reconciliation, unknown alias errors, rate-limit/backoff; no personal subscription routing |
| 6 | Beta hardening | real billing sandbox (no payouts), lifecycle reports, instrumentation, operator approval UI, onboarding and public demo; security/legal checklist | end-to-end beta with 3 resources, documented incidents & shutdown controls |

## Deferred to post-MVP
Commercial payout onboarding/KYC/tax, private code on community nodes, cross-region data residency, SIWC partner application/approved personal runtime, sophisticated spot pricing, distributed tracing beyond basic metrics, escrow/dispute arbitration and formal compliance review.

## Benchmark suite
Use curated public issues with pinned repository commit and explicit tests. Record provider/model/rate version, per-attempt and per-task success, retry count, latency (p50/p95), cost per verified success, failure labels. Compare random eligible, lowest-estimated-cost, and weighted selector under fixed workload seeds. Do not claim cost savings until measured.
