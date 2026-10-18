# Implementation status — Week 1

> Week 2 (remote worker network) status: see [WEEK2_STATUS.md](WEEK2_STATUS.md). The Week 1
> checklist below is unchanged except where noted.

Status values: **COMPLETE** (implemented and verified by an executed test), **PARTIAL**
(implemented with a stated gap), **BLOCKED** (not done, with reason).

Last verified run (2026-10-18, Docker Desktop 28.5.1, macOS host), from empty volumes:
`make clean && docker compose up --build -d && make migrate && make seed-dev && make seed-dev && make lint && make test && make smoke`
→ every step exit 0; **187 tests passed, 0 failed, coverage 94.8 %** (gate 80 %); smoke `SMOKE OK`.

## Backlog

| Day | Item | Status | Notes |
|---|---|---|---|
| 1 | Repository tree, Python 3.12, FastAPI, SQLAlchemy 2 async, asyncpg, Pydantic v2, redis.asyncio, pytest, httpx, Ruff | COMPLETE | Tree follows ARCHITECTURE §1; W2+ files (`sandbox.py`, worker CLI, `anthropic_api.py`, `apps/web`) intentionally absent. |
| 1 | Compose: Postgres 16, Redis 7, API, local mock worker; API on 127.0.0.1:8000; `.env.example` | COMPLETE | Healthchecks on postgres/redis/api. |
| 1 | Config validation: DEV_AUTH_ONLY requires APP_ENV=local; unknown provider mode fails | COMPLETE | SEC-003 (unit + subprocess startup test). |
| 1 | /healthz, /readyz, request-id middleware, JSON error envelope, structured JSON logs | COMPLETE | readyz 503 tested for DB down and Redis down. |
| 1 | Makefile up/down/migrate/seed-dev/test/lint/smoke | COMPLETE | Plus `validate-openapi`, `test-unit`, `format`, `logs`, `clean`. |
| 1 | CI | PARTIAL | `.github/workflows/ci.yml` written; **never executed** (no remote/CI here). |
| 2 | Transactional migration + tracking table (no Alembic) | COMPLETE | Checksum immutability + rollback-on-failure tested; seed repeatable (tested twice). |
| 2 | ORM models matching SQL exactly; DAOs | COMPLETE | Column/type/nullability equality test against live schema. |
| 2 | POST /v1/tasks strict validation + digest; owner only from principal | COMPLETE | |
| 2 | Idempotency (same key/body → same task 202; different body → 409) | COMPLETE | IDEMP-001/002/003 incl. 10 concurrent requests. |
| 2 | GET list/get, cancel, row-level ownership | COMPLETE | Cursor pagination. |
| 2 | CREATED→QUEUED + events in same tx; best-effort Redis wake-up; periodic scan | COMPLETE | Lost wake-up + worker restart verified live. |
| 3 | ResourceEligibilityPolicy (DB + app layer) | COMPLETE | POLICY-001/002 incl. dropped-constraint tamper test. |
| 3 | Scheduler: locking, capacity, deterministic UUID tie-break | COMPLETE | `FOR UPDATE SKIP LOCKED` on tasks and workers. |
| 3 | Leases (60 s), attempt counter, local mock worker | COMPLETE | |
| 3 | Deterministic MockProviderAdapter, no network | COMPLETE | Socket-guard test; fixture SUMMARY/PATCH/TEST_REPORT/LOG. |
| 3 | Transition validator; status only via transition service | COMPLETE | |
| 3 | Retries on injected transient errors (max 2), exponential backoff, expired leases rejected, cancel checked before artifacts | COMPLETE | |
| 4 | SSE with Last-Event-ID replay, monotonic seq, keepalive, auth errors | COMPLETE | Keepalive path not exercised by a test (needs a long-lived stream). |
| 4 | Local artifact store: content-addressed, SHA-256, traversal prevention; descriptor endpoint | COMPLETE | + owner-only content endpoint (D-07). |
| 4 | Gateway GET /v1/models, POST /v1/chat/completions (non-stream, mock-chat) | COMPLETE | 422 UNSUPPORTED_STREAMING, 404 MODEL_NOT_FOUND, 429 RATE_LIMITED. |
| 4 | Persist gateway request + usage (non-billable mock estimates) | COMPLETE | |
| 5 | Contract tests from OpenAPI; parser validation; routes match | COMPLETE | Found and fixed a YAML defect in the shipped contract (D-01). |
| 5 | scripts/smoke.sh | COMPLETE | 18 checks, no credentials. |
| 5 | Coverage ≥ 80 % on core | COMPLETE | 94.8 % total. |
| 5 | Migration from empty DB + seed twice | COMPLETE | |
| 5 | Docs (architecture/API/demo/limitations) | COMPLETE | README.md, this file, DECISIONS.md. |
| 5 | Optional live OpenAI adapter | BLOCKED | Deliberately not implemented: stretch goal, would need user-supplied API key and spend approval. `PROVIDER_MODE` accepts only `mock`. |
| — | PR-sized commit sequence (PR#1..PR#6) | PARTIAL | Week 1 landed as a single commit on `main` after the repo was initialised; Week 2 uses a feature branch with smaller commits. |

## Mandatory test IDs

| ID | Test(s) | Result |
|---|---|---|
| AUTH-001 | `test_tasks_api.py::test_auth_001_cross_user_access_is_404_without_leak`, `test_events_artifacts.py::test_cross_user_artifact_denied` | PASS |
| AUTH-002 | `test_tasks_api.py::test_auth_002_provider_cannot_self_approve` | PASS |
| POLICY-001 | `test_leases.py::test_policy_001_personal_public_eligible_violates_db_constraint` | PASS |
| POLICY-002 | `test_leases.py::test_policy_002_*` (2), `unit/test_policy.py::test_policy_002_*` | PASS |
| IDEMP-001/002/003 | `test_idempotency.py` | PASS |
| TASK-001 | `e2e/test_mock_task_flow.py::test_task_001_full_lifecycle`, `test_events_artifacts.py::test_task_001_*` | PASS |
| TASK-002 | `unit/test_task_schema.py::test_task_002_bad_sha`, `test_tasks_api.py::test_task_002_missing_sha_is_422` | PASS |
| TASK-003 | `test_tasks_api.py::test_cancel_queued_task`, `test_leases.py::test_task_003_cancel_while_running_publishes_nothing` | PASS |
| LEASE-001/002/003 | `test_leases.py::test_lease_00{1,2,3}_*` | PASS |
| GATE-001..004 | `test_gateway_api.py::test_gate_00{1,2,3,4}_*` | PASS |
| BILL-001 | `test_leases.py::test_bill_001_*`, LEASE-002 asserts one usage row | PASS |
| BILL-002 | `unit/test_budget.py::test_bill_002_*`, `test_leases.py::test_bill_002_*` | PASS |
| SEC-001 | `unit/test_security_units.py::test_sec_001_*`, `test_events_artifacts.py::test_sec_001_*` | PASS |
| SEC-002 | `unit/test_security_units.py::test_sec_002_*`, `test_events_artifacts.py::test_sec_002_*` | PASS |
| SEC-003 | `unit/test_security_units.py::test_sec_003_*` | PASS |

## Known limitations / unresolved

1. **No production authentication.** JWT/OIDC is not implemented; with `DEV_AUTH_ONLY=false` every authenticated route returns 401 (fail closed). Not deployable beyond localhost.
2. ~~Remote workers disabled~~ — resolved in Week 2 (node credentials + lease protocol, D-20..D-30).
3. **No real execution.** Mock outputs only; repositories are never cloned; no sandbox (W2/W3 gate).
4. **Budget hold is not persisted.** The reserve check (estimate + 10 % margin vs. remaining budget) runs at dispatch time; there is no ledger/hold row yet (W6).
5. **Gateway rate limiter fails open** when Redis is down (D-17).
6. **SSE uses DB polling** (0.5 s) rather than push; fine for W1 load, revisit for scale.
7. **Worker logs full tracebacks** while the schema is missing (before `make migrate`); it recovers automatically once migrated.
8. **Environment note:** the project lives under an iCloud-synced `~/Documents` folder. Spec files had been evicted to "dataless" placeholders, which made shell `cp`/`cat` hang. If iCloud evicts project files, `docker compose build` can hang the same way — keep the folder downloaded or move it outside iCloud Drive.
