// Observable state: adaptive refresh timer, alert fire-once/re-arm,
// account switch, credential re-capture, and the published menu-bar icon.
// Semantics ported from smartbar/core (unit-tested in Python).
import AppKit
import Combine
import Foundation

@MainActor
final class UsageStore: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var lastError: String?
    @Published var consecutiveFailures = 0
    @Published var isRefreshing = false
    @Published var icon: NSImage = MenuBarIcon.image(for: [])
    @Published var switchError: String?  // sticky until the next switch attempt
    @Published var removeError: String?  // sticky until the next remove attempt

    /// Per-account device counts. Owned here rather than beside the store
    /// because it needs every snapshot, including the ones taken while the
    /// popover is closed and no view exists to observe them.
    let presence = PresenceStatus()

    /// When cswap was last polled. Deliberately NOT @Published: it changes
    /// on every poll and would re-render the UI even when the data didn't
    /// move; the tooltip that shows it reads the current value on hover.
    private(set) var lastRefresh: Date?

    private var timer: Timer?
    private var timerInterval: TimeInterval = 0
    private var fired: [String: String] = [:]  // "acct-metricKey" -> resetsAt at fire time
    private var fetchGeneration = 0  // stamps fetches so superseded results are dropped
    private var lastAttempt: Date?   // throttles stacked refresh triggers
    // Menu-bar apps get App-Napped, which stretches the refresh timer far
    // past its interval; this activity keeps the cadence honest without
    // preventing system sleep.
    private let napActivity = ProcessInfo.processInfo.beginActivity(
        options: .userInitiatedAllowingIdleSystemSleep,
        reason: "AI smartbar periodic usage refresh")

    // `cswap add` pacing (mirror of smartbar/core/recapture.py): register
    // an unregistered /login, heal a dead active backup, and periodically
    // re-capture the live login so Claude Code's token rotations never
    // orphan cswap's stored credential.
    private var lastRegister: Date?
    private var lastHeal: Date?
    private var lastRecapture: Date?
    private static let registerCooldown: TimeInterval = 600
    private static let healCooldown: TimeInterval = 120
    private static let recaptureInterval: TimeInterval = 900

    private var wakeObserver: NSObjectProtocol?
    private var activationObserver: NSObjectProtocol?

    /// Base cadence: 60s harvests cswap's poll plans the moment they come
    /// due (incl. the 60s urgent cadence near the limit); the store still
    /// paces the real network, so faster polling adds no API traffic.
    private let baseInterval: TimeInterval = {
        let raw = ProcessInfo.processInfo.environment["SMARTBAR_INTERVAL"] ?? ""
        // Floor at 15 s: Timer substitutes 0.1 ms for a non-positive
        // interval, so "0" pegged the main run loop with ~10k fires/s.
        guard let value = Double(raw), value.isFinite else { return 60 }
        return max(15, value)
    }()

    /// Relaxed cadence while nothing is near a limit and the last fetch
    /// succeeded — the display can lag a little when nothing is at stake,
    /// and every skipped poll is a Python process that never spawns.
    private var idleInterval: TimeInterval {
        let raw = ProcessInfo.processInfo.environment["SMARTBAR_INTERVAL_IDLE"] ?? ""
        return max(baseInterval, Double(raw) ?? 180)
    }

    var isStale: Bool { consecutiveFailures > 0 && snapshot != nil }

    /// Measurement time for the header: when cswap read the usage API, not
    /// when we last ran cswap (its store may have served older data).
    // No lastRefresh fallback: with no measurement at all (every slot
    // relogin_required) the header printed the cswap POLL time as if it
    // were the API measurement time; the painted hosts print nothing.
    var dataUpdated: Date? { snapshot?.dataDate }

    var accessibilitySummary: String {
        if consecutiveFailures >= 3 { return "AI smartbar: no data" }
        guard let account = snapshot?.activeAccount else { return "AI smartbar: loading" }
        return "AI smartbar: \(account.summary)"
    }

    init() {
        rescheduleTimer(interval: baseInterval)
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

    private func rescheduleTimer(interval: TimeInterval) {
        guard interval != timerInterval else { return }
        timer?.invalidate()
        timerInterval = interval
        let timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        timer.tolerance = min(20, interval / 10)
        self.timer = timer
    }

    /// Fast cadence whenever staleness would hurt: no data yet, the last
    /// fetch failed, or the active account is close to a limit (cswap runs
    /// urgent 60s poll plans there and we want to harvest them on time).
    private func desiredInterval() -> TimeInterval {
        guard consecutiveFailures == 0, let snap = snapshot else { return baseInterval }
        guard let active = snap.activeAccount, active.ok else { return baseInterval }
        return active.worstPct >= 80 ? baseInterval : idleInterval
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
        // Belt for the disabled button: never restore a dead credential.
        if let target = snapshot?.accounts.first(where: { $0.number == number }),
           target.switchBlocked {
            switchError = "Cannot switch: \(target.stateText)"
            return
        }
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

    /// Remove a non-active Claude slot (confirmed in the card). Optimistic:
    /// the card disappears now; the forced refresh afterwards is the truth
    /// and resurrects it if the removal failed. The guard mirrors core's —
    /// the active slot is the live login, auto-registration would just
    /// re-add it.
    func removeAccount(_ number: Int) {
        guard let target = snapshot?.accounts.first(where: { $0.number == number }),
              !target.active else { return }
        removeError = nil
        snapshot?.accounts.removeAll { $0.number == number }
        fetchGeneration += 1  // any in-flight pre-removal fetch is now stale
        isRefreshing = false
        Task.detached(priority: .userInitiated) {
            let failure = AccountRemoval.remove(provider: "claude",
                                                identifier: String(number))
            await MainActor.run { [weak self] in
                if let failure { self?.removeError = "Remove failed: \(failure)" }
                self?.refresh(force: true)
            }
        }
    }

    private func apply(_ result: Result<Snapshot, Error>, generation: Int) {
        guard generation == fetchGeneration else { return }  // superseded
        isRefreshing = false
        switch result {
        case .success(let snap):
            lastRefresh = Date()
            // @Published fires on every assignment, equal or not — only
            // write when the value actually moves.
            if consecutiveFailures != 0 { consecutiveFailures = 0 }
            if lastError != nil { lastError = nil }
            // Publishing an identical snapshot would rebuild the icon and
            // re-render every card for nothing — most 60s polls change
            // nothing. Alerts only need to run when the data moved.
            if snap != snapshot {
                snapshot = snap
                icon = MenuBarIcon.image(for: snap.activeAccount?.pillStates ?? [])
            }
            // Every successful apply, not only a changed one: after an
            // optimistic switch the confirming fetch usually EQUALS the
            // optimistic snapshot, so a newly active account already ≥90%
            // raised no notification. checkAlerts gates itself via `fired`.
            checkAlerts(snap)
            maybeRecapture(snap)  // paces itself; must run even when unchanged
            presence.update(from: snap)   // ditto: the beat paces itself
            recordHistory(snap)   // every success, so a flat window still logs
            rescheduleTimer(interval: desiredInterval())
        case .failure(let error):
            consecutiveFailures += 1
            lastError = String(describing: error)
            if consecutiveFailures >= 3 {
                icon = MenuBarIcon.image(for: [])
            }
            rescheduleTimer(interval: baseInterval)
        }
    }

    /// `cswap add` is the one lever that keeps stored credentials alive:
    /// it registers an unregistered /login (no slot active), re-captures a
    /// live login whose backup died (active slot relogin_required), and on
    /// a slow cadence refreshes the backup so Claude Code's token
    /// rotations never orphan it. Mirror of smartbar/core/recapture.py.
    private func maybeRecapture(_ snap: Snapshot) {
        let env = ProcessInfo.processInfo.environment
        guard env["SMARTBAR_AUTO_ADD"] != "off" else { return }
        let now = Date()
        let action: String?
        if snap.activeAccount == nil {
            if due(lastRegister, Self.registerCooldown, now) {
                lastRegister = now
                action = "register"
            } else { action = nil }
        } else if env["SMARTBAR_RECAPTURE"] == "off" {
            action = nil
        } else if snap.needsRecapture {
            if due(lastHeal, Self.healCooldown, now) {
                lastHeal = now
                lastRecapture = now  // a heal IS a re-capture
                action = "heal"
            } else { action = nil }
        } else if lastRecapture == nil {
            // First healthy snapshot only sets the baseline: the first
            // routine re-capture waits a full interval (and a fresh
            // registration is not chased by a pointless second add).
            lastRecapture = now
            action = nil
        } else if due(lastRecapture, Self.recaptureInterval, now) {
            lastRecapture = now
            action = "refresh"
        } else { action = nil }
        guard let action else { return }
        Task.detached(priority: .utility) {
            let succeeded = (try? CswapClient.add()) != nil
            await MainActor.run { [weak self] in
                // Registration/heal changes what we show; a routine
                // refresh doesn't warrant an extra fetch.
                if succeeded && action != "refresh" { self?.refresh(force: true) }
            }
        }
    }

    /// Feed each account's per-metric % into the rolling history behind the
    /// hover-reveal trend. Runs on every successful poll — including the ones
    /// whose snapshot equals the last — so a window sitting still still leaves
    /// a flat, honest line rather than a hole.
    private func recordHistory(_ snap: Snapshot) {
        let now = Date()
        for account in snap.accounts where !account.metrics.isEmpty {
            for metric in account.metrics {
                UsageHistory.shared.record(provider: account.provider,
                                           email: account.email,
                                           metric: metric.key,
                                           pct: metric.pct, at: now)
            }
        }
    }

    private func due(_ last: Date?, _ interval: TimeInterval, _ now: Date) -> Bool {
        guard let last else { return true }
        return now.timeIntervalSince(last) >= interval
    }

    /// Mirror of core/alerts.window_identity: the usage window a resetsAt
    /// names, as a 5-minute bucket of the parsed timestamp. The API
    /// re-stamps resetsAt with fresh sub-seconds on every real fetch, so
    /// keyed on the raw string a ≥90% account re-notified every 1-3 min.
    private static func windowIdentity(_ resetsAt: String) -> String {
        guard let date = TimeRemaining.parseISO(resetsAt) else { return resetsAt }
        return String(Int((date.timeIntervalSince1970 / 300).rounded()))
    }

    private func checkAlerts(_ snap: Snapshot) {
        guard let account = snap.activeAccount else { return }
        let threshold = Thresholds.red
        for metric in account.metrics {
            let key = "\(account.number)-\(metric.key)"
            if metric.pct >= threshold {
                let window = Self.windowIdentity(metric.resetsAt)
                if fired[key] == window { continue }  // held for this window
                fired[key] = window
                // Same body as core/alerts._build: the live countdown from
                // resetsAt (cswap's fetch-time string as the fallback), one
                // line per part.
                var lines: [String] = []
                let countdown = metric.liveCountdown()
                if !countdown.isEmpty { lines.append("Resets in \(countdown).") }
                var title = "Claude: \(metric.label) — \(metric.usedPct)% used"
                if let best = snap.bestSwitch {
                    lines.append("Best switch: #\(best.number) \(best.email) (\(best.worstUsedPct)% used)")
                } else {
                    title += " — no accounts left"
                    lines.append("No other account available — you're on your own until this resets.")
                }
                Self.notify(title: title, body: lines.joined(separator: "\n"))
            } else {
                fired.removeValue(forKey: key)  // re-arm after reset
            }
        }
    }

    /// The one notification choke point for the app (usage alerts here,
    /// update-ready from UpdateStatus). Delegates to Notifier, which shows the
    /// app's own icon via UNUserNotificationCenter on a Developer-ID-signed
    /// build and falls back to osascript's generic-icon banner otherwise — see
    /// Notifier.swift for why the sender identity is bought with a signature.
    nonisolated static func notify(title: String, body: String) {
        Notifier.shared.post(title: title, body: body)
    }
}
