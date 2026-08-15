// Stage 06: the 30-day usage-history strip, a second card below the
// Overview tab's account rows. There is no shared layout function on this
// side of the app — AccountCardView/MetricBarRow/OverviewView already
// hand-mirror popover_layout's geometry rather than call into it — so this
// reads smartbar/core/usage_history.py's own JSON file directly, following
// UpdateStatus.swift's state-file idiom: resolve the path the same way
// (SMARTBAR_CACHE_DIR, else ~/.cache/ai-smartbar), and degrade quietly on
// anything missing or malformed rather than surfacing an error the popover
// has no good way to show.
import SwiftUI

/// One account/day's high-water marks, decoded straight from
/// usage-history.json's flat {date, provider, email, windows} records —
/// see smartbar/core/usage_history.py's module docstring for the shape and
/// the "highest %-used seen that day" reasoning behind it.
private struct HistoryRecord: Decodable {
    var date: String?
    var provider: String?
    var email: String?
    var windows: [String: Double]?
}

private struct HistoryStore: Decodable {
    var records: [HistoryRecord]?
}

enum UsageHistory {
    private static var fileURL: URL {
        let env = ProcessInfo.processInfo.environment
        let dir = env["SMARTBAR_CACHE_DIR"].flatMap { $0.isEmpty ? nil : $0 }
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".cache/ai-smartbar").path
        return URL(fileURLWithPath: dir)
            .appendingPathComponent("usage-history.json")
    }

    /// `days` floats-or-None ending TODAY, oldest first — the Swift twin of
    /// usage_history.series(). A missing/corrupt file, or an account with
    /// no history at all, both read as "no day has a value" rather than
    /// throwing: this backs a popover card that must never be able to take
    /// the panel down (mirror of UpdateStatus.reload's own contract).
    static func series(provider: String, email: String, key: String,
                       days: Int = 30) -> [Double?] {
        guard let data = try? Data(contentsOf: fileURL),
              let store = try? JSONDecoder().decode(HistoryStore.self, from: data)
        else { return Array(repeating: nil, count: days) }
        var byDate: [String: Double] = [:]
        for record in store.records ?? [] {
            guard record.provider == provider, record.email == email,
                  let date = record.date, let value = record.windows?[key]
            else { continue }
            byDate[date] = value
        }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.timeZone = .current
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: Date())
        return (0..<days).map { index in
            let offset = days - 1 - index
            guard let day = calendar.date(byAdding: .day, value: -offset, to: today)
            else { return nil }
            return byDate[formatter.string(from: day)]
        }
    }
}

/// The Overview tab's second card: one bar per day of the active account's
/// 7-day window, over the last 30 days (mirror of
/// popover_layout._strip_card / STRIP_H / STRIP_BAR_W / STRIP_GAP). Omitted
/// entirely when there is no history yet (a fresh install) rather than
/// drawing thirty empty stubs — see `history` being all-nil below.
struct SparklineCard: View {
    let history: [Double?]

    /// True once at least one day has a real reading — the one case this
    /// card draws nothing at all for, same rule popover_layout._history_
    /// present pins on the Python side.
    static func hasData(_ history: [Double?]) -> Bool {
        history.contains { $0 != nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text("Active account · 30 days")
                    .font(.callout.weight(.semibold))
                Spacer()
                Text("7-day window, % used")
                    .font(.caption2)
                    .foregroundStyle(Palette.dim)
            }
            HStack(alignment: .bottom, spacing: 2) {
                ForEach(Array(history.enumerated()), id: \.offset) { index, value in
                    bar(value: value, isToday: index == history.count - 1)
                }
            }
            .frame(height: 28)
        }
        .padding(.vertical, 9)
        .padding(.horizontal, 11)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(.thinMaterial)
        )
        .overlay(
            // Same quiet hairline every other card on this panel wears
            // (CARD_BORDER in the shared theme).
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.white.opacity(0.06), lineWidth: 1)
        )
    }

    /// A day with no recorded value draws a 1pt stub rather than a bar of
    /// height 0 — "0% used" and "never measured" are different facts, and
    /// only the stub says the second one honestly. TODAY draws in chalk
    /// rather than the status ramp: it is still moving, so coloring it as
    /// though the day were over would claim a verdict on a reading that
    /// hasn't finished happening yet.
    @ViewBuilder
    private func bar(value: Double?, isToday: Bool) -> some View {
        if let value {
            let fraction = min(max(value, 0), 100) / 100
            let height = max(1, 28 * fraction)
            RoundedRectangle(cornerRadius: 3.75, style: .continuous)
                .fill(isToday ? Palette.chalk
                              : Thresholds.status(forUsedPct: value).color)
                .frame(width: 7.5, height: height)
        } else {
            RoundedRectangle(cornerRadius: 3.75, style: .continuous)
                .fill(Color.white.opacity(0.09))
                .frame(width: 7.5, height: 1)
        }
    }
}
