import Cocoa
import WebKit

@main
class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        _ = NSApplicationMain(CommandLine.argc, CommandLine.unsafeArgv)
    }

    var window: NSWindow!
    var webView: WKWebView!
    var backendProcess: Process?
    let targetPort = 8000
    let sandboxPort = 8001

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)

        // 1. Setup native window with dark theme
        let screenRect = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let windowWidth: CGFloat = 1440
        let windowHeight: CGFloat = 900
        let windowRect = NSRect(
            x: screenRect.midX - windowWidth / 2,
            y: screenRect.midY - windowHeight / 2,
            width: windowWidth,
            height: windowHeight
        )

        window = NSWindow(
            contentRect: windowRect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Harness-Zero"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = NSColor(red: 9/255, green: 13/255, blue: 22/255, alpha: 1.0)
        window.delegate = self

        // 2. Setup WKWebView with developer tools and zero margin
        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.setValue(false, forKey: "drawsBackground") // Prevents white flicker
        webView.uiDelegate = self
        window.contentView?.addSubview(webView)

        // 3. Build native macOS menu bar
        setupMenuBar()

        // 4. Spawn bundled harness-engine daemon
        startBackendDaemon()

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func setupMenuBar() {
        let mainMenu = NSMenu()

        // App Menu
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Harness-Zero", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Settings…", action: #selector(openPreferences), keyEquivalent: ",")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Hide Harness-Zero", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = NSMenuItem(title: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(hideOthers)
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Quit Harness-Zero", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        // File Menu (Workspace Management)
        let fileMenuItem = NSMenuItem()
        let fileMenu = NSMenu(title: "File")
        fileMenu.addItem(withTitle: "Open Workspace…", action: #selector(openWorkspaceDialog), keyEquivalent: "o")
        fileMenu.addItem(NSMenuItem.separator())
        fileMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        fileMenuItem.submenu = fileMenu
        mainMenu.addItem(fileMenuItem)

        // Edit Menu
        let editMenuItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)

        // View Menu
        let viewMenuItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Reload", action: #selector(reloadWebView), keyEquivalent: "r")
        let inspectItem = NSMenuItem(title: "Inspect Webview", action: #selector(inspectWebView), keyEquivalent: "i")
        inspectItem.keyEquivalentModifierMask = [.command, .option]
        viewMenu.addItem(inspectItem)
        viewMenu.addItem(NSMenuItem.separator())
        viewMenu.addItem(withTitle: "Toggle Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        viewMenuItem.submenu = viewMenu
        mainMenu.addItem(viewMenuItem)

        // Window Menu
        let windowMenuItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.zoom(_:)), keyEquivalent: "")
        windowMenuItem.submenu = windowMenu
        mainMenu.addItem(windowMenuItem)

        NSApp.mainMenu = mainMenu
    }

    @objc func reloadWebView() {
        webView.reload()
    }

    @objc func openPreferences() {
        webView.evaluateJavaScript("openSettingsModal();", completionHandler: nil)
    }

    @objc func inspectWebView() {
        // Triggers WebKit developer inspector
        webView.evaluateJavaScript("console.log('[Harness-Zero] Inspecting...');", completionHandler: nil)
    }

    @objc func openWorkspaceDialog() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.title = "Select Project Workspace"
        panel.message = "Choose an isolated directory for the agent workspace jail (never your home directory)."
        panel.prompt = "Open Workspace"

        if panel.runModal() == .OK, let selected = panel.url {
            if selected.path == FileManager.default.homeDirectoryForCurrentUser.path {
                let alert = NSAlert()
                alert.messageText = "Invalid Workspace Selection"
                alert.informativeText = "Harness-Zero enforces strict workspace jail isolation and cannot use your root home directory ($HOME) as a workspace."
                alert.alertStyle = .critical
                alert.addButton(withTitle: "OK")
                alert.runModal()
                return
            }
            saveWorkspace(selected)
            restartBackend(with: selected)
        }
    }

    func resolveWorkspace() -> URL {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Harness-Zero")
        let stateFile = appSupport?.appendingPathComponent("state.json")

        // 1. Check remembered workspace
        if let sf = stateFile, FileManager.default.fileExists(atPath: sf.path),
           let data = try? Data(contentsOf: sf),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let lastWs = json["last_workspace"] as? String,
           !lastWs.isEmpty,
           lastWs != FileManager.default.homeDirectoryForCurrentUser.path,
           FileManager.default.fileExists(atPath: lastWs) {
            return URL(fileURLWithPath: lastWs)
        }

        // 2. Interactive modal picker if running with UI
        let isHeadless = CommandLine.arguments.contains("--headless") || CommandLine.arguments.contains("-h")
        if !isHeadless {
            let panel = NSOpenPanel()
            panel.canChooseFiles = false
            panel.canChooseDirectories = true
            panel.canCreateDirectories = true
            panel.allowsMultipleSelection = false
            panel.title = "Select Project Workspace"
            panel.message = "Choose an isolated directory for the agent workspace jail (never your home directory)."
            panel.prompt = "Select Workspace"

            if panel.runModal() == .OK, let selected = panel.url {
                if selected.path != FileManager.default.homeDirectoryForCurrentUser.path {
                    saveWorkspace(selected)
                    return selected
                }
            }
        }

        // 3. Fallback: current working directory (if not naked $HOME), else isolated scratch
        let currentPath = FileManager.default.currentDirectoryPath
        if currentPath != FileManager.default.homeDirectoryForCurrentUser.path {
            let currentUrl = URL(fileURLWithPath: currentPath)
            saveWorkspace(currentUrl)
            return currentUrl
        }

        let scratchDir = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("harness-workspace")
        try? FileManager.default.createDirectory(at: scratchDir, withIntermediateDirectories: true)
        saveWorkspace(scratchDir)
        return scratchDir
    }

    func saveWorkspace(_ url: URL) {
        guard let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Harness-Zero") else { return }
        try? FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
        let stateFile = appSupport.appendingPathComponent("state.json")

        var state: [String: Any] = [:]
        if FileManager.default.fileExists(atPath: stateFile.path),
           let data = try? Data(contentsOf: stateFile),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            state = json
        }

        state["last_workspace"] = url.path
        var recents = state["recent_workspaces"] as? [String] ?? []
        recents.removeAll(where: { $0 == url.path })
        recents.insert(url.path, at: 0)
        if recents.count > 10 {
            recents = Array(recents.prefix(10))
        }
        state["recent_workspaces"] = recents

        if let data = try? JSONSerialization.data(withJSONObject: state, options: [.prettyPrinted]) {
            try? data.write(to: stateFile)
        }
    }

    func restartBackend(with url: URL) {
        if let proc = backendProcess, proc.isRunning {
            proc.interrupt()
            proc.waitUntilExit()
            self.backendProcess = nil
        }
        startBackendDaemon(withWorkspace: url)
    }

    func startBackendDaemon(withWorkspace explicitWorkspace: URL? = nil) {
        let bundle = Bundle.main
        var candidates: [String] = []
        if let resPath = bundle.resourcePath {
            candidates.append((resPath as NSString).appendingPathComponent("harness-engine"))
        }
        candidates.append(bundle.bundleURL.appendingPathComponent("Contents/Resources/harness-engine").path)
        candidates.append(bundle.bundleURL.appendingPathComponent("harness-engine").path)
        candidates.append("/Applications/Harness-Zero.app/Contents/Resources/harness-engine")

        var engineExecutable: String? = nil
        for c in candidates {
            if FileManager.default.fileExists(atPath: c) {
                engineExecutable = c
                break
            }
        }

        if let execPath = engineExecutable {
            let workspaceURL = explicitWorkspace ?? resolveWorkspace()

            var args = ["daemon", "--workspace", workspaceURL.path, "--port", "\(targetPort)", "--sandbox-port", "\(sandboxPort)"]

            // Only pass --allow-host if explicitly provided in launcher arguments
            if let hostIdx = CommandLine.arguments.firstIndex(of: "--allow-host"), hostIdx + 1 < CommandLine.arguments.count {
                args.append(contentsOf: ["--allow-host", CommandLine.arguments[hostIdx + 1]])
            }

            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: execPath)
            proc.arguments = args
            
            // Set working directory to project workspace
            proc.currentDirectoryURL = workspaceURL
            
            do {
                try proc.run()
                self.backendProcess = proc
                NSLog("[Harness-Zero Launcher] Successfully launched harness-engine at %@ (PID: %d) with workspace %@", execPath, proc.processIdentifier, workspaceURL.path)
            } catch {
                NSLog("[Harness-Zero Launcher] Failed to launch daemon: %@", error.localizedDescription)
            }
        } else {
            NSLog("[Harness-Zero Launcher] Warning: harness-engine executable not found. Connecting to existing daemon if active...")
        }

        // Wait for health check then load URL (poll for up to 15 seconds)
        pollHealthAndLoadURL(attemptsRemaining: 60)
    }

    func pollHealthAndLoadURL(attemptsRemaining: Int) {
        let healthUrl = URL(string: "http://127.0.0.1:\(targetPort)/health")!
        let task = URLSession.shared.dataTask(with: healthUrl) { [weak self] (_, response, error) in
            guard let self = self else { return }
            if let httpResp = response as? HTTPURLResponse, httpResp.statusCode == 200 {
                DispatchQueue.main.async {
                    var tokenQuery = ""
                    let ws = self.resolveWorkspace()
                    let daemonFile = ws.appendingPathComponent(".harness/daemon.json")
                    if FileManager.default.fileExists(atPath: daemonFile.path),
                       let data = try? Data(contentsOf: daemonFile),
                       let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let token = json["token"] as? String, !token.isEmpty {
                        tokenQuery = "?token=\(token)"
                    }
                    let studioUrl = URL(string: "http://127.0.0.1:\(self.targetPort)/\(tokenQuery)")!
                    self.webView.load(URLRequest(url: studioUrl))
                    NSLog("[Harness-Zero Launcher] Daemon health confirmed. Studio loaded.")
                }
            } else if attemptsRemaining > 0 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                    self.pollHealthAndLoadURL(attemptsRemaining: attemptsRemaining - 1)
                }
            } else {
                DispatchQueue.main.async {
                    let studioUrl = URL(string: "http://127.0.0.1:\(self.targetPort)/")!
                    self.webView.load(URLRequest(url: studioUrl))
                }
            }
        }
        task.resume()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            window.makeKeyAndOrderFront(nil)
        }
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let proc = backendProcess, proc.isRunning {
            print("[Harness-Zero Launcher] Terminating backend daemon...")
            proc.interrupt()
            proc.waitUntilExit()
        }
    }

    // MARK: - WKUIDelegate (Native File Upload & Dialogs)
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let openPanel = NSOpenPanel()
        openPanel.canChooseFiles = true
        openPanel.canChooseDirectories = false
        openPanel.allowsMultipleSelection = parameters.allowsMultipleSelection
        openPanel.title = "Select File or Image to Attach"
        openPanel.prompt = "Attach"
        openPanel.begin { response in
            if response == .OK {
                completionHandler(openPanel.urls)
            } else {
                completionHandler(nil)
            }
        }
    }
}
