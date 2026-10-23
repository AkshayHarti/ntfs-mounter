// ntfsmnt menu-bar controller.
//
// Starts the loopback dashboard on demand through the narrow NOPASSWD sudo
// rule installed by scripts/install-privileged.sh, reads the one-time token
// URL from the child's stdout, and opens it in the browser. Stopping the
// dashboard terminates only the server process this app started.
//
// This is not a privileged helper: it never mounts, unmounts, probes, or
// repairs a volume. It never uses a shell or AppleScript. Every argument is
// handed to Process as an argv element, so paths with spaces and quotes are
// safe.
//
// Build: bash scripts/build-app.sh

import Cocoa

private let rootCLI = "/usr/local/libexec/ntfsmnt/bin/ntfsmnt"
private let sudoPath = "/usr/bin/sudo"
private let setupGuideURL = URL(string: "https://akshayharti.github.io/ntfs-mounter/")!
private let fullDiskAccessURL = URL(
    string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
)!
private let urlLinePattern = try! NSRegularExpression(pattern: #"^ntfsmnt web: (http://\S+)$"#)

private enum DashboardState: Equatable {
    /// The sudoers rule (or the root-owned CLI) is missing.
    case setupNeeded
    /// Detection passes; no server is running.
    case stopped
    /// The server is running but has not printed its URL yet.
    case starting
    /// The URL has been parsed and opened; the server is serving.
    case running
    /// SIGTERM was sent; the process is still winding down. Start is blocked
    /// until it is gone, so a second server can never race the first.
    case stopping
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var state: DashboardState = .setupNeeded

    private var process: Process?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?
    private var dashboardURL: URL?

    private var stderrBuffer = Data()
    private let stderrLock = NSLock()
    private let stderrDrain = DispatchGroup()

    /// The exact argv granted by `/etc/sudoers.d/ntfsmnt`. Detection probes
    /// this full command line, not a bare `web`: the rule carries arguments,
    /// and sudo only matches an invocation whose arguments match them.
    private var webArguments: [String] {
        ["web", "--port", "0", "--owner", "\(getuid()):\(getgid())"]
    }

    // MARK: - Lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(
                systemSymbolName: "externaldrive",
                accessibilityDescription: "ntfsmnt"
            )
            if button.image == nil {
                button.title = "ntfsmnt"
            }
        }

        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu

        refreshDetection()
        rebuildMenu()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // Never leave an orphaned root server behind on logout or Cmd-Q.
        stopDashboard(waitForExit: true)
        return .terminateNow
    }

    /// Re-detect when the menu opens, so a completed Set up… is picked up
    /// without relaunching the app.
    func menuNeedsUpdate(_ menu: NSMenu) {
        if state == .setupNeeded || state == .stopped {
            refreshDetection()
        }
    }

    // MARK: - Detection

    /// Contract: `sudo -n -l <root-cli> web --port 0 --owner <uid>:<gid>`
    /// exits 0 when the rule is usable. The full argv matters: a bare `web`
    /// does not match the `web *` rule.
    private func detectionPasses() -> Bool {
        let probe = Process()
        probe.executableURL = URL(fileURLWithPath: sudoPath)
        probe.arguments = ["-n", "-l", rootCLI] + webArguments
        probe.standardOutput = FileHandle.nullDevice
        probe.standardError = FileHandle.nullDevice
        do {
            try probe.run()
        } catch {
            return false
        }
        probe.waitUntilExit()
        return probe.terminationStatus == 0
    }

    private func refreshDetection() {
        // A running or starting server owns the state until it exits.
        guard state != .starting, state != .running else { return }
        let next: DashboardState = detectionPasses() ? .stopped : .setupNeeded
        if next != state {
            state = next
            rebuildMenu()
        }
    }

    // MARK: - Menu

    private func rebuildMenu() {
        guard let menu = statusItem?.menu else { return }
        menu.removeAllItems()

        switch state {
        case .setupNeeded:
            menu.addItem(item("Set up…", #selector(setUp)))
            menu.addItem(.separator())
            addCommonItems(to: menu)

        case .stopped:
            menu.addItem(item("Start dashboard", #selector(startDashboard)))
            menu.addItem(.separator())
            addCommonItems(to: menu)

        case .starting:
            let starting = item("Starting…", nil)
            starting.isEnabled = false
            menu.addItem(starting)
            menu.addItem(.separator())
            menu.addItem(item("Quit", #selector(quit)))

        case .stopping:
            let stopping = item("Stopping…", nil)
            stopping.isEnabled = false
            menu.addItem(stopping)
            menu.addItem(.separator())
            menu.addItem(item("Quit", #selector(quit)))

        case .running:
            menu.addItem(item("Open dashboard", #selector(openDashboard)))
            menu.addItem(item("Stop dashboard", #selector(stopDashboardAction)))
            menu.addItem(.separator())
            addCommonItems(to: menu)
        }
    }

    private func addCommonItems(to menu: NSMenu) {
        menu.addItem(item("Setup guide", #selector(openSetupGuide)))
        menu.addItem(item("Grant Full Disk Access…", #selector(openFullDiskAccess)))
        menu.addItem(.separator())
        menu.addItem(item("Quit", #selector(quit)))
    }

    private func item(_ title: String, _ action: Selector?) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        return item
    }

    // MARK: - Menu actions

    @objc private func setUp() {
        openInstaller()
    }

    @objc private func openSetupGuide() {
        NSWorkspace.shared.open(setupGuideURL)
    }

    @objc private func openFullDiskAccess() {
        NSWorkspace.shared.open(fullDiskAccessURL)
    }

    @objc private func openDashboard() {
        guard let url = dashboardURL else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func stopDashboardAction() {
        stopDashboard(waitForExit: false)
    }

    @objc private func quit() {
        stopDashboard(waitForExit: true)
        NSApp.terminate(nil)
    }

    /// Open the installer bundled at Contents/Resources/install-privileged.command.
    /// NSWorkspace runs it in Terminal, which prompts for the admin password.
    private func openInstaller() {
        guard let resources = Bundle.main.resourceURL else {
            showError("This build has no Resources directory, so the bundled installer is missing.")
            return
        }
        let command = resources.appendingPathComponent("install-privileged.command")
        guard FileManager.default.fileExists(atPath: command.path) else {
            showError("The bundled installer is missing:\n\n\(command.path)")
            return
        }
        NSWorkspace.shared.open(command)
    }

    // MARK: - Start

    @objc private func startDashboard() {
        guard state == .stopped, process == nil else { return }

        resetStderr()

        let child = Process()
        child.executableURL = URL(fileURLWithPath: sudoPath)
        child.arguments = ["-n", rootCLI] + webArguments

        let out = Pipe()
        let err = Pipe()
        child.standardOutput = out
        child.standardError = err

        process = child
        stdoutPipe = out
        stderrPipe = err
        state = .starting
        rebuildMenu()

        child.terminationHandler = { [weak self] finished in
            // Let the stderr reader reach EOF before reporting the failure, so
            // the alert has the child's last words. Bounded: a wedged pipe
            // must not wedge the app.
            DispatchQueue.global(qos: .utility).async {
                _ = self?.stderrDrain.wait(timeout: .now() + 0.5)
                DispatchQueue.main.async {
                    self?.processExited(finished)
                }
            }
        }

        do {
            // Enter before run() so an instantly-exiting child cannot beat the
            // stderr reader to the group.
            stderrDrain.enter()
            try child.run()
        } catch {
            stderrDrain.leave()
            process = nil
            stdoutPipe = nil
            stderrPipe = nil
            state = detectionPasses() ? .stopped : .setupNeeded
            rebuildMenu()
            showError("Could not run \(sudoPath): \(error.localizedDescription)")
            return
        }

        readLines(from: out.fileHandleForReading) { [weak self] line in
            self?.handleStdoutLine(line)
        }
        drainStderr(from: err.fileHandleForReading)
    }

    /// Read a pipe on a background queue, split it into lines, and hand each
    /// line to the main queue.
    private func readLines(from handle: FileHandle, onLine: @escaping (String) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            var buffer = Data()
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                while let newline = buffer.firstIndex(of: 0x0A) {
                    let lineData = buffer[buffer.startIndex..<newline]
                    let line = String(decoding: lineData, as: UTF8.self)
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    buffer.removeSubrange(buffer.startIndex...newline)
                    DispatchQueue.main.async { onLine(line) }
                }
            }
        }
    }

    /// Keep the stderr pipe drained so a chatty child can never block on a
    /// full pipe; the bytes are kept for the failure alert.
    private func drainStderr(from handle: FileHandle) {
        DispatchQueue.global(qos: .utility).async {
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                self.stderrLock.lock()
                self.stderrBuffer.append(chunk)
                self.stderrLock.unlock()
            }
            self.stderrDrain.leave()
        }
    }

    private func handleStdoutLine(_ line: String) {
        guard state == .starting else { return }
        guard let url = parseURL(line) else { return }

        dashboardURL = url
        state = .running
        rebuildMenu()
        NSWorkspace.shared.open(url)
    }

    private func parseURL(_ line: String) -> URL? {
        let range = NSRange(line.startIndex..<line.endIndex, in: line)
        guard
            let match = urlLinePattern.firstMatch(in: line, range: range),
            let captured = Range(match.range(at: 1), in: line)
        else {
            return nil
        }
        return URL(string: String(line[captured]))
    }

    // MARK: - Exit / stop

    private func processExited(_ finished: Process) {
        // A handler from a process we already replaced is stale.
        guard finished === process else { return }

        let stderrText = capturedStderr()
        let wasStopping = state == .stopping
        cleanupProcess()

        if wasStopping {
            // Deliberate stop: no alert, no failure report.
            state = detectionPasses() ? .stopped : .setupNeeded
            rebuildMenu()
            return
        }

        switch state {
        case .starting:
            // The server exited before it printed a URL.
            if finished.terminationStatus == 1,
               stderrText.contains("a password is required") {
                state = .setupNeeded
                rebuildMenu()
                showSetupAlert()
            } else {
                state = detectionPasses() ? .stopped : .setupNeeded
                rebuildMenu()
                let detail = stderrText.isEmpty
                    ? "The dashboard server exited with status \(finished.terminationStatus) before printing its URL."
                    : stderrText
                showError(detail)
            }

        case .running:
            // It was running and died on its own.
            state = detectionPasses() ? .stopped : .setupNeeded
            rebuildMenu()
            if !stderrText.isEmpty {
                showError(stderrText)
            }

        case .setupNeeded, .stopped, .stopping:
            break
        }
    }

    /// SIGTERM the dashboard server; if it is still alive after 5 s, force it.
    /// That signal goes to this app's own server tree only: it is the HTTP
    /// server, never a disk, mount, unmount, or repair operation.
    private func stopDashboard(waitForExit: Bool) {
        guard let child = process, child.isRunning else {
            cleanupProcess()
            state = detectionPasses() ? .stopped : .setupNeeded
            rebuildMenu()
            return
        }

        let pid = child.processIdentifier
        // Blocks Start until the termination handler clears `process`, so a
        // second server can never race the one that is winding down.
        state = .stopping
        rebuildMenu()

        child.terminate()  // SIGTERM; sudo forwards it to the server

        // sudo forwards SIGTERM but not SIGKILL. If the server survives the
        // grace period, signal the processes sudo started, then sudo itself.
        let forceStop = { [weak self] in
            guard let self else { return }
            for descendant in self.children(of: pid) {
                kill(descendant, SIGKILL)
            }
            kill(pid, SIGKILL)
        }

        if waitForExit {
            // Poll rather than wait on a main-queue callback: this runs on the
            // main thread while the app is quitting.
            if !waitUntilGone(child, timeout: 5) {
                forceStop()
                _ = waitUntilGone(child, timeout: 1)
            }
        } else {
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 5) {
                if child.isRunning { forceStop() }
            }
        }
    }

    /// Wait for `child` to exit without needing the main queue to run.
    private func waitUntilGone(_ child: Process, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while child.isRunning && Date() < deadline {
            usleep(50_000)
        }
        return !child.isRunning
    }

    /// Direct children of `pid`, read with pgrep. Used only to stop the server
    /// this app started (`sudo` → python), never to signal a disk process.
    private func children(of pid: pid_t) -> [pid_t] {
        let pgrep = Process()
        pgrep.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        pgrep.arguments = ["-P", String(pid)]
        let out = Pipe()
        pgrep.standardOutput = out
        pgrep.standardError = FileHandle.nullDevice
        do {
            try pgrep.run()
        } catch {
            return []
        }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        pgrep.waitUntilExit()
        return String(decoding: data, as: UTF8.self)
            .split(whereSeparator: { $0 == "\n" || $0 == " " })
            .compactMap { pid_t($0) }
    }

    private func cleanupProcess() {
        process?.terminationHandler = nil
        process = nil
        stdoutPipe?.fileHandleForReading.readabilityHandler = nil
        stdoutPipe = nil
        stderrPipe = nil
        dashboardURL = nil
    }

    private func resetStderr() {
        stderrLock.lock()
        stderrBuffer.removeAll(keepingCapacity: false)
        stderrLock.unlock()
    }

    private func capturedStderr() -> String {
        stderrLock.lock()
        let data = stderrBuffer
        stderrLock.unlock()
        return String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - Alerts

    private func showSetupAlert() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Set up ntfsmnt first"
        alert.informativeText = """
        The passwordless dashboard rule is not installed, so starting the \
        server needs a password. Run Set up… and enter your admin password once.
        """
        alert.addButton(withTitle: "Set up…")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            openInstaller()
        }
    }

    private func showError(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "The ntfsmnt dashboard did not start"
        alert.informativeText = String(message.prefix(2000))
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

// MARK: - Entry point

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
