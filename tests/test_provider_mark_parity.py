"""Stage 04's icon language exists twice, in two languages. Pin them
together.

ProviderMark.swift is the Swift twin of popover_draw.py's four drawing
functions (claude/openai/pause/warn), and two call sites —
PopoverView.tabButton and AccountCardView's blocked state line — each
duplicate a "glyph beside a line of text" size as a SwiftUI frame/spacing
literal: TAB_MARK/TAB_MARK_GAP for the tab marks, INLINE_ICON/
INLINE_ICON_GAP for the other. Same approach as test_metric_bar_row_parity.py and
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

# The kinds stage 04 added on the Python side (see
# popover_draw._GLYPH_DRAWERS) that have no SF Symbol equivalent and so get
# a drawn Swift twin. "refresh"/"close"/"power"/"quit" stay SF-Symbol-driven
# on macOS — they are not part of this parity claim. "clock" was one of
# these until the metric row's countdown became words ("resets in 1h 37m"),
# which say what a clock glyph beside them would only repeat.
NEW_KINDS = {"claude", "openai", "pause", "warn"}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _provider_mark_tails(text: str) -> list[str]:
    """The source that FOLLOWS each `ProviderMark(...)` call in `text`, from
    just after the call's own closing paren onward — enough for a caller to
    see what modifier comes next. Parens are balanced rather than matched
    with `[^)]*`, so an argument list that ever gains a nested call still
    ends at the right place. `\\b` keeps a hypothetical `MyProviderMark(`
    from counting."""
    tails = []
    for m in re.finditer(r"\bProviderMark\(", text):
        depth, j = 0, m.end() - 1        # start on the call's own "("
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        tails.append(text[j + 1:])        # everything past the ")"
    return tails


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


class TestEveryProviderMarkIsFramed(SwiftPresent):
    """ProviderMark's body is a bare `GeometryReader` — greedy, it fills
    whatever space it is offered and reports SwiftUI's own flexible size,
    the same shape the account list's collapsing `ScrollView` had (fixed in
    v1.3.11). It reads as a fixed-size icon ONLY because every call site
    pins it with a `.frame`; drop one and that mark balloons to fill its
    container, warping the row around it.

    The two current sites are pinned by NUMBER below (TAB_MARK, INLINE_ICON).
    This is the complementary guard: it catches a THIRD site added without
    any frame at all — the failure those number-pinned tests can't see
    because they only look where a mark is already known to be. Frame-first
    is the convention: a mark needs its size before any other modifier."""

    def test_every_call_site_is_immediately_framed(self):
        seen = 0
        for name in sorted(os.listdir(SWIFT_DIR)):
            if not name.endswith(".swift"):
                continue
            for tail in _provider_mark_tails(_read(os.path.join(SWIFT_DIR,
                                                                 name))):
                seen += 1
                self.assertRegex(
                    tail, r"\A\s*\.frame\(",
                    f"a ProviderMark(...) in {name} is not immediately "
                    "followed by .frame(...) — its greedy GeometryReader "
                    "will fill its container instead of staying an icon")
        # Never let the scan pass by finding nothing: the tab mark and the
        # blocked-line warn mark must both still be here (same floor the
        # card-gap parity test keeps).
        self.assertGreaterEqual(
            seen, 2, "expected at least the tab and blocked-line ProviderMarks")


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

    def test_the_gap_beside_the_mark_matches_tab_mark_gap(self):
        """An HStack: the mark sits BESIDE its label, so TAB_MARK_GAP is a
        horizontal gap. Pinning the container type as well as the number is
        deliberate — a stacked variant was tried and reverted, and flipping
        it back would leave the spacing "correct" while the tab looked
        nothing like the shared layout's."""
        match = re.search(
            r'HStack\(spacing: ([\d.]+)\) \{\s+ProviderMark\(kind: id\)',
            _read(POPOVER_SOURCE))
        self.assertIsNotNone(match, "could not find the tab HStack spacing")
        self.assertEqual(float(match.group(1)), theme.TAB_MARK_GAP)


class TestTheMetricRowWearsNoGlyph(SwiftPresent):
    """The countdown used to carry a clock mark. It says "resets in 1h 37m"
    now, which is the same claim in words — so the glyph would be repeating
    its own caption, and the row draws none at all. Pinned because the
    obvious "improvement" to a bare-looking caption is to put an icon back
    in front of it."""

    def test_the_row_draws_no_provider_mark(self):
        self.assertNotIn("ProviderMark", _read(ROW_SOURCE))

    def test_the_clock_is_gone_from_both_painters(self):
        # A kind nothing emits still draws when asked, so it survives as
        # code no test covers; both sides retire it together or neither.
        from smartbar.paint import popover_draw

        self.assertNotIn("clock", popover_draw._GLYPH_DRAWERS)
        self.assertNotIn('case "clock"', _read(MARK_SOURCE))


class TestBlockedWarnParity(SwiftPresent):
    """AccountCardView's blocked state line: the INLINE_ICON/
    INLINE_ICON_GAP pair, shared with the footer's pause mark rather than
    given its own constants, prefixing a warn mark instead of the
    "person.crop.circle.badge.exclamationmark" SF Symbol the blocked branch
    used before stage 04."""

    def test_the_warn_marks_frame_and_gap_match_inline_icon_constants(self):
        match = re.search(
            r'HStack\(alignment: \.top, spacing: ([\d.]+)\) \{\s+'
            r'ProviderMark\(kind: "warn"\)\s+\.frame\(width: ([\d.]+), '
            r'height: ([\d.]+)\)', _read(CARD_SOURCE))
        self.assertIsNotNone(match, "could not find the blocked line's warn mark")
        gap, width, height = (float(v) for v in match.groups())
        self.assertEqual(gap, theme.INLINE_ICON_GAP)
        self.assertEqual(width, theme.INLINE_ICON)
        self.assertEqual(height, theme.INLINE_ICON)

    def test_the_old_sf_symbol_no_longer_backs_the_blocked_branch(self):
        # Regression guard: this exact string used to be the systemImage
        # for BOTH branches (blocked and not); stage 04 must have moved it
        # off the blocked one specifically, onto ProviderMark.
        self.assertNotIn("person.crop.circle.badge.exclamationmark",
                         _read(CARD_SOURCE))


if __name__ == "__main__":
    unittest.main()
