# Week 6 controlled two-device pilot

**Status: NOT_RUN.** Week 5 proved one owner-only turn on one Mac. It did not prove
Alice-to-Bob sharing. This week's automated tests and UI preview do not change that.
LIVE remains false by default; no setup or check command enables it. No real turn is
part of CI. Do not use the old Week 5 one-turn permission to run another turn.

## Roles and installation

The **operator** runs the private coordinator, database, scheduler, verifier and dashboard.
**Alice** supplies one owner-controlled Mac with her own Plus login. **Bob** uses the
private dashboard on a second device. Bob needs no backend or Worker to request a review;
if he also volunteers capacity, he installs the same Worker-only package on his own Mac.
Never share a ChatGPT login, auth.json, browser cookie, OAuth token, password or API key.

Operator prerequisites: current `main` plus Week 6, migrations through `0009_friends_beta`,
private HTTPS reachable from both devices, trusted certificate/CA, invite-only browser
sessions, production dev-auth disabled, strong node-token pepper, backups and private
artifact storage. Use the Week 5 coordinator setup/runbook; do not install it on friends'
Macs. Run `scripts/friends_setup.py` to provision a **new two-member group** with
CODE_REVIEW only, concurrency 1, 3 tasks/rolling day, timeout 600. Its operator account
is administrative and is not a participant. Deliver each expiring login privately.

### Operator builds the small install bundle

On a reviewed checkout, using Python 3.12+ with pip:

```sh
python -m pip install --upgrade pip
python scripts/build_worker_bundle.py
```

Give friends `output/worker-bundle/setup-worker-macos.sh`, the
`spareagent_worker-0.6.0-py3-none-any.whl`, and its SHA-256 over your trusted private
channel. Review the script and compare its source to this revision. The wheel contains
only `packages.worker`, `packages.provider_sdk` and dependency-free common helpers;
its sole direct dependency is httpx. It contains no API, DB, Redis, Docker executor or
server settings. Build wheels from the repository, not an sdist: the packaging manifest
intentionally selects sibling packages. CI tests the actual wheel outside the checkout
on Linux and macOS; macOS CI also runs the installer against a fake CLI sentinel.
This PR does not publish a release or a PyPI package. Download the CI `worker-bundle-*`
artifact for the reviewed PR commit, or build it as above; communicate its checksum.

### Alice's macOS setup (no coordinator clone)

