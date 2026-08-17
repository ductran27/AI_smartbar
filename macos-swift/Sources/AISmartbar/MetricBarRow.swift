// One metric row, two stacked lines: the window name, its "resets in …"
// caption and a right-anchored "%" on the first; a full-width 6pt filling
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
    @Environment(\.colorScheme) private var colorScheme

    private var palette: Palette { Palette.of(colorScheme) }

    private var fraction: CGFloat {
        CGFloat(min(max(metric.pct, 0), 100)) / 100
    }

    var body: some View {
        // 14pt label line + 2.5pt gap + 7pt bar = ROW_LABEL_H + ROW_LABEL_GAP
        // + BAR_H in the shared theme (23.5pt total row height).
        VStack(alignment: .leading, spacing: 2.5) {
            HStack(spacing: 0) {
                Text(metric.label)
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(palette.text)
                    .lineLimit(1)
                    .frame(width: 46, alignment: .leading)
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
                .padding(.leading, 10.5)
                Spacer(minLength: 10.5)
                Text("\(metric.usedPct)%")
                    // Tabular figures via a font FEATURE
                    // (.monospacedDigit()) rather than the monospaced
                    // DESIGN this row used to ask for: cairo has no
                    // OpenType-feature API, so the painted front-ends have
                    // nothing to request and keep stopping digit-jitter the
                    // way they always have — fixed-width right anchoring
                    // alone. SIZE_ROW_VALUE in the shared theme.
                    .font(.system(size: 12, weight: .bold).monospacedDigit())
                    // A spent limit is a deliberate signal (purple bar), not
                    // a disabled row — keep its number readable, just
                    // distinguishable. Both inks come from the palette
                    // rather than the white-with-alpha literal this used to
                    // be, because that literal is invisible on a light card.
                    .foregroundStyle(metric.pct >= 100 ? palette.textSpent
                                                       : palette.text)
                    .lineLimit(1)
                    // VALUE_PCT_W in the shared theme.
                    .frame(width: 32, alignment: .trailing)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(palette.barTrack)
                    if fraction > 0 {
                        Capsule()
                            .fill(metric.status.color(in: colorScheme))
                            .frame(width: max(7, geo.size.width * fraction))
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
                        let half: CGFloat = 0.875   // PACE_W / 2
                        let center = min(max(geo.size.width * CGFloat(pace), half),
                                        geo.size.width - half)
                        Rectangle()
                            .fill(palette.pace)
                            // PACE_W x PACE_H in the shared theme.
                            .frame(width: 1.75, height: 3.5)
                            // BAR_H / 2 + PACE_H / 2: the ZStack centres its
                            // children on the bar, so this drops the tick to
                            // sit flush under it. It draws into the 8pt
                            // ROW_GAP below (AccountCardView's VStack
                            // spacing), which is why the row still measures
                            // ROW_H exactly.
                            .offset(x: center - half, y: 5.25)
                    }
                }
            }
            .frame(height: 7)
            .animation(.easeOut(duration: 0.6), value: metric.pct)
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
            .font(.system(size: 12))
            .foregroundStyle(palette.textSecondary)
            .lineLimit(1)
    }
}
