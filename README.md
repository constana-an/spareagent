# SpareAgent

**把自己 Mac 上的 Codex 额度一键分享给朋友，替他们执行任务。**
*Share the Codex allowance of your own ChatGPT plan with a small group of friends, one click on your own Mac.*

A small, invite-only, non-commercial experiment. Every task runs on the owner's own Mac with
the owner's own Codex login; credentials never leave that Mac and there is no payment,
resale or paid fallback.

Current release: **Mac App 0.17.5 (build 25)**. Version and build live in
[`apps/macos/version.json`](apps/macos/version.json).

## 使用说明（中文）

SpareAgent 里有三种角色：

| 角色 | 是谁 | 需要什么 |
|---|---|---|
| **服务方**（运营者） | 运行私有服务器的人：网页、API、数据库、调度和结果检查都在这台机器上 | 一台装了 Docker 和 Tailscale 的 Mac，以及本代码仓库 |
| **提供方**（主人） | 把自己 Mac 上的 Codex 额度分享出来的人，任务在他的电脑上运行 | 一台 Mac（已测试 Apple Silicon）、自己的 ChatGPT Plus/Pro、能连上服务方的 Tailscale |
| **使用者**（成员） | 加入主人的小组、提交任务的人 | 只要浏览器，手机或电脑都行 |

全程不收费、不转售，ChatGPT 登录信息只留在主人自己的 Mac 上。每个小组最多 5 人（含主人）。

### 服务方：搭好服务，发安装链接，发布更新

**日常维护。** 打开 Docker Desktop、连上 Tailscale，双击 `scripts/SpareAgent-Operator.command`。它会依次检查配置、备份数据库和交付文件、启动或恢复服务、验证 HTTPS，并生成或复用安装包，最后显示备份目录。它不会改动真实执行开关，也不会执行任何任务。源码有更新、需要重新构建服务镜像时，加 `--rebuild`；只想检查不想改动时，运行 `python3 scripts/operator_tool.py --check`。有未完成任务时它会停下，最好在没人提交任务的时候运行。

**给主人发安装链接。** 每个主人单独生成一条：

```sh
python scripts/owner_install_link.py create --label 小明    # 默认 7 天有效，最多 30 天
python scripts/owner_install_link.py list
python scripts/owner_install_link.py revoke 小明            # 对方装好后即可撤销
```

`create` 会打印一行 `curl -fsSL https://…/install.sh | bash`，私下发给对方。链接里含注册码，一人一条，不要发到群里；链接只能在 tailnet 内打开。

**发布 App 更新。** 先把 `apps/macos/version.json` 里的 `version` 和 `build` 调高（设备按 `build` 判断有没有新版），再编译并发布：

```sh
python scripts/build_macos_app.py --arch arm64
python scripts/owner_install_link.py publish-update
```

主人的菜单栏会在 6 小时内出现「更新到 x.y…」。如果新版本带数据库迁移，发布前要先备份数据库、完成迁移，再更新服务端和 App。

**成员入口。** 成员网页（8443 端口）通过 Tailscale Funnel 对外开放，成员不用装 Tailscale（D-102）；API（8444 端口）、主人设备和安装链接只在 tailnet 内。真实执行需要同时打开 `EXPERIMENT_LIVE_ENABLED` 并有运营者核实过的授权；没打开时任务只做模拟，交付占位文件。

更多细节见 [运营维护指南](docs/OPERATOR_GUIDE.md)。

### 提供方（主人）：安装 App，上线分享

