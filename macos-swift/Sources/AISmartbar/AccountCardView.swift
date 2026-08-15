// One card per Claude account: status dot, email, ACTIVE chip or switch
// button, and one horizontal filling bar per metric (5h / 7d / per-model).
// Every card wears the same quiet hairline; the active one is told apart by
// a leading rail instead of a loud outline (dark-only design). Accounts
// whose stored credential is dead say so and cannot be switched to.
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

    private var accountPlan: String {
        account.provider == "openai"
            ? account.plan
            : (planStatus.plans[account.email] ?? "")
    }

    /// "20x (2)", "Pro", "(2)", or "" — the plan/device suffix alone, drawn
    /// as its own micro-chip (planBadge) rather than riding inside the
    /// header string. Composed the same way model.account_badge is, so
    /// Swift's chip and Python's chip can never disagree (pinned by
    /// tests/test_account_card_parity.py).
    private var accountBadge: String {
        let plan = accountPlan
        guard devices > 0 else { return plan }
        return plan.isEmpty ? "(\(devices))" : "\(plan) (\(devices))"
    }

    /// "a@b.com · 20x (2)" — model.account_label(account) as a plain
    /// String, for the one spot (the remove confirmation) that still wants
    /// the full identity as one un-chipped line. Composed from
    /// accountBadge, the same way model.account_label composes from
    /// account_address/account_badge, so the two can never drift apart.
    private var accountLabel: String {
        let badge = accountBadge
        guard !badge.isEmpty else { return account.email }
        let separator = badge.hasPrefix("(") ? " " : " \u{00B7} "
        return "\(account.email)\(separator)\(badge)"
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
                if account.switchBlocked {
                    // A dead credential is the one data-less state that
                    // needs a drawn mark rather than an SF Symbol — the
                    // whole point of ProviderMark is that Linux's cairo
                    // painter can reproduce the exact same shape (see its
                    // file header), which "person.crop.circle.badge.
                    // exclamationmark" cannot. Same icon/gap as the
                    // countdown's clock mark (COUNTDOWN_ICON/
                    // COUNTDOWN_ICON_GAP in the shared theme).
                    HStack(alignment: .top, spacing: 3) {
                        ProviderMark(kind: "warn")
                            .frame(width: 9, height: 9)
                        Text(account.stateText)
                            .lineLimit(2)
                    }
                    .font(.caption)
                    .foregroundStyle(.orange)
                } else {
                    Label(account.stateText, systemImage: "hourglass")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
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
            // Every card gets the same quiet hairline now — the active one
            // used to wear a 1.5pt pure-white outline instead, the loudest
            // mark on the panel for information the ACTIVE chip already
            // carries. CARD_BORDER in the shared theme.
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.white.opacity(0.06), lineWidth: 1)
        )
        .overlay(alignment: .leading) {
            if account.active {
                // The rail replaces the outline as the active mark: a
                // quiet ink line rather than a colour, because colour on
                // this panel is reserved for how much budget is left —
                // see RAIL's comment in the shared theme. RAIL_W/
                // RAIL_INSET/RAIL there.
                RoundedRectangle(cornerRadius: 1.25, style: .continuous)
                    .fill(Palette.chalk)
                    .frame(width: 2.5)
                    .padding(.vertical, 3)
            }
        }
        .onHover { hovering = $0 }
    }

    private var cardHeader: some View {
        HStack(spacing: 7) {
            statusDot
            // Just the address now — the plan/device badge moved into its
            // own micro-chip (planBadge, below) so this line never has to
            // make room for it.
            Text(account.email)
                .font(.callout.weight(.semibold))
                .lineLimit(1)
                .truncationMode(.middle)
                .help(headerHelp)
            Spacer(minLength: 8)
            // The remove ✕'s HIT TARGET and TAP only exist while the
            // pointer is on a non-active card (mirror of the shared
            // layout's `on_card` guard on its remove hit) — the live
            // login would just be re-registered, so it never offers
            // removal. Its 18pt WIDTH, though, is reserved unconditionally
            // whenever the card is removable, not only while hovering:
            // reserving it only on hover let the header expand into that
            // space when idle, then re-truncate the instant the pointer
            // arrived (the shared layout's FINDING 4, popover_layout._card).
            if !account.active {
                Button { confirmToken = removalToken } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.tertiary)
                        // REMOVE_HIT in the shared theme
                        .frame(width: 18, height: 18)
                        .opacity(hovering ? 1 : 0)
                }
                .buttonStyle(.plain)
                .disabled(!hovering)
                .help("Remove \(account.email) from AI smartbar")
                .accessibilityLabel("Remove \(account.email)")
            }
            if !accountBadge.isEmpty {
                planBadge
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

    /// Two rows, not one: the full identity ("a@b.com · 20x (2)") on its
    /// own line, wrapped rather than middle-truncated, above the
    /// Remove/Keep buttons — mirror of the shared layout's confirm state
    /// (popover_layout._card / confirm_header_height). The old one-line
    /// "same height always" design elided an ordinary address at exactly
    /// the moment the user had to be sure what they were deleting; this
    /// trades that for a one-time card-height growth on an explicit user
    /// action, same trade-off the shared layout made.
    private var confirmHeader: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Remove \(accountLabel)?")
                .font(.callout.weight(.semibold))
                .lineLimit(2)
            HStack(spacing: 7) {
                Spacer(minLength: 0)
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
    }

    private func performRemoval() {
        if account.provider == "openai" {
            openai.remove(account.email)
        } else {
            store.removeAccount(account.number)
        }
    }

    /// Sits immediately left of the ACTIVE chip / Make Active button — a
    /// fact about the account, not something to press, so it gets the same
    /// neutral fill as a disabled control (BUTTON_DISABLED/CHIP_H/
    /// SIZE_CHIP in the shared theme) rather than an accent.
    private var planBadge: some View {
        Text(accountBadge)
            .font(.system(size: 9))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(Color.white.opacity(0.05)))
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
