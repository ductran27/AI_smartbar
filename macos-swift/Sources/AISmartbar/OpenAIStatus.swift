// OpenAI/ChatGPT accounts for the OpenAI tab, computed by Python
// (`ai-smartbar --openai --json`). ONE SHARED ANSWER, NOT A SWIFT PORT:
// the helper's JSON is final display data — plan badges, bar labels, state
// wording — and Swift only decodes it. Every mapping and every local data
// source lives in core/codex.py (a disabled helper prints an empty list,
// which hides the tab here too).
import Foundation

@MainActor
final class OpenAIStatus: ObservableObject {
    @Published private(set) var accounts: [Account] = []
    @Published var removeError: String?  // sticky until the next attempt

    /// These numbers move only while Codex is actually being used, so a
    /// couple of minutes of lag is invisible; the popover also refreshes
    /// on open. Pinned by tests/test_codex.py.
    static let refreshInterval: TimeInterval = 120

    private var timer: Timer?
    private var generation = 0  // stamps fetches; a removal supersedes them

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: Self.refreshInterval,
                                     repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        generation += 1
        let current = generation
        Task.detached(priority: .utility) {
            let fetched = Self.fetchAccounts()
            await MainActor.run { [weak self] in
                guard let self, let fetched,
                      current == self.generation else { return }
                if fetched != self.accounts { self.accounts = fetched }
            }
        }
    }

    /// Remove a remembered (signed-out) ChatGPT card. Optimistic: the card
    /// disappears now; the refresh afterwards is the truth. The guard
    /// mirrors core's — the live login would just be re-registered by the
    /// next sync, so it is never removable.
    func remove(_ email: String) {
        guard let target = accounts.first(where: { $0.email == email }),
              !target.active else { return }
        removeError = nil
        accounts.removeAll { $0.email == email }
        generation += 1  // any in-flight pre-removal list is now stale
        Task.detached(priority: .userInitiated) {
            let failure = AccountRemoval.remove(provider: "openai",
                                                identifier: email)
            await MainActor.run { [weak self] in
                if let failure { self?.removeError = "Remove failed: \(failure)" }
                self?.refresh()
            }
        }
    }

    /// nil = helper unavailable (missing checkout, bad JSON); keep the
    /// last-good list rather than blanking the tab on a hiccup.
    nonisolated private static func fetchAccounts() -> [Account]? {
        guard let root = PresenceStatus.repoRoot() else { return nil }
        let launcher = root + "/bin/ai-smartbar"
        guard FileManager.default.isExecutableFile(atPath: launcher) else {
            return nil
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = ["--openai", "--json"]
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
            let rows = raw["accounts"] as? [[String: Any]]
        else { return nil }
        return rows.enumerated().map { index, row in
            let metrics = (row["metrics"] as? [[String: Any]] ?? []).map {
                metric in
                Metric(key: metric["key"] as? String ?? "?",
                       label: metric["label"] as? String ?? "?",
                       short: metric["short"] as? String ?? "?",
                       pct: (metric["pct"] as? NSNumber)?.doubleValue ?? 0,
                       resetsAt: metric["resetsAt"] as? String ?? "",
                       countdown: "")
            }
            var account = Account(
                number: index + 1,
                email: row["email"] as? String ?? "?",
                org: "",
                active: row["active"] as? Bool ?? false,
                ok: (row["status"] as? String ?? "") == "ok",
                status: row["status"] as? String ?? "",
                metrics: metrics,
                fetchedAt: TimeRemaining.parseISO(
                    row["updatedAt"] as? String ?? ""))
            account.provider = "openai"
            account.plan = row["plan"] as? String ?? ""
            account.stateTextOverride = row["stateText"] as? String ?? ""
            return account
        }
    }
}
