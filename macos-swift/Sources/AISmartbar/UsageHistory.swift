// Rolling per-metric usage history behind the hover-reveal trend sparkline.
// Semantics ported 1:1 from smartbar/core/usage_history.py (unit-tested in
// Python; the constants + the gap-break rule are pinned by
// tests/test_usage_history_parity.py). Unlike the System tab, whose history
// core samples directly, the macOS app polls cswap/Codex itself — so
// UsageStore and OpenAIStatus record each poll's per-metric % here, and this
// store owns only the SHAPE: how long a sample is kept (spanMinutes) and when
// a hole in sampling breaks the drawn line rather than connecting across dead
// time (gapMinutes).
import Foundation

@MainActor
final class UsageHistory {
    static let shared = UsageHistory()

    // Mirror of usage_history.SPAN_MINUTES / GAP_MINUTES. Keep in lockstep
    // with the Python module — the parity test asserts these very numbers.
    static let spanMinutes = 7 * 24 * 60   // 10080 — a full 7-day window
    static let gapMinutes = 15             // a wider hole breaks the line

    // key ("provider|email|metricKey") -> ordered [(minute, pct)] ring.
    private var rings: [String: [[Int]]] = [:]
    private var lastSave = Date.distantPast
    private static let saveInterval: TimeInterval = 20  // debounce disk writes

    private init() { load() }

    private static func key(provider: String, email: String, metric: String) -> String {
        "\(provider)|\(email)|\(metric)"
    }

    private static func nowMinute(_ date: Date) -> Int {
        Int(date.timeIntervalSince1970 / 60)
    }

    /// Record one poll's reading for a metric. Mirror of usage_history.record:
    /// same-minute replaces, older-than-span drops, pct clamped 0...100.
    func record(provider: String, email: String, metric: String,
                pct: Double, at date: Date = Date()) {
        let minute = Self.nowMinute(date)
        let value = max(0, min(100, Int(pct.rounded())))
        let k = Self.key(provider: provider, email: email, metric: metric)
        var ring = (rings[k] ?? []).filter { $0[0] > minute - Self.spanMinutes }
        if let last = ring.last, last[0] == minute {
            ring[ring.count - 1] = [minute, value]
        } else {
            ring.append([minute, value])
        }
        rings[k] = ring
        maybeSave()
    }

    /// The metric's samples as a [Int?] for TrendChart, with a nil break
    /// wherever two consecutive samples are more than gapMinutes apart. Mirror
    /// of usage_history.series.
    func series(provider: String, email: String, metric: String) -> [Int?] {
        let ring = rings[Self.key(provider: provider, email: email, metric: metric)] ?? []
        var out: [Int?] = []
        var prev: Int? = nil
        for pair in ring {
            let minute = pair[0], pct = pair[1]
            if let prev, minute - prev > Self.gapMinutes { out.append(nil) }
            out.append(pct)
            prev = minute
        }
        return out
    }

    /// Peak / latest / count over the retained ring — the hover caption.
    /// Mirror of usage_history.summary.
    func summary(provider: String, email: String, metric: String)
        -> (peak: Int, last: Int, points: Int) {
        let ring = rings[Self.key(provider: provider, email: email, metric: metric)] ?? []
        let pcts = ring.map { $0[1] }
        return (peak: pcts.max() ?? 0, last: pcts.last ?? 0, points: pcts.count)
    }

    // MARK: - Persistence (best-effort; losing history is cosmetic)

    nonisolated private static var fileURL: URL {
        let env = ProcessInfo.processInfo.environment
        let dir = env["SMARTBAR_CACHE_DIR"].flatMap { $0.isEmpty ? nil : $0 }
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".cache/ai-smartbar").path
        return URL(fileURLWithPath: dir).appendingPathComponent("usage-history.json")
    }

    private func load() {
        guard let data = try? Data(contentsOf: Self.fileURL),
              let raw = try? JSONSerialization.jsonObject(with: data)
                  as? [String: [[Int]]]
        else { return }
        // Trim on load too: a file left from days ago should not resurrect
        // samples past the retention window.
        let cutoff = Self.nowMinute(Date()) - Self.spanMinutes
        rings = raw.mapValues { ring in ring.filter { $0.count == 2 && $0[0] > cutoff } }
                   .filter { !$0.value.isEmpty }
    }

    private func maybeSave() {
        let now = Date()
        guard now.timeIntervalSince(lastSave) >= Self.saveInterval else { return }
        lastSave = now
        let snapshot = rings
        Task.detached(priority: .utility) {
            guard let data = try? JSONSerialization.data(withJSONObject: snapshot)
            else { return }
            let url = Self.fileURL
            try? FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true)
            try? data.write(to: url, options: .atomic)
        }
    }
}
