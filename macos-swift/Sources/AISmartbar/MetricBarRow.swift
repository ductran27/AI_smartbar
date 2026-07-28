// One metric row: bold label, 6pt filling capsule, mono "% used · countdown".
// The bar fills as tokens are spent — same direction as /usage.
import SwiftUI

struct MetricBarRow: View {
    let metric: Metric

    private var fraction: CGFloat {
        CGFloat(min(max(metric.pct, 0), 100)) / 100
    }

    var body: some View {
        HStack(spacing: 9) {
            Text(metric.label)
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(.primary.opacity(0.9))
                .lineLimit(1)
                .frame(width: 40, alignment: .leading)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.09))
                    if fraction > 0 {
                        Capsule()
                            .fill(metric.status.color)
                            .frame(width: max(6, geo.size.width * fraction))
                    }
                }
            }
            .frame(height: 6)
            .animation(.easeOut(duration: 0.6), value: metric.pct)
            // The countdown ticks live from the absolute reset time while
            // the popover is open instead of freezing at fetch time.
            TimelineView(.everyMinute) { context in
                valueText(now: context.date)
            }
            // VALUE_PCT_W + VALUE_COUNTDOWN_W in the shared theme.
            .frame(width: 94, alignment: .trailing)
        }
    }

    private func valueText(now: Date) -> some View {
        let countdown = metric.liveCountdown(now: now)
        let color = metric.pct >= 100 ? Color.white.opacity(0.68)
                                       : Color.white.opacity(0.8)
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
            Text(countdown.isEmpty ? "" : " · \(countdown)")
                // VALUE_COUNTDOWN_W in the shared theme.
                .frame(width: 66, alignment: .trailing)
        }
        .font(.system(size: 10.5, design: .monospaced))
        // A spent limit is a deliberate signal (purple bar), not a
        // disabled row — keep its number readable, just distinguishable.
        .foregroundStyle(color)
        .lineLimit(1)
    }
}
