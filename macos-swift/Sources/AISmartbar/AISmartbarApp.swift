// Menu-bar-only SwiftUI app. The label is the twin-pill "% left" icon
// (same design as the Linux badge); the window-style extra hosts the
// popover UI.
import AppKit
import Carbon.HIToolbox   // controlKey / optionKey for the hotkey
import SwiftUI

@main
struct AISmartbarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = UsageStore()
    @StateObject private var updates = UpdateStatus()
    @StateObject private var plans = PlanStatus()
    @StateObject private var openai = OpenAIStatus()
    @StateObject private var system = SystemStatus()

    // Optional menu-bar text label — off by default (see AppOptionsMenu's
    // "Menu bar" section, which writes these same keys). The mode says what
    // to show, the source which window; both are read by model.menu_bar_label.
    @AppStorage("menuBarLabel") private var menuBarLabel = "off"
    @AppStorage("menuBarSource") private var menuBarSource = "5h"

    var body: some Scene {
        MenuBarExtra {
            PopoverView()
                .environmentObject(store)
                .environmentObject(updates)
                .environmentObject(store.presence)
                .environmentObject(plans)
                .environmentObject(openai)
                .environmentObject(system)
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
            // Optional text beside the icon (off by default). The colored
            // pills stay a baked NSImage; the label is live SwiftUI Text so
            // it inherits the menu bar's own light/dark vibrancy — a color
            // baked into the bitmap could not adapt to the bar behind it.
            let labelText = store.menuBarText(mode: menuBarLabel, source: menuBarSource)
            HStack(spacing: 3) {
                Image(nsImage: updates.pendingVersion.isEmpty
                      ? store.icon
                      : MenuBarIcon.badged(store.icon))
                if !labelText.isEmpty {
                    MenuBarLabel(text: labelText,
                                 status: store.menuBarStatus(source: menuBarSource))
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(summary)
        }
        .menuBarExtraStyle(.window)
    }
}

/// The optional text beside the icon, tinted to match the window it reads.
///
/// A small View rather than a colour computed up in the App scene, because
/// the tint is appearance-aware and only a View gets \.colorScheme.
private struct MenuBarLabel: View {
    let text: String
    let status: Status?
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Text(text)
            // Regular weight and small — a compact, quiet readout beside the
            // bar's other widgets (a temperature, the clock) rather than a
            // bolder number that reads as a badge shouting over them. The
            // status tint, not the size, is what carries the state.
            .font(.system(size: 6, weight: .regular))
            .monospacedDigit()
            .fixedSize()
            .foregroundStyle(color)
    }

    /// The number always wears its window's status colour — the same green→red
    /// the pill shows — so the label restates the state at a glance instead of
    /// a neutral figure. Scheme-aware (unlike the baked pill, StatusPalette's
    /// nsColor) because the label is live Text over the bar's own vibrancy: the
    /// light ramp keeps green and yellow legible on a light menu bar, exactly
    /// the wash-out the dark ramp would cause there. With no window to read,
    /// falls back to primary, which inherits that vibrancy directly.
    private var color: Color {
        guard let status else { return .primary }
        return status.color(in: colorScheme)
    }
}

/// Reaches the menu-bar icon's button, so the hotkey can open the popover
/// the same way a click on the icon does and the tooltip can be set on it.
/// SwiftUI's MenuBarExtra has no public API for either: nothing hands a
/// caller the status item, the window, or any handle that would let code
/// open the popover on demand.
///
/// The route in is the app's own NSStatusBarWindow, which exists the moment
/// the icon is on screen (see `menuBarButton`). If Apple ever renames that
/// window class the search finds nothing and this degrades to "hotkey
/// pressed, nothing opens" (logged, not a crash) — see
/// docs/superpowers/specs/2026-08-16-open-panel-hotkey-design.md.
final class StatusItemLocator {
    static let shared = StatusItemLocator()

    /// The last text actually written to the button, so a re-render that
    /// leaves the tooltip unchanged doesn't re-assign it — see
    /// `shouldApplyTooltip` for why that matters.
    private var appliedTooltip: String?
    /// The found button, held so an unchanged-text render costs nothing
    /// instead of re-walking every window's view tree. Weak: if the status
    /// window is ever torn down and rebuilt, this clears and `menuBarButton`
    /// finds the new one.
    private weak var cachedButton: NSStatusBarButton?

    /// The menu-bar icon's button, found by locating the status bar window
    /// among the app's own windows. (The popover CONTENT window is no route
    /// in: SwiftUI does not create it until the popover is first opened, so a
    /// hotkey pressed before that would find nothing.) Empirically the
    /// NSStatusBarWindow is the sole one among NSApp's windows and its button
    /// is an NSStatusBarButton in the view tree. Nil only in the instant
    /// before the icon is placed; the next state change re-pushes.
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
    private static let hotkeyKeyCode = 0x00
    private var hotkey: GlobalHotkey?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Menu-bar only: no Dock icon even when run as a bare binary
        // (the .app bundle also sets LSUIElement as belt-and-braces).
        NSApp.setActivationPolicy(.accessory)
        // Ask for notification authorization and set the presentation delegate.
        // On a Developer-ID-signed build this prompts once, then banners wear
        // the app's own icon; on the ad-hoc build it fails fast and Notifier
        // falls back to osascript. See Notifier.swift.
        Notifier.shared.configure()
        installHotkey()
        // Start Sparkle's background update schedule — a no-op on a checkout
        // install (that copy updates itself with git), live only on a DMG one.
        // Touched here purely to build the shared instance at launch; see
        // SparkleUpdater/Distribution.
        _ = SparkleUpdater.shared
    }

    /// Registers the open-panel hotkey. It works with no permission prompt
    /// (see GlobalHotkey); the one way it can fail is another app already
    /// owning ⌃⌥A, which is logged rather than fatal — the menu-bar icon
    /// still opens the panel.
    private func installHotkey() {
        hotkey = GlobalHotkey(keyCode: Self.hotkeyKeyCode,
                              modifiers: controlKey | optionKey) {
            DispatchQueue.main.async {
                if let button = StatusItemLocator.shared.menuBarButton {
                    // The one call this whole feature exists to make:
                    // simulating the exact click that already opens the
                    // popover, because MenuBarExtra exposes no direct
                    // "open" method of its own — see StatusItemLocator's
                    // own docstring.
                    button.performClick(nil)
                } else {
                    NSLog("ai-smartbar: ⌃⌥A fired but the menu-bar icon "
                          + "is not on screen yet — this should only happen "
                          + "in the instant right after launch")
                }
            }
        }
        if hotkey == nil {
            NSLog("ai-smartbar: could not register ⌃⌥A — another app "
                  + "already uses it")
        }
    }
}
