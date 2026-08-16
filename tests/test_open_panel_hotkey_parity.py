"""macOS open-panel hotkey: source-scrape parity.

See docs/superpowers/specs/2026-08-16-open-panel-hotkey-design.md for the
full design. Same technique as tests/test_menubar_hover_parity.py and
tests/test_plan.py::TestPlanParity: read the Swift as source text so this
runs on Linux/CI with no Swift toolchain, and pins that a future refactor
of AISmartbarApp.swift cannot silently drop the pieces this feature needs
to keep working:

  1. The global key monitor actually gets installed in
     applicationDidFinishLaunching, not merely defined and forgotten.
  2. It never crashes on a missing permission: AXIsProcessTrusted() is
     consulted (so the ungranted case is at least logged, per the
     feature's own "no crash, a clear log line" requirement) rather than
     the monitor being registered blind.
  3. The status-item lookup degrades instead of force-unwrapping: a nil
     button must not crash a hotkey press that fires before the popover
     has ever appeared once.
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


class TestHotkeyMonitorIsWiredAtLaunch(SwiftPresent):
    def test_did_finish_launching_installs_the_monitor(self):
        text = _read(APP_SOURCE)
        start = text.index("func applicationDidFinishLaunching(")
        rest = text[start:]
        next_brace_close = rest.find("\n    }")
        body = rest[:next_brace_close]
        self.assertIn("installHotkeyMonitor()", body,
                      "the hotkey monitor is defined but never installed "
                      "from applicationDidFinishLaunching")

    def test_the_monitor_uses_nseevents_global_monitor_api(self):
        text = _read(APP_SOURCE)
        self.assertIn("NSEvent.addGlobalMonitorForEvents(matching: .keyDown)",
                      text)


class TestPermissionHandlingIsGraceful(SwiftPresent):
    """No crash, a clear log line — the feature's own requirement for the
    case where Accessibility/Input Monitoring hasn't been granted."""

    def test_ax_is_process_trusted_is_consulted(self):
        text = _read(APP_SOURCE)
        self.assertIn("AXIsProcessTrusted()", text,
                      "the ungranted-permission case is not diagnosed at "
                      "all — a user who never sees the hotkey work would "
                      "have no log line explaining why")

    def test_both_permission_states_get_their_own_log_line(self):
        start = _read(APP_SOURCE).index("private func installHotkeyMonitor")
        body = _read(APP_SOURCE)[start:]
        end = body.find("\n    func ", 1)
        body = body[:end] if end != -1 else body
        self.assertGreaterEqual(body.count("NSLog("), 2,
                                "expected at least one NSLog for the "
                                "trusted case and one for the untrusted "
                                "case")


class TestStatusItemLookupDegradesGracefully(SwiftPresent):
    """A hotkey press before the popover has ever appeared once must not
    force-unwrap a nil status item or button."""

    def test_the_button_lookup_is_optional_not_force_unwrapped(self):
        text = _read(APP_SOURCE)
        self.assertIn("StatusItemLocator.shared.statusItem?.button", text)
        self.assertNotIn("StatusItemLocator.shared.statusItem!.button", text)

    def test_a_missing_button_falls_through_to_a_log_line_not_a_crash(self):
        start = _read(APP_SOURCE).index(
            "if let button = StatusItemLocator.shared.statusItem?.button")
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
        self.assertIn("hotkeyKeyCode: UInt16 = 0x00", text)


if __name__ == "__main__":
    unittest.main()
