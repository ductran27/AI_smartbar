// One card per Claude account: status dot, email, ACTIVE chip or switch
// button, and a row of ring gauges (5h / 7d / per-model buckets).
import SwiftUI

struct AccountCardView: View {
    let account: Account
    @EnvironmentObject private var store: UsageStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Circle()
                    .fill((account.rows.first?.status ?? Status.gray).color)
                    .frame(width: 8, height: 8)
                Text(account.email)
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 8)
                if account.active {
                    Text("ACTIVE")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(Capsule().fill(Color.accentColor))
                } else {
                    Button("Make Active") {
                        store.switchTo(account.number)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
            if account.metrics.isEmpty {
                Text("No usage data")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(account.metrics) { metric in
                        RingGauge(metric: metric)
                    }
                    Spacer(minLength: 0)
                }
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(.thinMaterial)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(account.active
                              ? Color.accentColor.opacity(0.35)
                              : Color.primary.opacity(0.06),
                              lineWidth: 1)
        )
    }
}
