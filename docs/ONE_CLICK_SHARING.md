# 一键提供算力 / One-click sharing

## 给提供算力的人（完全不需要懂技术）

1. 推荐用**一行命令安装**：先安装并登录 Tailscale，连上运营者的私网，然后打开「终端」，粘贴运营者私下发给你的那一行 `curl -fsSL https://…/install/…/install.sh | bash` 并回车。它会下载运营者预先编译好的 App，核对校验值和签名，安装到个人 Applications 文件夹并打开；不需要 Apple 命令行工具，也不用解压或编译。这个链接含注册码，只给你一个人用，过期或被撤销后失效；需要重装或更新时向运营者要新链接，已注册的设备、小组和设置都会保留。
   备用方式：免费本机安装包 `SpareAgent-local-install-arm64.zip`（Apple Silicon）。解压后按 [LOCAL_APP_INSTALL.md](LOCAL_APP_INSTALL.md) 在终端运行一次 `Install-SpareAgent.sh`，在你的 Mac 上编译菜单栏 App；缺少 Apple 命令行工具时按系统提示安装后重新运行。两种方式都无需 Apple 开发者会员、Homebrew、Python、Node 或完整 Xcode。
2. 以后双击 `SpareAgent.app` 即可启动，不需要终端。首次启动会在窗口中准备随包提供的 Python 和 Worker，并在安装前校验。本机编译不等于 Apple 公证，不能保证所有 macOS 版本和受管理电脑都没有额外系统提示。已有的预编译测试 App 包仍可使用，首次可能需要本人按 macOS 提示放行。
3. 当前私网版本需先安装并登录 Tailscale，确认已连接运营者的私网。浏览器里会打开「SpareAgent · 一键提供算力」页面。确认订阅类型，阅读分享说明，首次点 **同意并上线**，以后点 **上线**。设备名称和连接方式已自动填写，通常无需修改；需要时展开「设备名称与连接方式」，名称支持中文和空格。如果安装包里有多个网络（例如局域网和 Tailscale），「自动」会先试能连上的那个；不在同一个 Wi‑Fi 时用 Tailscale（你要登录运营者的 tailnet，或运营者把服务器共享给你）。只能在下线时切换。
   - 没有 Codex CLI 时自动下载运营者固定版本的官方 Codex（约 100 MB，校验 SHA-512 后放在 Worker 自己的目录，不改动系统）；
   - 没有 Apple 命令行工具（Git）时会弹出 Apple 官方安装窗口，但**不影响上线**：先只接文档和表格任务，装好后自动开始接代码任务；
   - 没有登录时打开 ChatGPT 登录页，用 **你自己的** 账号登录（密码只交给官方 Codex）；
   - 自动注册设备、创建你的小组，首次用你的额度跑一句很短的自检；自检通过后复用结果，开启分享；新小组默认自动批准成员任务；
   - 生成 **小组邀请码** 和邀请链接。
