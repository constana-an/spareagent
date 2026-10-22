# SpareAgent

**把自己 Mac 上的 Codex 额度一键分享给朋友，替他们执行任务。**
*Share the Codex allowance of your own ChatGPT plan with a small group of friends, one click on your own Mac.*

A small, invite-only, non-commercial experiment. Every task runs on the owner's own Mac with
the owner's own Codex login; credentials never leave that Mac and there is no payment,
resale or paid fallback.

## How it works

| Who | Does | Needs |
|---|---|---|
| **Operator** | Runs the private coordinator (API, database, scheduler, verifier, dashboard) and privately hands out the Mac package | Docker; this repository |
| **Owner** (provides compute) | Runs the local installer once to build `SpareAgent.app` on their Mac, then opens the App, reads the sharing terms and goes online: Codex is installed, the owner signs in to their own ChatGPT, a group and invite link are created. A menu-bar icon controls pause and quit | A Mac, Apple command line tools for the first local build, their own ChatGPT Plus/Pro, and access to the private coordinator (currently Tailscale) |
| **Member** (uses compute) | Opens the invite link, submits tasks with attachments, follows progress, downloads results | A browser (the dashboard is public via Tailscale Funnel, D-102; without it, Tailscale access) |

Tasks are approved by the owner's standing approval, dispatched to the owner's Mac, run by
Codex in a per-task sandboxed folder, checked by the platform and delivered as files.

The default free installer builds the menu-bar App locally once; later launches use the App directly. No paid Apple developer membership is required. See [local installation](docs/LOCAL_APP_INSTALL.md). This is not Apple notarization; first-install behavior on another Mac remains to be verified.

## What members can ask for

| Kind | Delivers |
|---|---|
| **General task** (default) | Discussion, writing, translation, tables, edits to uploaded code files, pictures drawn with code (SVG/PNG): `answer.md` + up to 5 files |
| Translate a document | `.docx` / `.md` / `.txt` in the target language (paragraph count and script checked) |
| Analyze spreadsheets | `report.md` + charts + cleaned tables from `.csv` / `.xlsx` |
| Change code / fix / write tests / review | Patch or review of a public repository at a pinned commit |

- **Internet (opt-in per task):** a member ticks *needs internet*; only devices whose owner
  allows it (on by default, can be switched off) run it with web access. File access stays
  limited to the task's own folder either way.
- **Owner controls:** group size (2–5), tasks per 24 h, minutes per task, 1–3 tasks at a
  time, allowed kinds and tools, auto-approval, invite rotation, member removal, and
  **per-member usage and caps** (tasks and execution minutes per 24 h).
- **Member view:** queue position and the reason a task waits, progress, Chinese failure
  reasons with advice, cancel, download with SHA-256 check, own usage and caps.
- **Recovery:** a task the owner hands back or that loses its device re-runs once; a paused
  owner still takes requests, which wait and say why.
- **HTTPS:** the current package uses Tailscale Serve with a browser-trusted certificate; no manual CA import. Owners' devices and the API stay on the tailnet; the member dashboard alone can be public through Tailscale Funnel (D-102), so members need only a browser. Legacy packages can list several server addresses; the app checks HTTPS readiness and picks a reachable one or the owner’s choice.
- Chinese by default, English on request.

## Limits to know

- Results are **structure-checked only** (formats, required files, no active content);
  nobody checks that an answer or translation is right.
- All one-click kinds were opened for real execution by operator decision without
  per-kind acceptance; real-device acceptance is still to be done ([design §7](docs/GENERAL_TASKS_DESIGN.md)).
- Groups are capped at 5 people by the written authorization the operator relies on.
- The Mac App currently uses ad-hoc signing for local testing, without Developer ID notarization; first-open security approval may still be required.

## Documentation

| For | Read |
|---|---|
| Owners | [docs/ONE_CLICK_SHARING.md](docs/ONE_CLICK_SHARING.md) |
| Members | [docs/MEMBER_GUIDE.md](docs/MEMBER_GUIDE.md) |
| Design of task kinds, execution, internet, recovery | [docs/GENERAL_TASKS_DESIGN.md](docs/GENERAL_TASKS_DESIGN.md) |
| Every decision and why | [docs/DECISIONS.md](docs/DECISIONS.md) (D-90 onwards for the current release) |
| API | [contracts/openapi.yaml](contracts/openapi.yaml) |
| Private Mac deployment | [docs/MAC_PRIVATE_DEPLOYMENT.md](docs/MAC_PRIVATE_DEPLOYMENT.md) |
| Maintain the existing Mac deployment | [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md) |

## Operator: build the Mac package

For the existing private Mac deployment, double-click `scripts/SpareAgent-Operator.command`.
It reads the saved configuration, backs up data, starts or restores services, checks HTTPS,
and builds or reuses the private source installer. It preserves the real-execution switch
and does not run inference. Use `--rebuild` when service images need rebuilding from the
current source. See [operator maintenance](docs/OPERATOR_GUIDE.md).

For separate packaging or legacy deployments:

```sh
python scripts/build_worker_bundle.py \
  --network "lan:局域网=https://LAN_IP:18443,https://LAN_IP:13443" \
  --network "tailscale:Tailscale=https://TS_IP:18443,https://TS_IP:13443" \
  --signup-code-file private/signup-code.txt --ca-file ca.pem
```

`output/worker-bundle/SpareAgent-mac.zip` contains the signup code: share it privately.
On macOS, follow with `python scripts/build_macos_app.py --arch arm64` to produce the
desktop App ZIP with bundled Python. Only Apple Silicon has been tested on this Mac;
the Intel build option remains unverified on an Intel device.
The server must list each extra dashboard address in `FRIENDS_WEB_EXTRA_ORIGINS`, listen on
it, and its certificate must cover it. Real execution is switched by `EXPERIMENT_LIVE_ENABLED`
plus an operator-verified authorization ([ONE_CLICK_SHARING.md](docs/ONE_CLICK_SHARING.md)).

## Development

```bash
cp .env.example .env
docker compose up --build -d
make migrate && make seed-dev
make lint && make test
```

`make test` runs about 600 tests (unit, integration, end-to-end acceptance script with the
real Worker agent) against an isolated database; Docker-sandbox and network tests are
skipped when their prerequisites are missing. The web dashboard (`apps/web`) has its own
`npm run typecheck`, `npm test` and `npm run build`.

---

# History: earlier milestones

The sections below record how the project got here (weeks 1–6). Some describe setups that
have since changed; the sections above describe the current release.

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
does not occupy a participant slot. (Week 6 was capped at two users; one-click groups now
allow up to five.)

Week 6 used LAN HTTPS. The current release also supports a Tailscale address next to the
LAN one (see *Networks* above). Since D-102 only the member dashboard port may be served through Tailscale Funnel; the API and devices remain tailnet-only.
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
tests/               unit / integration / e2e
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
