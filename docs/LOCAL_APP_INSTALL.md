# 在自己的 Mac 上安装 SpareAgent

> 更简单的方式：向运营者要一行安装命令（见 [ONE_CLICK_SHARING.md](ONE_CLICK_SHARING.md) 第 1 步），不需要 Apple 命令行工具，也不用编译。本文的本机编译安装是备用方式。

这是免费安装路线：首次在你的 Mac 上编译菜单栏 App，以后双击启动。不需要 Apple Developer Program 会员，也不会提交 Apple 公证。需要 macOS 13 或更新版本，以及与你处理器对应的私有安装包。

## 首次安装

1. 解压主人或运营者私下发给你的 `SpareAgent-local-install-arm64.zip`（Apple Silicon）或 `SpareAgent-local-install-x86_64.zip`（Intel）。本包有注册配置，只交给允许创建小组的人。
2. 打开「终端」，输入 `bash` 和一个空格，把解压目录中的 `Install-SpareAgent.sh` 拖入终端窗口，然后按回车。安装脚本会检查文件、在本机编译 App，并安装到你的个人 `Applications` 文件夹。App 安装不需要管理员密码；Apple 工具安装可能另需系统授权。
3. 如果系统提示安装 Apple 命令行工具，请本人按 Apple 窗口完成安装；其中的协议和权限提示需要本人处理。完成后重新运行同一个安装脚本。已有工具时会直接继续。
4. App 安装后自动打开，首次在窗口中准备内置 Python 和 Worker。浏览器控制页就绪后，确认已连接 Tailscale，再按页面提示登录、阅读分享说明和上线。安装与普通启动不会自动分享，也不会执行消耗额度的自检或任务；首次确认上线后才按分享说明继续。

以后直接双击个人 Applications 文件夹里的 `SpareAgent.app`。关闭终端不会退出 App，关闭浏览器也不会下线；暂停或退出请用菜单栏。完整分享与任务说明见代码仓库 `docs/ONE_CLICK_SHARING.md`。

本机编译不等于 Apple 公证，无法保证所有 macOS 版本和受管理的电脑都没有额外提示。出现系统安全拦截时，请本人核对来源并按系统提示处理。安装脚本不关闭 Gatekeeper，也不移除隔离属性。

## 更新和重试

先从菜单栏退出 SpareAgent；如果使用旧版终端启动程序，也先关闭它。解压新版安装包，重新运行安装脚本。编译与校验完成后才替换旧 App；替换失败会恢复旧 App。不会清空已注册的设备身份、成员、分享设置或 Codex 登录。

校验失败请重新获取完整包；处理器不匹配请换对应版本；编译失败请检查 Apple 命令行工具后重试。安装器不需要手动安装 Homebrew、Python、Node 或完整 Xcode。

## 当前验证范围

本路线需要在实际接收者电脑上验证首次下载、工具安装及系统提示。本机只验证 Apple Silicon；Intel 与另一台干净 Mac 的安装仍待补验。
