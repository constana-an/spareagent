import AppKit
import CoreImage
import Foundation
import ServiceManagement
import UserNotifications

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

/// Menu-bar text: "在线 · 运行 1 · 今日 3/5" while sharing, otherwise the phase.
func statusTitle(_ state: [String: Any]) -> String {
    let label = phaseLabel(state)
    guard label == "在线" else { return label }
    var parts = [label]
    let running = runningCount(state)
    if running > 0 { parts.append("运行 \(running)") }
    let server = state["server"] as? [String: Any] ?? [:]
    let usage = server["usage"] as? [String: Any] ?? [:]
    if let limit = (server["group"] as? [String: Any])?["max_tasks_per_day"] as? Int {
        parts.append("今日 \(usage["tasks_started_last_24h"] as? Int ?? 0)/\(limit)")
    }
    return parts.joined(separator: " · ")
}

struct OwnerEvent: Equatable {
    let title: String
    let body: String
}

let finishedStatuses: Set<String> = ["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"]

func serverTasks(_ state: [String: Any]) -> [[String: Any]] {
    (state["server"] as? [String: Any])?["tasks"] as? [[String: Any]] ?? []
}

func kindLabel(_ task: [String: Any], _ state: [String: Any]) -> String {
    let type = task["task_type"] as? String ?? ""
    let kinds = (state["server"] as? [String: Any])?["kinds"] as? [[String: Any]] ?? []
    return kinds.first(where: { $0["kind"] as? String == type })?["name_zh"] as? String ?? "任务"
}

/// What the owner should hear about between two polls. The first poll only sets the baseline.
func ownerEvents(previous: [String: Any]?, current: [String: Any]) -> [OwnerEvent] {
    guard let previous = previous else { return [] }
    var events: [OwnerEvent] = []
    var before: [String: String] = [:]
    for task in serverTasks(previous) {
        if let id = task["id"] as? String, let status = task["status"] as? String { before[id] = status }
    }
    for task in serverTasks(current) {
        guard let id = task["id"] as? String, let status = task["status"] as? String else { continue }
        let kind = kindLabel(task, current)
        if let old = before[id] {
            guard !finishedStatuses.contains(old), old != status else { continue }
            if status == "SUCCEEDED" {
                events.append(OwnerEvent(title: "任务完成", body: "\(kind)已完成，结果已交给成员。"))
            } else if status == "FAILED" || status == "TIMED_OUT" {
                let category = (task["failure"] as? [String: Any])?["category"] as? String
                let reason = category == "LOGIN" ? "需要重新登录 ChatGPT。"
                    : category == "USAGE_LIMIT" ? "ChatGPT 额度已用完，恢复后再接任务。"
                    : "打开控制页查看原因。"
                events.append(OwnerEvent(title: "任务没有完成", body: "\(kind)：\(reason)"))
            }
        } else if !finishedStatuses.contains(status) {
            events.append(OwnerEvent(title: "收到新任务", body: "小组成员提交了一个\(kind)。"))
        }
    }
    let was = previous["phase"] as? String, now = current["phase"] as? String
    if was == "ONLINE" && now == "ERROR" {
        events.append(OwnerEvent(title: "分享已中断", body: "打开控制页查看原因并重新上线。"))
    } else if now == "ONLINE" && !(previous["server_error"] is String) && current["server_error"] is String {
        events.append(OwnerEvent(title: "连不上服务器", body: "已暂停接新任务；网络恢复后会自动继续。"))
    }
    return events
}

/// A message the owner can paste into a chat together with the invite link.
func inviteMessage(_ link: String) -> String {
    """
    我在用 SpareAgent 和几个朋友共享我的 ChatGPT 额度，邀请你加入：
    1. 先安装 Tailscale 并登录，加入运营者的私网（运营者会给你邀请）。
    2. 打开这个链接，勾选同意即可加入：\(link)
    3. 在网页里提交任务，结果做好后可以直接下载。
    这个链接只给你，请不要转发。
    """
}

