// Content of the MenuBarExtra window: compact header (title, Updated
// stamp, stale marker, refresh and More menu), one card per account, and a
// transient updater footer that appears only while there is status to show.
// Dark-only design.
import SwiftUI

struct PopoverView: View {
    @EnvironmentObject private var store: UsageStore
    @EnvironmentObject private var updates: UpdateStatus
    @EnvironmentObject private var openai: OpenAIStatus
    @AppStorage("providerTab") private var providerTab = "claude"

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if showsTabs {
                providerTabs
            }
            // One error line for every card action — switching or removing,
            // whichever failed most recently.
            if let actionError = store.switchError ?? store.removeError
                    ?? openai.removeError {
                Label(actionError, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.orange)
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
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                accountList(snapshot)
            } else {
                loadingOrError
            }
            footer
        }
        .padding(.horizontal, 11)
        .padding(.bottom, 11)
        .padding(.top, 5)
        .frame(width: 330)
        .preferredColorScheme(.dark)
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

    /// The tab row exists only when BOTH providers have accounts — a
    /// single-provider Mac keeps exactly the popover it always had
    /// (mirror of popover_layout.build's rule).
    private var showsTabs: Bool {
        hasClaudeAccounts && !openai.accounts.isEmpty
    }

    private var selectedProvider: String {
        if showsTabs { return providerTab == "openai" ? "openai" : "claude" }
        return (!openai.accounts.isEmpty && !hasClaudeAccounts)
            ? "openai" : "claude"
    }

    private var providerTabs: some View {
        HStack(spacing: 6) {
            tabButton("Claude", id: "claude")
            tabButton("OpenAI", id: "openai")
            Spacer()
        }
    }

    /// Faded / not-faded rather than colored: the selected provider reads
    /// full strength, the other recedes (mirror of the cairo tab pills —
    /// popover_theme.TAB_BG*). The mark sits BESIDE the label, never
    /// instead of it — a tab must stay readable to anyone who doesn't
    /// recognise the provider's mark on sight (mirror of
    /// popover_layout.build's TAB_MARK/TAB_MARK_GAP), and it takes the
    /// label's own color so a faded tab reads as faded mark-and-all.
    private func tabButton(_ title: String, id: String) -> some View {
        let selected = selectedProvider == id
        let color = selected ? Palette.chalk : Palette.dim
        return Button { providerTab = id } label: {
            HStack(spacing: 5) {
                ProviderMark(kind: id)
                    .frame(width: 11, height: 11)
                Text(title)
                    .font(.caption.weight(selected ? .semibold : .regular))
            }
            .foregroundStyle(color)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(Capsule()
                .fill(Color.white.opacity(selected ? 0.16 : 0.06)))
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var openAIList: some View {
        let cards = VStack(spacing: 7) {
            ForEach(openai.accounts) { account in
                AccountCardView(account: account)
            }
        }
        if openai.accounts.count > 4 {
            ScrollView(showsIndicators: false) { cards }
                .frame(maxHeight: 440)
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
            HStack(spacing: 8) {
                // A failed launch outranks a policy hold: it is the one the
                // user just caused, and the only one they can retry from here.
                if !updates.launchError.isEmpty {
                    Label(updates.launchError,
                          systemImage: "exclamationmark.triangle")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .lineLimit(1)
                } else if !updates.blockedReason.isEmpty {
                    Label("Update held", systemImage: "pause.circle")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .help("Update held back: \(updates.blockedReason)")
                }
                Spacer(minLength: 6)
                if updates.isUpdating {
                    ProgressView()
                        .controlSize(.small)
                    Text("Updating…")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
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
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                } else if !updates.checkResult.isEmpty {
                    Text(updates.checkResult)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
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
        let cards = VStack(spacing: 7) {
            ForEach(snapshot.accounts) { account in
                AccountCardView(account: account)
            }
        }
        if snapshot.accounts.count > 4 {
            ScrollView(showsIndicators: false) { cards }
                .frame(maxHeight: 440)
        } else {
            cards
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            Text("AI smartbar")
                .font(.headline)
            if let updated = store.dataUpdated {
                Text("Updated \(updated.formatted(date: .omitted, time: .shortened))")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .help(freshnessHelp)
            }
            if store.isStale {
                Image(systemName: "wifi.slash")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.orange)
                    .help(store.lastError ?? "last refresh failed; showing old data")
            }
            Spacer()
            Button {
                store.refresh(force: true)
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 12.5, weight: .semibold))
                    // 28pt keeps a comfortable pointer target without the
                    // 44pt frame padding the whole header row out — the gap
                    // between the title and the cards/tabs was mostly this.
                    .frame(width: 28, height: 28)
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
                .font(.caption)
                .foregroundStyle(.orange)
                .lineLimit(3)
        } else {
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Loading usage…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
