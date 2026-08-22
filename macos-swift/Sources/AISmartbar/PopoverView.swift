// Content of the MenuBarExtra window: compact header (title, Updated
// stamp, stale marker, refresh and More menu), one card per account, and a
// transient updater footer that appears only while there is status to show.
// Follows the system appearance; every colour comes from Palette.
//
// Type is set with explicit `.system(size:)` values from the shared theme
// (SIZE_TITLE / SIZE_CAPTION / SIZE_EMAIL in popover_theme.py), NOT with
// the semantic styles (.headline / .callout / .caption / .caption2) this
// view used to ask for. Those styles resolve to macOS's own sizes, which
// meant the one platform whose frames the shared theme claims to mirror
// was the one platform ignoring its type scale: the theme's title size and
// the one macOS actually drew were points apart, and the gap widened every
// time the table was scaled while the semantic sizes stood still. Cards
// grew, the addresses inside them did not. Sizes come from the table now, so
// the panel is the same instrument on every platform in type as well as in
// geometry.
import SwiftUI

struct PopoverView: View {
    @EnvironmentObject private var store: UsageStore
    @EnvironmentObject private var updates: UpdateStatus
    @EnvironmentObject private var openai: OpenAIStatus
    @Environment(\.colorScheme) private var colorScheme
    @AppStorage("providerTab") private var providerTab = "claude"

