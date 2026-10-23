# SpareAgent

**把自己 Mac 上的 Codex 额度一键分享给朋友，替他们执行任务。**
*Share the Codex allowance of your own ChatGPT plan with a small group of friends, one click on your own Mac.*

A small, invite-only, non-commercial experiment. Every task runs on the owner's own Mac with
the owner's own Codex login; credentials never leave that Mac and there is no payment,
resale or paid fallback.

Current release: **Mac App 0.17.5 (build 25)**. Version and build live in
[`apps/macos/version.json`](apps/macos/version.json).

## How it works

| Who | Does | Needs |
|---|---|---|
| **Operator** | Runs the private coordinator (API, database, scheduler, verifier, member dashboard), hands out install links and publishes App updates | Docker; this repository; Tailscale |
| **Owner** (provides compute) | Installs `SpareAgent.app` once, reads the sharing terms and goes online. The App installs Codex if needed, the owner signs in to their own ChatGPT, and a group with an invite link is created. Day to day, the owner works from the menu bar and the owner console | A Mac (Apple Silicon tested), their own ChatGPT Plus/Pro, Tailscale access to the coordinator |
| **Member** (uses compute) | Opens the invite link, submits tasks with attachments, follows progress, downloads results | Only a browser, on a phone or a computer |

Tasks are approved by the owner (one by one, or by the group's standing auto-approval),
dispatched to the owner's Mac, run by Codex in a per-task sandboxed folder, checked by the
platform and delivered as files.

### Owner installation

- **Recommended: one-line install link.** The operator runs
  `scripts/owner_install_link.py create` and privately sends the owner a
  `curl -fsSL https://…/install.sh | bash` line. It downloads the prebuilt App, checks its
  checksum and signature, installs it into the owner's Applications folder and opens it. No
  Apple command line tools, unzipping or building. Each link is personal and expires.
- **Fallback: free local build.** `SpareAgent-local-install-arm64.zip` builds the menu-bar App
  on the owner's Mac once ([local installation](docs/LOCAL_APP_INSTALL.md)).
- **Updates are in-app (D-103).** After `owner_install_link.py publish-update`, owners see
  "更新到 x.y…" in the menu bar within 6 hours. Nothing installs until they click it; the App
  stops taking new tasks, lets running ones finish, swaps itself with rollback, reopens and
  goes back online if it was online before.

Neither path needs a paid Apple developer membership. The App is ad-hoc signed, not notarized,
so macOS may ask the owner to allow it on first open.

## What members get

Members only need the invite link. The dashboard is served through Tailscale Funnel with a
browser-trusted certificate (D-102), so there is no Tailscale install and no certificate import.
Owners' devices and the API stay on the tailnet.

- **One kind of request.** Since 0.17.2 every new request from the dashboard is a **general
  task**: discussion, writing, translation, tables and charts, edits to uploaded code files,
  pictures drawn with code (SVG/PNG). Up to 5 attachments of 20 MB each (documents, spreadsheets,
  PDFs, images, source files); the result is `answer.md` plus any files it produces. The API
  still accepts the older specialized kinds (translate, analyze spreadsheet, code tasks on a
  public repository at a pinned commit) for existing clients and records.
- **Internet is per task.** A member turns on *需要联网*; the task only goes to devices whose
  owner allows internet (on by default) and the answer lists the URLs it used. File access stays
  limited to the task's own folder either way.
- **Workspace.** A task table with search, a detail pane with Result and Log tabs, previews,
  download-all with SHA-256 checks, cancel, edit-and-resubmit, browser notifications when a task
  ends, and the member's own usage and caps. Queue position and the reason a task waits are
  shown in plain words; failures come with a Chinese reason and advice.
- **Friend view.** A member who shares no device of their own sees only their tasks, their
  group and an optional "share your own Mac" section.
- **Staying signed in.** A session lasts 90 days; opening the same invite link again returns to
  the same account and history without taking another seat (D-100).
- Chinese by default, English on request.

## What owners control

- **Menu bar.** State, running tasks and today's count in the title; a card with the plan's
  5-hour and weekly usage bars (read locally from Codex, nothing leaves the Mac); go online,
  pause, copy the invite text, show an invite QR code, open at login, restore the last online
  state, check for updates. macOS notifications for new, finished and failed tasks and lost
  connections.
- **Owner console in the App's own window (D-104).** Overview, tasks, members and settings in a
  sidebar layout. The App version is shown at the bottom of the sidebar.
- **Sharing presets:** 省着用 (3 tasks a day, 10 min each, 1 at a time, no internet), 标准
  (5 / 10 min / 1, internet), 放开用 (no task-count or time limit, 2 at a time, internet), or
  custom: group size (2–5, including the owner), tasks per 24 h, minutes per task (both may be
  unlimited), 1–3 concurrent tasks, local tools, internet, auto-approval.
- **Members:** per-member usage over the last 24 h, a note, per-member caps on tasks and
  execution minutes; **暂时断开** (stops open tasks, keeps the member and the invite code,
  reversible) or **踢出小组** (stops open tasks, removes the member and rotates the invite code).
- **Going offline:** finish running tasks first, or stop and hand them back. A handed-back task
  re-runs once and waits up to 10 minutes for the owner to return. While the owner is paused,
  members can still submit; their tasks wait and say why.

## Limits to know

- Results are **structure-checked only** (formats, required files, no active content); nobody
  checks that an answer or translation is right.
- All kinds were opened for real execution by operator decision without per-kind acceptance;
  real-device acceptance is still to be done ([design §7](docs/GENERAL_TASKS_DESIGN.md)).
- Groups are capped at 5 people by the written authorization the operator relies on. The cap is
  per group, so the operator also limits how many owners receive an install link.
- The App is not notarized (see [macOS release](docs/MACOS_RELEASE.md) for the Developer ID path,
  which has not been used yet). Only Apple Silicon has been tested.
- When the operator has not switched on real execution, tasks run as simulations and deliver
  placeholder files.

## Documentation

| For | Read |
|---|---|
| Owners | [docs/ONE_CLICK_SHARING.md](docs/ONE_CLICK_SHARING.md) |
| Members | [docs/MEMBER_GUIDE.md](docs/MEMBER_GUIDE.md) |
| Operating the existing Mac deployment | [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md) |
| Setting up a private Mac deployment | [docs/MAC_PRIVATE_DEPLOYMENT.md](docs/MAC_PRIVATE_DEPLOYMENT.md) |
| Building and signing the Mac App | [docs/MACOS_RELEASE.md](docs/MACOS_RELEASE.md), [docs/LOCAL_APP_INSTALL.md](docs/LOCAL_APP_INSTALL.md) |
| Task kinds, execution, internet, recovery | [docs/GENERAL_TASKS_DESIGN.md](docs/GENERAL_TASKS_DESIGN.md) |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Every decision and why | [docs/DECISIONS.md](docs/DECISIONS.md) (D-90 onwards for the current release) |
| API | [contracts/openapi.yaml](contracts/openapi.yaml) |

## Operator quick reference

Day-to-day maintenance of the existing deployment: double-click
`scripts/SpareAgent-Operator.command`. It reads the saved configuration, backs up data, starts
or restores services, checks HTTPS and builds or reuses the owner packages. It keeps the
real-execution switch as it is and runs no inference; add `--rebuild` to rebuild service images
from the current source. Details in the [operator guide](docs/OPERATOR_GUIDE.md).

```sh
python scripts/owner_install_link.py create --label NAME   # personal install link, 7 days by default
python scripts/owner_install_link.py list
python scripts/owner_install_link.py revoke NAME
python scripts/owner_install_link.py publish-update        # offer the built App to registered devices
```

Before `publish-update`, raise `version` and `build` in `apps/macos/version.json` and build the
App (`python scripts/build_macos_app.py --arch arm64`); devices compare the build number and a
lower build is refused. Releases that add a migration (for example
`0018_member_controls_and_unlimited_limits`) need a database backup and migration before the
new server and App go out.

Real execution needs both `EXPERIMENT_LIVE_ENABLED` and an operator-verified authorization
([ONE_CLICK_SHARING.md](docs/ONE_CLICK_SHARING.md)). Extra dashboard addresses must be listed in
`FRIENDS_WEB_EXTRA_ORIGINS`, served, and covered by the certificate.

## Development

Requires Docker (Compose v2), `make`, `curl` and `python3`.

```bash
cp .env.example .env
docker compose up --build -d
make migrate && make seed-dev
make lint && make test
```

`make test` runs the unit, integration and end-to-end acceptance tests (with the real Worker
agent) in containers against an isolated database; Docker-sandbox and network tests are skipped
when their prerequisites are missing. Other targets: `make e2e-remote` (remote workers, one
killed mid-run), `make e2e-code` (sandboxed code execution and verifier), `make e2e-experimental`,
`make validate-openapi`. The dashboard in `apps/web` has its own `npm run typecheck`, `npm test`
and `npm run build`.

For demos and tests, a prompt marker injects mock faults: `[mock:transient]` (first attempt
fails, the retry succeeds), `[mock:fail]` (permanent failure), `[mock:bad-patch]` (verification
fails).

## Policy invariants (enforced in code and tests)

- `PERSONAL_SUBSCRIPTION` resources are never scheduled for marketplace tasks and never routed by
  the gateway, enforced by DB CHECK constraints and `services/scheduler/policy.py`. There is no
  subscription resale or pooling code in this repository.
- Public eligibility can only be granted by an ADMIN through an audited endpoint.
- `DEV_AUTH_ONLY=true` is refused at startup unless `APP_ENV=local`, and is honoured only for
  localhost requests.
- Secrets are redacted from logs, events and artifacts.

## History

The project was built in weekly milestones; each status file records what was true at the time.

| Milestone | What it added | Record |
|---|---|---|
| Week 1 | Task API, event stream, mock executor and gateway | [WEEK1.md](docs/WEEK1.md) |
| Week 2 | Remote workers over outbound HTTPS with node credentials | [WEEK2_STATUS.md](docs/WEEK2_STATUS.md) |
| Week 3 | Sandboxed code execution and an independent verifier (off by default) | [WEEK3_STATUS.md](docs/WEEK3_STATUS.md) |
| Week 4 | Experimental Codex subscription sharing | [WEEK4_SUBSCRIPTION_STATUS.md](docs/WEEK4_SUBSCRIPTION_STATUS.md) |
| Week 5 | Invite-only friends dashboard; first real owner-only run (2026-10-18) | [WEEK5_STATUS.md](docs/WEEK5_STATUS.md) |
| Week 6 | Friends install only the Worker; first real two-device run (2026-10-19) | [WEEK6_STATUS.md](docs/WEEK6_STATUS.md), [security review](docs/WEEK6_SECURITY_REVIEW.md) |
| Since | One-click Mac App, general tasks, public member dashboard, in-app updates, owner console | [DECISIONS.md](docs/DECISIONS.md) D-90 onwards |
