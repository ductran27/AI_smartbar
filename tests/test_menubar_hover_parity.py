"""macOS menu-bar icon hover tooltip: source-scrape parity.

Linux (linux/tray.py) and Windows (windows/tray.py) both get a native
hover tooltip on the tray icon for free, because TrayController calls
host.set_title(model.title_line(account)) on every meaningful repaint
(normal poll, account switch, the error path — see tray_controller.py's
_apply_snapshot/_apply_error) and both hosts' set_title implementations
(AppIndicator.set_title, pystray's icon.title) render natively as a hover
tooltip. macOS's SwiftUI MenuBarExtra label had no such thing: its
.accessibilityLabel(store.accessibilitySummary) is VoiceOver-only.

A .help(...) modifier is the obvious fix but is silently DROPPED on a
MenuBarExtra label — macOS renders only the underlying status-item button's
native toolTip — so the label pushes the tooltip straight to that button
instead, via StatusItemLocator.shared.updateTooltip(store.tooltipSummary).
VoiceOver keeps the concise `accessibilitySummary`; the hover tooltip gets
the roomier `tooltipSummary` (active account plus each window's countdown).

Same technique as tests/test_plan.py::TestPlanParity and
tests/test_account_card_parity.py: read the Swift as source text, so this
runs on Linux with no Swift toolchain, and pins that a future refactor of
the MenuBarExtra label can't silently drop the hover tooltip again.
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


class TestMenuBarHoverTooltipParity(SwiftPresent):
    def _label_body(self) -> str:
        """The MenuBarExtra label closure alone — from its `} label: {` to
        the `.menuBarExtraStyle` that closes the scene — so an assertion
        can't be satisfied by code elsewhere in the file."""
        text = _read(APP_SOURCE)
        self.assertIn("MenuBarExtra {", text,
                       "expected the MenuBarExtra scene to still exist")
        return text[text.index("} label: {"):text.index(".menuBarExtraStyle")]

    def test_menu_bar_extra_label_pushes_a_native_hover_tooltip(self):
        # .help() is a no-op on a MenuBarExtra label, so the tooltip is
        # pushed to the status-item button directly. Pin that it still is,
        # alongside the VoiceOver label it complements.
        label_body = self._label_body()
        self.assertIn("StatusItemLocator.shared.updateTooltip(", label_body,
                       "the menu-bar icon lost its hover tooltip — a "
                       "sighted user hovering it sees nothing again")
        self.assertIn(".accessibilityLabel(", label_body,
                       "VoiceOver label regressed — nothing left to "
                       "complement the hover tooltip")

    def test_voiceover_and_hover_read_the_stores_two_summaries(self):
        """VoiceOver reads the concise accessibilitySummary; the hover
        tooltip gets the roomier tooltipSummary. Both must come from the
        store, so the icon and its labels can never describe different
        usage than the panel does."""
        label_body = self._label_body()
        self.assertIn("store.accessibilitySummary", label_body)
        self.assertIn("store.tooltipSummary", label_body)
        # The VoiceOver label is the concise summary, not the tooltip's text.
        self.assertRegex(label_body, r"\.accessibilityLabel\(summary\)")
