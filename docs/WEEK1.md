# Week 1 — Exact Implementation Backlog

## Definition of Done
Fresh clone + env + `docker compose up --build -d && make migrate && make seed-dev && make test && make smoke` exit 0. All integration tests use PostgreSQL + Redis containers; default provider is MOCK, no paid API calls; the mocked public code task travels QUEUED -> RESERVED -> RUNNING -> VERIFYING -> SUCCEEDED, with durable events and inspectable artifact. Gateway responds with documented OpenAI-style non-stream response. Security invariants tested.

## Day 1 — Bootstrapping (P0, 4–6 hours)
1. Create repository tree per ARCHITECTURE.md; Python 3.12+, FastAPI, SQLAlchemy 2 async, asyncpg, Pydantic v2, Alembic optional, redis.asyncio, pytest, httpx, Ruff.
2. Docker Compose for PostgreSQL 16, Redis 7, API, local mock worker. `api` binds localhost:8000 by default. Add `.env.example`: APP_ENV=local, DEV_AUTH_ONLY=true, DATABASE_URL, REDIS_URL, PROVIDER_MODE=mock, OPENAI_API_KEY= (unset).
3. Implement app config validation: reject DEV_AUTH_ONLY=true with APP_ENV != local; hard fail if unknown provider mode.
4. Implement /healthz, /readyz, request ID middleware, JSON standard errors, simple structured logging.
5. Create Makefile targets: up/down/migrate/seed-dev/test/lint/smoke.
6. Test: compose services become healthy, readyz is 200 with DB/Redis and 503 when one is unavailable.

## Day 2 — Persistence and task API (P0, 5–7 hours)
1. Apply 0001_initial.sql transactionally; add migration tracking table or use Alembic with an equivalent revision, NOT both systems in production.
2. Implement SQLAlchemy models matching SQL exactly; DAO for tasks, task_attempts, execution_events, resources, worker_nodes, artifacts, gateway_requests, usage_records.
3. Implement POST /v1/tasks with strict URL/SHA/prompt/budget validation; normalize request body JSON and store SHA-256 digest. Only owner identity from authenticated principal.
4. Enforce unique idempotency key per user. Same key/body => same task and 202; different body => 409 IDEMPOTENCY_CONFLICT.
5. Implement GET /v1/tasks, GET /v1/tasks/{id}, POST /v1/tasks/{id}/cancel. Row-level ownership check on all paths.
6. On successful creation transition CREATED to QUEUED and emit events in SAME DB transaction, then publish wake-up to Redis best-effort (periodic queue scan repairs lost wake-ups).
7. Test: invalid repository, missing SHA, cross-user IDOR, 10 parallel same-key requests, budget=0, overlong prompt, cancel queued.

## Day 3 — Scheduler and execution (P0, 6–8 hours)
1. Implement ResourceEligibilityPolicy (public eligibility approved + resource type != PERSONAL_SUBSCRIPTION); never trust client-supplied eligibility.
2. Implement scheduler selecting idle approved worker with transaction locking and capacity check; deterministic UUID tie-break.
3. Implement lease creation, 60 s expiration and attempt counter. W1 local mock worker drains queue and simulates status events.
4. Implement `MockProviderAdapter` with deterministic output keyed by task id; generate SUMMARY + PATCH-like fixture output, not actual code execution. No network calls.
5. Implement state transition validator and atomic event sequence; `status` never updated outside transition service.
6. Implement retries on injected transient mock errors; max attempts 2, expired leases rejected; cancellation checks before processing artifacts.
7. Test: no approved workers means QUEUED; personal resource is never eligible; duplicate worker completion cannot finalize twice; crash/restart recovers stuck leases.

## Day 4 — SSE, artifacts and gateway (P0, 6–8 hours)
1. Implement SSE GET /v1/tasks/{id}/events, replay `Last-Event-ID`, monotonic seq; keepalive comments while connected; error on unauthorized access.
2. Implement local artifact backend under non-web-accessible root, content-addressed keys, SHA-256, path traversal prevention; descriptor endpoint.
3. Implement gateway GET /v1/models and POST /v1/chat/completions, nonstream only using `mock-chat` alias. Reject `stream:true` as 422 UNSUPPORTED_STREAMING and unknown aliases as 404 MODEL_NOT_FOUND. Never route to personal resources.
4. Persist gateway request + usage in DB; include prompt/completion/total tokens as deterministic MOCK estimates labeled non-billable.
5. Test: SSE replay and event ordering, cross-user artifact denial, gateway does not enumerate PERSONAL_SUBSCRIPTION aliases.

## Day 5 — Hardening and demonstration (P0, 5–7 hours)
1. Add contract tests from OpenAPI; validate openapi.yaml with parser, confirm docs routes match.
2. Write `scripts/smoke.sh`: seed dev account, create task, poll <=30s, inspect artifacts/events, call gateway, assert statuses and counts. No real model credentials.
3. Run pytest with coverage target >=80% for tasks/policy/scheduler/gateway core; add explicit negative security tests.
4. Run migration from empty DB AND seed twice to confirm repeatable seed; fresh-clone reproducibility in CI.
5. Write documentation for architecture and API; capture demo commands/expected JSON output, known limitations.
6. Optional stretch: live OpenAI API adapter behind `PROVIDER_MODE=openai` with documented credential setup and explicit user-requested spending, never enabled in CI.

## Mandatory test case IDs
- AUTH-001 consumer cannot see another consumer task; AUTH-002 provider cannot self-approve resource.
- POLICY-001 personal resource public_eligible=true fails DB constraint; POLICY-002 scheduler rejects personal resource even if malicious fixture bypassed UI.
- IDEMP-001 same key + same body returns same ID; IDEMP-002 same key + different body 409; IDEMP-003 10 concurrent submissions result one row.
- TASK-001 create->success contains ordered events; TASK-002 invalid commit SHA 422; TASK-003 cancellation prevents post-cancel publishing.
- LEASE-001 expiration allows bounded retry; LEASE-002 duplicate completion produces one finalization; LEASE-003 expired lease 409.
- GATE-001 model list includes mock-chat; GATE-002 non-stream response schema matches; GATE-003 stream true 422; GATE-004 no PERSONAL_SUBSCRIPTION routing.
- BILL-001 no duplicate usage on repeated callbacks; BILL-002 never exceeds authorized estimated budget to start a task.
- SEC-001 accidental token in mock log is redacted; SEC-002 artifact key traversal rejected; SEC-003 DEV_AUTH_ONLY cannot start in production.

## Week 1 pull requests and commit sequence
PR#1 `chore/bootstrap`: compose, config, health, CI.
PR#2 `feat/db`: SQL migration, models, repository.
PR#3 `feat/tasks`: tasks API, idempotency, auth, state transitions.
PR#4 `feat/worker`: scheduler, policy, mock adapter, retries.
PR#5 `feat/gateway`: SSE, artifacts, chat endpoint, usage.
PR#6 `test/docs`: smoke, security and contract tests, docs, release gate.
Each PR includes tests and does not break `make test`. No silent feature changes.

## Stop conditions
Do not proceed to live community node execution if any IDOR, personal-subscription routing, credentials in logs, over-budget dispatch, replay double-finalization or unsafe exec bug remains. Week 1 deliberately has no arbitrary repository execution.
