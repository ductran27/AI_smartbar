// Content of the MenuBarExtra window: compact header (title, Updated
// stamp, stale marker, equal-size refresh/quit), one card per account, and
// a quiet footer naming the running version — which grows an upgrade
// button when the updater has a newer release waiting. Dark-only design.
import AppKit
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
        .padding(11)
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

    /// Version line, plus the one-click upgrade when a release is waiting.
    /// The button hands off to the launchd update job rather than doing the
    /// work here — applying an update restarts this very app.
    private var footer: some View {
        HStack(spacing: 8) {
            Text("v\(updates.currentVersion)")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .help(updates.blockedReason.isEmpty
                      ? "AI smartbar \(updates.currentVersion)"
                      : "Update held back: \(updates.blockedReason)")
            if !updates.blockedReason.isEmpty {
                Image(systemName: "pause.circle")
                    .font(.system(size: 9.5))
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
            } else {
                // The upgrade button above only appears once a check has
                // already FOUND something, and the agent only looks every 6
                // hours — so without this there is no way to ask.
                Button("Check for updates") { updates.checkNow() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .help("Ask now whether a newer release is waiting, instead "
                          + "of waiting for the 6-hourly check")
            }
        }
        .padding(.top, 1)
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
                    .frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .disabled(store.isRefreshing)
            .help("Refresh now")
            .accessibilityLabel("Refresh now")
            Button {
                NSApplication.shared.terminate(nil)
            } label: {
                Image(systemName: "power")
                    .font(.system(size: 12.5, weight: .semibold))
                    .frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .help("Quit AI smartbar")
            .accessibilityLabel("Quit AI smartbar")
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
