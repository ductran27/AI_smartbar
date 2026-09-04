// How many devices are on each account right now. Reads the JSON file that
// smartbar/presence_runner.py leaves behind, and spawns that runner on a
// timer — deliberately NOT a Swift port of it: presence is git plumbing
// (ls-remote, an atomic ref replace, clock-skew rules), and a second
// implementation of that in another language is a second thing to get
// wrong. Same arrangement as UpdateStatus + update_runner.py.
//
// The state file is keyed by plain address rather than the hash that goes
// on the wire, which is what keeps this side to a dictionary lookup.
import AppKit
import Foundation

@MainActor
final class PresenceStatus: ObservableObject {
    /// email -> devices currently on that account. Empty when the remote
    /// has never been read: an absent badge is honest, "(1)" would not be.
    @Published private(set) var counts: [String: Int] = [:]

    private var beatTimer: Timer?
    private var readTimer: Timer?
    private var activationObserver: NSObjectProtocol?
    private var terminateObserver: NSObjectProtocol?
    private var lastSnapshot: Snapshot?
    private var hasAnnounced = false

    // nonisolated: leave() runs off the main actor (it is called from the
    // terminate notification and must not hop actors on the way out), and
    // these only read the process environment.
    nonisolated private static let defaultInterval: TimeInterval = 300

    nonisolated private static var interval: TimeInterval {
        let raw = ProcessInfo.processInfo.environment["SMARTBAR_PRESENCE_INTERVAL"] ?? ""
        guard let value = Double(raw), value.isFinite else { return defaultInterval }
        return max(60, value)
    }

    /// Mirror of core/presence.ttl(): three missed beats by default, an
    /// explicit SMARTBAR_PRESENCE_TTL floored at two LOCAL intervals, and
    /// everything floored at three DEFAULT intervals — the window judges
    /// OTHER devices' cadence, which this device's config says nothing
    /// about (a 60s local interval must not declare every 300s device
    /// dead). tests/test_presence.py pins the two languages together.
    nonisolated private static var ttl: TimeInterval {
        let beat = interval
        let floorWindow = 3 * defaultInterval
        let raw = ProcessInfo.processInfo.environment["SMARTBAR_PRESENCE_TTL"] ?? ""
        guard let explicit = Double(raw), explicit.isFinite else {
            return max(3 * beat, floorWindow)
        }
        return max(2 * beat, max(floorWindow, explicit))
    }

    /// Mirror of core/presence.enabled() — SMARTBAR_PRESENCE=off is the
    /// kill switch for every remote write this app can make.
    nonisolated private static var isEnabled: Bool {
        let raw = ProcessInfo.processInfo.environment["SMARTBAR_PRESENCE"] ?? ""
        let value = raw.trimmingCharacters(in: .whitespaces).lowercased()
        // Same falsy spellings as core/presence.enabled().
        return !["off", "0", "false", "no"].contains(value)
    }