Use macOS with Python 3.12+ and Git (Apple Command Line Tools). If needed,
`brew install python@3.12` and `xcode-select --install`. Install the **official** CLI,
for example `brew install codex`; verify the publisher/source. Compatibility and actual
OS isolation are checked afterward; there is no fallback to a weaker CLI. See the
[official CLI installation guide](https://learn.chatgpt.com/docs/codex/cli) and
[ChatGPT authentication guide](https://learn.chatgpt.com/docs/auth).

```sh
bash setup-worker-macos.sh ./spareagent_worker-0.6.0-py3-none-any.whl OPERATOR_SHA256
export PATH="$HOME/.local/share/spareagent-worker/bin:$PATH"
codex login
spareagent-worker pilot-check
```

Use your own ChatGPT Plus login. Setup verifies wheel bytes, installs into a new private
venv, and prints next steps. It never logs in, registers, starts the worker, changes
shell files, enables sharing/LIVE, installs the backend or runs inference. It refuses
to overwrite an existing installation. Use `SPAREAGENT_PYTHON=/absolute/python3.12` or
`SPAREAGENT_CODEX_BIN=/absolute/codex` if needed. For Linux, create a Python 3.12
venv and pip-install the same reviewed wheel; pilot isolation must pass on that OS too.

### Registration and local consent

1. Alice signs into her invitation and accepts the current three group statements.
2. In **Connect your own Mac**, create a one-time enrollment token. This token grants
   only this SpareAgent node identity, and is unrelated to ChatGPT authentication.
3. On Alice's Mac, enter the token without putting it into shell history:

   ```sh
   read -rs SPAREAGENT_ENROLLMENT_TOKEN; export SPAREAGENT_ENROLLMENT_TOKEN; echo
   spareagent-worker register --api-url https://PRIVATE-COORDINATOR \
     --resource-id RESOURCE_ID --name alice-mac --max-concurrency 1
   unset SPAREAGENT_ENROLLMENT_TOKEN
   ```

   For a private CA add `--ca-file /path/to/trusted-ca.pem`. Remote HTTP and insecure
   certificate bypass are refused. Loopback HTTP is development only and will not pass
   pilot readiness. The local node key stays in owner-only `node.json`; no backend
   credentials are needed. Do not copy node.json to another device.
4. Connect the worker ID in the dashboard; choose the two-person group, CODE_REVIEW,
   600 seconds, concurrency 1, daily limit 3 and per-task owner approval. Explicitly
   accept all seven sharing statements. Enable sharing only by Alice's choice.
5. Run `spareagent-worker pilot-check` again. It does not poll, accept a task or attest
   success. `--task-id UUID` additionally checks the specific task on this device's
   connection. An unrelated task id returns 404.

## Reading pilot-check

JSON includes automatic checks, separate manual checks, runtime/CLI observations,
ChatGPT login classification, harmless OS sandbox checks and the node-scoped server
snapshot. The server uses the same dispatch policy: personal registration, owner opt-in,
current terms, verified route-covered authorization/expiry/cap, exactly two active
participants, approval, local/group limits, sharing/LIVE flags, health, cooldown and
subscription attestations. A fresh heartbeat is required; a registered but stopped
worker correctly reports offline. With LIVE off, `LIVE_EXECUTION_DISABLED` is expected.

Exit **1** = automatic blockers; **2** = automatic checks passed but manual attestations
still required. There is intentionally no unconditional READY/0 that could be read as
billing or two-physical-device proof. Local probes may pass on this Mac while registration,
remote HTTPS, operator authorization or other-device prerequisites remain blocked.
A version/help match does not authenticate a binary's publisher or prove paid-credit
exclusion. The Credits check is an owner statement, not a supported balance query.

## Manual activation — only immediately before the approved experiment

1. Operator personally checks the owner-supplied **project-specific** OpenAI Support
   correspondence, ticket **10766658**, the `CODEX_CLI_EXEC` route, expiry (no later than
   **2027-04-18T00:00:00Z**), non-commercial/free scope and authorized cap. Record and
   operator-verify its opaque reference using the Week 5 authorization API. A ticket
   number in a database is not general OpenAI endorsement. Keep this group capped at 2;
   never expand beyond the correspondence's ceiling of 5 or extend expiry automatically.
2. Both confirm distinct owner-controlled devices. Alice checks **ChatGPT Plus, zero
   additional purchased OR granted Credits, automatic reload off**. Stop if uncertain
   or an extra balance exists. No API key, access-token alternative, purchases or paid
   fallback. SpareAgent cannot force the upstream provider's choice of funding source.
3. Only if Alice explicitly chooses to consume usage for owner validation, manually run
   `spareagent-worker codex-check --attest-no-purchased-credits --timeout 60` on **her**
   Mac. Record its real result and manually confirm included usage/Credits afterward.
   An applicable prior check can be reviewed by the operator; do not relabel someone
   else's check, a fake CLI test or `--skip-inference` as Alice's successful run.
4. Alice records her owner check/no-Credits/no-reload attestations in the dashboard.
   Start the foreground worker only by Alice's explicit choice:

   ```sh
   spareagent-worker start --executor codex-subscription \
     --accept-shared-tasks --attest-no-purchased-credits
   ```

   This starts an available device; it does not enable the server's LIVE switch. To
   check availability with no inference, keep LIVE off and do not approve a live task.
5. Operator enables group sharing and **manually** sets `EXPERIMENT_LIVE_ENABLED=true`
   on the coordinator processes for the planned window. Keep `EXPERIMENT_ENABLED=true`.
   Restart affected coordinator processes consistently. Run pilot-check again on Alice's
   Mac and review all remaining manual conditions. This document is not automatic
   authorization to run a real task during implementation or CI.
6. Bob selects Alice and explicitly chooses LIVE. Submit **one** CODE_REVIEW, 600 seconds,
   no retries/account switching. Use:

   ```text
   repository_url: https://github.com/octocat/Hello-World
   commit_sha: 7fd1a60b01f91b314f59955a4e4d4e80d8edf11d
   prompt: Review the pinned public repository read-only. Summarize its contents and
           any concrete issues. Do not change files, read private paths, or use network tools.
   ```

7. Alice reads the entire prompt, public repo URL, SHA, requester and timeout in the inbox,
   then explicitly approves that task. Approval and worker acceptance must both happen
   within **10 minutes of submission**. Execution has its own **600-second limit starting
   at worker acceptance**; approval does not reset the waiting deadline. Expired tasks
   stay expired: inspect the cause and manually submit a new task only if both people
   choose to continue. Never automatically retry or approve a replacement. Optionally run `pilot-check --task-id UUID`.
   Stop if anything differs. Watch task timeline and worker lifecycle.
8. Bob reads the platform verification report. A review should be **UNVERIFIED**, even
   if execution is SUCCEEDED. Human review remains necessary. VERIFIED is reserved for
   supported trusted checks; it never means full semantic correctness.
9. Alice immediately checks upstream usage/Credits again. Record whether only included
   usage changed and extra Credits stayed unchanged. If not, stop and investigate;
   never infer funding from token counts or SpareAgent's zero-cost metering.
10. Export the redacted protocol report in the dashboard (or
    `GET /v1/experimental-tasks/{task_id}/pilot-report`). Combine it privately with the
    manual fields in `reports/week6_two_device_pilot.json`. Record task/attempt, owner/
    requester, authorization, CLI/auth/runtime, actual consent/approval/audit references,
    duration/token observations, verification, cancellation/retry observations, distinct
    devices, and owner-confirmed funding. Never attach credential files, private emails,
    prompts/results containing secrets, hostnames or full home paths.
11. Operator turns LIVE **off** on every affected process. Alice pauses/revokes sharing
    and stops the foreground worker when finished. Confirm new tasks do not start.

The server export remains `UNCONFIRMED` for LIVE protocol attempts, even in CI, because
it cannot know whether the node was simulated or whether there were two physical devices.
Only actual device execution plus operator/owner evidence may update the private real
pilot record. Leave its result **NOT_RUN** until then.

## Optional, separately opted-in real reliability drills

Only after the first pilot and additional explicit owner choice: cancel a running task,
disconnect/reconnect Alice's device, restart the coordinator during execution, or test
hard timeout. Each can consume subscription usage. Do not deliberately exhaust a plan.
Record actual observations or NOT_RUN, never infer them from deterministic tests.
Cancellation/expiry/revoke/invalid authority must withhold worker artifacts. After a
short disconnect the worker can renew the same lease; after lease loss it must stop,
and stale completions cannot publish. Repeated completion uses one finalization key.
Never retry with another account, API key or Credits after a limit/auth failure.

## Pause, revoke, upgrades and removal

Dashboard Pause (or `spareagent-worker sharing pause`) prevents new work; existing work
may finish. Dashboard Revoke cancels unfinished work, including verification; the worker
observes cancellation on renewal (at most five seconds between attempts, plus network
latency). For immediate local process shutdown, use `spareagent-worker stop` or Ctrl-C;
network failure can delay server notification. Do not expect a remote button to reach a
physically disconnected Mac instantly. An invalid/expired node token now stops local
renewal instead of allowing the executor to continue indefinitely.

Upgrade only while stopped and locally paused: review/build the new wheel, then
`~/.local/share/spareagent-worker/bin/python -m pip install --upgrade /reviewed/worker.whl`.
Re-run pilot-check after any Worker, Codex or OS upgrade. To remove: revoke sharing/node
access in the dashboard, stop the worker, delete the dedicated Worker venv and local
Worker state deliberately. Leave Codex login management to its owner and official CLI;
never have the coordinator delete or copy upstream credentials.


## Timeout follow-up (2026-10-19)

The first four failed LIVE requests all expired in QUEUED before worker acceptance;
there were no execution attempts or subscription calls. Their old 60-second waiting
window caused those failures. Waiting is now 10 minutes from submission. By the owner's
separate explicit request, the pilot also allows up to 600 seconds of actual execution
from worker acceptance. Configure both group and owner limits to 600 and submit a new
task with `max_duration_seconds: 600`; old timed-out tasks are never resurrected.
Per-task approval, concurrency 1, daily task limits and no automatic retry remain.
The separate optional owner-only `codex-check --timeout 60` is unchanged.