4. 把邀请链接私下发给信任的人。新版默认使用 `*.ts.net` HTTPS 地址；运营者开放公网入口后（D-102），成员用浏览器直接打开，不用装 Tailscale，也不需要导入证书。没开放时成员仍需先连上 Tailscale。旧版多网络安装包仍会显示每个网络各一条链接。他们打开链接、勾选同意即可加入并提交任务，任务会自动批准并在你的电脑上运行。成员的使用方法见 [MEMBER_GUIDE.md](MEMBER_GUIDE.md)。
5. 在「小组与任务设置」里，随时可以修改小组人数上限（含你自己，不超过运营者授权上限）、每 24 小时任务数、单个任务最长分钟数、同时运行的任务数（1–3）、本机工具和自动批准。任务数和单次时间留空表示不限；选择「放开用」时，这两项都不限，同时运行 2 个任务。成员单独设置的限额仍然生效。页面会显示正在运行的任务和过去 24 小时用量。成员页可以设置备注、单独限额，或选择「暂时断开」「踢出小组」：暂时断开会停止未完成任务，保留成员记录和邀请码，可随时恢复连接；踢出会停止未完成任务、移除成员并更换邀请码，再次加入需重新邀请。运行中的任务会先进入停止中，设备确认后才显示已取消。
6. **菜单栏**：图标旁显示当前状态，例如「在线 · 运行 1 · 今日 3/5」。同意过分享说明后，可以直接在菜单栏点「上线」。「复制邀请说明」会复制一段可以直接发给朋友的文字（含邀请链接；只有成员入口还在私网时才提示装 Tailscale），「显示邀请二维码」方便朋友用手机扫码。勾选「开机时自动打开」后 App 随开机启动；再勾选「打开后恢复上次的在线状态」，上次退出或关机时在线，下次打开就自动上线（默认不勾，仍然默认离线）。运营者发布新版后，菜单栏会出现「更新到 x.y…」：点了才安装，会先停止接新任务、等正在运行的任务完成，再自动替换并重新打开 App，设备身份和设置都保留，原来在线的会自动回到在线；也可以随时点「检查更新」。收到新任务、任务完成或没完成（例如 ChatGPT 额度用完、需要重新登录）、分享中断、连不上服务器时，会弹出系统通知，点通知打开控制页；第一次上线时 macOS 会询问是否允许通知。
7. 再点一下按钮或在菜单栏选「暂停分享」就 **下线**（暂停分享、停止接单）。有任务在运行时会问你怎么处理：「运行完再下线」，或「立即停止并交还」——交还的任务回到队列，每个任务最多重新运行一次，你 10 分钟内重新上线就继续，否则按超时结束。下线期间成员仍可提交，任务会显示「小组主人已暂停分享」并等你回来。关闭控制页或状态窗口不会退出 App；从菜单栏选「退出 SpareAgent」才会停止接单并退出，有任务时可选「运行完再退出」或「立即停止并交还」。重新打开 App 默认离线，需要你再次点击上线；勾选了「打开后恢复上次的在线状态」时例外。
8. 运营者没有开放真实执行时，任务只以「模拟」方式运行：流程完整，但交付的是占位文件，不是真正的译文或分析。

中文为默认界面，右上角可切换 English。

上线失败时，按页面提示修复后点「重试上线」。已安装的组件、已完成的登录和已通过的自检会复用；注册尚未完成且登记凭证仍有效时，会继续同一次登记。

旧版 `SpareAgent-mac.zip` / `SpareAgent.command` 脚本包仍可使用；它会打开终端，关闭运行它的终端窗口会下线。切换到 App 前，请先在旧版下线并关闭启动程序。

## For the operator

For an existing private Mac deployment, double-click `scripts/SpareAgent-Operator.command`
to back up data, restore services, check HTTPS, and build or reuse the free local installer.
It reuses the saved authorization and preserves real-execution and sharing switches.
See [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md). The steps below cover initial setup and manual packaging.

1. Pick the operator-verified, route-covered authorization that should cover self-serve groups, then:
   ```sh
   python scripts/self_serve_setup.py --authorization-id <uuid> --code-file private/signup-code.txt
   ```
   Add the printed `SELF_SERVE_ENABLED=true`, `SELF_SERVE_AUTHORIZATION_ID`, `SELF_SERVE_SIGNUP_CODE` to the server `.env` (with `EXPERIMENT_ENABLED`, `FRIENDS_AUTH_ENABLED`, and `EXPERIMENT_LIVE_ENABLED` when real execution is intended), apply migration `0010`, and restart api/scheduler/verifier.
