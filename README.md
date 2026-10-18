# SpareAgent — Week 1 vertical slice

Mock-first implementation of the SpareAgent MVP: an asynchronous coding-task marketplace and
an OpenAI-style chat gateway sharing one authorization policy, resource registry and usage
accounting. Week 1 runs **only** the deterministic local MOCK provider: no model calls, no
network calls, no repository checkout, no code execution, no charges.

Specifications: `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/WEEK1.md`, `docs/ACCEPTANCE.md`,
`contracts/openapi.yaml`, `db/migrations/0001_initial.sql`. Status: `docs/IMPLEMENTATION_STATUS.md`.
Deviations: `docs/DECISIONS.md`.

## Policy invariants (enforced in code and tests)
- `PERSONAL_SUBSCRIPTION` resources are never scheduled for marketplace tasks and never routed by
  the gateway — enforced by DB CHECK constraints **and** `services/scheduler/policy.py`. There is
  no subscription resale, pooling or SIWC code in this repository.
- Public eligibility can only be granted by an ADMIN through an audited endpoint.
- `DEV_AUTH_ONLY=true` is refused at startup unless `APP_ENV=local`, and is honoured only for
  localhost requests.
- Secrets are redacted from logs, events and artifacts; budgets are integer cents.

## Quick start
Requires Docker (Compose v2), `make`, `curl`, `python3`.
```bash
cp .env.example .env
docker compose up --build -d
make migrate && make seed-dev
make lint && make test && make smoke
```
API docs: http://localhost:8000/docs · health: `/healthz`, `/readyz`.

| Target | What it does |
|---|---|
| `make up` / `make down` | start (waits for health) / stop the stack |
| `make migrate` | apply `db/migrations/NNNN_*.sql` transactionally (seed files excluded) |
| `make seed-dev` | load `0002_seed_dev.sql` (local only, repeatable) |
| `make lint` | ruff check + ruff format --check + OpenAPI/event-schema validation |
| `make test` | full pytest suite in the `test` container, isolated DB `spareagent_test`, coverage gate 80 % |
| `make smoke` | `scripts/smoke.sh` against the running stack |
| `make clean` | stop and delete volumes |

## Layout
```
apps/api/            FastAPI app: config, routes, schemas, middleware (auth, request id)
services/tasks/      task service, repository, state machine (only writer of tasks.status)
services/scheduler/  eligibility policy, dispatcher, lease service, scoring
services/execution/  orchestrator, mock verifier, local worker process
services/gateway/    model aliases + chat gateway
services/metering/   integer-cent estimator, usage recording (deduplicated)
services/artifacts/  content-addressed local store with traversal protection
services/registry/   admin-only resource approval
packages/provider_sdk/  adapter protocol, types, deterministic MockProviderAdapter
packages/worker/     spareagent-worker CLI: state, HTTPS node client, agent loop
services/registry/node_auth.py  enrollment + node credentials (W2)
services/execution/remote.py    server side of the remote lease protocol (W2)
services/scheduler/runner.py    central expiry/retry/reconciliation loop (W2)
db/                  models (mirror of SQL), migrator, migrations
tests/               unit / integration / e2e (226 tests)
```

## Demo (seeded consumer `11111111-1111-4111-8111-111111111111`)
Create a task (202, `status` = `QUEUED`):
```bash
curl -i http://localhost:8000/v1/tasks -H 'Content-Type: application/json' -H 'X-Dev-User-ID: 11111111-1111-4111-8111-111111111111' -H 'Idempotency-Key: example-task-0001' -d '{"task_type":"WRITE_TESTS","repository_url":"https://github.com/example/public-fixture","commit_sha":"0123456789abcdef0123456789abcdef01234567","prompt":"Write two tests for the demo fixture","max_budget_cents":500,"max_duration_seconds":120}'
```
```json
{"id":"<uuid>","status":"QUEUED","task_type":"WRITE_TESTS","max_budget_cents":500,"created_at":"2026-10-18T04:07:23.1Z","result_summary":null}
```
Stream events (closes after the terminal event; add `-H 'Last-Event-ID: 3'` to replay from seq 4):
```bash
curl -N -H 'X-Dev-User-ID: 11111111-1111-4111-8111-111111111111' http://localhost:8000/v1/tasks/TASK_UUID/events
```
```
id: 1
event: task.created
data: {"task_id":"<uuid>","seq":1,"type":"task.created","timestamp":"...","payload":{"status":"CREATED"}}
... task.queued, task.reserved, task.started, task.progress x2, task.verifying ...
id: 8
event: task.succeeded
data: {"task_id":"<uuid>","seq":8,"type":"task.succeeded","timestamp":"...","payload":{"from":"VERIFYING","status":"SUCCEEDED","result_summary":"Mock WRITE_TESTS result ...","artifact_count":4}}
```
Artifacts (descriptors; content via `/v1/tasks/TASK_UUID/artifacts/ARTIFACT_UUID/content`):
```bash
curl -H 'X-Dev-User-ID: 11111111-1111-4111-8111-111111111111' http://localhost:8000/v1/tasks/TASK_UUID/artifacts
```
Gateway mock (200 `chat.completion`; header `X-SpareAgent-Billable: false`):
```bash
curl http://localhost:8000/v1/chat/completions -H 'Content-Type: application/json' -H 'X-Dev-User-ID: 11111111-1111-4111-8111-111111111111' -d '{"model":"mock-chat","stream":false,"messages":[{"role":"user","content":"Say hello"}]}'
```
```json
{"id":"chatcmpl-<hex>","object":"chat.completion","created":1792303643,"model":"mock-chat","choices":[{"index":0,"message":{"role":"assistant","content":"This is a deterministic mock response from mock-chat-v1 (no model was called). Digest ..."},"finish_reason":"stop"}],"usage":{"prompt_tokens":7,"completion_tokens":26,"total_tokens":33}}
```
`"stream": true` → 422 `UNSUPPORTED_STREAMING`; unknown model → 404 `MODEL_NOT_FOUND`.

### Week 2: remote workers
Remote nodes run `spareagent-worker` (installed by `pip install .`), connect **outbound over
HTTPS** with node-scoped credentials and never receive database credentials.
```bash
make up && make migrate && make seed-dev
make e2e-remote          # 3 worker containers, 10 tasks, one SIGKILLed mid-run
cat reports/week2_e2e_report.json
```
Manual flow: an admin (dev seed `55555555-5555-4555-8555-555555555555`) mints a single-use
token, then the node registers and starts:
```bash
curl -s http://localhost:8000/v1/enrollment-tokens -H 'Content-Type: application/json' -H 'X-Dev-User-ID: 55555555-5555-4555-8555-555555555555' -d '{"resource_id":"33333333-3333-4333-8333-333333333333"}'
```
```bash
spareagent-worker register --api-url https://localhost:8443 --ca-file ca.pem --resource-id 33333333-3333-4333-8333-333333333333 --name node-1 --enrollment-token sa_enr_...
```
(`ca.pem`: `docker compose cp api-tls:/certs/ca.pem ca.pem`.) Protocol details:
`docs/ARCHITECTURE.md` §11; decisions D-20..D-32 in `docs/DECISIONS.md`; status in
`docs/WEEK2_STATUS.md`.

## Mock fault injection (tests/demo only)
Put a marker in the prompt: `[mock:transient]` (attempt 1 fails, retry succeeds), `[mock:fail]`
(permanent failure), `[mock:bad-patch]` (verification fails).
