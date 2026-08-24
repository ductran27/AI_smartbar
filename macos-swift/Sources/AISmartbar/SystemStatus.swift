// Machine vitals + leftover/busy processes for the System tab, from
// `ai-smartbar --sysmon`. ONE SHARED ANSWER, NOT A SWIFT PORT: the payload is
// final display data (every string, the kill token, the auto-kill and alert
// decisions all live in smartbar/core/sysmon.py + sysmon_runner.py); this
// object only decodes it, and — while the tab is open — reads a one-line-per-
// second live stream. Two cadences mirror the runner: a 60 s background poll
// (which also runs auto-kill and history in its subprocess) and the 1 s
// stream. Pinned by tests/test_sysmon_parity.py.
import Foundation

@MainActor
final class SystemStatus: ObservableObject {
    @Published private(set) var payload: SystemPayload?
    @Published var actionError: String?    // sticky until the next attempt

    /// The background poll cadence — matches sysmon_runner's own default and
    /// is what carries auto-kill + history + notifications. Pinned by tests.
    static let pollInterval: TimeInterval = 60
    /// The live-stream tick while the tab is visible. Pinned by tests.
    static let streamInterval: TimeInterval = 1

    private var timer: Timer?
    private var streamProcess: Process?
    private var generation = 0
    private var firedAlerts = Set<String>()   // one notification per burst

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: Self.pollInterval,
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
            let fetched = Launcher.decode(SystemPayload.self,
                                          ["--sysmon", "--json"])
            let raw = Launcher.json(["--sysmon", "--json"])   // for alerts[]
            await MainActor.run { [weak self] in
                guard let self, current == self.generation else { return }
                if let fetched, fetched != self.payload {
                    self.payload = fetched
                }
                self.postAlerts(from: raw)
            }
        }
    }

    private func postAlerts(from raw: [String: Any]?) {
        guard let alerts = raw?["alerts"] as? [[String: Any]] else { return }
        for alert in alerts {
            let title = alert["title"] as? String ?? ""
            guard !title.isEmpty, !firedAlerts.contains(title) else { continue }
            firedAlerts.insert(title)
            UsageStore.notify(title: title,
                              body: alert["body"] as? String ?? "")
        }
        // Re-arm once the burst clears, so the next round can notify again.
        if alerts.isEmpty { firedAlerts.removeAll() }
    }

    /// Start the 1 s live stream (the tab became visible). The child exits on
    /// its own when this process dies; stopping it here is the clean path.
    func startStream() {
        guard streamProcess == nil,
              let process = Launcher.process(["--sysmon", "--stream"])
        else { return }
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else { return }
            for line in chunk.split(separator: UInt8(ascii: "\n")) {
                guard let decoded = try? JSONDecoder().decode(
                    SystemPayload.self, from: Data(line)) else { continue }
                Task { @MainActor [weak self] in
                    if decoded != self?.payload { self?.payload = decoded }
                }
            }
        }
        do { try process.run() } catch { return }
        streamProcess = process
    }

    /// Stop the live stream (the tab was hidden or the popover closed).
    func stopStream() {
        guard let process = streamProcess else { return }
        streamProcess = nil
        process.standardOutput.map {
            ($0 as? Pipe)?.fileHandleForReading.readabilityHandler = nil
        }
        process.terminate()
    }

    /// Kill the process a row names, through the one guarded runner.
    /// Optimistic: drop the row now, let the next poll be the truth.
    func kill(_ token: String) {
        actionError = nil
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
                }
                self?.refresh()
            }
        }
    }
}
