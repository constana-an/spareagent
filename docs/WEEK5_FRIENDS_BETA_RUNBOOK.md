# Two-person friends beta: Alice's device, Bob's review

For current friends installation and the first actual two-device pilot, use
[the Week 6 runbook](WEEK6_PILOT_RUNBOOK.md). Friends install only the Worker wheel;
the checkout/development setup below is historical operator/developer context.

LIVE stays **off** until the operator explicitly activates it. The protocol tests and
browser preview use simulated devices and never establish a real two-participant result.

## Scope and prerequisites

Preserve the owner-supplied project-specific correspondence, OpenAI Support ticket
**10766658**, described in `WEEK4_SUBSCRIPTION_STATUS.md`. The operator must personally
validate the correspondence and technical route; SpareAgent cannot authenticate the
email and this is not general vendor endorsement. Scope: official Codex CLI, each owner's
own ChatGPT login/device, relay server without credentials, voluntary non-commercial use,
no payments/resale/token redistribution, at most five participants, no additional Credits,
review within six months (recorded limit **2027-04-18T00:00:00Z**), or earlier if scope changes.

Start with exactly Alice and Bob, one task at a time, per-task owner approval and a 60-second
limit. Alice completes `WEEK5_LOCAL_CODEX_RUNBOOK.md` on her own Mac first. Do not copy
her login to the server or to Bob. Both accounts accept their group invitation separately;
sharing consent is additional and revocable.

## Bring up the local/private coordinator and dashboard

Use a dedicated beta database. Apply migrations through `0009_friends_beta`; earlier
migration files remain immutable. Back up any existing database before upgrading.

```sh
cp .env.example .env
# Edit .env: FRIENDS_AUTH_ENABLED=true, EXPERIMENT_ENABLED=true.
# Keep EXPERIMENT_LIVE_ENABLED=false. Set FRIENDS_WEB_ORIGIN=http://localhost:3000.
make up
make migrate
make seed-dev  # LOCAL ONLY; creates the existing demo ADMIN identity

docker compose --profile web up --build -d web
```

Open `http://localhost:3000`. For host-side development instead:

```sh
cd apps/web
npm ci --ignore-scripts
FRIENDS_WEB_ORIGIN=http://localhost:3000 SPAREAGENT_API_URL=http://127.0.0.1:8000 npm run dev
```

The server's `FRIENDS_WEB_ORIGIN` must match the browser origin exactly. The compose web
profile trusts only the generated dev CA for the HTTPS API. Dev auth is for local operator
bootstrap only. For a remote private beta use a private network or authenticated HTTPS
reverse proxy, `APP_ENV=production`, `DEV_AUTH_ONLY=false`, a strong `NODE_TOKEN_PEPPER`,
`FRIENDS_AUTH_ENABLED=true`, and an HTTPS origin. Configuration rejects production dev
auth and non-HTTPS production origins. The web relay never forwards development identity
headers or bearer credentials. No public deployment is created by this change.

## Provision invite-only access

With the server's database settings in your shell and the Python environment installed:

```sh
# Host DB example (the compose-internal hostname 'postgres' is not a host address):
export DATABASE_URL=postgresql+asyncpg://spareagent:spareagent@localhost:55432/spareagent
export FRIENDS_AUTH_ENABLED=true
export FRIENDS_WEB_ORIGIN=http://localhost:3000
PYTHONPATH=. .venv/bin/python scripts/friends_setup.py \
  --operator demo-admin@local.test --alice alice@example.test --bob bob@example.test \
  --output /private/tmp/friends-beta-logins.json
```

For a fresh production database, this local DB-admin script can bootstrap the operator;
for a nonempty database the specified operator must already be ADMIN. It creates a
**new** two-person group with a 3-task rolling-24-hour limit and 60-second task timeout.
It does not create vendor authorization, accept member consent, enable sharing or LIVE.
The output file is mode 0600, created exclusively. Deliver each one-time code privately;
the script does not send invitations. Codes expire in 15 minutes. Use
`POST /v1/friends/invites` as ADMIN to reissue an expired code (invalidates older codes).
Do not commit this file; remove it after delivery.

Alice and Bob enter their own codes in the dashboard, then explicitly accept all three
join statements. Sessions are opaque random tokens, hashed at rest, HttpOnly/SameSite
Strict, Secure outside local, expire after 12 hours, and can be revoked. Operator accounts
can use the API with their session cookie; browser mutation requests require the configured
Origin. For curl, log in with a mode-0600 cookie jar and `Origin: $FRIENDS_WEB_ORIGIN`.

## Operator records authorization (does not activate LIVE)

Using an ADMIN session, invoke the documented API; substitute returned IDs:

```text
POST /v1/experiments/groups/{group_id}/authorizations
{
 "provider_name":"openai-codex",
 "authorization_source":"VENDOR_SUPPORT_CORRESPONDENCE",
 "authorization_reference":"OpenAI support ticket 10766658",
 "authorization_scope":{"non_commercial":true,"no_purchased_credits":true,
                         "task_types":["CODE_REVIEW"]},
 "integration_route":"CODEX_CLI_EXEC", "max_participants":5,
 "expires_at":"2027-04-18T00:00:00Z"
}
POST /v1/experiments/authorizations/{authorization_id}/verify
{"verification_status":"OPERATOR_VERIFIED","integration_route_covered":true}
POST /v1/experiments/groups/{group_id}/sharing
{"enabled":true}
```

