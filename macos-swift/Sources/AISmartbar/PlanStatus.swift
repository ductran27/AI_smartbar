// Plan badges: email -> "20x" / "5x" / "Pro" / "Free", computed by Python
// (`ai-smartbar --plans --json`). ONE SHARED ANSWER, NOT A SWIFT PORT:
// Swift renders the labels verbatim and maps nothing — the tier strings
// and the SMARTBAR_PLANS kill switch live entirely in core/plan.py (the
// helper prints {} when disabled, which blanks every badge here too).
import Foundation

@MainActor
final class PlanStatus: ObservableObject {
    @Published private(set) var plans: [String: String] = [:]

    /// Plans change ~never (a tier change requires a fresh login), so a
    /// slow cadence is deliberate. Pinned by tests/test_plan.py.
    static let refreshInterval: TimeInterval = 900

    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: Self.refreshInterval,
                                     repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        Task.detached(priority: .utility) {
            let fetched = Self.fetchPlans()
            await MainActor.run { [weak self] in
                guard let self, let fetched else { return }
                if fetched != self.plans { self.plans = fetched }
            }
        }
    }

    /// nil = helper unavailable (missing checkout, bad JSON); keep the
    /// last-good map rather than blanking every badge on a hiccup.
    nonisolated private static func fetchPlans() -> [String: String]? {
        guard let root = PresenceStatus.repoRoot() else { return nil }
        let launcher = root + "/bin/ai-smartbar"
        guard FileManager.default.isExecutableFile(atPath: launcher) else {
            return nil
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = ["--plans", "--json"]
        // launchd hands a GUI app a bare PATH, and the launcher's shebang
        // has to be able to find python3 — same treatment as presence.
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        environment["PATH"] = [home + "/.local/bin", "/opt/homebrew/bin",
                               "/usr/local/bin", "/usr/bin", "/bin"]
            .joined(separator: ":")
        process.environment = environment
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard
            let raw = try? JSONSerialization.jsonObject(with: data)
                as? [String: Any],
            let plans = raw["plans"] as? [String: String]
        else { return nil }
        return plans
    }
}
