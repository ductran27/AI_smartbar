// One metric row, two stacked lines: the window name, its "resets in …"
// caption and a right-anchored "%" on the first; a full-width 7.5pt filling
// capsule on the second. The bar fills as tokens are spent — same direction
// as /usage.
//
// Only the percentage sits over the bar, because only the percentage is
// about the bar. The countdown used to be anchored over the bar's right end
// too, which is what made people read the whole row as a clock; see
// LABEL_W's comment in popover_theme.py.
//
// A three-line variant (name / bar / "45% used" readout) was tried and
// reverted; see ROW_LABEL_H's comment in popover_theme.py for why density
// won. Geometry is pinned by tests/test_metric_bar_row_parity.py.
import SwiftUI

struct MetricBarRow: View {
    let metric: Metric
    // Identity for the hover-reveal history lookup (UsageHistory is keyed by
    // provider+email+metric). Defaulted so existing call sites and previews
    // that don't care about the trend keep compiling.
    var provider: String = "claude"
    var accountEmail: String = ""
    @Environment(\.colorScheme) private var colorScheme
    @State private var showTrend = false
    @State private var hoverTask: Task<Void, Never>?

    private var palette: Palette { Palette.of(colorScheme) }

    private var fraction: CGFloat {
        CGFloat(min(max(metric.pct, 0), 100)) / 100
    }

    var body: some View {
        // 15.5pt label line + 3pt gap + 7.5pt bar = ROW_LABEL_H +
        // ROW_LABEL_GAP + BAR_H in the shared theme (26pt total row height).
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 0) {
                Text(metric.label)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(palette.text)
                    .lineLimit(1)
                    .frame(width: 73, alignment: .leading)
                // The countdown ticks live from the absolute reset time
                // while the popover is open instead of freezing at fetch
                // time.
                TimelineView(.everyMinute) { context in
                    resetText(now: context.date)
                }
                // BAR_GAP in the shared theme, twice: a fixed gap after the
                // label column, then the same number as a FLOOR before the
                // percentage. The caption truncates into that floor rather
                // than pushing the percentage off its own column.
                .padding(.leading, 11.5)
                Spacer(minLength: 11.5)
                Text("\(metric.usedPct)%")
                    // Tabular figures via a font FEATURE
                    // (.monospacedDigit()) rather than the monospaced
                    // DESIGN this row used to ask for: cairo has no
                    // OpenType-feature API, so the painted front-ends have
                    // nothing to request and keep stopping digit-jitter the
                    // way they always have — fixed-width right anchoring
                    // alone. SIZE_ROW_VALUE in the shared theme.
                    .font(.system(size: 13, weight: .bold).monospacedDigit())
                    // A spent limit is a deliberate signal (purple bar), not
                    // a disabled row — keep its number readable, just
                    // distinguishable. Both inks come from the palette
                    // rather than the white-with-alpha literal this used to
                    // be, because that literal is invisible on a light card.
                    .foregroundStyle(metric.pct >= 100 ? palette.textSpent
                                                       : palette.text)
                    .lineLimit(1)
                    // VALUE_PCT_W in the shared theme.
                    .frame(width: 35, alignment: .trailing)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(palette.barTrack)
                    if fraction > 0 {
                        Capsule()
                            .fill(metric.status.color(in: colorScheme))
                            .frame(width: max(7.5, geo.size.width * fraction))
                    }
                    // The pace caret marks how far through the reset window
                    // "now" sits, independent of how much is spent (the
                    // fill) — see PACE_W's comment in popover_theme.py for
                    // why that has to be a second mark, never a second
                    // colour layered onto the fill, and why it hangs UNDER
                    // the bar instead of cutting a notch through it. nil
                    // (no stated window length, or an unparseable/expired
                    // resetsAt) means no caret at all rather than a guessed
                    // one.
                    if let pace = metric.paceFraction() {
                        let half: CGFloat = 1   // PACE_W / 2
                        let center = min(max(geo.size.width * CGFloat(pace), half),
                                        geo.size.width - half)
                        Rectangle()
                            .fill(palette.pace)
                            // PACE_W x PACE_H in the shared theme.
                            .frame(width: 2, height: 4)
                            // BAR_H / 2 + PACE_H / 2: the ZStack centres its
                            // children on the bar, so this drops the tick to
                            // sit flush under it. It draws into the 9pt
                            // ROW_GAP below (AccountCardView's VStack
                            // spacing), which is why the row still measures
                            // ROW_H exactly.
                            .offset(x: center - half, y: 5.75)
                    }
                }
            }
            .frame(height: 7.5)
            .animation(.easeOut(duration: 0.6), value: metric.pct)
        }
        // Hover-reveal trend: dwell ~350ms anywhere on the row, then a small
        // popover draws this metric's recent %-used history with the same
        // TrendChart the System tab uses. The card itself gains no height —
        // the whole point of choosing hover over an inline sparkline.
        .contentShape(Rectangle())
        .onHover { inside in
            hoverTask?.cancel()
            if inside {
                hoverTask = Task {
                    try? await Task.sleep(nanoseconds: 350_000_000)
                    if !Task.isCancelled { showTrend = true }
                }
            } else {
                showTrend = false
            }
        }
        .popover(isPresented: $showTrend, arrowEdge: .trailing) {
            UsageTrendPopover(metric: metric, provider: provider,
                              accountEmail: accountEmail)
        }
    }

    /// "resets in 1h 37m", beside the window name it belongs to.
    ///
    /// Worded rather than a bare duration next to a clock mark: "1d 12h"
    /// sitting beside "80%" is read by some people as budget left rather
    /// than time left, which is the most expensive misreading this panel
    /// can produce. Secondary ink and regular weight keep it below the two
    /// facts it sits between — it qualifies the window name, it is not a
    /// third number to compare. SIZE_ROW_VALUE in the shared theme.
    private func resetText(now: Date) -> some View {
        let countdown = metric.liveCountdown(now: now)
        return Text(countdown.isEmpty ? "" : "resets in \(countdown)")
            .font(.system(size: 13))
            .foregroundStyle(palette.textSecondary)
            .lineLimit(1)
    }
}

/// The hover popover: this metric's rolling %-used history as a TrendChart,
/// with a peak/now caption. Reads UsageHistory (recorded by UsageStore and
/// OpenAIStatus on every poll); the numbers and the retention/gap rules are
/// core's — this view only lays them out. Sparse data (a fresh install, or a
/// metric only just seen) says so rather than drawing a one-point line.
private struct UsageTrendPopover: View {
    let metric: Metric
    let provider: String
    let accountEmail: String
    @Environment(\.colorScheme) private var colorScheme

    private var palette: Palette { Palette.of(colorScheme) }

    var body: some View {
        let series = UsageHistory.shared.series(
            provider: provider, email: accountEmail, metric: metric.key)
        let sum = UsageHistory.shared.summary(
            provider: provider, email: accountEmail, metric: metric.key)
        return VStack(alignment: .leading, spacing: 6) {
            Text("\(metric.label) · usage trend")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(palette.text)
            if sum.points >= 2 {
                TrendChart(values: series)
                    .frame(width: 240)
                Text("peak \(sum.peak)% · now \(sum.last)%")
                    .font(.system(size: 11))
                    .foregroundStyle(palette.textSecondary)
            } else {
                Text("Collecting history — check back after a few polls")
                    .font(.system(size: 11))
                    .foregroundStyle(palette.textSecondary)
                    .frame(width: 240, alignment: .leading)
            }
        }
        .padding(12)
    }
}
