# SpareAgent

把自己 Mac 上的 Codex 额度分享给朋友，替他们执行任务。

这是一个邀请制、非商业的小范围实验：朋友用浏览器提交需求，任务在你的 Mac 上，用你自己的 Codex 登录执行，完成后交付文件。不收费、不转售，也没有付费备用通道；ChatGPT 登录凭据只留在你的 Mac 上。

当前 Mac App：**0.17.5（build 25）**，版本以 [version.json](apps/macos/version.json) 为准。

## 怎么开始

| 你想做什么 | 需要什么 | 从这里开始 |
|---|---|---|
| 用朋友分享的算力 | 邀请链接和浏览器，手机或电脑都行 | [成员使用步骤](docs/USAGE_GUIDE.md#使用者成员打开链接提交任务) |
| 分享自己 Mac 的额度 | Mac、自己的 ChatGPT Plus/Pro、运营者提供的安装链接、Tailscale | [主人安装与分享](docs/USAGE_GUIDE.md#提供方主人安装-app上线分享) |
| 搭建和维护服务 | Docker、Tailscale 和本仓库 | [首次部署](docs/MAC_PRIVATE_DEPLOYMENT.md) · [日常维护](docs/OPERATOR_GUIDE.md) |

**成员**：打开邀请链接 → 加入小组 → 写需求、上传附件 → 提交 → 下载结果。

**主人**：安装 App → 用自己的 ChatGPT 登录 → 阅读说明并上线 → 把邀请链接私下发给朋友。首次上线会用少量额度自检；之后可以在菜单栏暂停分享，在控制台设置限额、批准任务和管理成员。

成员可以写作、翻译、整理表格和画图表、修改上传的代码文件，或用代码画图。附件最多 **5 个，每个 20 MB**。任务默认不联网，需要查资料时打开「需要联网」，主人也需允许联网。

完整操作见 [使用指南](docs/USAGE_GUIDE.md)。界面默认中文，可切换英文。

## 使用前要知道

- 每组最多 **5 人，含主人**。人数上限按组计算，运营者还需控制安装链接的发放范围，遵守授权。
- 成员网页开放公网入口后，只需浏览器；主人设备、API 和安装链接仍在 Tailscale 私网内。
- 需求和附件会经过运营者的服务器，并在主人的 Mac 上处理。不要上传密码、证件号等敏感内容。
- 平台只检查文件格式和完整性，**内容是否正确需要自行核对**。当前各类能力由运营者直接开放，尚未完成逐类实机验收，见 [验收要求](docs/GENERAL_TASKS_DESIGN.md#7-能力开放流程验收门槛)。
- 真实执行需要运营者开启开关并核实授权；未开启时只做模拟，交付占位文件。
- 目前只在 **Apple Silicon Mac** 上验证。App 使用临时签名，尚未经过 Apple 公证，首次打开可能需要按 macOS 提示手动允许。

## 本地开发

需要 Docker（Compose v2）、`make`、`curl` 和 `python3`。在本地开发目录运行：

```bash
cp .env.example .env
docker compose up --build -d
make migrate && make seed-dev
make lint && make test
```

测试范围、其他命令和历史记录见 [开发说明](docs/DEVELOPMENT.md)。

## 更多文档

- [成员指南](docs/MEMBER_GUIDE.md) · [一键分享指南](docs/ONE_CLICK_SHARING.md)
- [本机编译安装](docs/LOCAL_APP_INSTALL.md) · [Mac App 构建与签名](docs/MACOS_RELEASE.md)
- [任务执行设计](docs/GENERAL_TASKS_DESIGN.md) · [系统架构](docs/ARCHITECTURE.md)
- [API 契约](contracts/openapi.yaml) · [设计决策](docs/DECISIONS.md)
