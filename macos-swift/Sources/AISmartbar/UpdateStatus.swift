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
    @Published private(set) var isChecking = false
    /// Set when the button could not even launch the updater. Shown NEXT to
    /// the button rather than instead of it: the offer is still valid and
    /// still worth retrying, so replacing it would take away the only
    /// control that could recover.
    @Published private(set) var launchError = ""
    /// What the last manual check found, shown for a moment then cleared. The
    /// TEXT comes from Python — see checkNow().
    @Published private(set) var checkResult = ""

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
        // What to offer, and the same no-op guard on both halves: a target
        // equal to what we already run means the updater hasn't re-checked
        // since the restart, and re-offering it would be an upgrade to here.
        //
        // Two halves because the channels target different things. On
        // release the target is a tag and `pendingVersion` carries it. On
        // main the target is a COMMIT: ui_state leaves pendingVersion empty
        // and puts the sha in `pendingRef`, so reading only the version field
        // showed "up to date" to a device whose own updater had already
        // decided to rebuild it — the button and the icon badge could never
        // appear on a main-channel device at all.
        //
        // The fallback is reached only when pendingVersion is ABSENT, never
        // when the guard above emptied it: on release, pendingRef holds the
        // tag, and falling through to it would re-offer the very no-op the
        // version guard just suppressed.
        let pending = raw["pendingVersion"] as? String ?? ""
        let next: String
        if !pending.isEmpty {
            next = (pending == AppVersion.current) ? "" : pending
        } else {
            // Compared against the sha this bundle was BUILT from, not the
            // checkout's HEAD: the checkout moves on a fetch, the bundle only
            // on a rebuild, and it is the rebuild being offered.
            let ref = raw["pendingRef"] as? String ?? ""
            next = (ref.isEmpty || ref == AppBuild.sha)
                ? "" : String(ref.prefix(AppBuild.abbrev))
        }
        if next != pendingVersion { pendingVersion = next }
        let blocked = (raw["action"] as? String) == "blocked"
            ? (raw["reason"] as? String ?? "") : ""
        if blocked != blockedReason { blockedReason = blocked }
    }

    /// Ask the remote NOW, instead of waiting out the agent's 6-hourly timer.
    ///
    /// The button below only ever appears once a check has already FOUND
    /// something, so without this a device could sit hours behind a release
    /// with no way to ask.
    ///
    /// Deliberately not decided here: `--check-update --json` returns the
    /// wording AND the rules behind it (chiefly that run_once exits 0 both when
    /// a device is current and when another run holds the lock, so "up to date"
    /// may not be inferred from an exit code). Re-implementing that in Swift is
    /// how this app already came to disagree with itself about the presence
    /// staleness window — so this side only displays what Python decided.
    func checkNow() {
        guard !isChecking, !isUpdating else { return }
        isChecking = true
        checkResult = ""
        launchError = ""      // a fresh interaction supersedes the last one
        let root = repoRoot
        Task.detached(priority: .userInitiated) {
            let answer = Self.runCheck(root)
            await MainActor.run { [weak self] in
                guard let self else { return }
                self.isChecking = false
                // Every real outcome's wording comes from Python. This string
                // is the one case Python cannot describe: the helper did not
                // answer at all (missing checkout, unreadable JSON), so it is
                // deliberately different from any label check_outcome emits.
                self.checkResult = answer["label"] as? String ?? "✕ Could not check"
                self.reload()          // the run rewrote the state file
                if let title = answer["title"] as? String,
                   let body = answer["body"] as? String {
                    UsageStore.notify(title: title, body: body)
                }
                // Clear after a moment so the footer goes back to the version.
                Task { @MainActor [weak self] in
                    try? await Task.sleep(nanoseconds: 20_000_000_000)
                    if self?.isChecking == false { self?.checkResult = "" }
                }
            }
        }
    }

    /// Runs the check and returns its JSON, or [:] if anything went wrong.
    nonisolated private static func runCheck(_ root: String) -> [String: Any] {
        let launcher = (root.isEmpty ? nil : root).map { $0 + "/bin/ai-smartbar" }
        guard let launcher, FileManager.default.isExecutableFile(atPath: launcher)
        else { return [:] }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = ["--check-update", "--json"]
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        environment["PATH"] = [home + "/.local/bin", "/opt/homebrew/bin",
                               "/usr/local/bin", "/usr/bin", "/bin"]
            .joined(separator: ":")
        process.environment = environment
        let out = Pipe()
        process.standardOutput = out
        process.standardError = FileHandle.nullDevice
        // Read before waiting: a full pipe buffer would deadlock the child.
        guard (try? process.run()) != nil else { return [:] }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            ?? [:]
    }

    func installUpdate() {
        guard !isUpdating else { return }
        isUpdating = true
        triggeredAt = Date()
        launchError = ""
        checkResult = ""
        let uid = getuid()
        let root = repoRoot
        Task.detached(priority: .userInitiated) {
            if Self.startUpdater(uid: uid, root: root) { return }
            await MainActor.run { [weak self] in
                guard let self else { return }
                // Nothing is running, so the spinner must come down NOW
                // rather than at the 10-minute grace: a click that silently
                // starts nothing and then shows "Updating…" for ten minutes
                // is worse than one that plainly failed.
                self.isUpdating = false
                self.triggeredAt = nil
                // Same class as "✕ Could not check" below — the updater was
                // never reached, so Python has no wording for this either.
                self.launchError = "Could not start the updater"
            }
        }
    }

    /// Launches the updater out-of-process; true when something really started.
    ///
    /// Both arms are unwaitable by design (the callee restarts this app), so
    /// "did it work" can only mean "did the process spawn" — but that much
    /// has to be reported, because the no-agent-and-no-repoRoot case used to
    /// fall through both branches and launch nothing at all.
    nonisolated private static func startUpdater(uid: uid_t,
                                                 root: String) -> Bool {
        if agentInstalled() {
            // launchd owns the job, so it survives this app being restarted
            // by the update it is performing.
            return spawn("/bin/launchctl",
                         ["kickstart", "-k", "gui/\(uid)/\(label)"])
        }
        guard !root.isEmpty else { return false }
        // Device opted out of the agent: run the updater detached.
        return spawn("/bin/sh",
                     ["-c", "nohup \"\(root)/bin/ai-smartbar\" --update "
                          + ">/dev/null 2>&1 &"])
    }

    nonisolated private static func agentInstalled() -> Bool {
        let path = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(label).plist").path
        return FileManager.default.fileExists(atPath: path)
    }

    /// Fire and forget — never waited on: the callee restarts this process.
    /// Returns whether it was launched, which is all we can honestly observe.
    nonisolated private static func spawn(_ executable: String,
                                          _ arguments: [String]) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        do { try process.run() } catch { return false }
        return true
    }
}
