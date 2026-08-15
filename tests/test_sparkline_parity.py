"""The 30-day usage-history strip's geometry and wording exist twice, in two
languages. Pin them together.

Stage 06 duplicated three new geometry numbers as SwiftUI literals in
Sparkline.swift (STRIP_H/STRIP_BAR_W/STRIP_GAP) plus two header strings
verbatim. Same approach as test_metric_bar_row_parity.py and
test_account_card_parity.py: read the Swift as SOURCE TEXT, so a value or
string drifting on one side fails the ordinary unit suite with no Swift
toolchain and no Xcode required.
"""
from __future__ import annotations

import os
import re
import unittest

import smartbar
from smartbar.core import popover_theme as theme

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
SPARKLINE_SOURCE = os.path.join(SWIFT_DIR, "Sparkline.swift")
OVERVIEW_SOURCE = os.path.join(SWIFT_DIR, "OverviewView.swift")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (SPARKLINE_SOURCE, OVERVIEW_SOURCE):
            if not os.path.exists(path):
                raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestStripGeometryParity(SwiftPresent):
    """STRIP_H/STRIP_BAR_W/STRIP_GAP as SwiftUI frame/spacing literals."""

    def test_bar_column_height_matches_strip_h(self):
        match = re.search(r"let height = max\(1, ([\d.]+) \* fraction\)",
                          _read(SPARKLINE_SOURCE))
        self.assertIsNotNone(match, "could not find the bar's height formula")
        self.assertEqual(float(match.group(1)), theme.STRIP_H)

    def test_bars_row_frame_also_matches_strip_h(self):
        match = re.search(r"\.frame\(height: ([\d.]+)\)\s*\}\s*\.padding",
                          _read(SPARKLINE_SOURCE))
        self.assertIsNotNone(match, "could not find the bars row's own frame")
        self.assertEqual(float(match.group(1)), theme.STRIP_H)

    def test_bar_width_matches_strip_bar_w(self):
        text = _read(SPARKLINE_SOURCE)
        widths = set(re.findall(r"\.frame\(width: ([\d.]+), height:", text))
        self.assertEqual(widths, {str(theme.STRIP_BAR_W)})

    def test_bar_gap_matches_strip_gap(self):
        match = re.search(
            r"HStack\(alignment: \.bottom, spacing: ([\d.]+)\)",
            _read(SPARKLINE_SOURCE))
        self.assertIsNotNone(match, "could not find the bars' HStack spacing")
        self.assertEqual(float(match.group(1)), theme.STRIP_GAP)

    def test_null_day_stub_is_one_point_tall(self):
        self.assertIn(f"width: {theme.STRIP_BAR_W}, height: 1)",
                      _read(SPARKLINE_SOURCE))


class TestTodayIsChalkParity(SwiftPresent):
    """TODAY draws in Palette.chalk (== theme.TEXT), never the status ramp —
    the same rule popover_layout._strip_card pins on the Python side."""

    def test_today_branch_uses_chalk_not_the_status_ramp(self):
        match = re.search(
            r"\.fill\(isToday \? Palette\.chalk\s*\n?\s*"
            r": Thresholds\.status\(forUsedPct: value\)\.color\)",
            _read(SPARKLINE_SOURCE))
        self.assertIsNotNone(match,
                             "could not find the isToday ? chalk : ramp fill")


class TestHeaderWordingParity(SwiftPresent):
    """The header line's two pieces of text are copied verbatim onto both
    sides — a wording change on one that isn't mirrored is exactly the kind
    of drift this file's siblings already guard against for tooltips."""

    def test_title_matches_python(self):
        py_source = _read(os.path.join(
            os.path.dirname(theme.__file__), "popover_layout.py"))
        self.assertIn('"Active account · 30 days"', py_source)
        self.assertIn('"Active account · 30 days"', _read(SPARKLINE_SOURCE))

    def test_caption_matches_python(self):
        py_source = _read(os.path.join(
            os.path.dirname(theme.__file__), "popover_layout.py"))
        self.assertIn('"7-day window, % used"', py_source)
        self.assertIn('"7-day window, % used"', _read(SPARKLINE_SOURCE))


class TestPathResolutionParity(SwiftPresent):
    """Sparkline.swift resolves usage-history.json the same way
    UpdateStatus.swift resolves update-state.json: SMARTBAR_CACHE_DIR, else
    ~/.cache/ai-smartbar — never a hardcoded path of its own."""

    def test_reads_the_cache_dir_env_override(self):
        self.assertIn('env["SMARTBAR_CACHE_DIR"]', _read(SPARKLINE_SOURCE))

    def test_falls_back_to_the_same_default_directory_as_update_status(self):
        update_status = _read(os.path.join(SWIFT_DIR, "UpdateStatus.swift"))
        fallback = re.search(
            r'appendingPathComponent\("(\.cache/ai-smartbar)"\)', update_status)
        self.assertIsNotNone(fallback, "UpdateStatus.swift's own fallback moved")
        self.assertIn(f'appendingPathComponent("{fallback.group(1)}")',
                      _read(SPARKLINE_SOURCE))

    def test_reads_the_exact_file_name_usage_history_records_to(self):
        self.assertIn(f'"{"usage-history.json"}"', _read(SPARKLINE_SOURCE))


class TestOverviewWiring(SwiftPresent):
    """SparklineCard is actually reachable from the Overview tab, keyed on
    the ACTIVE account, and gated the same way popover_layout gates the
    whole card on _history_present."""

    def test_overview_view_references_sparkline_card(self):
        self.assertIn("SparklineCard(", _read(OVERVIEW_SOURCE))

    def test_gated_on_has_data_not_drawn_unconditionally(self):
        self.assertIn("SparklineCard.hasData(history)", _read(OVERVIEW_SOURCE))

    def test_keyed_on_the_active_account(self):
        self.assertIn("store.snapshot?.activeAccount", _read(OVERVIEW_SOURCE))


