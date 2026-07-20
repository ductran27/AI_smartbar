// One card per Claude account: status dot, email, ACTIVE chip or switch
// button, and one horizontal draining bar per metric (5h / 7d / per-model).
// The active card wears a white outline (dark-only design).
import SwiftUI

struct AccountCardView: View {
    let account: Account
    @EnvironmentObject private var store: UsageStore

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 7) {
                Circle()
                    .fill(account.worstStatus.color)
                    .frame(width: 7, height: 7)
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
                VStack(spacing: 7) {
                    ForEach(account.metrics) { metric in
                        MetricBarRow(metric: metric)
                    }
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
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(account.active
                              ? Color.white.opacity(0.92)
                              : Color.white.opacity(0.07),
                              lineWidth: account.active ? 1.5 : 1)
        )
    }
}
