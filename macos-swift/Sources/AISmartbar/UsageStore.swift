// Observable state: refresh timer, alert fire-once/re-arm, account switch,
// and the published menu-bar pill icon.
// Semantics ported from smartbar/core (unit-tested in Python).
import AppKit
import Combine
import Foundation

@MainActor
final class UsageStore: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var lastError: String?
    @Published var consecutiveFailures = 0
    @Published var lastRefresh: Date?
    @Published var isRefreshing = false
    @Published var icon: NSImage = MenuBarIcon.image(for: [])
    @Published var switchError: String?  // sticky until the next switch attempt

    private var timer: Timer?
    private var fired: [String: String] = [:]  // "acct-metricKey" -> resetsAt at fire time
    private var fetchGeneration = 0  // stamps fetches so superseded results are dropped
    private var lastAttempt: Date?   // throttles stacked refresh triggers
    private var lastAutoAdd: Date?   // auto-registration cooldown
    private static let autoAddCooldown: TimeInterval = 600
    private var wakeObserver: NSObjectProtocol?
    private var activationObserver: NSObjectProtocol?
    // Menu-bar apps get App-Napped, which stretches the refresh timer far
    // past its interval; this activity keeps the cadence honest without
    // preventing system sleep.
    private let napActivity = ProcessInfo.processInfo.beginActivity(
        options: .userInitiatedAllowingIdleSystemSleep,
        reason: "AI smartbar periodic usage refresh")

    var isStale: Bool { consecutiveFailures > 0 && snapshot != nil }

    /// Measurement time for the header: when cswap read the usage API, not
    /// when we last ran cswap (its store may have served older data).
    var dataUpdated: Date? { snapshot?.dataDate ?? lastRefresh }

    var accessibilitySummary: String {
        if consecutiveFailures >= 3 { return "AI smartbar: no data" }
        guard let account = snapshot?.activeAccount else { return "AI smartbar: loading" }
        return "AI smartbar: \(account.summary)"
    }

    init() {
        let raw = ProcessInfo.processInfo.environment["SMARTBAR_INTERVAL"] ?? ""
        // 60s harvests cswap's poll plans the moment they come due (incl.
        // the 60s urgent cadence near the limit); the store still paces the
        // real network, so faster polling adds no API traffic.
        let interval = Double(raw) ?? 60
        let timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        timer.tolerance = min(20, interval / 10)
        self.timer = timer
        // The moments the display is most likely stale: the Mac just woke,
        // or the user just opened the popover (the app becomes active).
        wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        activationObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        refresh()
    }

    deinit {
        if let wakeObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(wakeObserver)
        }
        if let activationObserver {
            NotificationCenter.default.removeObserver(activationObserver)
        }
    }

    /// Triggers stack (timer, wake, activation, popover open): a short
    /// throttle dedupes them; `force` (manual button, post-switch) bypasses.
    func refresh(force: Bool = false) {
        guard !isRefreshing else { return }
        if !force, let last = lastAttempt, Date().timeIntervalSince(last) < 3 {
            return
        }
        lastAttempt = Date()
        isRefreshing = true
        fetchGeneration += 1
        let generation = fetchGeneration
        Task.detached(priority: .utility) {
            let result: Result<Snapshot, Error>
            do {
                result = .success(try CswapClient.fetch(fresh: true))
            } catch {
                result = .failure(error)
            }
            await MainActor.run { [weak self] in
                self?.apply(result, generation: generation)
            }
        }
    }

    func switchTo(_ number: Int) {
        // Optimistic flip: ACTIVE chip, outline and icon move immediately;
        // the follow-up fetch confirms (or reverts and surfaces the error).
        switchError = nil
        if var snap = snapshot {
            for index in snap.accounts.indices {
                snap.accounts[index].active = snap.accounts[index].number == number
            }
            snapshot = snap
            icon = MenuBarIcon.image(for: snap.activeAccount?.pillStates ?? [])
        }
        fetchGeneration += 1  // any in-flight pre-switch fetch is now stale
        isRefreshing = false
        Task.detached(priority: .userInitiated) {
            let failure: String?
            do {
                try CswapClient.switchTo(number)
                failure = nil
            } catch {
                failure = String(describing: error)
            }
            await MainActor.run { [weak self] in
                if let failure {
                    self?.switchError = "Switch failed: \(failure)"
                }
                self?.isRefreshing = false
                self?.refresh(force: true)
            }
        }
    }

    private func apply(_ result: Result<Snapshot, Error>, generation: Int) {
        guard generation == fetchGeneration else { return }  // superseded
        isRefreshing = false
        switch result {
        case .success(let snap):
            snapshot = snap
            lastError = nil
            consecutiveFailures = 0
            lastRefresh = Date()
            icon = MenuBarIcon.image(for: snap.activeAccount?.pillStates ?? [])
            checkAlerts(snap)
            maybeAutoRegister(snap)
        case .failure(let error):
            consecutiveFailures += 1
            lastError = String(describing: error)
            if consecutiveFailures >= 3 {
                icon = MenuBarIcon.image(for: [])
            }
        }
    }

    /// /login with an unregistered account leaves no slot active — register
    /// it through cswap's own non-interactive `add` so the bar picks it up
    /// with zero setup. The cooldown stops retry spam while add cannot
    /// succeed (logged out, locked keychain). SMARTBAR_AUTO_ADD=off disables.
    private func maybeAutoRegister(_ snap: Snapshot) {
        let env = ProcessInfo.processInfo.environment
        guard env["SMARTBAR_AUTO_ADD"] != "off" else { return }
        guard snap.activeAccount == nil else { return }
        if let last = lastAutoAdd,
           Date().timeIntervalSince(last) < Self.autoAddCooldown {
            return
        }
        lastAutoAdd = Date()
        Task.detached(priority: .utility) {
            let succeeded = (try? CswapClient.add()) != nil
            await MainActor.run { [weak self] in
                if succeeded { self?.refresh(force: true) }
            }
        }
    }

    private func checkAlerts(_ snap: Snapshot) {
        guard let account = snap.activeAccount else { return }
        let threshold = Thresholds.red
        for metric in account.metrics {
            let key = "\(account.number)-\(metric.key)"
            if metric.left <= threshold {
                if fired[key] == metric.resetsAt { continue }  // held for this window
                fired[key] = metric.resetsAt
                var body = metric.countdown.isEmpty ? "" : "Resets in \(metric.countdown). "
                if let best = snap.bestSwitch {
                    body += "Best switch: #\(best.number) \(best.email) (\(best.worstLeftPct)% left)"
                } else {
                    body += "No other account available."
                }
                Self.notify(title: "Claude: \(metric.label) — \(metric.leftPct)% left",
                            body: body)
            } else {
                fired.removeValue(forKey: key)  // re-arm after reset
            }
        }
    }

    /// osascript notification: works from a bare SPM binary, unlike
    /// UNUserNotificationCenter which needs a bundle identity + permissions.
    nonisolated static func notify(title: String, body: String) {
        let escapedTitle = title.replacingOccurrences(of: "\"", with: "\\\"")
        let escapedBody = body.replacingOccurrences(of: "\"", with: "\\\"")
        let script = "display notification \"\(escapedBody)\" with title \"\(escapedTitle)\""
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script]
        try? process.run()
    }
}
