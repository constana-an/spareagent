# Technical Architecture and Repository Contract

## 1. Repository tree (target implementation)
```text
spareagent/
  README.md
  Makefile
  .env.example
  docker-compose.yml
  pyproject.toml
  apps/
    api/
      __init__.py
      main.py
      config.py
      dependencies.py
      routes/
        __init__.py
        health.py
        tasks.py
        gateway.py
        workers.py
      schemas/
        __init__.py
        tasks.py
        gateway.py
        workers.py
      middleware/
        request_id.py
        auth.py
    web/                              # W4 Next.js
      package.json
      app/
        tasks/new/page.tsx
        tasks/[id]/page.tsx
        gateway/page.tsx
        provider/nodes/page.tsx
  services/
    __init__.py
    tasks/
      service.py
      state_machine.py
      repository.py
    queue/
      publisher.py
      consumer.py
    scheduler/
      policy.py
      scoring.py
      lease_service.py
    execution/
      orchestrator.py
      sandbox.py                   # W2
      verifier.py                  # W3
    gateway/
      service.py
      aliases.py
    metering/
      service.py
      estimator.py
    artifacts/
      store.py
    registry/
      service.py
  packages/
    __init__.py
    provider_sdk/
      types.py
      base.py
      mock.py
      openai_api.py               # optional live integration W1
      anthropic_api.py            # W5
    worker/
      cli.py                       # W2
      agent.py                     # W2
      lease_client.py              # W2
  db/
    migrations/
      0001_initial.sql
      0002_seed_dev.sql
    connection.py
  tests/
    unit/
      test_state_machine.py
      test_policy.py
      test_budget.py
    integration/
      test_tasks_api.py
      test_gateway_api.py
      test_leases.py
      test_idempotency.py
    e2e/
      test_mock_task_flow.py
  scripts/
    migrate.py
    seed_dev.py
    smoke.sh
  docs/
    PRD.md
    ARCHITECTURE.md
    WEEK1.md
    openapi.yaml
```

## 2. Runtime topology
- `api` FastAPI ASGI: auth, validation, REST, SSE; stateless.
- `postgres` durable system of record: users, resources, task/attempt/event, usage, leases. Only DB transactions commit authoritative states.
- `redis` task wake-up queues and ephemeral worker heartbeat cache; not source of truth.
- `worker` process: starts in local mode W1 using mock adapter; W2 remote worker polls/pulls via lease API; no inbound ports required.
- `minio` optional from W2 for artifacts; in W1 local filesystem adapter with content hashes and guarded paths.
- `web` Next.js W4. Do not block W1 on frontend.

## 3. Trust zones
1. API trust zone: customer JWT, budget/auth policies, DB.
2. Public execution zone: commercial provider API / provisioned isolated code runner. External node must never receive platform control-plane credentials.
3. Personal user-controlled zone: separate runtime, credentials stay under owner's control; owner-only request routing, no resale or aggregation.
4. Payments trust zone (future): ledger and payments isolated from worker.

## 4. Core domain interfaces
```python
from dataclasses import dataclass
from typing import AsyncIterator, Protocol, Literal

@dataclass(frozen=True)
class ExecutionRequest:
    task_id: str
    attempt_id: str
    user_id: str
    resource_scope: Literal['PUBLIC_MARKETPLACE', 'OWNER_ONLY']
    prompt: str
    repository_url: str | None
    commit_sha: str | None
    max_duration_seconds: int
    max_budget_cents: int

@dataclass(frozen=True)
class ProviderCapabilities:
    resource_type: Literal['MANAGED_API', 'LICENSED_PROVIDER', 'SELF_HOSTED', 'PERSONAL_SUBSCRIPTION']
    supports_code: bool
    supports_chat: bool
    supports_stream: bool
    max_concurrency: int

class ProviderAdapter(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    async def estimate_cents(self, request: ExecutionRequest) -> int: ...
    def execute(self, request: ExecutionRequest) -> AsyncIterator[dict]: ...
    async def cancel(self, attempt_id: str) -> None: ...

class ArtifactStore(Protocol):
    async def put(self, task_id: str, name: str, content: bytes, media_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
```
Rules: adapter never computes marketplace eligibility; policy engine owns authorization. Mock adapter deterministic, no external HTTP. OpenAI API adapter uses separate `OPENAI_API_KEY`, never ChatGPT subscription tokens. Repository URL allowlist HTTPS github.com for W1, fixed SHA, no shell injection.

