# 这台 Mac 的维护入口

适用于已经配置好的 Mac 私有部署。日常启动、服务恢复、健康检查和免费安装包生成使用同一个入口；首次搭建空服务器不在此入口范围内。早期部署记录见 [MAC_PRIVATE_DEPLOYMENT.md](MAC_PRIVATE_DEPLOYMENT.md)，当前维护以本文和私有目录的 README 为准。

## 日常使用

先打开 Docker Desktop，并连接 Tailscale，然后双击代码目录里的 `scripts/SpareAgent-Operator.command`。终端会依次显示：

1. 检查已保存的配置、现有授权、私网地址和未完成任务。
2. 备份配置、数据库、交付文件和 Tailscale 入口配置。
3. 启动或恢复现有服务，补回缺失的 SpareAgent 私网入口。
4. 验证网页与 API 的 HTTPS，并检查真实执行开关和原有私网入口。
5. 生成免费本机安装包；输入和文件校验均未变化时直接复用。

成功后会显示安装包路径和本次备份目录。Apple Silicon 的默认安装包是 `output/macos-app/SpareAgent-local-install-arm64.zip`，含私有注册码，只私下发给允许创建小组的人。提供者按 [LOCAL_APP_INSTALL.md](LOCAL_APP_INSTALL.md) 首次安装，以后双击 App。

入口读取相邻的 `spareagent-private/.env` 和 `compose.yaml`；地址保存于该目录的 `operator.json`。没有这些现有配置或数据库卷时停止，不会另建一个空部署。需要 Python 3.12+；打包还需要可用的 setuptools 69+ 和 wheel。当前 Mac 已具备这些条件。依赖缓存有效时离线复用；缓存不足时从包仓库下载，固定 Python 运行环境仍按原有校验值验证。

## 只检查或升级源码

在代码目录执行：

```sh
# 只读检查，不启动服务、不备份、不打包
python3 scripts/operator.py --check

# 当前源码需要部署到服务端时：先备份，再构建并应用镜像
./scripts/SpareAgent-Operator.command --rebuild

# 私有部署不在相邻目录时
./scripts/SpareAgent-Operator.command --private-dir /path/to/spareagent-private
```

普通双击复用 `.env` 已选定的镜像。`--rebuild` 根据源码内容生成镜像标签，两份镜像都构建成功后才修改镜像配置；不自动拉取代码。数据库迁移由现有 Compose 服务执行。

## 保留什么

- 保留设备身份、成员、邀请码、任务记录、交付文件和分享设置；不创建或重置它们。
- 保留 `.env` 中的真实执行开关，不因终端环境变量或重新部署而开启。维护不会上线 Worker、提交任务或执行推理。
- 保留其他应用的 Serve/Funnel；只补回缺失的 SpareAgent 入口。端口冲突时停止，不自动覆盖。
- 已有未完成任务时停止维护。多个维护进程不能同时执行。

当前运行中的普通服务仍可接受用户请求，维护没有单独的全站停写模式；应在无人提交任务的维护时段运行。维护入口不再扫描多张表比对摘要，避免把成员加入或任务提交误报成维护失败。`before.json` 和结果中的 `counts_before` 记录维护前数量，不代表维护期间数据冻结或内容逐项核验。

## 失败时

终端显示停止的步骤和日志目录。每次写入操作的记录位于私有部署目录的 `private/operator-runs/<时间>-<随机值>/`：

- `operation.log`：该次命令输出。
- `database.dump`、`artifacts.tar`：数据库与交付文件备份。
- `.env`、Compose、Nginx 和 Tailscale JSON：操作前配置。
- `before.json`、`result.json` 或 `failure.json`：维护前状态、运行结果或失败步骤。
- 替换安装包时保留 `previous-installer.zip` 和 `previous-worker-bundle/`。

这些备份含私有配置，应保留在本机。修复所报问题后可重新运行。新安装包全部构建完成才替换旧包，替换失败会恢复旧包。服务升级失败不自动回滚数据库；需要时根据该次备份和旧配置手动恢复，避免迁移后的结构与旧镜像不兼容。

当前验证范围限于这台 Apple Silicon Mac。另一台 Mac 的首次下载、工具安装提示、Gatekeeper 和跨设备接入仍待实机验证。
