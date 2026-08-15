"""AccountCardView's card chrome and badge chip exist twice, in two
languages. Pin them together.

Stage 03 replaced the active card's 1.5pt pure-white outline with a hairline
border shared by every card, and moved the plan/device badge off the header
string into its own micro-chip — behaviours duplicated as SwiftUI literals
in AccountCardView.swift: the hairline (CARD_BORDER) and the badge
composition (account_badge). The leading rail that briefly marked the active
card lived here too; it is gone, and TestNoActiveCardChrome keeps it gone in
both languages at once.
Same approach as test_popover_theme_parity.py and
test_metric_bar_row_parity.py: read the Swift as SOURCE TEXT, so a value or
formula drifting on one side fails the ordinary unit suite with no Swift
toolchain and no Xcode required.
"""
from __future__ import annotations

import os
import re
import unittest

import smartbar
from smartbar.core import model

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
CARD_SOURCE = os.path.join(SWIFT_DIR, "AccountCardView.swift")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CARD_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestHairlineBorderParity(SwiftPresent):
    """Every card — active or not — shares one hairline (card_border); the
    old account.active ? 0.92 : 0.07 branch is gone.

    The alpha itself moved into the Palette when the panel gained a light
    appearance (a white-alpha literal is invisible on white), so it is
    pinned by test_popover_theme_parity.py now. What is left to pin HERE is
    that the card takes it from the palette at all, rather than reintroducing
    a hardcoded colour that only happens to look right in one appearance.
    """

    def test_every_card_strokes_the_palettes_hairline(self):
        match = re.search(
            r"strokeBorder\(palette\.cardBorder, lineWidth: ([\d.]+)\)",
            _read(CARD_SOURCE))
        self.assertIsNotNone(match, "could not find the card's strokeBorder")
        self.assertEqual(float(match.group(1)), 1.0)

    def test_the_card_ground_is_the_palettes_too(self):
        """The pace notch is drawn as this exact colour showing through a
        bar, so a card that went back to `.thinMaterial` (or any other
        ground) would leave the notch painting a near-miss grey stripe."""
        self.assertIn(".fill(palette.cardBG)", _read(CARD_SOURCE))


class TestNoActiveCardChrome(SwiftPresent):
    """The active card carries no chrome of its own in EITHER language —
    the ACTIVE chip states it in words.

    This is a "stayed deleted" guard rather than a value comparison. Two
    marks have been tried and removed here (a 1.5pt pure-white outline,
    then a leading rail), and either could plausibly come back on one side
    only: SwiftUI makes an active-gated overlay a two-line change, while
    the cairo painter would need a new Box — so the two renderers would
    disagree about what an active card even looks like, on a difference no
    colour or geometry test would notice.
    """

    def test_no_active_gated_leading_overlay_remains(self):
        text = _read(CARD_SOURCE)
        self.assertNotIn(".overlay(alignment: .leading)", text)
        self.assertNotIn("palette.rail", text)


class TestBadgeChipParity(SwiftPresent):
    """accountBadge's composition, read as source text and cross-checked
    against model.account_badge for the same matrix of (plan, devices)
    TestAccountBadge/TestAccountLabelComposition already cover in
    tests/test_model.py — not just a value that happens to agree today."""

    def test_the_guard_and_ternary_are_present_verbatim(self):
        text = _read(CARD_SOURCE)
        self.assertIn("guard devices > 0 else { return plan }", text)
        self.assertIn(
            'return plan.isEmpty ? "(\\(devices))" : "\\(plan) (\\(devices))"',
            text)

    def _swift_badge(self, plan: str, devices: int) -> str:
        """Executes the exact algorithm pinned above, in Python, so a value
        drifting between the two languages fails here rather than only at
        runtime on a Mac neither CI nor this sandbox can build."""
        if devices <= 0:
            return plan
        return f"({devices})" if not plan else f"{plan} ({devices})"

    def test_swift_badge_matches_model_account_badge(self):
        for plan, devices in (("", 0), ("Pro", 0), ("", 2), ("20x", 2)):
            with self.subTest(plan=plan, devices=devices):
                acct = model.Account(number=1, email="a@x.com")
                acct.plan, acct.devices = plan, devices
                self.assertEqual(self._swift_badge(plan, devices),
                                 model.account_badge(acct))

    def test_the_chip_sits_before_the_active_chip_and_make_active_button(self):
        text = _read(CARD_SOURCE)
        header = text[text.index("private var cardHeader"):
                      text.index("private var confirmHeader")]
        # planBadge must appear after the remove (x) button's `if` block and
        # before both the ACTIVE chip and the Make Active button — "sits
        # immediately left of" either one. rindex, not index: an earlier
        # comment on this same header also mentions "planBadge" by name.
        badge_at = header.rindex("planBadge")
        active_at = header.index('Text("ACTIVE")')
        make_active_at = header.index('Button("Make Active")')
        self.assertLess(badge_at, active_at)
        self.assertLess(badge_at, make_active_at)


if __name__ == "__main__":
    unittest.main()
