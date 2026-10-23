// ntfsmnt menu-bar launcher.
//
// This is a launcher, not a privileged helper: it never runs a server, never
// escalates privileges, and never touches mounted volumes. Every action is a
// Terminal window running the `ntfsmnt` CLI, so sudo prompts (and the
// terminal's Full Disk Access grant) stay with the user's terminal.
//
// Build: bash scripts/build-app.sh

import Cocoa

private let setupGuideURL = "https://akshayharti.github.io/ntfs-mounter/"

// Install commands shown when `ntfsmnt` is missing. Each line is copyable.
private let installCommands = """
git clone https://github.com/AkshayHarti/ntfs-mounter.git && cd ntfs-mounter && ./scripts/setup.sh
uv tool install "ntfsmnt[web]"
brew tap AkshayHarti/ntfs-mounter https://github.com/AkshayHarti/ntfs-mounter && brew install AkshayHarti/ntfs-mounter/ntfsmnt
"""

/// First `ntfsmnt` found: PATH entries in order, then the fixed fallbacks.
private func findCLI() -> String? {
    var dirs = (ProcessInfo.processInfo.environment["PATH"] ?? "")
        .split(separator: ":")
        .map(String.init)
    dirs += [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        (NSHomeDirectory() as NSString).appendingPathComponent(".local/bin"),
    ]
    for dir in dirs where !dir.isEmpty {
        let candidate = (dir as NSString).appendingPathComponent("ntfsmnt")
        if FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
    }
    return nil
}

/// Single-quote for zsh, so a path with spaces or quotes still works.
private func shellQuote(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

/// Write an executable `.command` file under Application Support.
private func writeCommand(named name: String, body: String) throws -> URL {
    let fileManager = FileManager.default
    guard let base = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
    else {
        throw NSError(
            domain: "ntfsmnt", code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Application Support is unavailable."])
    }
    let dir = base.appendingPathComponent("ntfsmnt", isDirectory: true)
    try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
    let url = dir.appendingPathComponent(name)
    try ("#!/bin/zsh\n" + body + "\n").write(to: url, atomically: true, encoding: .utf8)
    try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
    return url
}

private func openSetupGuide() {
    if let url = URL(string: setupGuideURL) {
        NSWorkspace.shared.open(url)
    }
}

private func showError(_ message: String, _ detail: String) {
    let alert = NSAlert()
    alert.alertStyle = .warning
    alert.messageText = message
    alert.informativeText = detail
    alert.addButton(withTitle: "OK")
    NSApp.activate(ignoringOtherApps: true)
    alert.runModal()
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "ntfsmnt"

        let menu = NSMenu()
        menu.addItem(menuItem("Open dashboard", #selector(openDashboard)))
        menu.addItem(menuItem("Run doctor", #selector(runDoctor)))
        menu.addItem(menuItem("Setup guide", #selector(setupGuide)))
        menu.addItem(.separator())
        menu.addItem(menuItem("Quit", #selector(quit)))
        item.menu = menu

        statusItem = item
    }

    private func menuItem(_ title: String, _ action: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        return item
    }

    /// Run the CLI in Terminal through an executable `.command` file. Terminal
    /// prompts for the sudo password; there is no other privilege path here.
    private func run(_ name: String, body: (String) -> String) {
        guard let cli = findCLI() else {
            showInstallAlert()
            return
        }
        do {
            let url = try writeCommand(named: name, body: body(shellQuote(cli)))
            NSWorkspace.shared.open(url)
        } catch {
            showError("Could not write the launcher", error.localizedDescription)
        }
    }

    @objc private func openDashboard() {
        run("open-dashboard.command") { cli in "exec sudo \(cli) web --open" }
    }

    @objc private func runDoctor() {
        run("run-doctor.command") { cli in "\(cli) doctor; echo; read _REPLY" }
    }

    @objc private func setupGuide() {
        openSetupGuide()
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func showInstallAlert() {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "ntfsmnt is not installed"
        alert.informativeText =
            "Install it with one of the commands below, then reopen this menu."

        let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 460, height: 110))
        text.string = installCommands
        text.isEditable = false
        text.isSelectable = true
        text.isVerticallyResizable = true
        text.autoresizingMask = [.width]
        text.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        text.textContainerInset = NSSize(width: 6, height: 6)

        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 460, height: 110))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.documentView = text
        alert.accessoryView = scroll

        alert.addButton(withTitle: "Open setup guide")
        alert.addButton(withTitle: "Close")

        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            openSetupGuide()
        }
    }
}

// Top-level code runs on the main thread; the runtime just cannot prove it.
MainActor.assumeIsolated {
    let application = NSApplication.shared
    let delegate = AppDelegate()
    application.delegate = delegate
    application.setActivationPolicy(.accessory)
    application.run()
}
