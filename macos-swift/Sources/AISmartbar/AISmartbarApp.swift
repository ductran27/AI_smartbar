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
    @StateObject private var openai = OpenAIStatus()

    var body: some Scene {
        MenuBarExtra {
            PopoverView()
                .environmentObject(store)
                .environmentObject(updates)
                .environmentObject(store.presence)
                .environmentObject(plans)
                .environmentObject(openai)
        } label: {
            // A waiting release badges the icon itself, so a device announces
            // an update without the user opening anything.
            let summary = updates.pendingVersion.isEmpty
                ? store.accessibilitySummary
                : "\(store.accessibilitySummary). Update to "
                  + "\(updates.pendingVersion) available"
            Image(nsImage: updates.pendingVersion.isEmpty
                  ? store.icon
                  : MenuBarIcon.badged(store.icon))
                .accessibilityLabel(summary)
                // .help() is the sighted-user counterpart to the
                // accessibilityLabel above: VoiceOver already spoke this
                // text, but before this a sighted user hovering the icon
                // saw nothing (Linux's AppIndicator.set_title and
                // Windows' pystray icon.title both already show a native
                // hover tooltip here — see tray_controller.py's
                // set_title calls — so macOS was the one platform
                // without one). Same string as the VoiceOver label so
                // the two can't drift apart.
                .help(summary)
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
