"""The optional macOS menu-bar text label exists twice: model.menu_bar_label
(Python, the source of truth and the shape a Linux tray would later reuse)
and Account.menuBarLabel (SwiftUI, what actually renders today). Pin them
together, plus the settings wiring that must agree on the same literals.

Three things drift here, and each shows the wrong thing to a user rather
than crashing:

* the mode/source STRINGS ("off"/"percentLeft"/"reset", "5h"/"7d") are a
  cross-language protocol — the @AppStorage value the menu writes is the
  same string this function branches on — so a rename on one side silently
  turns the label off;
* the percent-left FORMULA (100 − used) and the reset countdown must match
  the model, or macOS and a future painted UI disagree about the number;
* "off by default" is the whole promise of the feature (opt-in, never
  forced), so the stored default is pinned in both files that declare it.

Same technique as tests/test_account_card_parity.py: read the Swift as
SOURCE TEXT, so a drift fails the ordinary unit suite with no Swift
toolchain required. The behaviour of the Python side itself is covered by
tests/test_model.py::TestMenuBarLabel.
"""
from __future__ import annotations

import os
import unittest

import smartbar
from smartbar.core import model

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
MODELS_SOURCE = os.path.join(SWIFT_DIR, "Models.swift")
APP_SOURCE = os.path.join(SWIFT_DIR, "AISmartbarApp.swift")
MENU_SOURCE = os.path.join(SWIFT_DIR, "AppOptionsMenu.swift")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(MODELS_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestLabelLogicParity(SwiftPresent):
    def _swift_body(self) -> str:
        text = _read(MODELS_SOURCE)
        start = text.index("func menuBarLabel(mode: String, source: String")
        return text[start:text.index("func menuBarLabelStatus")]

    def test_the_model_uses_the_protocol_literals(self):
        # The Python constants ARE the strings Swift stores and branches on.
        self.assertEqual(model.MENU_LABEL_OFF, "off")
        self.assertEqual(model.MENU_LABEL_PERCENT_LEFT, "percentLeft")
        self.assertEqual(model.MENU_LABEL_RESET, "reset")

    def test_swift_branches_on_the_same_mode_literals(self):
        body = self._swift_body()
        self.assertIn('if mode == "off" { return "" }', body)
        self.assertIn('case "percentLeft":', body)
        self.assertIn('case "reset":', body)

    def test_percent_left_formula_matches_the_model(self):
        # model.menu_bar_label: f"{max(0, 100 - used_pct(pct))}%"; Swift must
        # be the same 100-minus-used, clamped at 0, or the two platforms show
        # different numbers for the same account.
        body = self._swift_body()
        self.assertIn(r'return "\(max(0, 100 - metric.usedPct))%"', body)

    def test_reset_reuses_the_live_countdown(self):
        # Same live countdown the hover tooltip uses, so a menu-bar "resets
        # in" and the tooltip's can never disagree.
        body = self._swift_body()
        self.assertIn("return metric.liveCountdown(now: now)", body)


class TestSettingsWiring(SwiftPresent):
    """The picker writes @AppStorage; the label reads it. Both files must
    name the same keys and offer exactly the modes/sources the logic handles."""

    def test_app_and_menu_agree_on_the_storage_keys(self):
        for source in (APP_SOURCE, MENU_SOURCE):
            text = _read(source)
            self.assertIn('@AppStorage("menuBarLabel")', text)
            self.assertIn('@AppStorage("menuBarSource")', text)
            self.assertIn('@AppStorage("menuBarTint")', text)

    def test_the_default_is_off_in_both_places(self):
        # The feature is opt-in — it must ship OFF, wherever the default is
        # declared, so the menu bar never changes until the user asks.
        for source in (APP_SOURCE, MENU_SOURCE):
            self.assertRegex(
                _read(source),
                r'@AppStorage\("menuBarLabel"\)[^\n]*=\s*"off"',
                f"menu-bar label must default to off in {os.path.basename(source)}")

    def test_the_picker_offers_exactly_the_handled_modes(self):
        text = _read(MENU_SOURCE)
        for tag in ("off", "percentLeft", "reset"):
            self.assertIn(f'.tag("{tag}")', text)

    def test_the_source_picker_offers_five_hour_and_weekly(self):
        text = _read(MENU_SOURCE)
        self.assertIn('.tag("5h")', text)
        self.assertIn('.tag("7d")', text)

    def test_the_label_reads_the_stored_mode_and_source(self):
        text = _read(APP_SOURCE)
        self.assertIn("store.menuBarText(mode: menuBarLabel, source: menuBarSource)",
                      text)


if __name__ == "__main__":
    unittest.main()