    init() {
        reload()
        guard Self.isEnabled else { return }
        // Reading is a small local file; beating talks to the network. They
        // run at different rates on purpose — the file is re-read often so
        // ANOTHER device joining shows up promptly, while this device
        // announces itself only as often as it needs to.
        let readTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) {
            [weak self] _ in
            Task { @MainActor in self?.reload() }
        }
        readTimer.tolerance = 10
        self.readTimer = readTimer
        let beatTimer = Timer.scheduledTimer(withTimeInterval: Self.interval,
                                             repeats: true) { [weak self] _ in
            Task { @MainActor in self?.beat() }
        }
        beatTimer.tolerance = 30
        self.beatTimer = beatTimer
        activationObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didBecomeActiveNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.reload() }
        }
        // Quitting withdraws this device, so the others stop counting it
        // straight away instead of waiting out the whole TTL.
        terminateObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            self?.leave()
        }
    }

    deinit {
        beatTimer?.invalidate()
        readTimer?.invalidate()
        for observer in [activationObserver, terminateObserver] {
            if let observer { NotificationCenter.default.removeObserver(observer) }
        }
    }

    private static var stateURL: URL {
        let env = ProcessInfo.processInfo.environment
        let dir = env["SMARTBAR_CACHE_DIR"].flatMap { $0.isEmpty ? nil : $0 }
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".cache/ai-smartbar").path
        return URL(fileURLWithPath: dir)
            .appendingPathComponent("presence-state.json")
    }

    func reload() {
        // Opting out hides the badges at once rather than leaving whatever
        // the last beat wrote on screen for ever.
        guard Self.isEnabled else {
            if !counts.isEmpty { counts = [:] }
            return
        }
        guard let data = try? Data(contentsOf: Self.stateURL),
              let raw = (try? JSONSerialization.jsonObject(with: data))
                  as? [String: Any]
        else { return }
        // Mirror of presence_client.counts(): the file may be left over
        // from a session that ended yesterday, and it keeps sitting there
        // if beats stop happening at all. Counts older than the window are
        // not an answer.
        let checked = raw["checkedAt"] as? Double ?? 0
        let window = Self.ttl
        guard checked > 0, Date().timeIntervalSince1970 - checked <= window else {
            if !counts.isEmpty { counts = [:] }
            return
        }
        var fresh: [String: Int] = [:]
        for (email, value) in raw["counts"] as? [String: Any] ?? [:] {
            if let number = value as? Int, number > 0 { fresh[email] = number }
        }
        // @Published fires on every assignment: only write when it moved,
        // or every card would re-render on a minute timer for nothing.
        if fresh != counts { counts = fresh }
    }

    /// Remember what the store last saw, so a beat can hand the runner the
    /// account list instead of making it run cswap again. The first real
    /// snapshot also triggers the first beat: a device that just started
    /// should appear to the others now, not in five minutes.
    func update(from snapshot: Snapshot?) {
        lastSnapshot = snapshot
        guard !hasAnnounced, snapshot != nil, Self.isEnabled else { return }
        hasAnnounced = true
        beat()
    }

    func beat() {
        guard Self.isEnabled else { return }
        let active = lastSnapshot?.activeAccount?.email ?? ""
        let emails = lastSnapshot?.accounts.map { $0.email } ?? []
        let payload: [String: Any] = ["active": active, "accounts": emails]
        guard let body = try? JSONSerialization.data(withJSONObject: payload)
        else { return }
        Task.detached(priority: .utility) {
            Self.spawn(["--presence-beat"], stdin: body)
            // The runner writes its state file when it finishes; give the
            // round trip a moment, then show what it found.
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            await MainActor.run { [weak self] in self?.reload() }
        }
    }

    /// Withdraw on quit so the other devices stop counting this one at once.
    nonisolated func leave() {
        guard Self.isEnabled else { return }
        Self.spawn(["--presence-leave"], stdin: Data())
    }

    /// The checkout this bundle was built from. The app is a COPY in
    /// ~/Applications and has no idea where the repo is, so: what the
    /// installer baked into the LaunchAgent, else what the updater
    /// recorded, else the conventional location.
    // Shared with PlanStatus, which spawns the same launcher.
    nonisolated static func repoRoot() -> String? {
        let env = ProcessInfo.processInfo.environment
        if let baked = env["SMARTBAR_REPO_ROOT"], !baked.isEmpty { return baked }
        let cache = env["SMARTBAR_CACHE_DIR"].flatMap { $0.isEmpty ? nil : $0 }
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".cache/ai-smartbar").path
        let updateState = URL(fileURLWithPath: cache)
            .appendingPathComponent("update-state.json")
        if let data = try? Data(contentsOf: updateState),
           let raw = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           let root = raw["repoRoot"] as? String, !root.isEmpty {
            return root
        }
        let fallback = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("AI_smartbar").path
        if FileManager.default.isExecutableFile(
            atPath: fallback + "/bin/ai-smartbar") {
            return fallback
        }
        // Last resort: a DMG copy has no checkout at all, so package-dmg.sh
        // ships one inside the app. Only a DMG build has this directory, so a
        // checkout copy never reaches here — the fallback is self-gating.
        return bundledBackendRoot()
    }

    /// The Python backend install/package-dmg.sh copies into the app bundle
    /// (Contents/Resources/backend), so a dragged-in DMG copy with no clone can
    /// still run the launcher-backed features (System tab, OpenAI card, account
    /// removal). nil for a checkout build, which ships no such directory.
    nonisolated static func bundledBackendRoot() -> String? {
        guard let resources = Bundle.main.resourceURL else { return nil }
        let root = resources.appendingPathComponent("backend").path
        return FileManager.default.isExecutableFile(
            atPath: root + "/bin/ai-smartbar") ? root : nil
    }

    /// Fire and forget. Presence must never be able to stall the UI, so
    /// nothing here is waited on and every failure is silent.
    nonisolated private static func spawn(_ arguments: [String], stdin: Data) {
        // Same launcher resolution as every other caller, including the
        // bundled-backend interpreter injection a DMG copy needs.
        guard let (executable, args) = Launcher.invocation(arguments) else { return }
        let process = Process()
        process.executableURL = executable
        process.arguments = args
        // One PATH fix for every helper — Launcher.environment().
        process.environment = Launcher.environment()
        let input = Pipe()
        process.standardInput = input
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        guard (try? process.run()) != nil else { return }
        try? input.fileHandleForWriting.write(contentsOf: stdin)
        try? input.fileHandleForWriting.close()
    }
}
