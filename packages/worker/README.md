# Standalone friends Worker (Week 6)

Friends use the worker-only wheel and macOS setup script. They do not install this
repository's root package, requirements-dev, backend, Docker, PostgreSQL or Redis.
See [the controlled runbook](../../docs/WEEK6_PILOT_RUNBOOK.md).

Operator build: `python scripts/build_worker_bundle.py`. The package manifest in this
folder selects worker/provider SDK and dependency-free common helpers only; direct
dependency: httpx. CI installs the built wheel outside this checkout on macOS/Linux.

`spareagent-worker pilot-check` checks CLI/login/OS isolation and node-scoped coordinator
readiness with no inference. It never accepts leases or enables LIVE. Exit 1 is blocked;
2 means automatic checks passed but manual attestations remain. `--task-id UUID` checks
that device's specific owner approval too. The `code` executor is available only in the
operator distribution; friends need `codex-subscription`, with explicit device/credit
consent flags and their own ChatGPT Plus login. Never use an API key or extra Credits.

The older worker protocol reference follows for operator/developer context.

# spareagent-worker

Outbound-only remote worker for SpareAgent (Week 2). Installs with the repository package:

```bash
pip install .
spareagent-worker register --api-url https://api.example:8443 --ca-file ca.pem \
  --resource-id <approved-resource-uuid> --name node-1 --enrollment-token sa_enr_...
spareagent-worker start --mock-step-delay 1
spareagent-worker status
spareagent-worker stop
```

* Needs only the API URL, a CA bundle and its own node key — never database or Redis
  credentials. State lives in `$SPAREAGENT_WORKER_HOME` (default `~/.spareagent-worker`,
  files mode 0600).
* Executes only the deterministic `MockProviderAdapter` in Week 2: no repository checkout,
  no shell commands.
* `start` runs in the foreground; use a process manager (systemd, compose) to daemonize.

## Week 4: owner-only Codex subscription check

On your own machine, after `codex login` with your ChatGPT account:

    spareagent-worker codex-check --attest-no-purchased-credits  # after account checks
    spareagent-worker sharing pause|resume   # local kill switch for experimental leases

`codex-check` never reads or uploads credentials and sends nothing to SpareAgent. It refuses
(`FAILED_CLOSED`) if Codex is logged in with an API key or not logged in. Shared (cross-user)
work is a separate, explicit opt-in; see below and `docs/WEEK4_SUBSCRIPTION_STATUS.md`.

## Week 4 Milestone D: sharing your device with your experiment group

Only after `codex-check` passes, your device is enrolled via
`POST /v1/subscriptions/enrollment-tokens`, and you have attested via
`POST /v1/subscriptions/attest`:

    spareagent-worker start --executor codex-subscription \
        --accept-shared-tasks --attest-no-purchased-credits

Only use `--attest-no-purchased-credits` if your ChatGPT account holds no purchased Codex
credits and auto-reload is off. `spareagent-worker sharing pause` stops shared work on this
device immediately, whatever the server says. You approve each incoming task unless you
granted scoped advance consent.

## Week 5 owner validation and friends beta

Before real inference use the [macOS runbook](../../docs/WEEK5_LOCAL_CODEX_RUNBOOK.md).
`codex-check` now requires `--attest-no-purchased-credits` unless `--skip-inference` is used.
Confirm no additional purchased/granted credits and no auto-reload; the runtime cannot
inspect their balance. Token event counts are observations, not quota or a billing receipt.

Shared tasks use a named checkout-only Codex permission profile (minimal system reads,
no home reads, networking, escalation, user configuration/rules, MCP, apps or web search).
The CLI must support these controls; there is no weaker fallback. Run the no-inference
`scripts/codex_sandbox_check.py` probe after installing/updating Codex. A local pause blocks
new acceptance; interrupt/stop the worker to immediately terminate running work.
The [friends runbook](../../docs/WEEK5_FRIENDS_BETA_RUNBOOK.md) covers enrollment, explicit
per-task approval, revocation, disconnects and manual LIVE activation.
