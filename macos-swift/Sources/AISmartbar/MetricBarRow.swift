// One metric row: bold label, 6pt draining capsule, mono "% left · countdown".
import SwiftUI

struct MetricBarRow: View {
    let metric: Metric

    private var fraction: CGFloat {
        CGFloat(min(max(metric.left, 0), 100)) / 100
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
            valueText
                .frame(width: 104, alignment: .trailing)
        }
    }

    private var valueText: some View {
        (Text("\(metric.leftPct)%").fontWeight(.bold)
            + Text(metric.countdown.isEmpty ? "" : " · \(metric.countdown)"))
            .font(.system(size: 10.5, design: .monospaced))
            .foregroundStyle(metric.left <= 0 ? Color.white.opacity(0.45)
                                              : Color.white.opacity(0.8))
            .lineLimit(1)
    }
}
