// One metric row, two stacked lines: bold label + right-anchored "%" /
// countdown on the first, a full-width 6pt filling capsule on the second.
// The bar fills as tokens are spent — same direction as /usage.
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
        // 12pt label line + 2pt gap + 6pt bar = ROW_LABEL_H + ROW_LABEL_GAP
        // + BAR_H in the shared theme (20pt total row height).
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 0) {
                Text(metric.label)
                    .font(.system(size: 10.5, weight: .bold))
                    .foregroundStyle(palette.text)
                    .lineLimit(1)
                    .frame(width: 40, alignment: .leading)
                // BAR_GAP in the shared theme: a floor on the label/value
                // gap, not a fixed one — the value area right-anchors on
                // its own two trailing frames regardless of how much
                // space this Spacer ends up absorbing.
                Spacer(minLength: 9)
                // The countdown ticks live from the absolute reset time
                // while the popover is open instead of freezing at fetch
                // time.
                TimelineView(.everyMinute) { context in
                    valueText(now: context.date)
                }
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(palette.barTrack)
                    if fraction > 0 {
                        Capsule()
                            .fill(metric.status.color(in: colorScheme))
                            .frame(width: max(6, geo.size.width * fraction))
                    }
                    // The pace caret marks how far through the reset window
                    // "now" sits, independent of how much is spent (the
                    // fill) — see PACE_W's comment in popover_theme.py for
                    // why that has to be a second mark, never a second
                    // colour layered onto the fill, and why it is a NOTCH
                    // (the card's own ground cut through the bar) rather
                    // than a translucent hairline over it. nil (no stated
                    // window length, or an unparseable/expired resetsAt)
                    // means no caret at all rather than a guessed one.
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
            .frame(height: 6)
            .animation(.easeOut(duration: 0.6), value: metric.pct)
        }
    }

    private func valueText(now: Date) -> some View {
        let countdown = metric.liveCountdown(now: now)
        // A spent limit is a deliberate signal (purple bar), not a disabled
        // row — keep its number readable, just distinguishable. Both inks
        // come from the palette rather than the white-with-alpha literal
        // this used to be, because that literal is invisible on a light card.
        let color = metric.pct >= 100 ? palette.textSpent : palette.text
        // Percentage and countdown are two INDEPENDENTLY right-anchored
        // labels, each in its own fixed-width trailing frame, not one
        // concatenated Text right-anchored as a block — a single string
        // makes the percentage slide sideways every time the countdown's
        // length changes (e.g. "1h 0m" -> "59m"), a 19pt swing on the "·"
        // (the shared layout's FINDING 3, popover_layout._card_body).
        return HStack(spacing: 0) {
            Text("\(metric.usedPct)%")
                .fontWeight(.bold)
                // VALUE_PCT_W in the shared theme.
                .frame(width: 28, alignment: .trailing)
            // The leading " · " is gone; a clock mark fills the space it
            // used to reserve instead of a redundant separator ("· 🕐 2h 5m"
            // would double up). An HStack gets "immediately left of the
            // countdown's own text" for free from real text metrics, where
            // the shared layout has to fake it with text_width() because
            // cairo has no font engine to ask ahead of time (see
            // popover_layout._card_body's own comment on it).
            HStack(spacing: 3) {
                if !countdown.isEmpty {
                    ProviderMark(kind: "clock")
                        .frame(width: 9, height: 9)
                }
                Text(countdown.isEmpty ? "" : " \(countdown)")
            }
            // VALUE_COUNTDOWN_W in the shared theme.
            .frame(width: 66, alignment: .trailing)
        }
        // Tabular figures via a font FEATURE (.monospacedDigit()) rather
        // than the monospaced DESIGN this row used to ask for: cairo has
        // no OpenType-feature API, so the painted front-ends have nothing
        // to request and keep stopping digit-jitter the way they always
        // have — fixed-width right anchoring alone.
        .font(.system(size: 10.5).monospacedDigit())
        .foregroundStyle(color)
        .lineLimit(1)
    }
}