## 5. Scheduling algorithm
1. Transactionally lock task (`SELECT ... FOR UPDATE SKIP LOCKED`).
2. Verify task remains QUEUED and budget reserve passes.
3. Filter workers: ACTIVE, recently seen, available slot, resource.public_eligible, authorization_status=APPROVED, allowed region/capabilities, resource not PERSONAL_SUBSCRIPTION, estimate <= budget.
4. Sort eligible by normalized cost, quality, latency and availability. W1 use deterministic tie-break by worker UUID, W2 score model.
5. Lock selected worker capacity, create attempt + lease UUID with 60 s expiry, mark RESERVED, commit.
6. Worker accepts lease using short-lived node credentials, checks scope, executes, streams events, submits result with attempt/lease ID and idempotency token.
7. Transition RUNNING -> VERIFYING -> terminal in a transaction; release capacity. If timeout, revoke lease, increment attempt, retry <= max_attempts subject to budget.
8. Network retries cannot create multiple accepted completions; reject stale lease `409 LEASE_EXPIRED`.

W1 can run worker locally and simulate leases, but schema and contract must support remote workers.

## 6. Transactionality, idempotency, event ordering
- Task create: unique (`consumer_id`, `idempotency_key`), retain normalized request digest, same key + different body => 409.
- Event ordering: monotonic `seq` per task allocated transactionally; unique(task_id, seq). SSE id=seq supports `Last-Event-ID` replay.
- Lease completion: one final completion for each attempt, unique finalization key; task terminal exactly once.
- Metering: unique(attempt_id, provider_request_id), repeat provider callback ignored.
- Budget: reserve and reconcile in integer USD cents, *never* float. Provider rates snapshots by model version.

## 7. Authentication and permissions
- W1 DEV_AUTH_ONLY=true for localhost, `X-Dev-User-ID` selects seeded account; reject this mode whenever APP_ENV != local and never exposed on public network.
- Production: verified OIDC JWT with issuer/audience/JWKS, actor from claims only; never accept caller-controlled user_id from task body.
- Worker: signed short-lived bearer node token (future mTLS), scope to node ID and lease IDs; no normal user token reuse.
- Admin: separate RBAC; provider cannot self-approve `public_eligible`.

## 8. Gateway and Task coupling
- Gateway only routes to `MANAGED_API`, `LICENSED_PROVIDER`, `SELF_HOSTED`; W1 managed + mock. Map public model aliases to approved adapter/physical model.
- Chat completions are stateless (within request); coding tasks may be asynchronous and have artifacts. Share PolicyEngine, Registry, Metering but not execution handlers.
- Gateway errors use `{error:{code,message,request_id}}`; never leak upstream secrets.

## 9. Security design
- W1 mock only; live code execution disabled until W2 security gate.
- W2 sandbox: non-root, no docker.sock, read-only base, no host home mounts, strict CPU/memory/time limits, egress allowlist; consider gVisor/microVM before community production.
- Public sample repositories only initially, pin SHA and compare worktree after execution.
- Do not execute untrusted user-provided shell on API host. Sanitize logs, redact keys, constrain max input length.
- OAuth SIWC future flag disabled, only after approved app onboarding. User-controlled token storage and owner-only request checks at remote+local boundary.

## 10. Monitoring
Metrics: task_count(status), queue_wait_seconds, attempt_duration_seconds, verified_success_ratio, cost_per_verified_success_cents, lease_expiry_count, active_workers, gateway_requests_by_model, provider_rate_limit, token_usage. Logs include request_id/task_id/attempt_id, never raw secrets.
