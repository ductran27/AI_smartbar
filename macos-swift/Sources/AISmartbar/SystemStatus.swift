// Machine vitals + leftover/busy processes for the System tab, from
// `ai-smartbar --sysmon`. ONE SHARED ANSWER, NOT A SWIFT PORT: the payload is
// final display data (every string, the kill token, the auto-kill and alert
// decisions all live in smartbar/core/sysmon.py + sysmon_runner.py); this
// object only decodes it, and — while the tab is open — reads a one-line-per-
// second live stream. Two cadences mirror the runner: a background poll
// (which also runs auto-kill and history in its subprocess; its period is
// carried IN the payload so the device's configured interval is honoured
// without this side reading any setting) and the 1 s stream. Pinned by
// tests/test_sysmon_parity.py.
import Foundation

@MainActor
final class SystemStatus: ObservableObject {
    @Published private(set) var payload: SystemPayload?
    @Published var actionError: String?    // sticky until the next attempt

    /// The background poll cadence — matches sysmon_runner's own default and
    /// is what carries auto-kill + history + notifications. Pinned by tests;
    /// the payload's `pollInterval` (the device's configured period, floored
    /// by core/sysmon.interval) reschedules it.
    static let pollInterval: TimeInterval = 60
    /// The live-stream tick while the tab is visible. Pinned by tests.
    static let streamInterval: TimeInterval = 1

    private var timer: Timer?
    private var timerInterval: TimeInterval = SystemStatus.pollInterval
    private var streamProcess: Process?
    private var streamWanted = false
    private var generation = 0
    private var firedAlerts = Set<String>()   // alert keys already notified
    private var pendingKills = Set<String>()  // rows dropped, awaiting truth

    init() {
        refresh()
        schedule(every: Self.pollInterval)
    }

    private func schedule(every interval: TimeInterval) {
        timer?.invalidate()
        timerInterval = interval
        timer = Timer.scheduledTimer(withTimeInterval: interval,
                                     repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    /// One background poll: decode the payload, and surface any leftover
    /// notifications the runner decided on. Kept even while the stream runs —
    /// the stream is display-only, the poll is what does the side effects.
    func refresh() {
        generation += 1
        let current = generation
        Task.detached(priority: .utility) {
            // ONE run. Decoding the payload and reading alerts[] from two
            // separate runs meant two side-effecting ticks per poll — and the
            // auto-kill notification emitted by the first was read from the
            // second, which no longer saw the process it had killed.
            let data = Launcher.run(["--sysmon", "--json"])
            let fetched = data.flatMap {
                try? JSONDecoder().decode(SystemPayload.self, from: $0)
            }
            let raw = data.flatMap {
                (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any]
            }
            await MainActor.run { [weak self] in
                guard let self, current == self.generation else { return }
                if let fetched {
                    // Clear a pending token only when THIS poll confirms the
                    // process is actually gone. Wiping every token on any
                    // successful poll dropped the optimistic filter while the
                    // runner's 3 s grace could still list the just-killed row,
                    // so it flashed back into the live stream. Keep tokens
                    // still present; drop the rest.
                    let stillListed = Set(fetched.leftovers.rows.map(\.token))
                        .union(fetched.busy.rows.map(\.token))
                    self.pendingKills.formIntersection(stillListed)
                    // While the stream runs it owns the display: the poll's
                    // ~1.5 s-old sample would blink LIVE off and step the
                    // numbers backwards once a minute.
                    if self.streamProcess == nil, fetched != self.payload {
                        self.payload = fetched
                    }
                    if let period = fetched.pollInterval,
                       TimeInterval(period) != self.timerInterval {
                        self.schedule(every: TimeInterval(period))
                    }
                } else if raw?["disabled"] as? Bool == true {
                    self.payload = nil     // the feature is off: no tab
                }
                self.postAlerts(from: raw)
            }
        }
    }

    private func postAlerts(from raw: [String: Any]?) {
        let alerts = raw?["alerts"] as? [[String: Any]] ?? []
        var current = Set<String>()
        for alert in alerts {
            let title = alert["title"] as? String ?? ""
            guard !title.isEmpty else { continue }
            // Dedupe on the runner's stable `key`: the title embeds a
            // sampled core count that flaps between ticks (5 → 6 → 5).
            let key = alert["key"] as? String ?? title
            current.insert(key)
            guard !firedAlerts.contains(key) else { continue }
            firedAlerts.insert(key)
            UsageStore.notify(title: title,
                              body: alert["body"] as? String ?? "")
        }
        firedAlerts.formIntersection(current)   // re-arm what cleared
    }

    /// Start the 1 s live stream (the tab became visible). The child exits on
    /// its own when this process dies; stopping it here is the clean path.
    func startStream() {
        streamWanted = true
        guard streamProcess == nil,
              let process = Launcher.process(["--sysmon", "--stream"])
        else { return }
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else {
                // EOF: the child ended. An installed handler on a closed
                // pipe fires continuously and spun a core until the tab
                // was left.
                handle.readabilityHandler = nil
                return
            }
            for line in chunk.split(separator: UInt8(ascii: "\n")) {
                guard var decoded = try? JSONDecoder().decode(
                    SystemPayload.self, from: Data(line)) else { continue }
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    if !self.pendingKills.isEmpty {
                        // A row killed a second ago is still listed by the
                        // stream during the runner's 3 s grace; keep the
                        // optimistic drop until the next poll is the truth.
                        decoded.leftovers.rows.removeAll {
                            self.pendingKills.contains($0.token)
                        }
                        decoded.busy.rows.removeAll {
                            self.pendingKills.contains($0.token)
                        }
                    }
                    if decoded != self.payload { self.payload = decoded }
                }
            }
        }
        process.terminationHandler = { [weak self] ended in
            Task { @MainActor [weak self] in
                guard let self, self.streamProcess === ended else { return }
                self.streamProcess = nil
                self.markNotLive()
                // The runner caps a stream at 30 min; while the tab is
                // still on screen, LIVE has to mean live.
                if self.streamWanted { self.startStream() }
            }
        }
        do { try process.run() } catch { return }
        streamProcess = process
    }

    /// Stop the live stream (the tab was hidden or the popover closed).
    func stopStream() {
        streamWanted = false
        guard let process = streamProcess else { return }
        streamProcess = nil
        process.terminationHandler = nil
        (process.standardOutput as? Pipe)?
            .fileHandleForReading.readabilityHandler = nil
        process.terminate()
        markNotLive()
    }

    private func markNotLive() {
        if var current = payload, current.live {
            current.live = false
            payload = current
        }
    }

    /// Kill the process a row names, through the one guarded runner.
    /// Optimistic: drop the row now, let the next poll be the truth.
    func kill(_ token: String) {
        actionError = nil
        pendingKills.insert(token)
        if var current = payload {
            current.leftovers.rows.removeAll { $0.token == token }
            current.busy.rows.removeAll { $0.token == token }
            payload = current
        }
        Task.detached(priority: .userInitiated) {
            let raw = Launcher.json(["--kill", token])
            await MainActor.run { [weak self] in
                if (raw?["ok"] as? Bool) != true {
                    let detail = raw?["error"] as? String ?? "kill failed"
                    self?.actionError = "Kill failed: \(detail)"
                    // The kill was refused, so the process is still alive and
                    // still listed. Undo the optimistic drop now — otherwise
                    // the token stays pending (the poll keeps seeing the row)
                    // and the row stays hidden as if it had died.
                    self?.pendingKills.remove(token)
                }
                self?.refresh()
            }
        }
    }
}
