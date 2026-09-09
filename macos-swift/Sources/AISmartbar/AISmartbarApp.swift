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
            // Sighted-user hover tooltip. `.help()` on a MenuBarExtra label is
            // a no-op — macOS only renders the status item button's native
            // toolTip — so push it there directly. This closure re-runs on
            // every published store change (ObservableObject re-renders on any
            // @Published mutation), so the tooltip stays live in the
            // background even with the popover closed. Richer than the concise
            // `summary` VoiceOver label because a tooltip has the room: the
            // active account plus each window's reset countdown. (Linux's
            // AppIndicator.set_title and Windows' pystray icon.title already
            // show a native hover tooltip — see tray_controller.py's set_title
            // — so macOS was the one platform without one.)
            let _ = StatusItemLocator.shared.updateTooltip(
                updates.pendingVersion.isEmpty
                ? store.tooltipSummary
                : "\(store.tooltipSummary)\n\nUpdate to "
                  + "\(updates.pendingVersion) available")
            Image(nsImage: updates.pendingVersion.isEmpty
                  ? store.icon
                  : MenuBarIcon.badged(store.icon))
                .accessibilityLabel(summary)
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

    /// The last text actually written to the button, so a re-render that
    /// leaves the tooltip unchanged doesn't re-assign it — see
    /// `shouldApplyTooltip` for why that matters.
    private var appliedTooltip: String?
    /// The found button, held so an unchanged-text render costs nothing
    /// instead of re-walking every window's view tree. Weak: if the status
    /// window is ever torn down and rebuilt, this clears and `menuBarButton`
    /// finds the new one.
    private weak var cachedButton: NSStatusBarButton?

    func capture(from view: NSView) {
        guard statusItem == nil, let window = view.window else { return }
        statusItem = window.value(forKey: "statusItem") as? NSStatusItem
    }

    /// The menu-bar icon's button. Found by locating the status bar window
    /// among the app's own windows, rather than through the `statusItem`
    /// above: that reference is captured from the popover CONTENT window,
    /// which SwiftUI does not create until the popover is first opened — so it
    /// stays nil (and the tooltip unset) for a user who only ever hovers. The
    /// NSStatusBarWindow, by contrast, exists the moment the icon is on
    /// screen. Empirically it is the sole NSStatusBarWindow among NSApp's
    /// windows and its button is an NSStatusBarButton in the view tree. Nil
    /// only in the instant before the icon is placed; the next state change
    /// re-pushes.
    var menuBarButton: NSStatusBarButton? {
        if let cachedButton { return cachedButton }
        for window in NSApp.windows where window.className == "NSStatusBarWindow" {
            if let button = Self.firstStatusBarButton(in: window.contentView) {
                cachedButton = button
                return button
            }
        }
        return nil
    }

    private static func firstStatusBarButton(in view: NSView?) -> NSStatusBarButton? {
        guard let view else { return nil }
        if let button = view as? NSStatusBarButton { return button }
        for subview in view.subviews {
            if let button = firstStatusBarButton(in: subview) { return button }
        }
        return nil
    }

    /// Set the native hover tooltip on the icon's button. This is the one
    /// thing macOS actually renders on hover — the SwiftUI `.help()` modifier
    /// is silently dropped on a MenuBarExtra label.
    ///
    /// The label closure that calls this re-runs on every SwiftUI publish (any
    /// of the five app-level stores, several ticks a minute between them), but
    /// the tooltip text only moves when the live reset countdown ticks (~once a
    /// minute). Skipping the unchanged writes is not just economy: re-assigning
    /// `toolTip` reinstalls the view's tooltip tracking rectangle, and doing
    /// that while the tooltip is on screen disrupts its mouse-exit dismissal —
    /// the tooltip lingers and fades slowly. Write only when it changed.
    func updateTooltip(_ text: String) {
        guard let button = menuBarButton else { return }  // not placed yet; a later render retries
        guard shouldApplyTooltip(text) else { return }
        button.toolTip = text
    }

    /// The pure "did the tooltip text move?" decision behind `updateTooltip`,
    /// split out so it is unit-testable without a live status-bar button:
    /// true the first time a text is seen and whenever it changes (remembering
    /// it), false for a repeat of the last-applied text.
    func shouldApplyTooltip(_ text: String) -> Bool {
        guard text != appliedTooltip else { return false }
        appliedTooltip = text
        return true
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
        // Ask for notification authorization and set the presentation delegate.
        // On a Developer-ID-signed build this prompts once, then banners wear
        // the app's own icon; on the ad-hoc build it fails fast and Notifier
        // falls back to osascript. See Notifier.swift.
        Notifier.shared.configure()
        installHotkeyMonitor()
        // Start Sparkle's background update schedule — a no-op on a checkout
        // install (that copy updates itself with git), live only on a DMG one.
        // Touched here purely to build the shared instance at launch; see
        // SparkleUpdater/Distribution.
        _ = SparkleUpdater.shared
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
