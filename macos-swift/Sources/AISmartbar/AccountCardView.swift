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
    @EnvironmentObject private var openai: OpenAIStatus
    @State private var hovering = false
    // The card whose removal awaits confirmation, named in full — NOT a
    // Bool: card views are recycled by slot number, and OpenAI numbers are
    // re-enumerated positions, so a bare flag could survive a data refresh
    // and point the question at a different address. A token that no
    // longer matches simply dismisses itself (same rule as the shared
    // layout's `confirm` parameter).
    @State private var confirmToken: String?

    private var removalToken: String {
        "\(account.provider):\(account.number):\(account.email)"
    }

    /// Same address, different provider: duc@… can be a Claude AND a
    /// ChatGPT account, so an OpenAI card must not borrow the Claude
    /// device count (pinned by TestOpenAIParity).
    private var devices: Int {
        account.provider == "openai" ? 0
            : (presence.counts[account.email] ?? 0)
    }

    /// "a@b.com · 20x (2)" — one string, three segments; the plan segment
    /// is dimmed. MUST stay in step with model.account_label (pinned by
    /// TestPlanParity in tests/test_plan.py). An OpenAI card's badge comes
    /// with its account payload; the Claude badge from the plans helper.
    private var headerText: Text {
        var text = Text(account.email)
        let plan = account.provider == "openai"
            ? account.plan
            : (planStatus.plans[account.email] ?? "")
        if !plan.isEmpty {
            text = text + Text(" \u{00B7} \(plan)").foregroundColor(.secondary)
        }
        if devices > 0 {
            text = text + Text(" (\(devices))")
        }
        return text
    }

    /// OpenAI cards say when their numbers were measured (they move only
    /// while Codex is actually used); Claude cards keep the device story.
    private var headerHelp: String {
        if account.provider == "openai" {
            guard let measured = account.fetchedAt else { return "" }
            return "Usage measured "
                + measured.formatted(date: .abbreviated, time: .shortened)
        }
        return devicesHelp
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
            if confirmToken == removalToken && !account.active {
                confirmHeader
            } else {
                cardHeader
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
        .onHover { hovering = $0 }
    }

    private var cardHeader: some View {
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
                .help(headerHelp)
            Spacer(minLength: 8)
            // The remove ✕ exists only while the pointer is on a
            // non-active card (mirror of the shared layout's remove hit);
            // the live login would just be re-registered, so it never
            // offers removal.
            if hovering && !account.active {
                Button { confirmToken = removalToken } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.tertiary)
                        // REMOVE_HIT in the shared theme
                        .frame(width: 18, height: 18)
                }
                .buttonStyle(.plain)
                .help("Remove \(account.email) from AI smartbar")
                .accessibilityLabel("Remove \(account.email)")
            }
            if account.active {
                Text("ACTIVE")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Status.green.color))
            } else if account.provider == "openai" {
                // Read-only card: no switcher exists for ChatGPT logins.
                EmptyView()
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
    }

    /// Header-row-only confirm: same height, the bars stay put (mirror of
    /// the shared layout's `confirm` state in popover_layout._card).
    private var confirmHeader: some View {
        HStack(spacing: 7) {
            Text("Remove \(account.email)?")
                .font(.callout.weight(.semibold))
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer(minLength: 8)
            Button("Remove") {
                confirmToken = nil
                performRemoval()
            }
            .buttonStyle(.borderedProminent)
            // DANGER in the shared theme — the status ramp's critical red.
            .tint(Status.critical.color)
            .controlSize(.small)
            .help(account.provider == "openai"
                  ? "Forget this card (labels and last numbers). Signing in with Codex brings it back"
                  : "Deletes claude-swap's stored credential backup for slot \(account.number). Signing in as this account re-registers it")
            .accessibilityLabel("Confirm removing \(account.email)")
            Button("Keep") { confirmToken = nil }
                .buttonStyle(.bordered)
                .controlSize(.small)
        }
    }

    private func performRemoval() {
        if account.provider == "openai" {
            openai.remove(account.email)
        } else {
            store.removeAccount(account.number)
        }
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
