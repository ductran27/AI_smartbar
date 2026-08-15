"""MetricBarRow's row geometry and the pace caret's math exist twice, in two
languages. Pin them together.

Stage 02 gave every metric row a second line (the bar, now the card's full
inner width) and a pace caret, and duplicated a handful of new numbers to do
it: ROW_LABEL_GAP/BAR_H/PACE_W/PACE's alpha as SwiftUI frame/opacity
literals in MetricBarRow.swift, and window_seconds/pace_fraction's whole
formula as metricWindowSeconds/Metric.paceFraction in Models.swift. Same
approach as test_model_parity.py and test_popover_theme_parity.py: read the
Swift as SOURCE TEXT, so a value or formula drifting on one side fails the
ordinary unit suite with no Swift toolchain and no Xcode required.
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
ROW_SOURCE = os.path.join(SWIFT_DIR, "MetricBarRow.swift")
MODELS_SOURCE = os.path.join(SWIFT_DIR, "Models.swift")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (ROW_SOURCE, MODELS_SOURCE):
            if not os.path.exists(path):
                raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestRowGeometryParity(SwiftPresent):
    """Every stage-02 geometry number MetricBarRow.swift hardcodes as a
    SwiftUI literal, matched against its popover_theme.py twin."""

    def test_label_line_gap_matches_row_label_gap(self):
        match = re.search(
            r"VStack\(alignment: \.leading, spacing: ([\d.]+)\)",
            _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the row's VStack spacing")
        self.assertEqual(float(match.group(1)), theme.ROW_LABEL_GAP)

    def test_bar_height_matches_bar_h(self):
        match = re.search(r"\.frame\(height: ([\d.]+)\)", _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the bar's frame(height:)")
        self.assertEqual(float(match.group(1)), theme.BAR_H)

    def test_label_column_width_matches_label_w(self):
        match = re.search(r"\.frame\(width: ([\d.]+), alignment: \.leading\)",
                          _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the label's frame(width:)")
        self.assertEqual(float(match.group(1)), theme.LABEL_W)

    def test_label_and_value_font_sizes_match_the_shared_type_scale(self):
        """The one drift a geometry-only parity test misses.

        Stage 01 dropped SIZE_ROW_LABEL 11.0 -> 10.5 precisely so the label
        and the value, which stage 02 put on the SAME line, would share one
        optical size. Nothing pinned the Swift, so it kept its 11pt label
        and the Mac alone grew a half-point size step between two words on
        one line — a hierarchy the design says is not there, invisible to a
        suite that only checked frames and spacings.
        """
        text = _read(ROW_SOURCE)
        label = re.search(
            r"\.font\(\.system\(size: ([\d.]+), weight: \.bold\)\)", text)
        value = re.search(
            r"\.font\(\.system\(size: ([\d.]+)\)\.monospacedDigit\(\)\)", text)
        self.assertIsNotNone(label, "could not find the row label's font")
        self.assertIsNotNone(value, "could not find the row value's font")
        self.assertEqual(float(label.group(1)), theme.SIZE_ROW_LABEL)
        self.assertEqual(float(value.group(1)), theme.SIZE_ROW_VALUE)
        self.assertEqual(theme.SIZE_ROW_LABEL, theme.SIZE_ROW_VALUE,
                         "the row label and row value share a line, so they "
                         "must share a size — retuning one without the other "
                         "reintroduces the step this test exists to catch")

    def test_label_value_gap_matches_bar_gap(self):
        match = re.search(r"Spacer\(minLength: ([\d.]+)\)", _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the label/value Spacer")
        self.assertEqual(float(match.group(1)), theme.BAR_GAP)

    def test_value_subcolumns_match_pct_and_countdown_widths(self):
        text = _read(ROW_SOURCE)
        pct = re.search(
            r"// VALUE_PCT_W in the shared theme\.\s+\.frame\(width: "
            r"([\d.]+), alignment: \.trailing\)", text)
        countdown = re.search(
            r"// VALUE_COUNTDOWN_W in the shared theme\.\s+\.frame\(width: "
            r"([\d.]+), alignment: \.trailing\)", text)
        self.assertIsNotNone(pct, "could not find the pct trailing frame")
        self.assertIsNotNone(countdown,
                             "could not find the countdown trailing frame")
        self.assertEqual(float(pct.group(1)), theme.VALUE_PCT_W)
        self.assertEqual(float(countdown.group(1)), theme.VALUE_COUNTDOWN_W)

    def test_the_caret_matches_pace_w_and_pace_alpha(self):
        match = re.search(
            r"Rectangle\(\)\s+\.fill\(Color\.white\.opacity\(([\d.]+)\)\)\s+"
            r"\.frame\(width: ([\d.]+)\)", _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the caret Rectangle")
        alpha, width = match.groups()
        self.assertEqual(float(width), theme.PACE_W)
        self.assertEqual(float(alpha), theme.PACE[3])


class TestPaceFractionParity(SwiftPresent):
    """model.window_seconds/pace_fraction's Swift twins, read as source text
    so this pins the actual regex and multipliers Models.swift hardcodes —
    not just a value that happens to agree with them today."""

    def _swift_window_seconds(self, key: str):
        text = _read(MODELS_SOURCE)
        pattern_match = re.search(r'key\.range\(of: #"(.+?)"#', text)
        self.assertIsNotNone(pattern_match,
                             "could not find metricWindowSeconds's regex")
        multiplier_match = re.search(
            r'return amount \* \(key\.hasSuffix\("d"\) \? ([\d.]+) : '
            r'([\d.]+)\)', text)
        self.assertIsNotNone(multiplier_match,
                             "could not find the day/hour multipliers")
        day_seconds, hour_seconds = (float(v)
                                     for v in multiplier_match.groups())
        found = re.match(pattern_match.group(1), key)
        if not found:
            return None
        return float(found.group(1)) * (day_seconds if key.endswith("d")
                                        else hour_seconds)

    def test_every_key_model_py_recognises_matches_the_swift_regex_too(self):
        for key in ("5h", "7d", "3d", "2h", "24h", "10d", "spend",
                   "scoped:Fable", "", "weekly"):
            with self.subTest(key=key):
                self.assertEqual(
                    self._swift_window_seconds(key), model.window_seconds(key),
                    f"window_seconds({key!r}) disagrees between model.py "
                    "and Models.swift")

    def test_the_scraper_actually_found_the_regex_and_multipliers(self):
        # Guard against a rename turning the test above into ten
        # comparisons of None to None — green, and worthless.
        self.assertIsNotNone(self._swift_window_seconds("5h"))

    def test_pace_fraction_keeps_model_pys_clamp_and_past_reset_guard(self):
        # Structural, not a value comparison: the clamped formula and the
        # past-reset early-out have to be PRESENT, mirroring
        # model.pace_fraction's own `if remaining <= 0: return None` and
        # `min(max(1.0 - remaining / window, 0.0), 1.0)`.
        text = _read(MODELS_SOURCE)
        self.assertIn("guard remaining > 0 else { return nil }", text)
        self.assertIn("min(max(1 - remaining / window, 0), 1)", text)


if __name__ == "__main__":
    unittest.main()
