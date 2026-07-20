// Menu-bar-only SwiftUI app. The label mirrors the Linux badge as dotted
// text segments; the window-style extra hosts the popover UI.
import AppKit
import SwiftUI

@main
struct AISmartbarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = UsageStore()

    var body: some Scene {
        MenuBarExtra {
            PopoverView()
                .environmentObject(store)
        } label: {
            Text(store.menuBarTitle)
                .monospacedDigit()
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
