"""The PANEL's own two numbers exist twice, in two languages. Pin them.

Every other geometry constant in the shared theme already has a parity
test reading its SwiftUI twin as source text (test_account_card_parity,
test_metric_bar_row_parity, test_provider_mark_parity). Two numbers had
none: the panel's own width, and how tall the card list may grow before it
scrolls. Both were therefore updated by hand on each of the two scale-ups
this table has been through, and nothing would have failed if either had
been missed — the macOS panel would simply have been a different SIZE from
the Linux and Windows ones, which is the single claim popover_theme.py
exists to make.

Same approach as its sibling files: read the Swift as SOURCE TEXT, so a
value drifting on one side fails the ordinary unit suite with no Swift
toolchain and no Xcode required.
"""
from __future__ import annotations

import os
import re
import unittest

import smartbar
from smartbar.core import popover_theme as theme

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
POPOVER_SOURCE = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                              "PopoverView.swift")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(POPOVER_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestPanelWidthParity(SwiftPresent):
    def test_the_panels_frame_matches_width(self):
        """The one number that makes the panel the same object on macOS,
        Linux and Windows rather than three lookalikes."""
        match = re.search(r"\.frame\(width: ([\d.]+)\)\s*\n\s*//",
                          _read(POPOVER_SOURCE))
        self.assertIsNotNone(match, "could not find the panel's frame")
        self.assertEqual(float(match.group(1)), theme.WIDTH)


class TestListMaxHeightParity(SwiftPresent):
    """listMaxHeight has no constant of its own in the shared theme — the
    painted front-ends cap the panel against the real SCREEN instead (see
    linux/popover_window._max_panel_height). So it is pinned to what its
    own comment claims it is: a cap that shows at least three full cards
    and starts scrolling before a fourth.

    Pinned as a RANGE rather than a value because that is the actual
    intent; a literal here would just be a fourth number to hand-update on
    the next scale-up, which is the very problem this file exists for.
    """

    def _three_metric_card(self) -> float:
        return (theme.CARD_PAD_V * 2 + theme.CARD_HEADER_H
                + theme.CARD_INNER_GAP + 3 * theme.ROW_H + 2 * theme.ROW_GAP)

    def _cap(self) -> float:
        match = re.search(r"listMaxHeight: CGFloat = ([\d.]+)",
                          _read(POPOVER_SOURCE))
        self.assertIsNotNone(match, "could not find listMaxHeight")
        return float(match.group(1))

    def test_the_cap_shows_at_least_three_full_cards(self):
        card = self._three_metric_card()
        self.assertGreaterEqual(self._cap(), 3 * card + 2 * theme.CARD_GAP)

    def test_the_cap_starts_scrolling_before_a_fourth_card(self):
        """Above this the panel is taller than it is useful — a menu-bar
        panel has to answer its question before you have finished opening
        it (see ROW_LABEL_H's comment in popover_theme.py)."""
        card = self._three_metric_card()
        self.assertLess(self._cap(), 4 * card + 3 * theme.CARD_GAP)

    def test_the_cap_and_its_scroll_threshold_move_together(self):
        """listScrollsPast is how many cards are worth wrapping in a
        ScrollView at all; listMaxHeight is how tall that ScrollView may
        get. A cap that no longer holds listScrollsPast cards means the
        list scrolls at a count the cap was never sized for — the drift
        the pair's own comment warns about."""
        text = _read(POPOVER_SOURCE)
        match = re.search(r"listScrollsPast = (\d+)", text)
        self.assertIsNotNone(match, "could not find listScrollsPast")
        cards = int(match.group(1))
        card = self._three_metric_card()
        self.assertLess(self._cap(),
                        cards * card + (cards - 1) * theme.CARD_GAP,
                        "the cap holds every card the list is willing to "
                        "show without scrolling, so it can never scroll")