    private var palette: Palette { Palette.of(colorScheme) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            if showsTabs {
                providerTabs
            }
            // One error line for every card action — switching or removing,
            // whichever failed most recently.
            if let actionError = store.switchError ?? store.removeError
                    ?? openai.removeError {
                Label(actionError, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12.5))
                    .foregroundStyle(palette.warning)
                    .lineLimit(2)
            }
            if selectedProvider == "openai" {
                openAIList
            } else if let snapshot = store.snapshot {
                if snapshot.activeAccount == nil {
                    Label(snapshot.accounts.isEmpty
                            ? "No accounts yet — sign in to Claude Code and it will be registered automatically"
                            : "Current login isn't registered — adding it automatically",
                          systemImage: "person.crop.circle.badge.plus")
                        .font(.system(size: 12.5))
                        .foregroundStyle(palette.textSecondary)
                        .lineLimit(2)
                }
                accountList(snapshot)
            } else {
                loadingOrError
            }
            footer
        }
        .padding(.horizontal, 14)
        .padding(.bottom, 14)
        .padding(.top, 6)
        .frame(width: 418)
        // WINDOW_BG in the shared theme. Painted explicitly rather than left
        // to the MenuBarExtra's own chrome so the Mac and the cairo painters
        // agree on the ground every other colour was tuned against — the
        // panel is meant to read as one instrument on every platform, not as
        // whatever each host's popover material happens to be.
        .background(palette.windowBG)
        // Opening the popover is the user looking: fetch now so the numbers
        // match /usage (cswap's store paces the real network traffic), and
        // re-read the updater's state file (a cheap local read).
        .onAppear {
            store.refresh()
            updates.reload()
            openai.refresh()
        }
    }

    private var hasClaudeAccounts: Bool {
        !(store.snapshot?.accounts.isEmpty ?? true)
    }

    /// The tab row exists to choose BETWEEN providers, so it appears only
    /// when both of them actually have accounts (mirror of
    /// popover_layout.build). One provider means one pill, which is a
    /// button that changes nothing.
    private var showsTabs: Bool {
        hasClaudeAccounts && !openai.accounts.isEmpty
    }

    private static let tabIDs = ["claude", "openai"]

    /// The provider a returning user last picked, but only honoured while
    /// the tab row is on screen — stale AppStorage from before this Mac
    /// had a second provider (or naming a tab that no longer exists) falls
    /// back to the plain auto-resolve instead.
    private var selectedProvider: String {
        guard showsTabs, Self.tabIDs.contains(providerTab) else {
            return autoSelectedProvider
        }
        return providerTab
    }

    private var autoSelectedProvider: String {
        (!openai.accounts.isEmpty && !hasClaudeAccounts) ? "openai" : "claude"
    }

    private var providerTabs: some View {
        HStack(spacing: 7.5) {
            ForEach(Self.tabIDs, id: \.self) { id in
                tabButton(tabTitle(for: id), id: id)
            }
            Spacer()
        }
    }

    private func tabTitle(for id: String) -> String {
        id == "openai" ? "OpenAI" : "Claude"
    }

    /// The mark sits BESIDE its label and the tabs read as faded /
    /// not-faded rather than coloured (mirror of popover_layout.build's tab
    /// row — TAB_H, TAB_MARK, TAB_MARK_GAP).
    ///
    /// A stacked mark over a filled accent pill was tried and reverted: it
    /// was the loudest thing on a panel whose only job is to colour-code how
    /// much budget is left, and it won that contest. The mark never REPLACES
    /// the label, so a tab stays readable to anyone who doesn't recognise the
    /// provider's mark on sight, and it takes the label's own colour so an
    /// unselected tab recedes mark-and-all rather than the mark competing
    /// with the fade as a second signal.
    private func tabButton(_ title: String, id: String) -> some View {
        let selected = selectedProvider == id
        let color = selected ? palette.text : palette.textTertiary
        return Button { providerTab = id } label: {
            HStack(spacing: 6.5) {
                ProviderMark(kind: id)
                    .frame(width: 14, height: 14)
                Text(title)
                    .font(.system(size: 12.5, weight: selected ? .semibold : .regular))
            }
            .foregroundStyle(color)
            .padding(.horizontal, 10.5)
            .padding(.vertical, 5)
            .background(Capsule()
                .fill(selected ? palette.tabBGSelected : palette.tabBG))
        }
        .buttonStyle(.plain)
    }

    /// How tall the card list may grow before it starts scrolling, and how
    /// many cards are worth wrapping in a ScrollView at all.
    ///
    /// A three-metric card is ~153.5pt (23 padding + 25.5 header + 9 gap +
    /// 3x26 rows + 2x9 row gaps), so 555 clears three of them and stops short of
    /// a fourth — the point past which the panel would be taller than it is
    /// useful. These are named rather
    /// than repeated at both call sites below, which is the one thing the
    /// three-line-row experiment left behind: the pair has to move together,
    /// and it did not when the cards got taller.
    private static let listMaxHeight: CGFloat = 555
    private static let listScrollsPast = 4

    @ViewBuilder
    private var openAIList: some View {
        let cards = VStack(spacing: 9) {
            ForEach(openai.accounts) { account in
                AccountCardView(account: account)
            }
        }
        if openai.accounts.count > Self.listScrollsPast {
            ScrollView(showsIndicators: false) { cards }
                .frame(maxHeight: Self.listMaxHeight)
        } else {
            cards
        }
    }

    /// Transient updater status, plus the one-click upgrade when a release is
    /// waiting. With no update activity this section disappears completely;
    /// the running version lives in the About menu row.
    @ViewBuilder
    private var footer: some View {
        if showsFooter {
            HStack(spacing: 10) {
                // A failed launch outranks a policy hold: it is the one the
                // user just caused, and the only one they can retry from here.
                if !updates.launchError.isEmpty {
                    Label(updates.launchError,
                          systemImage: "exclamationmark.triangle")
                        .font(.system(size: 12.5))
                        .foregroundStyle(palette.warning)
                        .lineLimit(1)
                } else if !updates.blockedReason.isEmpty {
                    Label("Update held", systemImage: "pause.circle")
                        .font(.system(size: 12.5))
                        .foregroundStyle(palette.textTertiary)
                        .help("Update held back: \(updates.blockedReason)")
                }
                Spacer(minLength: 6.5)
                if updates.isUpdating {
                    ProgressView()
                        .controlSize(.small)
                    Text("Updating…")
                        .font(.system(size: 12.5))
                        .foregroundStyle(palette.textSecondary)
                } else if !updates.pendingVersion.isEmpty {
                    Button("Update to \(updates.pendingVersion)") {
                        updates.installUpdate()
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .help("Fetch, rebuild and restart AI smartbar")
                    .accessibilityLabel("Update to version \(updates.pendingVersion)")
                } else if updates.isChecking {
                    ProgressView()
                        .controlSize(.small)
                    Text("Checking…")
                        .font(.system(size: 12.5))
                        .foregroundStyle(palette.textSecondary)
                } else if !updates.checkResult.isEmpty {
                    Text(updates.checkResult)
                        .font(.system(size: 12.5))
                        .foregroundStyle(palette.textSecondary)
                }
            }
            .padding(.top, 1)
        }
    }

    private var showsFooter: Bool {
        !updates.blockedReason.isEmpty
            || updates.isUpdating
            || !updates.pendingVersion.isEmpty
            || updates.isChecking
            || !updates.checkResult.isEmpty
            || !updates.launchError.isEmpty
    }

    @ViewBuilder
    private func accountList(_ snapshot: Snapshot) -> some View {
        let cards = VStack(spacing: 9) {
            ForEach(snapshot.accounts) { account in
                AccountCardView(account: account)
            }
        }
        if snapshot.accounts.count > Self.listScrollsPast {
            ScrollView(showsIndicators: false) { cards }
                .frame(maxHeight: Self.listMaxHeight)
        } else {
            cards
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("AI smartbar")
                .font(.system(size: 16.5, weight: .semibold))
                .foregroundStyle(palette.text)
            if let updated = store.dataUpdated {
                Text("Updated \(updated.formatted(date: .omitted, time: .shortened))")
                    .font(.system(size: 12.5))
                    .foregroundStyle(palette.textTertiary)
                    .help(freshnessHelp)
            }
            if store.isStale {
                Image(systemName: "wifi.slash")
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(palette.warning)
                    .help(store.lastError ?? "last refresh failed; showing old data")
            }
            Spacer()
            Button {
                store.refresh(force: true)
            } label: {
                // Header chrome sits a step back from the cards it frames —
                // they are what you opened the panel to read.
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(palette.textSecondary)
                    // 35pt keeps a comfortable pointer target without the
                    // 44pt frame padding the whole header row out — the gap
                    // between the title and the cards/tabs was mostly this.
                    .frame(width: 35, height: 35)
            }
            .buttonStyle(.borderless)
            .disabled(store.isRefreshing)
            .help("Refresh now")
            .accessibilityLabel("Refresh now")
            AppOptionsMenu()
        }
    }

    private var freshnessHelp: String {
        var parts: [String] = []
        if let measured = store.snapshot?.dataDate {
            parts.append("Usage measured \(measured.formatted(date: .omitted, time: .standard))")
        }
        if let polled = store.lastRefresh {
            parts.append("cswap polled \(polled.formatted(date: .omitted, time: .standard))")
        }
        return parts.isEmpty ? "No data yet" : parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var loadingOrError: some View {
        if let error = store.lastError {
            Label(error, systemImage: "exclamationmark.triangle")
                .font(.system(size: 12.5))
                .foregroundStyle(palette.warning)
                .lineLimit(3)
        } else {
            HStack(spacing: 9) {
                ProgressView()
                    .controlSize(.small)
                Text("Loading usage…")
                    .font(.system(size: 12.5))
                    .foregroundStyle(palette.textSecondary)
            }
        }
    }
}
