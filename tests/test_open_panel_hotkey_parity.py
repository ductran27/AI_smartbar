"""macOS open-panel hotkey: source-scrape parity.

See docs/superpowers/specs/2026-08-16-open-panel-hotkey-design.md for the
full design. Same technique as tests/test_menubar_hover_parity.py and
tests/test_plan.py::TestPlanParity: read the Swift as source text so this
runs on Linux/CI with no Swift toolchain, and pins that a future refactor
cannot silently drop the pieces this feature needs to keep working:

  1. The hotkey actually gets registered in applicationDidFinishLaunching,
     not merely defined and forgotten.
  2. It is a Carbon RegisterEventHotKey — needs no Accessibility permission
     and wakes the app only for the one combination. An NSEvent global
     key monitor is the trap: it needs that permission (so on an ungranted
     machine the hotkey silently never works) and calls back on every
     keystroke system-wide.
  3. A refused registration (another app owns the combo) is logged, never
     fatal, and the button lookup degrades instead of force-unwrapping: a
     nil button must not crash a press that fires before the icon is placed.
  4. The button comes from the always-present status bar window, not from
     the popover's own window (which SwiftUI builds only on first open —
     the hotkey would then do nothing until the popover was clicked once).
"""
from __future__ import annotations

import os
import unittest

import smartbar

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
APP_SOURCE = os.path.join(SWIFT_DIR, "AISmartbarApp.swift")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(APP_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")


HOTKEY_SOURCE = os.path.join(SWIFT_DIR, "GlobalHotkey.swift")


class TestHotkeyIsWiredAtLaunch(SwiftPresent):
    def test_did_finish_launching_installs_the_hotkey(self):
        text = _read(APP_SOURCE)
        start = text.index("func applicationDidFinishLaunching(")
        rest = text[start:]
        next_brace_close = rest.find("\n    }")
        body = rest[:next_brace_close]
        self.assertIn("installHotkey()", body,
                      "the hotkey is defined but never installed "
                      "from applicationDidFinishLaunching")


class TestHotkeyNeedsNoPermissionAndNoKeystrokeMonitor(SwiftPresent):
    def test_it_registers_a_carbon_hot_key(self):
        text = _read(HOTKEY_SOURCE)
        self.assertIn("RegisterEventHotKey(", text)
        self.assertIn("UnregisterEventHotKey(", text,
                      "a registered hot key must be released in deinit")

    def test_no_global_key_monitor_or_accessibility_gate_comes_back(self):
        for path in (APP_SOURCE, HOTKEY_SOURCE):
            text = _read(path)
            code = "\n".join(line for line in text.splitlines()
                             if not line.lstrip().startswith("//"))
            self.assertNotIn("addGlobalMonitorForEvents", code,
                             "a global key monitor needs Accessibility "
                             "trust and wakes the app on every keystroke")
            self.assertNotIn("AXIsProcessTrusted", code)

    def test_a_refused_registration_is_logged_not_fatal(self):
        start = _read(APP_SOURCE).index("private func installHotkey")
        body = _read(APP_SOURCE)[start:]
        self.assertIn("if hotkey == nil", body)
        self.assertIn("NSLog(", body[body.index("if hotkey == nil"):])


class TestStatusItemLookupDegradesGracefully(SwiftPresent):
    """A hotkey press before the icon is placed must not force-unwrap a nil
    button — and must not depend on the popover having been opened."""

    def test_the_popover_window_is_no_longer_the_route_to_the_button(self):
        text = _read(APP_SOURCE)
        self.assertNotIn("StatusItemAccessor", text)
        self.assertNotIn('forKey: "statusItem"', text)

    def test_the_button_lookup_is_optional_not_force_unwrapped(self):
        text = _read(APP_SOURCE)
        self.assertIn("StatusItemLocator.shared.menuBarButton", text)
        self.assertNotIn("StatusItemLocator.shared.menuBarButton!", text)

    def test_a_missing_button_falls_through_to_a_log_line_not_a_crash(self):
        start = _read(APP_SOURCE).index(
            "if let button = StatusItemLocator.shared.menuBarButton")
        body = _read(APP_SOURCE)[start:]
        end = body.find("\n            }")
        body = body[:end]
        self.assertIn("else", body)
        self.assertIn("NSLog(", body)


class TestKeyCodeIsPhysicalNotCharacter(SwiftPresent):
    """kVK_ANSI_A (0x00) — keyed by physical position like every other
    system-wide hotkey library, not by whatever character the active
    keyboard layout happens to produce."""

    def test_hotkey_key_code_is_the_documented_vk_ansi_a_value(self):
        text = _read(APP_SOURCE)
        self.assertIn("hotkeyKeyCode = 0x00", text)

    def test_the_modifiers_are_control_and_option(self):
        self.assertIn("controlKey | optionKey", _read(APP_SOURCE))


if __name__ == "__main__":
    unittest.main()
