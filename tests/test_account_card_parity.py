"""AccountCardView's card chrome and badge chip exist twice, in two
languages. Pin them together.

Stage 03 replaced the active card's 1.5pt pure-white outline with a hairline
border shared by every card plus a leading rail on the active one, and moved
the plan/device badge off the header string into its own micro-chip — three
new numbers/behaviours duplicated as SwiftUI literals in
AccountCardView.swift: the hairline's opacity (CARD_BORDER), the rail's
width/inset (RAIL_W/RAIL_INSET), and the badge composition (account_badge).
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
from smartbar.core import popover_theme as theme

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


class TestRailParity(SwiftPresent):
    """The active-card leading rail: RAIL_W/RAIL_INSET as SwiftUI frame/
    padding literals, filled from the appearance's own `rail` (which each
    Scheme points at its own `text`, pinned by
    test_popover_theme_parity.py).

    This mark has been removed and restored once. What made removal cheap
    to get wrong is the asymmetry: an active-gated overlay is a two-line
    change in SwiftUI, while the cairo painter needs a whole Box — so one
    renderer can gain or lose the rail alone, and the two would disagree
    about what an active card looks like with every colour and geometry
    number still matching. Pinning it in both languages is what makes that
    a failure instead of a difference nobody sees.
    """

    def test_rail_geometry_matches_rail_w_and_rail_inset(self):
        text = _read(CARD_SOURCE)
        match = re.search(
            r"RoundedRectangle\(cornerRadius: ([\d.]+), style: \.continuous\)\s+"
            r"\.fill\(palette\.rail\)\s+"
            r"\.frame\(width: ([\d.]+)\)\s+"
            r"\.padding\(\.vertical, ([\d.]+)\)", text)
        self.assertIsNotNone(match, "could not find the rail RoundedRectangle")
        corner_radius, width, inset = (float(v) for v in match.groups())
        self.assertEqual(width, theme.RAIL_W)
        self.assertEqual(corner_radius, theme.RAIL_W / 2)
        self.assertEqual(inset, theme.RAIL_INSET)

    def test_the_rail_is_gated_on_account_active(self):
        text = _read(CARD_SOURCE)
        overlay = text[text.index(".overlay(alignment: .leading)"):]
        self.assertIn("if account.active {", overlay[:120])


class TestBadgeChipParity(SwiftPresent):
    """accountBadge's composition, read as source text and cross-checked
    against model.account_badge for the same matrix of plans —
    TestAccountBadge/TestAccountLabelComposition already cover in
    tests/test_model.py — not just a value that happens to agree today."""

    def test_the_badge_is_just_the_plan(self):
        text = _read(CARD_SOURCE)
        self.assertIn("private var accountBadge: String { accountPlan }", text)

    def test_swift_badge_matches_model_account_badge(self):
        for plan in ("", "Pro", "20x"):
            with self.subTest(plan=plan):
                acct = model.Account(number=1, email="a@x.com")
                acct.plan = plan
                self.assertEqual(plan, model.account_badge(acct))

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
