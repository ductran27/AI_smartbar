// Content of the MenuBarExtra window: compact header (title, Updated
// stamp, stale marker, equal-size refresh/quit), then one card per
// account. Dark-only design; no footer row.
import AppKit
import SwiftUI

struct PopoverView: View {
    @EnvironmentObject private var store: UsageStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if let snapshot = store.snapshot {
                accountList(snapshot)
            } else {
                loadingOrError
            }
        }
        .padding(11)
        .frame(width: 330)
        .preferredColorScheme(.dark)
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
            if let refreshed = store.lastRefresh {
                Text("Updated \(refreshed.formatted(date: .omitted, time: .shortened))")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            if store.isStale {
                Image(systemName: "wifi.slash")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.orange)
                    .help(store.lastError ?? "last refresh failed; showing old data")
            }
            Spacer()
            Button {
                store.refresh()
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 12.5, weight: .semibold))
                    .frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .disabled(store.isRefreshing)
            .help("Refresh now")
            Button {
                NSApplication.shared.terminate(nil)
            } label: {
                Image(systemName: "power")
                    .font(.system(size: 12.5, weight: .semibold))
                    .frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .help("Quit AI smartbar")
        }
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
