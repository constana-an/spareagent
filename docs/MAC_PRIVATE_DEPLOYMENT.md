# Mac 私有部署、设备角色与停机记录

记录日期：2026-10-18（America/New_York）。部署基于已合并 Week 6 的 main
`5068eadf01cefa2abdc1d1687ed6f4e4d568e3aa`。

**当前状态：本机 Docker 容器已全部停止，数据保留情况见下文。LIVE 关闭，真实双设备实验 NOT_RUN。**
本记录说明已经执行的本机部署及其边界，不提供公网服务地址，也不是自动部署脚本。

## 1 号提供主服务，2 号、3 号等是 user

| 设备编号 | 角色 | 负责内容 |
|---|---|---|
| **1 号** | 主服务端 / operator | API、Postgres、Redis、Scheduler、Verifier、Dashboard、私有 HTTPS 入口及运维。 |
| **2 号、3 号等** | 用户端 / user | 用浏览器访问 1 号；自愿提供算力时安装官方 Codex CLI 和 SpareAgent Worker，使用自己的 ChatGPT Plus 登录。无需安装后端、数据库或 Docker。 |

编号不是数据库用户 ID。1 号管理员不占参与者名额；当前两人试验对应 2 号、3 号两位用户。
后续设备也称为 user，但不能自动扩大本轮两人上限。每个人分别接受邀请、条款及分享同意，
每次真实任务仍须所有者批准。Tailscale 网络成员身份也不替代这些应用内权限。

Worker 安装及真实试验条件以 [Week 6 运行手册](WEEK6_PILOT_RUNBOOK.md)为准。
真实订阅消耗全部手动选择；只使用本人 ChatGPT Plus 包含的 Codex 额度，不使用 API key、
额外购买或赠送 Credits、自动充值或付费回退。已完成的单机验证不能当作两人试验结果。

## 已完成的 LAN HTTPS 部署

独立 Compose 项目 `spareagent-pilot` 包含七个常驻服务：API、Postgres、Redis、Scheduler、
Verifier、Dashboard 和 HTTPS 代理。迁移是独立的一次性服务。

- 使用独立 Postgres、Redis 和 artifacts 持久卷，不复用旧开发数据库。
- 仅代理开放两个入口：Dashboard 的 HTTPS 13443 和 Worker API 的 HTTPS 18443；
  绑定本机私有 LAN IPv4 和 loopback。数据库、Redis 不发布宿主机端口。
- 生产模式关闭开发身份头，使用邀请登录、强随机口令及节点 pepper。
- 使用专用私有 CA；浏览器和 Worker 校验证书链及目标地址。服务端私钥及 CA 私钥不分享。
- API、Scheduler、Verifier 的 LIVE 开关均保持 false；服务器不挂载本机 Codex 登录目录。
  只有可信 Verifier 组件有 Docker socket，任务沙箱没有该 socket。
- 已通过本机 HTTPS、数据库/Redis readiness、邀请登录/退出、认证拒绝、整套重启及数据保留检查。
  Verifier 的无模型沙箱自检通过，Worker wheel 的隔离安装与 macOS 安装脚本检查通过。

以上检查没有模型推理、节点登记、分享同意或真实任务。第二台设备的网络连通仍未验证。
本机部署配置、口令、私钥、数据库备份、邀请码和登录记录保留在操作员本地，不上传 GitHub。

## CMU Wi-Fi 和 Tailscale 备选

可以先使用 CMU-SECURE，但相同 Wi-Fi 名称不保证两台设备能够互访；要由 2 号、3 号访问
1 号验证。CMU 私有 IP 动态分配，换网后应确认新的 LAN IP，并更新服务绑定、浏览器 Origin、
证书和 Worker 连接地址。[CMU 官方说明](https://www.cmu.edu/computing/services/endpoint/network-access/wireless/how-to/connect.html)

**可以把 Tailscale 作为校园网无法互访或异地接入的备选，目前尚未为 SpareAgent 配置。**

1. 1 号与各用户设备安装并连接 Tailscale，经邀请进入同一私有 tailnet。使用各自账号，
   访问规则只允许获准用户连接 SpareAgent 入口，数据库和 Redis 不对用户开放。
2. 使用 **Tailscale Serve 的私有 HTTPS**，不为 SpareAgent 启用公开 Funnel。
   Serve 受 tailnet 访问规则约束，并需配置 tailnet HTTPS。
   [官方 Serve 说明](https://tailscale.com/docs/features/tailscale-serve)
3. 先检查现有 Serve/Funnel 占用，再选择独立端口。本机已有其他应用占用 443 Funnel，
   不覆盖或重置该配置；同一端口不能同时承载私有 Serve 和公开 Funnel。
   [官方端口说明](https://tailscale.com/docs/features/tailscale-serve)
4. 配置本机转发和新 HTTPS 地址，同步更新 Dashboard Origin、必要代理配置和 Worker API URL。
   不能仅把 LAN IP 改成 Tailscale IP：现有 LAN 证书没有覆盖新地址，本机配置脚本也只接受
   RFC1918 LAN IPv4，不支持直接传入 Tailscale 的 `100.x` 地址。
5. 从用户设备重新检查证书、登录和无推理的 `spareagent-worker pilot-check`。连接成功不会
   自动启用 LIVE、同意分享、消耗订阅额度或完成真实两人试验。

Tailscale 不替代 1 号开机、保持唤醒、联网和运行 Docker Desktop；也不自动使 SpareAgent 上线。
切换网络之前，仍需完成上述应用和 HTTPS 配置。

## 停机与数据保留

操作员已按要求停止全部旧环境及新部署容器。新部署的七个容器均在停机前检查了
`HostConfig.AutoRemove=false`；停机后容器和以下三个卷仍保留，本机运行容器数为零：

- `spareagent-pilot_pgdata`
- `spareagent-pilot_redisdata`
- `spareagent-pilot_artifacts`

旧 `spareagent` Compose 项目的容器已停止，九个关联数据卷（含主数据库、文件、证书及
Worker 状态）仍保留。

**数据丢失记录：** 先前停止两个 Week 4 临时容器 `sa-week4-pg`、`sa-week4-redis` 时，
漏查了退出自动删除设置，Docker 自动删除了它们的容器及匿名卷。工作区未找到这两个
测试库的备份，尚未恢复。不能把这两个测试库描述为“数据已保留”；新部署和旧主环境
的保留卷不等于它们的备份。

以后停机前先检查 `AutoRemove` 和卷挂载；若为自动删除容器，先导出可恢复备份并验证。
`docker stop` 对普通容器保留容器和卷，但不能据此推断退出自动删除容器也会保留数据。
不要使用 `docker compose down -v`、`make clean` 或 volume prune 来停机。

本机专用部署恢复时，在其私有 Compose 目录执行 `docker compose up -d --wait`，
先确认当前 IP 和证书仍匹配。不要误用仓库开发 quick start 启动旧测试环境。
启动后仍应保持 LIVE 关闭，直到实际运行手册的全部人工条件满足。
