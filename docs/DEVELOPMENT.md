# 开发说明

本地启动步骤见 [README](../README.md#本地开发)。现有私有部署的升级、备份和数据库迁移见 [运营维护指南](OPERATOR_GUIDE.md)，不要对现有部署运行开发数据初始化命令。

## 测试

`make test` 在容器中运行单元、集成和端到端验收测试，包含实际 Worker 进程，使用隔离的测试数据库。Docker 沙箱和网络测试在缺少前置条件时会跳过。

| 命令 | 用途 |
|---|---|
| `make lint` | Ruff 检查、格式检查和 OpenAPI 校验 |
| `make e2e-remote` | 远程 Worker 测试，包括运行中终止一个 Worker |
| `make e2e-code` | 沙箱代码执行和独立结果检查 |
| `make e2e-experimental` | 邀请制分享的模拟测试 |
| `make validate-openapi` | 单独校验 API 契约 |

网页位于 `apps/web`，有独立的 `npm run typecheck`、`npm test` 和 `npm run build`，启动方法见 [网页开发说明](../apps/web/README.md)。

演示和测试可在需求中加入标记，注入模拟故障：

- `[mock:transient]`：第一次失败，重试成功。
- `[mock:fail]`：永久失败。
- `[mock:bad-patch]`：结果检查失败。

## API 兼容

从 0.17.2 起，网页新建请求统一为通用任务，交付 `answer.md` 和生成的文件。API 仍接受旧的专用任务类型，供已有客户端和记录使用，包括翻译、表格分析，以及固定提交版本的公开仓库代码任务。接口见 [OpenAPI](../contracts/openapi.yaml)。

## 代码和测试强制执行的规则

- `PERSONAL_SUBSCRIPTION` 资源不参与市场任务调度，也不经网关路由；由数据库 CHECK 约束和 `services/scheduler/policy.py` 执行。仓库没有订阅转售或池化代码。
- 只有 ADMIN 能通过有审计记录的接口授予公开调度资格。
- `DEV_AUTH_ONLY=true` 只允许在 `APP_ENV=local` 下启动，且只接受本机请求。
- 日志、事件和交付文件中的密钥会被脱敏。

## 历史记录

以下文件记录各阶段当时的状态，当前版本以 [使用指南](USAGE_GUIDE.md) 和 [设计决策](DECISIONS.md)（D-90 起）为准。

| 阶段 | 新增内容 | 记录 |
|---|---|---|
| Week 1 | 任务 API、事件流、模拟执行器和网关 | [WEEK1.md](WEEK1.md) |
| Week 2 | 通过出站 HTTPS 和设备凭据连接的远程 Worker | [WEEK2_STATUS.md](WEEK2_STATUS.md) |
| Week 3 | 沙箱代码执行和独立结果检查，默认关闭 | [WEEK3_STATUS.md](WEEK3_STATUS.md) |
| Week 4 | Codex 订阅分享实验 | [WEEK4_SUBSCRIPTION_STATUS.md](WEEK4_SUBSCRIPTION_STATUS.md) |
| Week 5 | 邀请制成员网页；2026-10-18 首次主人单机真实运行 | [WEEK5_STATUS.md](WEEK5_STATUS.md) |
| Week 6 | 成员只安装 Worker；2026-10-19 首次双设备真实运行 | [WEEK6_STATUS.md](WEEK6_STATUS.md) · [安全审查](WEEK6_SECURITY_REVIEW.md) |
| 后续 | 一键 Mac App、通用任务、成员公网网页、App 内更新和主人控制台 | [DECISIONS.md](DECISIONS.md) |
