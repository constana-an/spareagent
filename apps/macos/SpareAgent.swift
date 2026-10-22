import AppKit
import Foundation

struct PanelEndpoint {
    let url: URL
    let key: String
    let origin: URL

    init?(_ text: String) {
        guard var parts = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme == "http", parts.host == "127.0.0.1", parts.port != nil,
              parts.user == nil, parts.password == nil, parts.query == nil,
              parts.path == "/" || parts.path.isEmpty,
              let fragment = parts.fragment,
              let key = URLComponents(string: "?" + fragment)?.queryItems?.first(where: { $0.name == "k" })?.value,
              !key.isEmpty, let url = parts.url else { return nil }
        self.url = url
        self.key = key
        parts.fragment = nil
        self.origin = parts.url!
    }
}

func phaseLabel(_ state: [String: Any]) -> String {
    if state["server_error"] is String { return "连接异常" }
    switch state["phase"] as? String {
    case "ONLINE": return "在线"
    case "OFFLINE": return "离线"
    case "STARTING": return "正在上线"
    case "STOPPING": return "正在下线"
    default: return "需要处理"
    }
}

func runningCount(_ state: [String: Any]) -> Int {
    let server = state["server"] as? [String: Any] ?? [:]
    let usage = server["usage"] as? [String: Any] ?? [:]
    return max(state["running_here"] as? Int ?? 0, usage["running"] as? Int ?? 0)
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let files = FileManager.default
    let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? "SpareAgent"
    let stateDirectory: URL = {
        let path = ProcessInfo.processInfo.environment["SPAREAGENT_WORKER_HOME"] ?? "~/.spareagent-worker"
        return URL(fileURLWithPath: NSString(string: path).expandingTildeInPath)
    }()
    var window: NSWindow!
    var message: NSTextField!
    var spinner: NSProgressIndicator!
    var retryButton: NSButton!
    var openButton: NSButton!
    var item: NSStatusItem!
    var pauseItem: NSMenuItem!
    var retryItem: NSMenuItem!
    var worker: Process?
    var output: Pipe?
    var endpoint: PanelEndpoint?
    var state: [String: Any] = [:]
    var timer: Timer?
    var polling = false
    var ready = false
    var opened = false
    var failed = false
    var quitting = false
    var canTerminate = false
    var launchTime = Date()
    var shutdownAcknowledged = false
    var failureDetail = ""

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let identifier = Bundle.main.bundleIdentifier,
           let other = NSRunningApplication.runningApplications(withBundleIdentifier: identifier)
            .first(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            DistributedNotificationCenter.default().postNotificationName(
                NSNotification.Name(identifier + ".openPanel"), object: nil, userInfo: nil, deliverImmediately: true)
            other.activate(options: [])
            NSApp.terminate(nil)
            return
        }
        if let identifier = Bundle.main.bundleIdentifier {
            DistributedNotificationCenter.default().addObserver(self, selector: #selector(reopenPanel),
                name: NSNotification.Name(identifier + ".openPanel"), object: nil)
        }
        makeUI()
        begin()
    }

    func makeUI() {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "bolt.circle", accessibilityDescription: appName)
        item.button?.title = " 准备中"
        let menu = NSMenu()
        menu.autoenablesItems = false
        func entry(_ title: String, _ action: Selector) -> NSMenuItem {
            let result = NSMenuItem(title: title, action: action, keyEquivalent: "")
            result.target = self
            menu.addItem(result)
            return result
        }
        _ = entry("打开控制页", #selector(openPanel))
        pauseItem = entry("暂停分享", #selector(pauseSharing))
        pauseItem.isEnabled = false
        retryItem = entry("重试启动", #selector(retry))
        retryItem.isHidden = true
        _ = entry("显示状态", #selector(showWindow))
        menu.addItem(.separator())
        _ = entry("退出 " + appName, #selector(quit))
        item.menu = menu
        let mainMenu = NSMenu()
        let rootMenu = NSMenuItem()
        let appMenu = NSMenu()
        let quitMenu = NSMenuItem(title: "退出 " + appName, action: #selector(quit), keyEquivalent: "q")
        quitMenu.target = self
        appMenu.addItem(quitMenu)
        rootMenu.submenu = appMenu
        mainMenu.addItem(rootMenu)
        NSApp.mainMenu = mainMenu

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 490, height: 230),
            styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = appName
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        let title = NSTextField(labelWithString: appName)
        title.font = .boldSystemFont(ofSize: 24)
        title.frame = NSRect(x: 28, y: 169, width: 425, height: 34)
        message = NSTextField(wrappingLabelWithString: "正在准备…")
        message.frame = NSRect(x: 28, y: 75, width: 430, height: 78)
        spinner = NSProgressIndicator(frame: NSRect(x: 28, y: 30, width: 24, height: 24))
        spinner.style = .spinning
        spinner.startAnimation(nil)
        retryButton = NSButton(title: "重试", target: self, action: #selector(retry))
        retryButton.frame = NSRect(x: 267, y: 25, width: 80, height: 32)
        retryButton.bezelStyle = .rounded
        retryButton.isHidden = true
        openButton = NSButton(title: "打开控制页", target: self, action: #selector(openPanel))
        openButton.frame = NSRect(x: 349, y: 25, width: 115, height: 32)
        openButton.bezelStyle = .rounded
        openButton.isEnabled = false
        [title, message, spinner, retryButton, openButton].forEach { window.contentView?.addSubview($0) }
        showWindow()
    }

    @objc func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func readEndpoint() -> PanelEndpoint? {
        guard let text = try? String(contentsOf: stateDirectory.appendingPathComponent("app.url"), encoding: .utf8) else { return nil }
        return PanelEndpoint(text)
    }

    func request(_ route: String, body: [String: Any] = [:], completion: @escaping ([String: Any]?, Error?) -> Void) {
        guard let endpoint = endpoint else { completion(nil, nil); return }
        var req = URLRequest(url: endpoint.origin.appendingPathComponent("api/" + route))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(endpoint.key, forHTTPHeaderField: "X-App-Key")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        req.timeoutInterval = 4
        URLSession.shared.dataTask(with: req) { data, response, error in
            let object = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { completion(ok ? object : nil, error) }
        }.resume()
    }

    func begin() {
        guard !quitting else { return }
        failed = false
        ready = false
        opened = false
        failureDetail = ""
        retryButton.isHidden = true
        retryItem.isHidden = true
        openButton.isEnabled = false
        spinner.startAnimation(nil)
        message.stringValue = "正在检查本机接单程序…"
        item.button?.title = " 准备中"
        launchTime = Date()
        endpoint = readEndpoint()
        if endpoint != nil {
            request("state") { status, _ in
                if let status = status {
                    if status["desktop_protocol"] as? Int == 1 {
                        self.update(status)
                        self.startPolling()
                    } else {
                        self.fail("旧版接单程序仍在运行。请先在旧版下线并关闭启动窗口，再点重试。")
                    }
                } else { self.launchWorker() }
            }
        } else { launchWorker() }
    }

    func launchWorker() {
        guard let resources = Bundle.main.resourceURL else { fail("App 不完整，请重新获取安装包。"); return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [resources.appendingPathComponent("SpareAgent.command").path]
        var environment = ProcessInfo.processInfo.environment
        environment["SPAREAGENT_NATIVE_APP"] = "1"
        process.environment = environment
        process.currentDirectoryURL = resources
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        output = pipe
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            guard let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                for line in text.components(separatedBy: .newlines) {
                    if line.hasPrefix("正在") || line.hasPrefix("首次运行") {
                        self.message.stringValue = line.components(separatedBy: " / ")[0]
                    }
                    if ["安装包", "运行环境", "下载运行环境", "App 缺少", "不支持"].contains(where: { line.hasPrefix($0) }) {
                        self.failureDetail = line.components(separatedBy: " / ")[0]
                    }
                }
            }
        }
        process.terminationHandler = { process in
            DispatchQueue.main.async {
                self.output?.fileHandleForReading.readabilityHandler = nil
                if self.quitting && self.shutdownAcknowledged { self.finishQuit() }
                else if !self.quitting {
                    self.fail(process.terminationStatus == 0
                        ? "接单程序已停止。点重试重新打开，恢复后仍需在控制页点击上线。"
                        : (self.failureDetail.isEmpty ? "启动没有完成。请检查安装包是否完整，或重新获取安装包后重试。" : self.failureDetail))
                }
            }
        }
        worker = process
        do { try process.run(); startPolling() }
        catch { fail("无法启动接单程序，请重新获取适合这台 Mac 的安装包。") }
    }

    func startPolling() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { _ in self.poll() }
        poll()
    }

    func poll() {
        guard !polling, !failed else { return }
        endpoint = readEndpoint()
        guard endpoint != nil else {
            if quitting && shutdownAcknowledged { finishQuit() }
            else if !ready && Date().timeIntervalSince(launchTime) > 180 {
                worker?.terminate()
                fail("准备时间较长。请稍后重试，或重新获取安装包。")
            }
            return
        }
        polling = true
        request("state") { status, _ in
            self.polling = false
            if let status = status { self.update(status) }
            else if self.quitting && self.shutdownAcknowledged { self.finishQuit() }
            else if self.ready {
                self.item.button?.title = " 连接异常"
                self.message.stringValue = "无法连接本机接单程序。请稍后重试；也可从菜单栏退出。"
            }
        }
    }

    func update(_ status: [String: Any]) {
        state = status
        ready = true
        spinner.stopAnimation(nil)
        let label = phaseLabel(status)
        item.button?.title = " " + (quitting ? "正在退出" : label)
        pauseItem.isEnabled = !quitting && ["ONLINE", "STARTING"].contains(status["phase"] as? String ?? "")
        openButton.isEnabled = true
        if quitting {
            spinner.startAnimation(nil)
            message.stringValue = "已停止接新任务。正在等待运行中的任务结束，然后退出…"
        } else {
            let next = status["phase"] as? String == "OFFLINE" ? "在控制页确认后才能上线。" : "请在控制页查看任务和处理提示。"
            message.stringValue = "当前：\(label)\n\(next)关闭控制页会继续运行，可从菜单栏暂停或退出。"
        }
        if !opened && !quitting {
            opened = true
            if ProcessInfo.processInfo.environment["SPAREAGENT_NO_BROWSER"] != "1" { openPanel() }
        }
    }

    func fail(_ detail: String) {
        failed = true
        ready = false
        timer?.invalidate()
        spinner.stopAnimation(nil)
        message.stringValue = detail
        item.button?.title = " 启动失败"
        retryButton.isHidden = false
        retryItem.isHidden = false
        pauseItem.isEnabled = false
        showWindow()
    }

    @objc func retry() {
        guard worker?.isRunning != true else {
            message.stringValue = "接单程序还在准备，请稍候。"; return
        }
        begin()
    }

    @objc func openPanel() {
        if let endpoint = endpoint, ready { NSWorkspace.shared.open(endpoint.url) }
        else { showWindow() }
    }

    @objc func reopenPanel() { showWindow(); openPanel() }

    func offlineChoice(exiting: Bool) -> String? {
        guard runningCount(state) > 0 else { return "finish" }
        let alert = NSAlert()
        alert.messageText = exiting ? "还有任务正在运行" : "暂停分享"
        alert.informativeText = "暂停后不再接新任务。可以等当前任务完成，或立即停止并交还队列。"
        alert.addButton(withTitle: exiting ? "运行完再退出" : "运行完再下线")
        alert.addButton(withTitle: "立即停止并交还")
        alert.addButton(withTitle: "取消")
        NSApp.activate(ignoringOtherApps: true)
        switch alert.runModal() {
        case .alertFirstButtonReturn: return "finish"
        case .alertSecondButtonReturn: return "now"
        default: return nil
        }
    }

    @objc func pauseSharing() {
        guard let mode = offlineChoice(exiting: false) else { return }
        request("pause", body: ["offline_mode": mode]) { status, _ in
            if let status = status { self.update(status) }
            else { self.showWindow(); self.message.stringValue = "暂停请求没有完成，请打开控制页检查后重试。" }
        }
    }

    @objc func quit() { NSApp.terminate(nil) }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if canTerminate || window == nil { return .terminateNow }
        if quitting { return .terminateCancel }
        guard ready else {
            worker?.terminate()
            canTerminate = true
            return .terminateNow
        }
        guard let mode = offlineChoice(exiting: true) else { return .terminateCancel }
        quitting = true
        showWindow()
        spinner.startAnimation(nil)
        message.stringValue = "正在暂停分享并退出…"
        request("shutdown", body: ["offline_mode": mode]) { status, _ in
            if let status = status {
                self.shutdownAcknowledged = true
                self.update(status)
            } else {
                self.quitting = false
                self.spinner.stopAnimation(nil)
                self.message.stringValue = "退出请求没有完成。请打开控制页暂停分享后重试。"
                NSApp.reply(toApplicationShouldTerminate: false)
            }
        }
        return .terminateLater
    }

    func finishQuit() {
        guard quitting else { return }
        canTerminate = true
        timer?.invalidate()
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        reopenPanel()
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

if CommandLine.arguments.contains("--self-test") {
    precondition(PanelEndpoint("http://127.0.0.1:1234/#k=test")?.key == "test")
    for text in ["https://example.com/#k=x", "http://localhost:123/#k=x", "http://127.0.0.1:123/", "http://u@127.0.0.1:123/#k=x"] {
        precondition(PanelEndpoint(text) == nil)
    }
    precondition(runningCount(["running_here": 2, "server": ["usage": ["running": 1]]]) == 2)
    precondition(phaseLabel(["phase": "OFFLINE"]) == "离线")
    precondition(phaseLabel(["phase": "ONLINE", "server_error": "offline"]) == "连接异常")
    print("PASS: local endpoint validation, task count and status labels")
} else {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.setActivationPolicy(.accessory)
    app.delegate = delegate
    app.run()
}
