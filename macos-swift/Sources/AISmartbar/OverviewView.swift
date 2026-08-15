// Stage 05: the "where do I go next" tab, answering that without switching
// tabs. One card: a lead line naming the account with the most headroom
// (Snapshot.bestSwitch — Claude-only, since a switch can only ever target a
// Claude slot, same as model.best_switch), then one compact row per account,
// BOTH providers merged into a single list ranked by how much headroom each
// has left. Rows are read-only in this stage — no switch/remove action on
// them (mirror of popover_layout._overview_card; geometry constants live
// there as OVERVIEW_*, this file just has to read as the same panel).
import SwiftUI

struct OverviewView: View {
    @EnvironmentObject private var store: UsageStore
    @EnvironmentObject private var openai: OpenAIStatus

    /// Most headroom first; an account with no usable data sorts last.
    /// Array index, not Account.id, backs each row's identity below — a
    /// Claude slot and an OpenAI card can legitimately share the same
    /// `number` (cswap's slot numbers and OpenAIStatus's re-enumerated
    /// positions are two unrelated counters), so `id: Int` alone cannot
    /// tell every row in this MERGED list apart the way it can within
    /// either provider's own, separate ForEach.
    private var rows: [Account] {
        let all = (store.snapshot?.accounts ?? []) + openai.accounts
        return all.sorted { overviewKey($0) < overviewKey($1) }
    }

    private func overviewKey(_ account: Account) -> (Int, Double) {
        account.worstMetric.map { (0, $0.pct) } ?? (1, 0)
    }

    private var leadLine: String {
        guard let suggestion = store.snapshot?.bestSwitch,
              let worst = suggestion.worstMetric else {
            // True whether there are simply no other Claude accounts to
            // offer, or every one of them is blocked/data-less/active --
            // Snapshot.bestSwitch collapses those cases on purpose (mirror
            // of model.best_switch), and "no spare headroom" is honest
            // about all of them without claiming to know which.
            return "No account has spare headroom"
        }
        let pct = Int(max(0, worst.pct).rounded())
        return "Most headroom: \(suggestion.email) — \(pct)% used"
    }

    /// The strip only ever shows the ACTIVE Claude account's own 7-day
    /// history — a switch changes which account "active" names, and the
    /// strip follows it, the same as every other active-account fact on
    /// this tab. Without one there is nothing to key the read on, so the
    /// card is skipped the same way a fresh install with no records is.
    private var sparklineHistory: [Double?]? {
        guard let account = store.snapshot?.activeAccount else { return nil }
        return UsageHistory.series(provider: account.provider,
                                   email: account.email, key: "7d")
    }

    var body: some View {
        VStack(spacing: 7) {
            VStack(alignment: .leading, spacing: 7) {
                Text(leadLine)
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                VStack(spacing: 5) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, account in
                        OverviewRow(account: account)
                    }
                }
            }
            .padding(.vertical, 9)
            .padding(.horizontal, 11)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(.thinMaterial)
            )
            .overlay(
                // Same quiet hairline every account card wears (CARD_BORDER
                // in the shared theme) — this is one more card on the same
                // panel, not a different kind of surface.
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(Color.white.opacity(0.06), lineWidth: 1)
            )
            if let history = sparklineHistory, SparklineCard.hasData(history) {
                SparklineCard(history: history)
            }
        }
    }
}

/// One account, one line: provider mark, status dot, address, then either a
/// short bar+caret and its percentage, or — without data — the account's
/// state text and an em dash in the percentage's place.
private struct OverviewRow: View {
    let account: Account

    var body: some View {
        HStack(spacing: 6) {
            ProviderMark(kind: account.provider)
                .frame(width: 10, height: 10)
                .foregroundStyle(Palette.dim)
            statusDot
            Text(account.email)
                .font(.caption)
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let metric = account.worstMetric {
                bar(for: metric)
                    .frame(width: 56, height: 6)
                Text("\(metric.usedPct)%")
                    .font(.system(size: 10.5).monospacedDigit())
                    .foregroundStyle(metric.pct >= 100
                                     ? Palette.spent : Color.white.opacity(0.8))
                    .frame(width: 28, alignment: .trailing)
            } else {
                Text(account.stateText.isEmpty ? "No usage data" : account.stateText)
                    .font(.caption)
                    .foregroundStyle(account.switchBlocked ? .orange : .secondary)
                    .lineLimit(1)
                    .frame(width: 56, alignment: .leading)
                Text("—")
                    .font(.system(size: 10.5).monospacedDigit())
                    .foregroundStyle(.tertiary)
                    .frame(width: 28, alignment: .trailing)
            }
        }
        .frame(height: 16)
    }

    /// Solid = a real reading (purple when a limit is spent). Hollow gray =
    /// no measurement at all — same convention as AccountCardView's dot.
    @ViewBuilder
    private var statusDot: some View {
        if account.dotHollow {
            Circle()
                .strokeBorder(account.worstStatus.color, lineWidth: 1.5)
                .frame(width: 7, height: 7)
        } else {
            Circle()
                .fill(account.worstStatus.color)
                .frame(width: 7, height: 7)
        }
    }

    /// Track + proportional fill + pace caret, same maths as
    /// MetricBarRow's — just narrower, since a summary row's bar only has
    /// to say "roughly how full", not carry the full card's width.
    private func bar(for metric: Metric) -> some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.white.opacity(0.09))
                let fraction = CGFloat(min(max(metric.pct, 0), 100)) / 100
                if fraction > 0 {
                    Capsule()
                        .fill(metric.status.color)
                        .frame(width: max(6, geo.size.width * fraction))
                }
                if let pace = metric.paceFraction() {
                    let half: CGFloat = 0.75   // PACE_W / 2
                    let center = min(max(geo.size.width * CGFloat(pace), half),
                                    geo.size.width - half)
                    Rectangle()
                        .fill(Color.white.opacity(0.38))
                        .frame(width: 1.5)
                        .offset(x: center - half)
                }
            }
        }
    }
}
