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
                // Record before the change gate: these numbers hold still
                // between Codex uses, and a flat stretch is still history the
                // hover-reveal trend should show (as a flat line, not a hole).
                let now = Date()
                for account in fetched where !account.metrics.isEmpty {
                    for metric in account.metrics {
                        UsageHistory.shared.record(provider: account.provider,
                                                   email: account.email,
                                                   metric: metric.key,
                                                   pct: metric.pct, at: now)
                    }
                }
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
        // One shared answer via the shared Launcher (was ~25 lines of
        // inlined Process setup — see Launcher.swift).
        guard let raw = Launcher.json(["--openai", "--json"]),
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
