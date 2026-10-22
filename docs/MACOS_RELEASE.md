# Mac App 签名与公证

当前状态：这台 Mac 有 `codesign`、`notarytool` 和 `stapler`，但钥匙串中没有可用的代码签名身份。用户尚未加入 Apple Developer Program。发布脚本已准备好，正式 Developer ID 签名、Apple 公证和新 Mac 首次打开验收尚未完成。

当前默认采用免费本机编译安装路线，见 [LOCAL_APP_INSTALL.md](LOCAL_APP_INSTALL.md)。下面的会员、Developer ID 和公证流程保留给以后正式分发使用，不是当前安装的前提。

## 需要本人完成的一次性设置

1. [加入 Apple Developer Program](https://developer.apple.com/programs/enroll/)。个人入会需要开启双重认证的 Apple Account；身份核验、协议和付款由本人完成。
2. 会员生效后，在这台 Mac 上生成证书签名请求，并按 [Apple 的说明创建 Developer ID Application 证书](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)，安装到钥匙串，保留配套私钥。这里需要的是 **Developer ID Application**，不是 Apple Development 或 Developer ID Installer。
3. 在本机交互式配置公证凭据，不把密码或私钥放入仓库、命令参数或聊天：

   ```sh
   xcrun notarytool store-credentials spareagent-notary
   ```

   根据 Apple 工具提示输入 Apple Account、Team ID 和 App 专用密码；凭据存入钥匙串。也可以按 Apple 工具支持的方式使用 App Store Connect API 密钥。

用以下命令确认签名身份可用：

```sh
security find-identity -v -p codesigning
```

## 构建与公证

先按 `ONE_CLICK_SHARING.md` 准备私有 Worker 包。只签名、暂不上传：

```sh
python scripts/build_macos_app.py --arch arm64 \
  --sign-identity 'Developer ID Application: YOUR NAME (TEAMID)'
```

**公证会把整个私有 App 提交给 Apple，其中含 `bundle.json` 的注册配置。确认这些内容适合提交后，再执行上传命令。本轮没有向 Apple 上传任何安装包。**

```sh
python scripts/build_macos_app.py --arch arm64 \
  --sign-identity 'Developer ID Application: YOUR NAME (TEAMID)' \
  --notary-profile spareagent-notary
```

脚本会先检查证书，再校验官方 Python 原始压缩包，逐个签名其中的 Mach-O 组件，重新打成可供公证检查的 ZIP，并记录独立的内置包 SHA-256。官方来源的 URL 和原始校验值保留。App 最后签名，启用 Hardened Runtime 和安全时间戳。Python 解释器单独允许加载本地扩展，以支持现有可选数据处理工具；这个例外不加到 Swift App 上。

只有公证结果为 `Accepted`，票据装订、签名和 Gatekeeper 检查都通过后，才生成 `output/macos-app/SpareAgent-mac-arm64-notarized.zip`。检查原始公证日志中的警告；日志保存在 `output/macos-app/notary-arm64/`。只签名的包使用 `-signed.zip` 后缀，不等同于公证通过。缺证书或公证失败时，不覆盖现有分发包。

不传签名参数仍构建本机测试包，不会上传 Apple，也不会安装到用户的 Applications 目录。所有带注册配置的包仍需私下分发。

## 已验证与待验证

- 47 项相关测试通过，覆盖证书缺失、内部组件签名顺序、Python 权限范围，以及公证未通过时不进入票据装订和 Gatekeeper 验收。
- 本机隔离目录中完成了 11 个 Python 组件的 ad-hoc 签名彩排，启用 Hardened Runtime 后，ZIP 首次安装、标准库、第三方本地扩展导入、私网 TLS 和正常退出通过；没有注册新设备或执行真实推理。
- 这些结果不代表 Developer ID 签名或 Apple 公证已通过。实际会员、证书和凭据就绪后，还需完成真实提交、检查全部组件的公证日志，并从分发渠道下载到另一台干净 Mac，验证首次打开、安装、登录和退出。当前仅验证 Apple Silicon。

依据：[Apple 公证要求](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)、[命令行公证流程](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)。
