// Reads the updater's state file so the popover can name this device's
// version and offer a one-click upgrade. All the real work lives in
// smartbar/update_runner.py — this is a reader plus a trigger, and it is a
// trigger *outside* this process on purpose: applying an update restarts
// this very app, so it has to run as the launchd update job, not as a child
// of the window that started it.
import AppKit
import Foundation

@MainActor
final class UpdateStatus: ObservableObject {
    @Published private(set) var pendingVersion = ""   // "" when up to date
    @Published private(set) var blockedReason = ""    // policy hold, e.g. dirty tree
    @Published private(set) var isUpdating = false

    // nonisolated: the detached trigger below reads it off the main actor.
    nonisolated static let label = "com.ductran.ai-smartbar.update"
    /// The updater can take a while (a cold release build); give up waiting
    /// only after this, so a blocked run can't pin the button forever.
    private static let updateGrace: TimeInterval = 600

    private var repoRoot = ""      // learned from the state file
    private var triggeredAt: Date?
    private var timer: Timer?
    private var activationObserver: NSObjectProtocol?

    var currentVersion: String { AppVersion.current }

    init() {
        reload()
        // The badge has to appear without the user opening anything, so poll
        // the state file — slowly, since the updater itself only looks for a
        // new release every 6 h. This is a small local file read.
        let timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) {
            [weak self] _ in
            Task { @MainActor in self?.reload() }
        }
        timer.tolerance = 30
        self.timer = timer
        activationObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didBecomeActiveNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.reload() }
        }
    }

    deinit {
        timer?.invalidate()
        if let activationObserver {
            NotificationCenter.default.removeObserver(activationObserver)
        }
    }

    private static var stateURL: URL {
        let env = ProcessInfo.processInfo.environment
        let dir = env["SMARTBAR_CACHE_DIR"].flatMap { $0.isEmpty ? nil : $0 }
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".cache/ai-smartbar").path
        return URL(fileURLWithPath: dir)
            .appendingPathComponent("update-state.json")
    }

    /// Cheap: a small local JSON file, re-read whenever the popover opens.
    func reload() {
        if let started = triggeredAt,
           Date().timeIntervalSince(started) > Self.updateGrace {
            isUpdating = false
            triggeredAt = nil
        }
        guard let data = try? Data(contentsOf: Self.stateURL),
              let raw = (try? JSONSerialization.jsonObject(with: data))
                  as? [String: Any]
        else { return }
        if let root = raw["repoRoot"] as? String, !root.isEmpty {
            repoRoot = root
        }
        // A "pending" version equal to what we already run means the updater
        // hasn't re-checked since the restart — never offer a no-op upgrade.
        let pending = raw["pendingVersion"] as? String ?? ""
        let next = (pending == AppVersion.current) ? "" : pending
        if next != pendingVersion { pendingVersion = next }
        let blocked = (raw["action"] as? String) == "blocked"
            ? (raw["reason"] as? String ?? "") : ""
        if blocked != blockedReason { blockedReason = blocked }
    }

    func installUpdate() {
        guard !isUpdating else { return }
        isUpdating = true
        triggeredAt = Date()
        let uid = getuid()
        let root = repoRoot
        Task.detached(priority: .userInitiated) {
            if Self.agentInstalled() {
                // launchd owns the job, so it survives this app being
                // restarted by the update it is performing.
                Self.spawn("/bin/launchctl",
                           ["kickstart", "-k", "gui/\(uid)/\(Self.label)"])
            } else if !root.isEmpty {
                // Device opted out of the agent: run the updater detached.
                Self.spawn("/bin/sh",
                           ["-c", "nohup \"\(root)/bin/ai-smartbar\" --update "
                                + ">/dev/null 2>&1 &"])
            }
        }
    }

    nonisolated private static func agentInstalled() -> Bool {
        let path = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(label).plist").path
        return FileManager.default.fileExists(atPath: path)
    }

    /// Fire and forget — never waited on: the callee restarts this process.
    nonisolated private static func spawn(_ executable: String,
                                          _ arguments: [String]) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        try? process.run()
    }
}
