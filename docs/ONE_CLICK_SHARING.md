# 一键提供算力 / One-click sharing

## 给提供算力的人（完全不需要懂技术）

1. 解压运营者私下发给你的 `SpareAgent-mac.zip`。
2. 双击 `SpareAgent.command`（第一次请 **右键 → 打开**；较新的 macOS 需要到「系统设置 → 隐私与安全性」点「仍要打开」）。不需要事先安装 Homebrew、Python 或 Node：首次运行会自动下载并校验官方 Python 运行环境（约 25 MB），再离线安装 Worker。
3. 浏览器里会打开「SpareAgent · 一键提供算力」页面。如果安装包里有多个网络（例如局域网和 Tailscale），先在「连接方式」里选择：「自动」会先试能连上的那个；不在同一个 Wi‑Fi 时用 Tailscale（你要登录运营者的 tailnet，或运营者把服务器共享给你）。只能在下线时切换。阅读按钮下方的确认事项，点 **上线**：
   - 没有 Codex CLI 时自动下载运营者固定版本的官方 Codex（约 100 MB，校验 SHA-512 后放在 Worker 自己的目录，不改动系统）；
   - 没有 Apple 命令行工具（Git）时会弹出 Apple 官方安装窗口，但**不影响上线**：先只接文档和表格任务，装好后自动开始接代码任务；
   - 没有登录时打开 ChatGPT 登录页，用 **你自己的** 账号登录（密码只交给官方 Codex）；
   - 自动注册设备、创建你的小组、用你的额度跑一句很短的自检、开启分享和自动批准；
   - 生成 **小组邀请码** 和邀请链接。
4. 把邀请链接私下发给信任的人（页面会给出每个网络各一条链接，发对方能连上的那条）。他们打开链接、勾选同意即可加入并提交任务，任务会自动批准并在你的电脑上运行。成员的使用方法见 [MEMBER_GUIDE.md](MEMBER_GUIDE.md)。
5. 页面里随时可以修改：小组人数上限（含你自己，不超过运营者授权上限）、每 24 小时任务数、单个任务最长分钟数、同时运行的任务数（1–3）、允许的任务种类和本机工具、是否自动批准；也可以换邀请码或移除成员。页面还会显示正在运行的任务和过去 24 小时的用量。
6. 再点一下按钮就 **下线**（暂停分享、停止接单）。有任务在运行时会问你怎么处理：「运行完再下线」，或「立即停止并交还」——交还的任务回到队列，每个任务最多重新运行一次，你 10 分钟内重新上线就继续，否则按超时结束。下线期间成员仍可提交，任务会显示「小组主人已暂停分享」并等你回来。关闭运行它的终端窗口也会自动下线。
7. 运营者没有开放真实执行时，任务只以「模拟」方式运行：流程完整，但交付的是占位文件，不是真正的译文或分析。

中文为默认界面，右上角可切换 English。

## For the operator

1. Pick the operator-verified, route-covered authorization that should cover self-serve groups, then:
   ```sh
   python scripts/self_serve_setup.py --authorization-id <uuid> --code-file private/signup-code.txt
   ```
   Add the printed `SELF_SERVE_ENABLED=true`, `SELF_SERVE_AUTHORIZATION_ID`, `SELF_SERVE_SIGNUP_CODE` to the server `.env` (with `EXPERIMENT_ENABLED`, `FRIENDS_AUTH_ENABLED`, and `EXPERIMENT_LIVE_ENABLED` when real execution is intended), apply migration `0010`, and restart api/scheduler/verifier.
2. Build the newcomer package. With one address:
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
   Share `output/worker-bundle/SpareAgent-mac.zip` privately: anyone holding it can create a sharing group.
3. Revoking the template revokes every group copy at once (D-73) and stops new signups; revoking one copy stops only that group. The kill switch still disables everything.
4. Runtime pins live in `scripts/runtime-pins.json` (Python from python-build-standalone, Codex from the npm registry, per CPU, with checksums). Update both URL and checksum together, rebuild, and re-share the zip. The package itself is ~0.6 MB.
5. The `.command` is not signed or notarized. On current macOS a stranger must allow it once in System Settings → Privacy & Security. Removing that step needs a Developer ID-signed, notarized installer (an Apple Developer account).

Every self-serve group copies the template's `max_participants`; the effective member cap is min(`EXPERIMENT_MAX_GROUP_MEMBERS`, `max_participants`). Note that this caps each group, not the total across all self-serve groups; keep the number of people you give the package to within the authorized scope.
