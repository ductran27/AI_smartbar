"""macOS menu-bar icon hover tooltip: source-scrape parity.

Linux (linux/tray.py) and Windows (windows/tray.py) both get a native
hover tooltip on the tray icon for free, because TrayController calls
host.set_title(model.title_line(account)) on every meaningful repaint
(normal poll, account switch, the error path — see tray_controller.py's
_apply_snapshot/_apply_error) and both hosts' set_title implementations
(AppIndicator.set_title, pystray's icon.title) render natively as a hover
tooltip. macOS's SwiftUI MenuBarExtra label had no such thing: its
.accessibilityLabel(store.accessibilitySummary) is VoiceOver-only, so a
sighted user hovering the icon saw nothing until a .help(...) modifier was
added reusing that same string.

Same technique as tests/test_plan.py::TestPlanParity and
tests/test_account_card_parity.py: read the Swift as source text, so this
runs on Linux with no Swift toolchain, and pins that a future refactor of
the MenuBarExtra label can't silently drop the modifier again.
"""
from __future__ import annotations

import os
import re
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


class TestMenuBarHoverTooltipParity(SwiftPresent):
    def test_menu_bar_extra_label_carries_a_help_modifier(self):
        text = _read(APP_SOURCE)
        self.assertIn("MenuBarExtra {", text,
                       "expected the MenuBarExtra scene to still exist")
        # The label closure: everything between the `label: {` that starts
        # MenuBarExtra's second trailing closure and its matching `}`.
        label_start = text.index("} label: {")
        label_body = text[label_start:]
        self.assertIn(".accessibilityLabel(", label_body,
                       "VoiceOver label regressed — nothing to keep in "
                       "sync with .help(...) anymore")
        self.assertIn(".help(", label_body,
                       "the menu-bar icon lost its hover tooltip — a "
                       "sighted user hovering it sees nothing again")

    def test_help_and_accessibility_label_read_the_same_summary(self):
        """Both modifiers must be handed the same string (`summary`), or
        VoiceOver and a sighted hover could say different things about the
        same icon."""
        text = _read(APP_SOURCE)
        match = re.search(
            r"\.accessibilityLabel\((\w+)\)\s+.*?\.help\((\w+)\)",
            text, re.DOTALL)
        self.assertIsNotNone(
            match, "could not find .accessibilityLabel(...)/.help(...) "
                   "on the menu-bar icon")
        self.assertEqual(match.group(1), match.group(2))