func qrImage(_ text: String, size: CGFloat) -> NSImage? {
    guard let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
    filter.setValue(Data(text.utf8), forKey: "inputMessage")
    filter.setValue("M", forKey: "inputCorrectionLevel")
    guard let output = filter.outputImage else { return nil }
    // A white quiet zone so phone cameras can read it on a dark window too.
    let margin = (size / 12).rounded()
    let scale = (size - 2 * margin) / output.extent.width
    let rep = NSCIImageRep(ciImage: output.transformed(by: CGAffineTransform(scaleX: scale, y: scale)))
    let image = NSImage(size: NSSize(width: size, height: size))
    image.lockFocus()
    NSColor.white.setFill()
    NSRect(x: 0, y: 0, width: size, height: size).fill()
    NSGraphicsContext.current?.imageInterpolation = .none
    rep.draw(in: NSRect(x: margin, y: margin, width: size - 2 * margin, height: size - 2 * margin))
    image.unlockFocus()
    return image
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, UNUserNotificationCenterDelegate {
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
    var onlineItem: NSMenuItem!
    var copyInviteItem: NSMenuItem!
    var qrItem: NSMenuItem!
    var loginItem: NSMenuItem!
    var resumeItem: NSMenuItem!
    var inviteWindow: NSWindow?
    var previousState: [String: Any]?
    var askedNotifications = false
    var resumeChecked = false
    let defaults = UserDefaults.standard
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
        onlineItem = entry("上线", #selector(goOnline))
        onlineItem.isHidden = true
        pauseItem = entry("暂停分享", #selector(pauseSharing))
        pauseItem.isEnabled = false
        retryItem = entry("重试启动", #selector(retry))
        retryItem.isHidden = true
        menu.addItem(.separator())
        copyInviteItem = entry("复制邀请说明", #selector(copyInvite))
        qrItem = entry("显示邀请二维码", #selector(showInvite))
        copyInviteItem.isEnabled = false
        qrItem.isEnabled = false
        menu.addItem(.separator())
        loginItem = entry("开机时自动打开", #selector(toggleLoginItem))
        loginItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
        resumeItem = entry("打开后恢复上次的在线状态", #selector(toggleResume))
        resumeItem.state = defaults.bool(forKey: "resumeOnline") ? .on : .off
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
        let phase = status["phase"] as? String ?? ""
        item.button?.title = " " + (quitting ? "正在退出" : statusTitle(status))
        pauseItem.isEnabled = !quitting && ["ONLINE", "STARTING"].contains(phase)
        // Before the owner has agreed in the control page, "上线" opens that page instead.
        onlineItem.isHidden = quitting || !["OFFLINE", "ERROR"].contains(phase)
        let hasInvite = status["invite_link"] is String
        copyInviteItem.isEnabled = hasInvite
        qrItem.isEnabled = hasInvite
        notify(ownerEvents(previous: previousState, current: status))
        previousState = status
        if phase == "ONLINE" { askNotificationPermission() }
        resumeIfWanted(status)
        openButton.isEnabled = true
        if quitting {
            spinner.startAnimation(nil)
            message.stringValue = "已停止接新任务。正在等待运行中的任务结束，然后退出…"
        } else {
            let next = status["phase"] as? String == "OFFLINE"
                ? (status["consented"] as? Bool == true ? "可以从菜单栏点「上线」。" : "在控制页确认后才能上线。")
                : "请在控制页查看任务和处理提示。"
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

    @objc func goOnline() {
        guard state["consented"] as? Bool == true, state["registered"] as? Bool == true else {
            openPanel(); return
        }
        request("toggle") { status, _ in
            if let status = status { self.update(status) }
            else { self.showWindow(); self.message.stringValue = "上线请求没有完成，请打开控制页重试。" }
        }
    }

    /// Remembers whether sharing was on when the App last ran, and turns it back on at the
    /// next start only if the owner ticked the menu option.
    func resumeIfWanted(_ status: [String: Any]) {
        let phase = status["phase"] as? String ?? ""
        if !resumeChecked {
            resumeChecked = true
            if defaults.bool(forKey: "resumeOnline") && defaults.bool(forKey: "wasOnline")
                && phase == "OFFLINE" && status["consented"] as? Bool == true
                && status["registered"] as? Bool == true {
                goOnline()
                return
            }
        }
        if !quitting && (phase == "ONLINE" || phase == "OFFLINE") {
            defaults.set(phase == "ONLINE", forKey: "wasOnline")
        }
    }

    @objc func toggleResume() {
        let on = !defaults.bool(forKey: "resumeOnline")
        defaults.set(on, forKey: "resumeOnline")
        resumeItem.state = on ? .on : .off
    }

    @objc func toggleLoginItem() {
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled { try service.unregister() } else { try service.register() }
        } catch {
            let alert = NSAlert()
            alert.messageText = "没有设置成功"
            alert.informativeText = "请在「系统设置 → 通用 → 登录项」中手动添加 SpareAgent。\n\(error.localizedDescription)"
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
        }
        loginItem.state = service.status == .enabled ? .on : .off
        if service.status == .requiresApproval {
            SMAppService.openSystemSettingsLoginItems()
        }
    }

    func askNotificationPermission() {
        guard !askedNotifications else { return }
        askedNotifications = true
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func notify(_ events: [OwnerEvent]) {
        guard !events.isEmpty else { return }
        askNotificationPermission()
        for event in events {
            let content = UNMutableNotificationContent()
            content.title = event.title
            content.body = event.body
            content.sound = .default
            UNUserNotificationCenter.current().add(
                UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        DispatchQueue.main.async { self.openPanel() }
        completionHandler()
    }

    @objc func copyInvite() {
        guard let link = state["invite_link"] as? String else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(inviteMessage(link), forType: .string)
    }

    @objc func showInvite() {
        guard let link = state["invite_link"] as? String else { return }
        let window = inviteWindow ?? NSWindow(contentRect: NSRect(x: 0, y: 0, width: 360, height: 430),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "邀请朋友"
        window.isReleasedWhenClosed = false
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 360, height: 430))
        let image = NSImageView(frame: NSRect(x: 70, y: 180, width: 220, height: 220))
        image.image = qrImage(link, size: 220)
        image.imageScaling = .scaleProportionallyUpOrDown
        let hint = NSTextField(wrappingLabelWithString: "朋友连上 Tailscale 后，用手机相机扫码或打开下面的链接即可加入。这个链接只发给你信任的人。")
        hint.frame = NSRect(x: 24, y: 110, width: 312, height: 60)
        let text = NSTextField(wrappingLabelWithString: link)
        text.isSelectable = true
        text.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        text.frame = NSRect(x: 24, y: 64, width: 312, height: 40)
        let copy = NSButton(title: "复制邀请说明", target: self, action: #selector(copyInvite))
        copy.bezelStyle = .rounded
        copy.frame = NSRect(x: 200, y: 18, width: 136, height: 32)
        [image, hint, text, copy].forEach { view.addSubview($0) }
        window.contentView = view
        window.center()
        inviteWindow = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
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
    let online: [String: Any] = ["phase": "ONLINE", "running_here": 1, "server": [
        "usage": ["running": 1, "tasks_started_last_24h": 3], "group": ["max_tasks_per_day": 5],
        "kinds": [["kind": "GENERAL", "name_zh": "通用任务"]],
        "tasks": [["id": "a", "status": "RUNNING", "task_type": "GENERAL"]]]]
    precondition(statusTitle(online) == "在线 · 运行 1 · 今日 3/5")
    precondition(statusTitle(["phase": "OFFLINE"]) == "离线")
    precondition(ownerEvents(previous: nil, current: online).isEmpty)
    var later = online
    later["server"] = ["kinds": [["kind": "GENERAL", "name_zh": "通用任务"]], "tasks": [
        ["id": "a", "status": "FAILED", "task_type": "GENERAL", "failure": ["category": "USAGE_LIMIT"]],
        ["id": "b", "status": "QUEUED", "task_type": "GENERAL"]]]
    precondition(ownerEvents(previous: online, current: later) == [
        OwnerEvent(title: "任务没有完成", body: "通用任务：ChatGPT 额度已用完，恢复后再接任务。"),
        OwnerEvent(title: "收到新任务", body: "小组成员提交了一个通用任务。")])
    precondition(ownerEvents(previous: later, current: later).isEmpty)
    var broken = later
    broken["phase"] = "ERROR"
    precondition(ownerEvents(previous: later, current: broken).map(\.title) == ["分享已中断"])
    var unreachable = later
    unreachable["server_error"] = "offline"
    precondition(ownerEvents(previous: later, current: unreachable).map(\.title) == ["连不上服务器"])
    precondition(inviteMessage("https://h.ts.net:8443/#join=X").contains("https://h.ts.net:8443/#join=X"))
    precondition(qrImage("https://h.ts.net:8443/#join=X", size: 220) != nil)
    print("PASS: local endpoint validation, task count and status labels, menu title, owner notifications, invite")
} else {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.setActivationPolicy(.accessory)
    app.delegate = delegate
    app.run()
}
