// Animated ring gauge for one usage metric + the shared color palette
// (matches the Linux badge colors exactly).
import SwiftUI

extension Status {
    var color: Color {
        switch self {
        case .green: return Color(red: 0.18, green: 0.65, blue: 0.32)
        case .yellow: return Color(red: 0.85, green: 0.65, blue: 0.13)
        case .red: return Color(red: 0.80, green: 0.16, blue: 0.16)
        case .gray: return Color(red: 0.45, green: 0.45, blue: 0.45)
        }
    }
}

struct RingGauge: View {
    let metric: Metric

    private var fraction: CGFloat {
        CGFloat(min(max(metric.pct, 0), 100)) / 100
    }

    var body: some View {
        VStack(spacing: 5) {
            ZStack {
                Circle()
                    .stroke(Color.primary.opacity(0.08), lineWidth: 6)
                Circle()
                    .trim(from: 0, to: fraction)
                    .stroke(metric.status.color,
                            style: StrokeStyle(lineWidth: 6, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .animation(.easeOut(duration: 0.6), value: metric.pct)
                Text("\(metric.roundedPct)")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .monospacedDigit()
            }
            .frame(width: 48, height: 48)
            Text(metric.label)
                .font(.caption2.weight(.medium))
                .foregroundStyle(.secondary)
            Text(metric.countdown.isEmpty ? " " : metric.countdown)
                .font(.system(size: 9))
                .foregroundStyle(.tertiary)
        }
        .frame(minWidth: 60)
    }
}
