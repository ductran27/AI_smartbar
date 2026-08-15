// One metric row, three stacked lines: the metric's NAME, a full-width
// filling capsule under it, then the readout — what is spent on the left,
// when it comes back on the right. The bar fills as tokens are spent, the
// same direction as /usage.
//
// The name used to share the readout's line, which forced both into one
// optical size and gave the name a fixed column to truncate inside; see
// ROW_HEAD_H's comment in popover_theme.py for why they were separated and
// what that bought. Geometry is pinned by
// tests/test_metric_bar_row_parity.py.
import SwiftUI

struct MetricBarRow: View {
    let metric: Metric
    @Environment(\.colorScheme) private var colorScheme

    private var palette: Palette { Palette.of(colorScheme) }

    private var fraction: CGFloat {
        CGFloat(min(max(metric.pct, 0), 100)) / 100
    }

    private var spent: Bool { metric.pct >= 100 }

    var body: some View {
        // 13 + 4 + 8 + 5 + 12 = 42pt, matching ROW_HEAD_H + ROW_HEAD_GAP +
        // BAR_H + ROW_META_GAP + ROW_META_H in the shared theme. Spacing is
        // 0 and every gap is an explicit padding, so each number here is the
        // theme constant it mirrors rather than a sum a reader has to undo.
        VStack(alignment: .leading, spacing: 0) {
            // SIZE_ROW_HEAD / ROW_HEAD_H in the shared theme.
            Text(metric.title)
                .font(.system(size: 12.0, weight: .semibold))
                .foregroundStyle(palette.text)
                .lineLimit(1)
                .frame(height: 13.0, alignment: .leading)
            // ROW_HEAD_GAP / BAR_H in the shared theme.
            bar
                .padding(.top, 4.0)
                .frame(height: 8.0)
            // ROW_META_GAP / ROW_META_H in the shared theme. The countdown
            // ticks from the absolute reset time while the popover is open
            // instead of freezing at fetch time.
            TimelineView(.everyMinute) { context in
                readout(now: context.date)
            }
            .padding(.top, 5.0)
            .frame(height: 12.0)
        }
    }

    private var bar: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(palette.barTrack)
                if fraction > 0 {
                    Capsule()
                        .fill(metric.status.color(in: colorScheme))
                        // The floor is the bar's own height, so the smallest
                        // possible fill is a dot the track's width rather
                        // than a sliver clipped by its own corner radius.
                        .frame(width: max(8.0, geo.size.width * fraction))
                }
                // The pace caret marks how far through the reset window
                // "now" sits, independent of how much is spent (the fill).
                // It is a NOTCH — the card's own ground cut through the bar
                // — so it contrasts with whatever it interrupts; see
                // PACE_W's comment in popover_theme.py. nil (no stated
                // window length, or an unparseable/expired resetsAt) means
                // no caret at all rather than a guessed one.
                if let pace = metric.paceFraction() {
                    let half: CGFloat = 0.75   // PACE_W / 2
                    let center = min(max(geo.size.width * CGFloat(pace), half),
                                    geo.size.width - half)
                    Rectangle()
                        .fill(palette.pace)
                        .frame(width: 1.5)
                        .offset(x: center - half)
                }
            }
        }
        .animation(.easeOut(duration: 0.6), value: metric.pct)
    }

    /// What is spent, anchored left; when it comes back, anchored right.
    /// Opposite edges rather than two fixed sub-columns: a shared right
    /// anchor is what used to make the percentage slide sideways every time
    /// the countdown changed length ("1h 0m" -> "59m"), and separating the
    /// anchors removes the cause instead of reserving width around it.
    private func readout(now: Date) -> some View {
        let countdown = metric.liveCountdown(now: now)
        return HStack(spacing: 0) {
            // "45%" alone never said WHICH scale it was on — the row has
            // room for the word now, so it says so.
            Text("\(metric.usedPct)% used")
                .foregroundStyle(spent ? palette.textSpent : palette.text)
            Spacer(minLength: 8)
            if !countdown.isEmpty {
                // A drawn clock rather than an SF Symbol: the whole point of
                // ProviderMark is that cairo can reproduce the exact same
                // shape. COUNTDOWN_ICON/COUNTDOWN_ICON_GAP in the theme.
                HStack(spacing: 3) {
                    ProviderMark(kind: "clock")
                        .frame(width: 9, height: 9)
                    Text(countdown)
                }
                .foregroundStyle(spent ? palette.textSpent
                                       : palette.textSecondary)
            }
        }
        // Tabular figures via a font FEATURE (.monospacedDigit()) rather
        // than the monospaced DESIGN this row used to ask for: cairo has no
        // OpenType-feature API, so the painted front-ends have nothing to
        // request and stop digit-jitter the way they always have — fixed
        // anchoring alone. SIZE_ROW_META in the shared theme.
        .font(.system(size: 10.5).monospacedDigit())
        .lineLimit(1)
    }
}
