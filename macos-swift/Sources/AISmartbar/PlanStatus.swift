// Plan badges: email -> "20x" / "5x" / "Pro" / "Free", computed by Python
// (`ai-smartbar --plans --json`). ONE SHARED ANSWER, NOT A SWIFT PORT:
// Swift renders the labels verbatim and maps nothing — the tier strings
// and the kill switch live entirely in core/plan.py (a disabled helper
// prints {}, which blanks every badge here too).
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
        timer?.tolerance = Self.refreshInterval / 10   // lets macOS coalesce wakeups
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
        Launcher.json(["--plans", "--json"])?["plans"] as? [String: String]
    }
}