class TestOverviewGeometryParity(SwiftPresent):
    """Every OVERVIEW_* number OverviewView.swift hardcodes as a SwiftUI
    literal, matched against its popover_theme.py twin.

    TestOverviewWiring above only checks that the right VIEWS are reachable
    from this file — it never looks at a single dimension, so all six of
    these could drift while the whole suite stayed green and the Overview
    tab quietly stopped matching the account cards on the same panel.
    """

    #: (theme attribute, regex capturing the Swift literal). Kept as data
    #: so a missing pin is a one-line addition rather than a new method,
    #: and so the "found something" guard below can check all of them.
    PINS = (
        ("OVERVIEW_ROW_GAP", r"VStack\(spacing: ([\d.]+)\) \{\s*\n\s*ForEach"),
        ("OVERVIEW_GAP", r"HStack\(spacing: ([\d.]+)\) \{\s*\n\s*ProviderMark"),
        ("OVERVIEW_MARK_W",
         r"ProviderMark\(kind: account\.provider\)\s*\n\s*"
         r"\.frame\(width: ([\d.]+), height: [\d.]+\)"),
        ("OVERVIEW_BAR_W", r"\.frame\(width: ([\d.]+), height: 6\)"),
        ("OVERVIEW_PCT_W", r"\.frame\(width: ([\d.]+), alignment: \.trailing\)"),
        ("OVERVIEW_ROW_H", r"\.frame\(height: ([\d.]+)\)"),
    )

    def test_every_overview_dimension_matches_the_shared_theme(self):
        text = _read(OVERVIEW_SOURCE)
        for name, pattern in self.PINS:
            with self.subTest(constant=name):
                match = re.search(pattern, text)
                self.assertIsNotNone(
                    match, f"could not find the Swift literal for {name} — "
                           f"OverviewView.swift moved and this pin went blind")
                self.assertEqual(float(match.group(1)), getattr(theme, name))

    def test_the_scrapers_all_found_something(self):
        """Without this, renaming anything in OverviewView.swift turns the
        loop above into six skipped comparisons instead of six failures —
        the same trap test_model_parity.py guards against."""
        text = _read(OVERVIEW_SOURCE)
        found = [name for name, pattern in self.PINS
                 if re.search(pattern, text)]
        self.assertEqual(len(found), len(self.PINS),
                         f"only {found} matched; the rest are blind")

    def test_the_dataless_row_spans_the_bar_gap_and_percentage_columns(self):
        """The short state name replaces a bar AND a percentage, so its
        frame has to be the sum of all three — left as that literal sum in
        the Swift precisely so this can check the arithmetic rather than a
        pre-added magic number."""
        match = re.search(
            r"\.frame\(width: ([\d.]+) \+ ([\d.]+) \+ ([\d.]+), "
            r"alignment: \.trailing\)", _read(OVERVIEW_SOURCE))
        self.assertIsNotNone(match, "the data-less row's span moved")
        self.assertEqual(
            [float(g) for g in match.groups()],
            [theme.OVERVIEW_BAR_W, theme.OVERVIEW_GAP, theme.OVERVIEW_PCT_W])

    def test_the_row_caret_uses_the_same_half_width_as_the_metric_row(self):
        """OverviewView draws its own copy of the caret math rather than
        sharing MetricBarRow's, so PACE_W is duplicated a third time."""
        match = re.search(r"let half: CGFloat = ([\d.]+)", _read(OVERVIEW_SOURCE))
        self.assertIsNotNone(match, "could not find the Overview caret's half")
        self.assertEqual(float(match.group(1)), theme.PACE_W / 2)


class TestCardHairlineParity(SwiftPresent):
    """CARD_BORDER as a strokeBorder literal on EVERY card surface.

    Three files draw a card on this panel — AccountCardView, OverviewView
    and Sparkline — and each spells the hairline out for itself. Only the
    first was pinned, so retuning CARD_BORDER's alpha would have left the
    Overview and strip cards visibly stale beside the account cards.
    """

    SOURCES = ("AccountCardView.swift", "OverviewView.swift", "Sparkline.swift")

    def test_every_card_surface_strokes_the_same_hairline(self):
        for name in self.SOURCES:
            with self.subTest(source=name):
                text = _read(os.path.join(SWIFT_DIR, name))
                match = re.search(
                    r"\.strokeBorder\(Color\.white\.opacity\(([\d.]+)\), "
                    r"lineWidth: ([\d.]+)\)", text)
                self.assertIsNotNone(
                    match, f"{name} draws no hairline border any more")
                self.assertEqual(float(match.group(1)), theme.CARD_BORDER[3])
                self.assertEqual(float(match.group(2)), 1.0)


class TestStateSummaryParity(SwiftPresent):
    """Account.stateSummary must split on the same separator every
    STATE_TEXT entry is written with, or the Mac's Overview row shows a
    different word from every other front-end's."""

    def test_swift_splits_on_the_same_separator(self):
        self.assertIn('components(separatedBy: " — ")',
                      _read(os.path.join(SWIFT_DIR, "Models.swift")))

    def test_the_separator_really_is_what_state_text_uses(self):
        """Guards the pin above: if STATE_TEXT stopped using " — ", both
        sides would agree on a split that no longer splits anything."""
        from smartbar.core import model
        multi = [text for text in model.STATE_TEXT.values() if " — " in text]
        self.assertTrue(multi, "no STATE_TEXT entry uses ' — ' any more, so "
                               "state_summary splits nothing on either side")


if __name__ == "__main__":
    unittest.main()
