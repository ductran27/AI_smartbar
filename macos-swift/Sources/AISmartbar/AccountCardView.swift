// One card per Claude account: status dot, email, ACTIVE chip or switch
// button, and one horizontal filling bar per metric (5h / 7d / per-model).
// The active card wears a white outline (dark-only design). Accounts whose
// stored credential is dead say so and cannot be switched to.
import SwiftUI

struct AccountCardView: View {
    let account: Account
    @EnvironmentObject private var store: UsageStore
    @EnvironmentObject private var presence: PresenceStatus
    @EnvironmentObject private var planStatus: PlanStatus

    private var devices: Int { presence.counts[account.email] ?? 0 }

    /// "a@b.com · 20x (2)" — one string, three segments; the plan segment
    /// is dimmed. MUST stay in step with model.account_label (pinned by
    /// TestPlanParity in tests/test_plan.py).
    private var headerText: Text {
        var text = Text(account.email)
        let plan = planStatus.plans[account.email] ?? ""
        if !plan.isEmpty {
            text = text + Text(" \u{00B7} \(plan)").foregroundColor(.secondary)
        }
        if devices > 0 {
            text = text + Text(" (\(devices))")
        }
        return text
    }

    /// Says what the badge counts, and — the part worth being precise about
    /// — that it can only ever see devices running AI smartbar.
    private var devicesHelp: String {
        switch devices {
        case 0: return ""      // nobody on it, or the other devices are unseen
        case 1: return "Only this device has this account active"
        default:
            return "\(devices) devices running AI smartbar have this account "
                 + "active — they share its 5h and weekly limits"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 7) {
                statusDot
                // "a@b.com · 20x (2)" — the plan badge and how many devices
                // are on this account right now, so a quota burning twice as
                // fast has a visible cause. Middle truncation keeps the
                // badges even on a long address.
                headerText
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(devicesHelp)
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

    /// Solid = a real reading (purple when a limit is spent). Hollow gray =
    /// no measurement at all — no data yet, or a dead credential; the label
    /// below spells out which.
    @ViewBuilder
    private var statusDot: some View {
        if account.dotHollow {
            Circle()
                .strokeBorder(account.worstStatus.color, lineWidth: 1.5)
                .frame(width: 7, height: 7)
                .accessibilityLabel("no usage data")
        } else {
            Circle()
                .fill(account.worstStatus.color)
                .frame(width: 7, height: 7)
        }
    }
}
