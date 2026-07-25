// Native More menu for secondary app commands. The frequently used refresh
// action stays visible in the popover header; infrequent and app-level actions
// live here so the header remains compact as the app grows.
import AppKit
import SwiftUI

struct AppOptionsMenu: View {
    @EnvironmentObject private var updates: UpdateStatus

    var body: some View {
        Menu {
            Button {
                updates.checkNow()
            } label: {
                Label(updateCheckTitle, systemImage: "arrow.down.circle")
            }
            .disabled(updates.isChecking || updates.isUpdating)

            Divider()

            Button {
                showAboutPanel()
            } label: {
                Label("About AI smartbar · v\(updates.currentVersion)",
                      systemImage: "info.circle")
            }

            Button {
                NSApplication.shared.terminate(nil)
            } label: {
                Label("Quit AI smartbar", systemImage: "power")
            }
            .keyboardShortcut("q", modifiers: .command)
        } label: {
            Label("More options", systemImage: "ellipsis.circle")
                .labelStyle(.iconOnly)
                .font(.system(size: 14, weight: .semibold))
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .menuIndicator(.hidden)
        .menuStyle(.button)
        .buttonStyle(.borderless)
        .help("More options")
        .accessibilityLabel("More options")
    }

    private var updateCheckTitle: String {
        updates.isChecking ? "Checking for Updates…" : "Check for Updates"
    }

    private func showAboutPanel() {
        let credits = NSMutableAttributedString(string: "Created by Duc Tran\n")
        if let profile = URL(string: "https://github.com/ductran27/") {
            credits.append(NSAttributedString(
                string: "github.com/ductran27",
                attributes: [
                    .foregroundColor: NSColor.linkColor,
                    .underlineStyle: NSUnderlineStyle.single.rawValue,
                    .link: profile,
                ]
            ))
        } else {
            credits.append(NSAttributedString(string: "github.com/ductran27"))
        }

        NSApplication.shared.activate(ignoringOtherApps: true)
        NSApplication.shared.orderFrontStandardAboutPanel(options: [
            .credits: credits,
        ])
    }
}
