// Content of the MenuBarExtra window: header, one card per account, footer.
import AppKit
import SwiftUI

struct PopoverView: View {
    @EnvironmentObject private var store: UsageStore

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if let snapshot = store.snapshot {
                VStack(spacing: 10) {
                    ForEach(snapshot.accounts) { account in
                        AccountCardView(account: account)
                    }
                }
            } else {
                loadingOrError
            }
            footer
        }
        .padding(14)
        .frame(width: 330)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("AI smartbar")
                .font(.headline)
            Spacer()
            Button {
                store.refresh()
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .disabled(store.isRefreshing)
            .help("Refresh now")
            Button {
                NSApplication.shared.terminate(nil)
            } label: {
                Image(systemName: "power")
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

    private var footer: some View {
        HStack(spacing: 6) {
            if store.isStale {
                Label("stale", systemImage: "wifi.slash")
                    .font(.caption2)
                    .foregroundStyle(.orange)
                    .help(store.lastError ?? "")
            }
            Spacer()
            if let refreshed = store.lastRefresh {
                Text("Updated \(refreshed.formatted(date: .omitted, time: .shortened))")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }
}
