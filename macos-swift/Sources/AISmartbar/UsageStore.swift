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

    var isStale: Bool { consecutiveFailures > 0 && snapshot != nil }

    var accessibilitySummary: String {
        if consecutiveFailures >= 3 { return "AI smartbar: no data" }
        guard let account = snapshot?.activeAccount else { return "AI smartbar: loading" }
        return "AI smartbar: \(account.summary)"
    }

    init() {
        let raw = ProcessInfo.processInfo.environment["SMARTBAR_INTERVAL"] ?? ""
        let interval = Double(raw) ?? 300
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        refresh()
    }

    func refresh() {
        guard !isRefreshing else { return }
        isRefreshing = true
        fetchGeneration += 1
        let generation = fetchGeneration
        Task.detached(priority: .utility) {
            let result: Result<Snapshot, Error>
            do {
                result = .success(try CswapClient.fetch())
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
            var failure: String?
            do {
                try CswapClient.switchTo(number)
            } catch {
                failure = String(describing: error)
            }
            await MainActor.run { [weak self] in
                if let failure {
                    self?.switchError = "Switch failed: \(failure)"
                }
                self?.isRefreshing = false
                self?.refresh()
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
        case .failure(let error):
            consecutiveFailures += 1
            lastError = String(describing: error)
            if consecutiveFailures >= 3 {
                icon = MenuBarIcon.image(for: [])
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