Never extend the expiry/cap or infer permission from the presence of a ticket reference.
Store only the opaque reference, not private correspondence or credentials.

## Alice connects her own Mac

1. In **Connect your own Mac**, create an enrollment token. On Alice's Mac, supply it
   through `SPAREAGENT_ENROLLMENT_TOKEN` (hidden shell input) and run:

   ```sh
   spareagent-worker register --api-url https://YOUR-PRIVATE-API \
     --resource-id RESOURCE_ID --name alice-mac --max-concurrency 1
   # For local development only: --api-url http://localhost:8000 --allow-insecure-http
   # With the development TLS endpoint, use --ca-file /path/to/ca.pem.
   ```

   The CLI saves the node credential locally with restrictive permissions. The enrollment
   token is not a ChatGPT credential. Clear the enrollment environment variable afterward.
2. Enter the returned worker ID/device label in the dashboard and confirm local ChatGPT
   login. In the device's expanded controls set daily limit 3, timeout 60, all seven sharing
   statements, then **Enable sharing**. The UI always requires owner approval.
3. Record the actual successful owner check, no additional purchased/granted Credits and
   no auto-reload. A skipped/failed check cannot satisfy LIVE eligibility. These are
   owner statements, not remotely verified balances; withdraw them if conditions change.
4. Run the no-inference `codex_sandbox_check.py` probe on Alice's Mac; stop if it fails.
5. When Alice intentionally opts the device in, run:

   ```sh
   spareagent-worker start --executor codex-subscription \
     --accept-shared-tasks --attest-no-purchased-credits
   ```

   This does not turn on the coordinator's LIVE gate. Alice can stop the foreground
   worker at any time. Shared execution uses minimal-system/checkout-only filesystem
   permissions, disabled command networking and escalation, and no user rules/config,
   MCP, apps, web search or subagents. Unsupported CLI/profile configurations fail closed.

## Explicit manual LIVE activation and first review

Only after the above checks, the operator deliberately sets `EXPERIMENT_LIVE_ENABLED=true`
and restarts the API and scheduler (and the TLS API listener). Do this manually, never
from CI or a setup/demo script. Authorization must still be current; Alice and Bob must
still be active, and Alice's device, opt-in, Credits statements and limits must hold.

Bob signs in, chooses Alice's device, and submits **CODE_REVIEW** in **LIVE** mode:

- Repository: `https://github.com/octocat/Hello-World`
- Commit: `7fd1a60b01f91b314f59955a4e4d4e80d8edf11d` (public pin checked on 2026-10-18)
- Prompt: `Read README only. Summarize it in one sentence. Do not run commands, edit files,
  access the network or read anything outside the checkout.`
- Hard timeout: `60`

The task must wait for Alice. Alice reads the complete prompt, repository, commit, mode
and timeout in **Incoming requests**, then approves **that task**. Advance consent does
not approve LIVE work. Policy is checked again at lease acceptance, so a pause, expired
permission or withdrawn Credits statement between reservation and acceptance blocks it.

Bob watches persisted SSE events. Transient browser reconnects resume with Last-Event-ID;
refreshing the page reloads task state/history. Both selected participants can inspect
artifacts/events; another group member cannot read their task. Download checks SHA-256
in the API store and browser. A review completes with `verification_status=UNVERIFIED`:
artifact integrity is checked, but review correctness needs human assessment. It must
never be described as independently verified code.

Run the platform verifier even for LIVE reviews. With the existing compose development
stack start `docker compose --profile code up -d fixtures verifier`. That trusted platform
process launches restricted test containers; participant devices never receive its socket,
database or Redis access. Arbitrary repositories are not executed as tests.

Afterward Alice checks included plan usage and unchanged Credits again. Save task ID,
approval/audit IDs, actual CLI version, token observations, timeout, final state, platform
report and both participants' manual confirmations. Do not save credentials/raw login
logs. This repository intentionally contains **no claimed two-participant live proof**.

## Stop, revoke and recover

- **Dashboard Pause** stops new execution, including a reserved lease at acceptance;
  an already running task may finish. Queued tasks wait. Explicit consent resumes sharing.
- **Dashboard Revoke** cancels unfinished tasks; a running worker observes cancellation
  on renewal (at most five seconds between renewal attempts) and kills its Codex process
  group. Never publish a late successful result for a cancelled task.
- `spareagent-worker sharing pause` blocks device acceptance. To immediately stop an
  already running process locally, stop/interrupt the foreground worker; do not interpret
  the local pause file alone as an immediate running-task kill.
- Network loss prevents lease renewal; the worker stops once its renewal window is lost.
  A disconnected node cannot report success on an expired lease. Scheduler recovery and
  verifier claims use persisted state across restarts. A lost completion response is
  replayed with the same finalization key, without duplicate observations/artifacts.
- A subscription-limit error fails once, records LIMITED plus a reset hint and starts a
  one-hour cooldown. It never tries another account, key, paid fallback or credits purchase.
- Operator emergency stop: `POST /v1/experiments/kill-switch` as ADMIN, then set both
  experiment switches false and restart processes. Revoke browser sessions with
  `POST /v1/friends/sessions/{user_id}/revoke`; codes are invalidated as well.

Finally turn LIVE off again and record the outcome in `WEEK5_STATUS.md`. Real cancellation,
account-limit and two-device disconnect drills remain operator-run checks; automated
protocol/sandbox tests are separate evidence.
