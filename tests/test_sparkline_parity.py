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


if __name__ == "__main__":
    unittest.main()
