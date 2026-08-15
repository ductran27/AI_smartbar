"""Stage 04's icon language exists twice, in two languages. Pin them
together.

ProviderMark.swift is the Swift twin of popover_draw.py's five new drawing
functions (claude/openai/clock/pause/warn), and three call sites —
PopoverView.tabButton, MetricBarRow's countdown, AccountCardView's blocked
state line — each duplicate a "glyph beside a line of text" size as a
SwiftUI frame/spacing literal: TAB_MARK/TAB_MARK_GAP for the tab marks,
COUNTDOWN_ICON/COUNTDOWN_ICON_GAP (shared by the clock and warn marks) for
the other two. Same approach as test_metric_bar_row_parity.py and
test_account_card_parity.py: read the Swift as SOURCE TEXT, so a value
drifting on one side fails the ordinary unit suite with no Swift toolchain
and no Xcode required.
"""
from __future__ import annotations

import os
import re
import unittest

import smartbar
from smartbar.core import popover_theme as theme

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
MARK_SOURCE = os.path.join(SWIFT_DIR, "ProviderMark.swift")
POPOVER_SOURCE = os.path.join(SWIFT_DIR, "PopoverView.swift")
ROW_SOURCE = os.path.join(SWIFT_DIR, "MetricBarRow.swift")
CARD_SOURCE = os.path.join(SWIFT_DIR, "AccountCardView.swift")

# The five kinds stage 04 added on the Python side (see
# popover_draw._GLYPH_DRAWERS) that have no SF Symbol equivalent and so get
# a drawn Swift twin. "refresh"/"close"/"power"/"quit" stay SF-Symbol-driven
# on macOS — they are not part of this parity claim.
NEW_KINDS = {"claude", "openai", "clock", "pause", "warn"}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (MARK_SOURCE, POPOVER_SOURCE, ROW_SOURCE, CARD_SOURCE):
            if not os.path.exists(path):
                raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestProviderMarkDrawsEveryNewKind(SwiftPresent):
    """ProviderMark.swift is meant to draw every kind popover_draw.py's
    dispatch added, not a subset of them — a kind missing here would not
    fail loudly, it would just render as nothing (Path()'s empty default),
    the SwiftUI-side twin of the "silently paints as power" trap
    tests/test_popover_draw.py guards on the cairo side.
    """

    def test_every_new_kind_appears_as_a_case_in_the_mark(self):
        text = _read(MARK_SOURCE)
        cases = set(re.findall(r'case "(\w+)":', text))
        missing = NEW_KINDS - cases
        self.assertEqual(missing, set(),
                         f"{missing} have no case in ProviderMark.swift")

    def test_it_draws_from_paths_not_sf_symbols(self):
        # The whole point of this file (see its header) is that Linux's
        # cairo painter can reproduce the same shape — an SF Symbol
        # anywhere in here would defeat that.
        text = _read(MARK_SOURCE)
        self.assertNotIn("systemImage", text)
        self.assertNotIn("Image(systemName", text)


class TestTabMarkParity(SwiftPresent):
    """PopoverView.tabButton's mark: TAB_MARK sizes the Glyph/ProviderMark,
    TAB_MARK_GAP is the HStack spacing before the label."""

    def test_the_marks_frame_matches_tab_mark(self):
        match = re.search(
            r'ProviderMark\(kind: id\)\s+\.frame\(width: ([\d.]+), '
            r'height: ([\d.]+)\)', _read(POPOVER_SOURCE))
        self.assertIsNotNone(match, "could not find the tab mark's frame")
        width, height = (float(v) for v in match.groups())
        self.assertEqual(width, theme.TAB_MARK)
        self.assertEqual(height, theme.TAB_MARK)

    def test_the_gap_before_the_label_matches_tab_mark_gap(self):
        match = re.search(
            r'HStack\(spacing: ([\d.]+)\) \{\s+ProviderMark\(kind: id\)',
            _read(POPOVER_SOURCE))
        self.assertIsNotNone(match, "could not find the tab HStack spacing")
        self.assertEqual(float(match.group(1)), theme.TAB_MARK_GAP)


class TestCountdownIconParity(SwiftPresent):
    """MetricBarRow's clock mark: COUNTDOWN_ICON/COUNTDOWN_ICON_GAP as a
    frame/spacing literal pair."""

    def test_the_clocks_frame_and_gap_match_countdown_icon_constants(self):
        match = re.search(
            r'HStack\(spacing: ([\d.]+)\) \{\s+if !countdown\.isEmpty \{\s+'
            r'ProviderMark\(kind: "clock"\)\s+\.frame\(width: ([\d.]+), '
            r'height: ([\d.]+)\)', _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the countdown's clock mark")
        gap, width, height = (float(v) for v in match.groups())
        self.assertEqual(gap, theme.COUNTDOWN_ICON_GAP)
        self.assertEqual(width, theme.COUNTDOWN_ICON)
        self.assertEqual(height, theme.COUNTDOWN_ICON)


class TestBlockedWarnParity(SwiftPresent):
    """AccountCardView's blocked state line: the same COUNTDOWN_ICON/
    COUNTDOWN_ICON_GAP pair, reused rather than given its own constants,
    prefixing a warn mark instead of the "person.crop.circle.badge.
    exclamationmark" SF Symbol the blocked branch used before stage 04."""

    def test_the_warn_marks_frame_and_gap_match_countdown_icon_constants(self):
        match = re.search(
            r'HStack\(alignment: \.top, spacing: ([\d.]+)\) \{\s+'
            r'ProviderMark\(kind: "warn"\)\s+\.frame\(width: ([\d.]+), '
            r'height: ([\d.]+)\)', _read(CARD_SOURCE))
        self.assertIsNotNone(match, "could not find the blocked line's warn mark")
        gap, width, height = (float(v) for v in match.groups())
        self.assertEqual(gap, theme.COUNTDOWN_ICON_GAP)
        self.assertEqual(width, theme.COUNTDOWN_ICON)
        self.assertEqual(height, theme.COUNTDOWN_ICON)

    def test_the_old_sf_symbol_no_longer_backs_the_blocked_branch(self):
        # Regression guard: this exact string used to be the systemImage
        # for BOTH branches (blocked and not); stage 04 must have moved it
        # off the blocked one specifically, onto ProviderMark.
        self.assertNotIn("person.crop.circle.badge.exclamationmark",
                         _read(CARD_SOURCE))


if __name__ == "__main__":
    unittest.main()
