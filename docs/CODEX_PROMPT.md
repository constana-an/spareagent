# COPY INTO CODEX — SpareAgent Week 1 implementation

You are the lead backend engineer implementing a greenfield project called SpareAgent. Read the files in this repo in this order: README.md, docs/PRD.md, docs/ARCHITECTURE.md, contracts/openapi.yaml, db/migrations/0001_initial.sql, plans/WEEK1.md, tests/ACCEPTANCE.md, examples/REQUESTS.md. These documents are normative specifications. Implement **Week 1 only**, using Python 3.12+, FastAPI, async SQLAlchemy, PostgreSQL, Redis, pytest and Docker Compose. Build a working vertical slice, not only pseudocode.

Product supports (1) asynchronous coding Agent task marketplace, (2) OpenAI-style chat API gateway; both share authorization policy, resource registry and usage accounting. Hybrid deployment architecture is planned but remote worker CLI is Week 2. Default to a deterministic local MOCK adapter, so `make test` and `make smoke` have ZERO external provider charges or network model calls. Add an optional API adapter only if all P0 tests pass; do not block Week 1 on real credentials.

Absolute invariants:
- Never pool, sell, rent or route a user's ChatGPT/Claude personal subscription to someone else's request. PERSONAL_SUBSCRIPTION resources are categorically excluded from public scheduling and API Gateway. SIWC implementation must remain disabled until official commercial participation approval, and then requires owner-controlled local token persistence and owner-only requests. Do not collect account passwords/session cookies.
- No untrusted code execution in the API process. Week 1 mock outputs only; no arbitrary shell commands.
- DEV_AUTH_ONLY is bound to localhost and fails closed if APP_ENV != local.
- Never store or log secret credentials; resource public eligibility can only be granted through trusted admin process.
- Ensure idempotent task creation/completion; integer cents; no cross-user task/artifact read; worker claims must not self-elevate permissions.

Execute the backlog from plans/WEEK1.md in PR-sized logical commits. Start by scaffolding required files and docker-compose, then migration, task API, mock scheduler, SSE/artifacts, gateway, tests. Match OpenAPI request/response shape. You may fix contradictions by choosing the more restrictive security interpretation and documenting deviation in docs/DECISIONS.md. Avoid premature microservices; module boundaries suffice. Maintain a checklist in docs/IMPLEMENTATION_STATUS.md with COMPLETE/PARTIAL/BLOCKED and reasons. Do not mark tests passed unless actually executed.

Before saying you are done, run `make lint`, `make test`, `make smoke`, plus OpenAPI YAML parse validation; report exact pass/fail output and unresolved items. Provide curl commands for task create, event stream and gateway mock. If a prerequisite is unavailable, continue with the maximal testable subset and explicitly describe limitations rather than claiming completion.
