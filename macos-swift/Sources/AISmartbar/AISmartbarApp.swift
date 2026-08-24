// Menu-bar-only SwiftUI app. The label is the twin-pill "% left" icon
// (same design as the Linux badge); the window-style extra hosts the
// popover UI.
import AppKit
import ApplicationServices
import SwiftUI

@main
struct AISmartbarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = UsageStore()
    @StateObject private var updates = UpdateStatus()
    @StateObject private var plans = PlanStatus()
    @StateObject private var openai = OpenAIStatus()
    @StateObject private var system = SystemStatus()

    var body: some Scene {
        MenuBarExtra {
            PopoverView()
                .environmentObject(store)
                .environmentObject(updates)
                .environmentObject(store.presence)
                .environmentObject(plans)
                .environmentObject(openai)
                .environmentObject(system)
                // Invisible: exists only so StatusItemLocator can capture a
                // live NSStatusItem reference for the hotkey below to open
                // this same window with. See StatusItemLocator's own
                // docstring for why this is the least-invasive route into
                // an object MenuBarExtra otherwise never hands out.
                .background(StatusItemAccessor())
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

/// Holds the one NSStatusItem MenuBarExtra builds for this app, so the
/// hotkey below can open its window the same way a click on the icon
/// does. SwiftUI's MenuBarExtra has no public API for this at all —
/// nothing hands a caller the status item, the window, or any handle
/// that would let code open the popover on demand.
///
/// The route in: the window SwiftUI wraps the popover content in carries
/// its owning NSStatusItem under the "statusItem" key, reachable only
/// through Key-Value Coding (confirmed empirically against macOS 14/15;
/// not documented, not part of any public API contract). That is the
/// least-invasive working approach found — see
/// docs/superpowers/specs/2026-08-16-open-panel-hotkey-design.md for the
/// alternatives it was weighed against (a private CGEventTap-driven
/// window search; walking NSApp.windows by class-name substring) and why
/// this one was chosen. If Apple ever renames or drops that key, `value
/// (forKey:)` returns nil (or a value that fails the `as?` cast) rather
/// than throwing, so this degrades to "hotkey captured, nothing opens"
/// (logged, not a crash) instead of breaking the build or the app.
final class StatusItemLocator {
    static let shared = StatusItemLocator()
    private(set) weak var statusItem: NSStatusItem?

    func capture(from view: NSView) {
        guard statusItem == nil, let window = view.window else { return }
        statusItem = window.value(forKey: "statusItem") as? NSStatusItem
    }
}

/// A zero-size NSView planted inside the popover content purely to get a
/// live NSWindow reference into StatusItemLocator once SwiftUI has
/// actually attached one — see that class's own docstring for why this
/// indirection exists at all.
private struct StatusItemAccessor: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        // The view has no window yet the instant it is created — SwiftUI
        // attaches one moments later — so this reads the window on the
        // next run-loop turn instead of racing that attachment.
        DispatchQueue.main.async { StatusItemLocator.shared.capture(from: view) }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {}
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    // ⌃⌥A (Control+Option+A). Chosen because Control+Option is a modifier
    // pair almost nothing in macOS's own shortcuts or common third-party
    // apps' default bindings claims — unlike bare ⌥ (dozens of system
    // shortcuts), ⌘ (nearly everything), or ⇧⌘ (most "alternate" actions)
    // — and "A" mnemonics "AI smartbar". Matched by physical key position
    // (kVK_ANSI_A = 0x00), not by the character a layout produces, the same
    // way every system-wide-hotkey library keys off a virtual key code: the
    // combo fires from the same physical key regardless of layout, even
    // one where that key does not literally type "a". Windows' equivalent
    // (Ctrl+Alt+A, smartbar/windows/tray.py) mirrors the same physical
    // pair for the same muscle memory across platforms.
    private static let hotkeyKeyCode: UInt16 = 0x00
    private var hotkeyMonitor: Any?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Menu-bar only: no Dock icon even when run as a bare binary
        // (the .app bundle also sets LSUIElement as belt-and-braces).
        NSApp.setActivationPolicy(.accessory)
        installHotkeyMonitor()
    }

    /// Global key monitor for the open-panel hotkey. NSEvent's global
    /// monitor only ever invokes its handler once macOS has granted this
    /// app Accessibility trust (the same grant some macOS versions surface
    /// under Input Monitoring instead) — there is no separate request/
    /// callback for that the way there is for e.g. location permission, so
    /// an ungranted app simply never sees the callback fire. That is not a
    /// crash and not this method's job to fix: it logs which state it
    /// found at launch (once, for diagnosis) and installs the monitor
    /// either way, because a permission granted from System Settings AFTER
    /// launch starts working immediately with no relaunch needed — macOS
    /// re-checks trust per event, not once at registration time.
    private func installHotkeyMonitor() {
        if AXIsProcessTrusted() {
            NSLog("ai-smartbar: Accessibility permission granted — the "
                  + "⌃⌥A open-panel hotkey is active")
        } else {
            NSLog("ai-smartbar: Accessibility (or Input Monitoring) "
                  + "permission not granted — the ⌃⌥A open-panel hotkey "
                  + "will do nothing until it is allowed in System "
                  + "Settings > Privacy & Security. See the README's "
                  + "Requirements section.")
        }
        hotkeyMonitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { event in
            let mods = event.modifierFlags.intersection(
                [.command, .control, .option, .shift])
            guard mods == [.control, .option],
                  event.keyCode == Self.hotkeyKeyCode else { return }
            // Global-monitor callbacks are documented as running in the
            // event-owning (foreground) process's own context, which in
            // practice is this app's main thread — dispatched explicitly
            // anyway rather than assumed, since touching AppKit off the
            // main thread is undefined behaviour if that assumption is
            // ever wrong on some macOS version.
            DispatchQueue.main.async {
                if let button = StatusItemLocator.shared.statusItem?.button {
                    // The one call this whole feature exists to make:
                    // simulating the exact click that already opens the
                    // popover, because MenuBarExtra exposes no direct
                    // "open" method of its own — see StatusItemLocator's
                    // own docstring.
                    button.performClick(nil)
                } else {
                    NSLog("ai-smartbar: ⌃⌥A fired but no status item has "
                          + "been captured yet — this should only happen "
                          + "in the instant right after launch")
                }
            }
        }
    }
}
