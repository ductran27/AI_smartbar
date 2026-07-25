// Menu-bar-only SwiftUI app. The label is the twin-pill "% left" icon
// (same design as the Linux badge); the window-style extra hosts the
// popover UI.
import AppKit
import SwiftUI

@main
struct AISmartbarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = UsageStore()
    @StateObject private var updates = UpdateStatus()
    @StateObject private var plans = PlanStatus()

    var body: some Scene {
        MenuBarExtra {
            PopoverView()
                .environmentObject(store)
                .environmentObject(updates)
                .environmentObject(store.presence)
                .environmentObject(plans)
        } label: {
            // A waiting release badges the icon itself, so a device announces
            // an update without the user opening anything.
            Image(nsImage: updates.pendingVersion.isEmpty
                  ? store.icon
                  : MenuBarIcon.badged(store.icon))
                .accessibilityLabel(updates.pendingVersion.isEmpty
                    ? store.accessibilitySummary
                    : "\(store.accessibilitySummary). Update to "
                      + "\(updates.pendingVersion) available")
        }
        .menuBarExtraStyle(.window)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Menu-bar only: no Dock icon even when run as a bare binary
        // (the .app bundle also sets LSUIElement as belt-and-braces).
        NSApp.setActivationPolicy(.accessory)
    }
}