2. Prefer a Tailscale Serve HTTPS origin for the dashboard and a separate HTTPS port for the API. Keep existing Serve/Funnel entries, bind HTTP upstreams to loopback only, and use `tailscale serve --bg --https=<port> http://127.0.0.1:<upstream>`. Never enable Funnel for the API port. To let members in without Tailscale (D-102), set `FRIENDS_WEB_PUBLIC=true`, add `"public_dashboard": true` to `operator.json`, rate-limit the dashboard in the proxy, and serve the dashboard port with `tailscale funnel --bg --https=8443 http://127.0.0.1:13000` (Funnel allows 443, 8443 and 10000). Set `FRIENDS_WEB_ORIGIN` to the dashboard's full HTTPS origin, retain any legacy origins in `FRIENDS_WEB_EXTRA_ORIGINS`, then restart the affected services. Serve manages the browser-trusted certificate; members still need Tailscale access. See [Serve documentation](https://tailscale.com/docs/reference/tailscale-cli/serve).
   Build the newcomer package without `--ca-file` for the browser-trusted endpoint:
   ```sh
   python scripts/build_worker_bundle.py --api-url https://HOST.ts.net:8444 --dashboard-url https://HOST.ts.net:8443 \
     --signup-code-file private/signup-code.txt
   ```
   Legacy private-CA deployments remain supported. With one such address:
   ```sh
   python scripts/build_worker_bundle.py --api-url https://HOST:18443 --dashboard-url https://HOST:13443 \
     --signup-code-file private/signup-code.txt --ca-file ca.pem
   ```
   With several networks (D-94), repeat `--network id:Label=API_URL,DASHBOARD_URL`; the first is the default for older apps:
   ```sh
   python scripts/build_worker_bundle.py \
     --network "lan:局域网=https://LAN_IP:18443,https://LAN_IP:13443" \
     --network "tailscale:Tailscale=https://TS_IP:18443,https://TS_IP:13443" \
     --signup-code-file private/signup-code.txt --ca-file ca.pem
   ```
   The server must list every extra dashboard origin in `FRIENDS_WEB_EXTRA_ORIGINS` (comma-separated), listen on each address, and its certificate must cover each address.
   On a Mac with Apple's command line build tools, build the desktop package next:
   ```sh
   python scripts/build_macos_app.py --arch arm64 --source-installer
   ```
   This builds `output/macos-app/SpareAgent-local-install-arm64.zip` with Swift source, a local installer, and the pinned Python archive. The recipient compiles the native launcher on their Mac; this does not require a Developer ID membership. It reuses the prepared `output/worker-bundle` settings and wheels. Omit `--source-installer` to build a precompiled test App instead. `--arch x86_64` selects the Intel package; only Apple Silicon has been exercised on the current Mac. All distribution packages contain the signup code: share privately with people who may create a group. The old script ZIP remains available for compatibility.
3. Revoking the template revokes every group copy at once (D-73) and stops new signups; revoking one copy stops only that group. The kill switch still disables everything.
4. Runtime pins live in `scripts/runtime-pins.json` (Python from python-build-standalone, Codex from the npm registry, per CPU, with checksums). Update both URL and checksum together, rebuild, and re-share the zip. The script bundle is about 0.6 MB; the Mac App also includes the Python archive (about 25 MB).
5. The current distributed App still uses ad-hoc signing for local testing. The builder supports Developer ID signing and notarization once the operator has a membership, a Developer ID Application identity and a notarytool keychain profile. No Apple submission has been performed on this Mac. See [MACOS_RELEASE.md](MACOS_RELEASE.md) for setup, the private configuration included in submissions, and release commands. The legacy `.command` ZIP is not notarized.

Every self-serve group copies the template's `max_participants`; the effective member cap is min(`EXPERIMENT_MAX_GROUP_MEMBERS`, `max_participants`). Note that this caps each group, not the total across all self-serve groups; keep the number of people you give the package to within the authorized scope.

不限任务数量和单次执行时间不影响手动停止、并发、成员资格、任务批准、联网和订阅授权检查。成员页的每日任务上限和每日分钟上限留空，表示不单独限制该成员。服务端用 `null` 表示不限；更新设置时省略字段表示保持原值，不能用很大的数字代替不限。新增字段和空限制由 `0018_member_controls_and_unlimited_limits` 迁移支持，发布前需要备份并迁移数据库，再更新服务端和 Worker／Mac App。现有设置不会被迁移自动放宽。
