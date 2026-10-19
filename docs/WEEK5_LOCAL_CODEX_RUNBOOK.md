# Owner-only Codex validation on macOS Apple Silicon

For current friends installation and the first actual two-device pilot, use
[the Week 6 runbook](WEEK6_PILOT_RUNBOOK.md). Friends install only the Worker wheel;
the checkout/development setup below is historical operator/developer context.

This is an owner-operated check on the owner's Mac. It does not activate cross-user
sharing, upload authentication, invoke a commercial API or buy Credits. CI never runs it.

## Installation and login

1. Check `uname -m` is `arm64`. Use Python 3.12+ and Node 22+.
2. Install the official CLI, for example `npm install -g @openai/codex@0.163.0`.
   An already-installed official ChatGPT app's bundled CLI is also usable: set
   `SPAREAGENT_CODEX_BIN` to its executable path. Record `codex --version` and the install
   source. On this Mac the executable was the OpenAI-signed arm64 CLI inside ChatGPT.app,
   version `0.163.0-alpha.10.2`; bundle and executable signature verification passed.
3. Run `codex login` and select ChatGPT, using your own Plus account. Run `codex login status`.
   Do not copy `auth.json`, Keychain entries, cookies, tokens or passwords into SpareAgent.
4. Install SpareAgent in this checkout:

   ```sh
   python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements-dev.txt -e .
   .venv/bin/spareagent-worker codex-check --skip-inference
   ```

   An API-key login, unknown status, missing executable or expired login fails closed.
   `--skip-inference` proves authentication only; it cannot satisfy the LIVE attestation.
   `--codex-bin /absolute/path/to/codex` selects the executable explicitly.

The CLI must support `exec --json --ephemeral --ignore-user-config --ignore-rules` and,
for shared tasks, named permission profiles. Unsupported versions fail; there is no
fallback to weaker permissions or API-key authentication.

Official OpenAI documentation describes [authentication](https://learn.chatgpt.com/docs/auth),
[non-interactive JSON events](https://learn.chatgpt.com/docs/non-interactive-mode) and
[permission-profile settings](https://learn.chatgpt.com/docs/config-file/config-reference).

## Before consuming subscription usage

In your ChatGPT usage settings confirm **no extra Credits, including purchased or granted
balances**, and automatic reload is off. If any extra balance is available, stop here.
Do not infer this condition from a successful ChatGPT login. SpareAgent cannot query or
prevent the upstream provider's use of already-available Credits.

Keep `.env`, API keys and other private material out of the review checkout. Shared
execution uses a named profile granting minimal system reads plus checkout reads/writes,
with tool networking disabled, no approval escalation, apps, MCP or web search. User
configuration and execpolicy rules are ignored. Owner-only `codex-check` uses a temporary
read-only workspace and the fixed minimal prompt below.

## One real read-only run

Run only after the owner confirms the Credits condition:

```sh
.venv/bin/spareagent-worker codex-check --attest-no-purchased-credits --timeout 60
```

The default prompt requests exactly `SPAREAGENT_OWNER_ONLY_OK` and no commands. The
prompt uses stdin. Only allowlisted environment variables reach Codex; API billing keys
and alternate base URLs are excluded. The official CLI is forced to ChatGPT login.

Expected JSON: `auth_method=CHATGPT`, `api_billing_env_passed_to_codex=[]`,
`result=OWNER_ONLY_SUBSCRIPTION_RUN_OK`, and the expected summary. Success requires an
explicit completed-turn event and exit 0; a zero-exit truncated event stream is not proof.
Input/output counts come from JSON events. Cached-input and reasoning counts are details,
not added again to the corresponding totals. No count is a remaining quota percentage.
`billable=false` means SpareAgent does not charge; it is not an upstream billing receipt.

The process has a hard timeout, a dedicated process group and TERM/KILL cleanup, including
background descendants. Ctrl-C cancels the owner command. Raw commands and stderr are not
published as progress; text is redacted. Do not enable CLI debug logging or publish raw
Codex home files as evidence.

After the run, check the ChatGPT usage page again and record that only included usage
changed and Credits did not change. [Official usage guidance](https://learn.chatgpt.com/docs/pricing)
distinguishes plan limits from extra Credits; token counts cannot establish the funding source.

## Repeatable checks without subscription consumption

```sh
PYTHONPATH=. .venv/bin/python scripts/codex_sandbox_check.py --codex-bin /absolute/path/to/codex
.venv/bin/python -m pytest tests/unit/test_codex_subscription.py tests/unit/test_week5_runtime.py -q
```

The first command uses the actual Codex OS sandbox with harmless temporary markers and an
empty Codex home, never `codex exec`. It verifies directory read isolation and both write
modes. The unit tests use a fake CLI and real subprocesses to exercise timeout, cancellation,
background-child cleanup, malformed/oversized output, single concurrency, auth errors,
limits and redaction. Run these checks again after changing CLI versions or OS policy.

## Actual evidence and remaining manual checks

On 2026-10-18 the owner explicitly authorized one real task, confirmed Plus/no additional
Credits/no reload beforehand, and confirmed subscription-only usage and unchanged Credits
afterward. The run returned the expected sentinel with **16,264 input / 11 output tokens**.
See `reports/week5_owner_codex_validation.json` and
`reports/week5_codex_sandbox_validation.json`. These are real execution and OS-sandbox
records, separate from mocked protocol tests.

Still manual: repeat on each participant's own Mac; validate cancellation/timeout during a
real model turn only if the owner separately chooses to consume usage for that test;
exercise actual account-limit behavior when naturally reached; review logs privately.
No real limit was intentionally exhausted, no extra inference was used to test cancellation,
and no Alice/Bob cross-user subscription execution has been performed.