1. **安装。** 先安装并登录 Tailscale，连上服务方的私网。打开「终端」，粘贴服务方私下发给你的那一行命令并回车。它会下载预编译好的 App，核对校验值和签名，装进你个人的「应用程序」文件夹并打开。不需要 Apple 开发者账号，也不用编译。（备用方式是本机编译安装包，见 [本机安装](docs/LOCAL_APP_INSTALL.md)。）App 没有经过 Apple 公证，第一次打开时 macOS 可能要你手动允许。
2. **第一次上线。** App 会打开「SpareAgent 控制台」窗口。在「你的订阅」里选好订阅类型，读完分享说明后点 **同意并上线**。首次上线会用掉少量额度做自检；请确认账号没有额外购买或赠送的 Credits，并已关闭自动充值。App 会自动完成剩下的事：没有 Codex 就下载官方版本；没登录就打开 ChatGPT 登录页，用 **你自己的** 账号登录；注册设备，创建你的小组，用你的额度跑一次很短的自检，最后生成邀请码和邀请链接。新小组默认自动批准成员的任务。
3. **邀请朋友。** 在菜单栏点「复制邀请说明」，或「显示邀请二维码」让朋友用手机扫，然后私下发给你信任的人。朋友用浏览器打开、勾选同意就能加入。邀请码只在生成时显示一次；想让旧码作废，就在控制台的邀请码旁点「换一个」，旧码立即失效。
4. **菜单栏。** 标题显示当前状态（例如「在线 · 运行 1 · 今日 3/5」）。点开后能看到你的 ChatGPT 5 小时额度和本周额度还剩多少、什么时候重置（只在本机读取，不会上传），以及正在运行的任务。读不到额度时显示「额度未知」和上次读取的时间。菜单里有：打开控制台、上线、暂停分享、复制邀请说明、显示邀请二维码、开机时自动打开、打开后恢复上次的在线状态、显示状态、检查更新、退出 SpareAgent。收到新任务、任务完成或失败、分享中断、连不上服务器时，会弹出系统通知。
5. **控制台。** 左侧有「概览」「任务」「成员」「设置」四页，底部显示 App 版本。
   - **设置**：三档预设——「省着用」（每天 3 个任务、单个 10 分钟、同时 1 个、不联网）、「标准」（5 个、10 分钟、同时 1 个、可联网）、「放开用」（任务数和时间都不限、同时 2 个、可联网），也可以自定义：小组人数、每 24 小时任务数（1–50 或不限）、单个任务最长时间（1–60 分钟或不限）、同时运行的任务数（1–3）、允许的本机工具、是否允许联网、是否自动批准。
   - **成员**：每个人过去 24 小时用了几个任务、多少分钟。成员按你的备注、TA 自填的昵称、成员编号的顺序显示（昵称是成员自己填的，不代表身份认证）。可以加备注，单独限制每天的任务数和分钟数（留空表示不单独限制）。还可以「暂时断开」：停止 TA 未完成的任务，保留成员记录和邀请码，之后可以「恢复连接」；或者「踢出小组」：停止未完成的任务，移除成员，同时更换邀请码，对方要重新邀请才能加入。
6. **下线和退出。** 在菜单栏点「暂停分享」就下线。有任务在跑时可以选「运行完再下线」或「立即停止并交还」：交还的任务回到队列，最多重新运行一次，你 10 分钟内回来就继续，否则按超时结束。下线期间成员仍可提交任务，任务会显示「小组主人已暂停分享」并等你回来。关掉控制台窗口不会退出 App，要从菜单栏点「退出 SpareAgent」。重新打开后默认是离线状态，除非你勾选了「打开后恢复上次的在线状态」。
7. **更新。** 菜单栏出现「更新到 x.y…」时，点了才会安装。App 会先停止接新任务、等运行中的任务完成，然后自动替换并重新打开；设备身份和设置都保留，原来在线的会自动回到在线；新版本校验不过时自动换回旧版。

完整说明见 [一键分享指南](docs/ONE_CLICK_SHARING.md)。

### 使用者（成员）：打开链接，提交任务

1. **加入。** 打开主人私下发给你的邀请链接（形如 `https://….ts.net:8443/#join=…`），可以填一个昵称（最多 40 字，方便主人认出你），读完说明、勾选同意就加入了。不用装软件，不用导入证书，也不用注册账号；直接打开网站首页时，在「用邀请码加入」里输入邀请码也一样。登录在这个浏览器里保持 90 天，过期后重新打开同一个链接，会回到原来的账号和任务记录。
2. **提交任务。** 点「＋ 新任务」，写清楚你要什么结果。可以讨论问题、写作、翻译、整理表格和画图表、修改你上传的代码文件、用代码画图。想要哪种语言或格式（例如 Word、Excel），直接写在需求里。附件最多 5 个，每个不超过 20 MB，可以拖进来或粘贴截图。任务默认不联网；需要查资料就打开「需要联网」，回答里会列出用到的网址。「高级」里可以选设备、最长运行时间和执行方式，一般不用改。提交按钮会写明这次是「提交真实任务」还是「开始试运行」（试运行只走流程，交付占位文件）。
3. **看进度、取结果。** 任务列表里会显示排第几位、在等什么（例如「小组主人已暂停分享」「你今天的任务数已到主人设的上限」）以及运行进度。点开一个任务，「结果」页可以预览和下载文件（下载时自动核对 SHA-256），「日志」页可以查看执行过程。点「完成时提醒我」，任务结束时浏览器会弹出通知。任务结束前随时可以取消；想换个说法重做，点「改一改再提交」。任务没有完成时，页面会说明是要改输入、检查后重试，还是需要主人处理；点「检查后重试」只会把需求填回表单，仍需你确认后提交。需要找主人帮忙时，把「技术详情」里的内容发给 TA。
4. **注意。** 平台只检查结果的格式和完整性，**内容对不对请你自己核对**。你的需求和附件会经过服务方的服务器，并在主人的 Mac 上处理，不要上传密码、证件号或其他敏感内容。如果主人暂时断开了你，历史记录会保留，恢复连接后可以继续使用；被踢出小组后，需要主人重新邀请。

完整说明见 [成员指南](docs/MEMBER_GUIDE.md)。

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
