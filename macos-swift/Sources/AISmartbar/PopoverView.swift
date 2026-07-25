// Content of the MenuBarExtra window: compact header (title, Updated
// stamp, stale marker, refresh and More menu), one card per account, and a
// transient updater footer that appears only while there is status to show.
// Dark-only design.
import SwiftUI

struct PopoverView: View {
    @EnvironmentObject private var store: UsageStore
    @EnvironmentObject private var updates: UpdateStatus

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if let switchError = store.switchError {
                Label(switchError, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .lineLimit(2)
            }
            if let snapshot = store.snapshot {
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
        }
    }

    /// Transient updater status, plus the one-click upgrade when a release is
    /// waiting. With no update activity this section disappears completely;
    /// the running version lives in the About menu row.
    @ViewBuilder
    private var footer: some View {
        if showsFooter {
            HStack(spacing: 8) {
                if !updates.blockedReason.isEmpty {
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
                    .frame(width: 44, height: 44)
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
