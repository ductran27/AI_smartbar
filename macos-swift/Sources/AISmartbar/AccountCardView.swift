// One card per Claude account: status dot, email, ACTIVE chip or switch
// button, and one horizontal filling bar per metric (5h / 7d / per-model).
// The active card wears a white outline (dark-only design). Accounts whose
// stored credential is dead say so and cannot be switched to.
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
                        .background(Capsule().fill(Status.green.color))
                } else {
                    Button("Make Active") {
                        store.switchTo(account.number)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(account.switchBlocked)
                    .help(account.switchBlocked
                          ? "Stored credential is dead — switching would log Claude Code out. \(account.stateText)."
                          : "Switch Claude Code to \(account.email)")
                    .accessibilityLabel("Make \(account.email) active")
                }
            }
            if account.metrics.isEmpty {
                Label(account.stateText,
                      systemImage: account.switchBlocked
                          ? "person.crop.circle.badge.exclamationmark" : "hourglass")
                    .font(.caption)
                    .foregroundStyle(account.switchBlocked ? .orange : .secondary)
                    .lineLimit(2)
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
