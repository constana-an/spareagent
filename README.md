# SpareAgent — private friends pilot

SpareAgent combines a mock-first task coordinator with an invite-only, voluntary friends
experiment using each owner's own ChatGPT Plus Codex login and device. Defaults remain
simulated and LIVE-off. Real subscription tasks require manual operator activation,
owner consent and individual approval; no payments, credential sharing or paid fallback.
The original marketplace/gateway policy still excludes personal subscriptions.

Specifications: `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/WEEK1.md`, `docs/ACCEPTANCE.md`,
`contracts/openapi.yaml`, `db/migrations/0001_initial.sql`. Status: `docs/IMPLEMENTATION_STATUS.md`.
Deviations: `docs/DECISIONS.md`.

## 一键提供算力 / One-click sharing

新人解压运营者的 `SpareAgent-mac.zip`，双击 `SpareAgent.command`，点一下「上线」即可：自动安装、登录自己的 ChatGPT、创建小组与邀请码、自动批准组员任务；再点一下下线。人数与任务上限随时可调，界面默认中文。See [docs/ONE_CLICK_SHARING.md](docs/ONE_CLICK_SHARING.md) and D-68…D-72; members (browser only) see [docs/MEMBER_GUIDE.md](docs/MEMBER_GUIDE.md).

## Week 6: friends install the Worker only

Friends need the official Codex CLI and a standalone SpareAgent Worker (Python 3.12+
with Git); **no backend, database, Redis or Docker installation**. The operator runs the
private coordinator. Bob can request a review through the dashboard without a Worker.

### Device roles: 1 号 is the service host; 2 号、3 号等 are users

| Device | Role | Runs |
|---|---|---|
| **#1 / 1 号** | Main service host / operator | API, database, Redis, Scheduler, Verifier, Dashboard and private HTTPS ingress. |
| **#2, #3, etc. / 2 号、3 号等** | Users | Browser access to #1; official Codex CLI and SpareAgent Worker only when volunteering their own device. No backend installation. |

These are device labels, not database user IDs. The operator's administrative identity
does not occupy a participant slot. The current pilot is still capped at two users
(#2 and #3); further device numbers do not authorize expanding the experiment.

Use LAN HTTPS first. **Tailscale is an optional fallback** for blocked campus peer access
or off-campus users: approved devices join the private network, with private HTTPS via
Serve and explicit access rules. Funnel exposes a service publicly and is not the
SpareAgent fallback. Installing Tailscale alone does not configure the application.
See the [Mac deployment and shutdown record](docs/MAC_PRIVATE_DEPLOYMENT.md) for setup
boundaries, HTTPS changes and preserving data when stopping containers. The local stack
was validated and then stopped at the owner's request; this is not a continuously hosted
service or a completed real two-user experiment.

### Worker installation

The operator builds `python scripts/build_worker_bundle.py` and privately distributes the
reviewed macOS script, small worker wheel and SHA-256. On the friend's Mac:

```sh
bash setup-worker-macos.sh ./spareagent_worker-0.12.0-py3-none-any.whl OPERATOR_SHA256
export PATH="$HOME/.local/share/spareagent-worker/bin:$PATH"
codex login  # your own ChatGPT Plus login
spareagent-worker pilot-check
```

`pilot-check` performs **no inference** and changes no sharing/LIVE switches. It separates
automatic blockers from required owner/operator confirmations. Setup does not start the
Worker or enable LIVE. All subscription-consuming checks remain manually opted in;
no API keys, purchased/granted Credits or paid fallback. The first **real two-device Alice/Bob execution succeeded on 2026-10-19**, with
explicit owner confirmation of distinct physical devices and subscription-only funding.
Its code-review content remains **UNVERIFIED** and requires human assessment. The LIVE
window was closed after the run; later subscription-consuming tasks require a new opt-in.

Read the [first two-person test plan](docs/TWO_PERSON_TEST_PLAN.md),
[controlled runbook](docs/WEEK6_PILOT_RUNBOOK.md),
[readiness and status](docs/WEEK6_STATUS.md), [security review](docs/WEEK6_SECURITY_REVIEW.md),
[metrics definitions](docs/WEEK6_METRICS.md) and
[real pilot record](reports/week6_two_device_pilot.json).
The Docker quick start below is for the **operator/developer**, not friends.

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
services/execution/{repository_manager,sandbox,code_adapter,catalog,patch_policy}.py  (W3)
services/verification/          independent platform verifier (W3)
sandbox/Dockerfile              sandbox image: python3 + node 22 only (W3)
db/                  models (mirror of SQL), migrator, migrations
tests/               unit / integration / e2e (318 tests)
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

## Week 3: sandboxed code execution (disabled by default)
Allow-listed fixture repositories (pinned commits) can be fixed by a deterministic executor that
runs trusted tests in a hardened, network-less Docker sandbox; a separate platform verifier
re-applies the patch to a clean checkout and re-runs the tests before the task can succeed.
```bash
make e2e-code            # python + typescript fixes, isolation probe, forged-patch rejection
cat reports/week3_e2e_report.json
```
Details: `docs/ARCHITECTURE.md` §12, decisions D-33..D-43, status and **residual risks** in
`docs/WEEK3_STATUS.md`. Do not enable `CODE_EXECUTION_ENABLED` for real workloads.

## Mock fault injection (tests/demo only)
Put a marker in the prompt: `[mock:transient]` (attempt 1 fails, retry succeeds), `[mock:fail]`
(permanent failure), `[mock:bad-patch]` (verification fails).

## Week 5 private friends beta

`apps/web/` provides the invite-only Next.js dashboard for owner-controlled experimental
reviews. [Owner validation](docs/WEEK5_LOCAL_CODEX_RUNBOOK.md),
[two-participant runbook](docs/WEEK5_FRIENDS_BETA_RUNBOOK.md), and
[actual status/evidence](docs/WEEK5_STATUS.md) describe setup and manual activation.
Use `FRIENDS_AUTH_ENABLED=true` for browser sessions and `docker compose --profile web up
--build -d web` after migrating. LIVE remains off by default and is never run in CI.

LIVE results now enter the existing independent verifier: integrity, fresh pinned checkout,
clean patch application and trusted fixture tests. Unsupported repositories and reviews
carry explicit `UNVERIFIED` provenance; structural checks never imply semantic correctness.
The real owner-only check succeeded on 2026-10-18; the owner confirmed subscription-only
usage and unchanged Credits. No real Alice/Bob subscription run has been performed.
