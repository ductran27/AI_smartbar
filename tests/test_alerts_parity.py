"""Lockout-alert escalation: source-scrape parity.

smartbar/core/alerts.py::AlertManager._build appends " — no accounts left"
to the notification title (and a fuller "you're on your own" body line)
when best_switch(snapshot) is None — i.e. every account is at/above the
red threshold, so there's nowhere left to switch to. UsageStore.swift's
checkAlerts is a hand-port of the same logic (macOS has no shared
TrayController/AlertManager path — see UsageStore.swift's own notify()
docstring). Pin that both sides keep the same escalation wording so a
future edit to one can't silently drift from the other.

Same technique as tests/test_plan.py::TestPlanParity and
tests/test_menubar_hover_parity.py: read the Swift as source text, so
this runs with no Swift toolchain.
"""
from __future__ import annotations

import os
import unittest

import smartbar

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
USAGE_STORE_SOURCE = os.path.join(SWIFT_DIR, "UsageStore.swift")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(USAGE_STORE_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestLockoutAlertEscalationParity(SwiftPresent):
    def test_check_alerts_escalates_title_when_no_switch_available(self):
        text = _read(USAGE_STORE_SOURCE)
        self.assertIn("func checkAlerts", text,
                       "expected checkAlerts(_:) to still exist")
        body_start = text.index("func checkAlerts")
        body = text[body_start:body_start + 2000]
        self.assertIn("no accounts left", body,
                       "macOS's hand-ported alert lost the lockout title "
                       "escalation that smartbar/core/alerts.py has")

    def test_check_alerts_still_falls_back_to_no_other_account_body(self):
        text = _read(USAGE_STORE_SOURCE)
        body_start = text.index("func checkAlerts")
        body = text[body_start:body_start + 2000]
        self.assertIn("No other account available", body)


if __name__ == "__main__":
    unittest.main()
