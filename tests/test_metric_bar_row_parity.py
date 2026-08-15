"""MetricBarRow's row geometry, its heading text and the pace caret's math
exist twice, in two languages. Pin them together.

A metric row is three stacked lines now — name, bar, readout — and each gap
between them is duplicated as a SwiftUI frame/padding literal in
MetricBarRow.swift, alongside model.metric_title's twin (Metric.title) and
model.window_seconds/pace_fraction's (metricWindowSeconds/paceFraction) in
Models.swift. Same approach as test_model_parity.py and
test_popover_theme_parity.py: read the Swift as SOURCE TEXT, so a value or
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


class TestRowHeightAddsUp(unittest.TestCase):
    """Python-only, and the one number nothing else would catch.

    card_height() reserves ROW_H per metric while _card_body() lays out the
    three lines from their individual constants. If those disagree, every
    card is drawn a few points taller or shorter than the space reserved for
    it — the rows still look right, and the CARD quietly overlaps the one
    below or leaves a gap, on every platform at once.
    """

    def test_the_three_lines_and_two_gaps_sum_to_row_h(self):
        self.assertEqual(
            theme.ROW_HEAD_H + theme.ROW_HEAD_GAP + theme.BAR_H
            + theme.ROW_META_GAP + theme.ROW_META_H,
            theme.ROW_H)


class TestRowGeometryParity(SwiftPresent):
    """Every geometry number MetricBarRow.swift hardcodes as a SwiftUI
    literal, matched against its popover_theme.py twin. Each is anchored on
    the marker comment above it, so a regex cannot drift onto a different
    frame that happens to carry the same shape."""

    def test_the_name_line_matches_size_row_head_and_row_head_h(self):
        text = _read(ROW_SOURCE)
        match = re.search(
            r"// SIZE_ROW_HEAD / ROW_HEAD_H in the shared theme\.\s+"
            r"Text\(metric\.title\)\s+"
            r"\.font\(\.system\(size: ([\d.]+), weight: \.semibold\)\)",
            text)
        self.assertIsNotNone(match, "could not find the name line's font")
        self.assertEqual(float(match.group(1)), theme.SIZE_ROW_HEAD)
        height = re.search(
            r"\.frame\(height: ([\d.]+), alignment: \.leading\)", text)
        self.assertIsNotNone(height, "could not find the name line's frame")
        self.assertEqual(float(height.group(1)), theme.ROW_HEAD_H)

    def test_the_bar_matches_row_head_gap_and_bar_h(self):
        match = re.search(
            r"// ROW_HEAD_GAP / BAR_H in the shared theme\.\s+bar\s+"
            r"\.padding\(\.top, ([\d.]+)\)\s+\.frame\(height: ([\d.]+)\)",
            _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the bar's padding/frame")
        gap, height = (float(v) for v in match.groups())
        self.assertEqual(gap, theme.ROW_HEAD_GAP)
        self.assertEqual(height, theme.BAR_H)

    def test_the_readout_matches_row_meta_gap_and_row_meta_h(self):
        match = re.search(
            r"\.padding\(\.top, ([\d.]+)\)\s+\.frame\(height: ([\d.]+)\)\s+\}",
            _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the readout's frame")
        gap, height = (float(v) for v in match.groups())
        self.assertEqual(gap, theme.ROW_META_GAP)
        self.assertEqual(height, theme.ROW_META_H)

    def test_the_minimum_fill_is_the_bars_own_height(self):
        """The floor stops a 1%-used bar being a sliver its own corner
        radius clips away. It is BAR_H, not a constant that merely equalled
        it when written — BAR_H has already grown once."""
        match = re.search(r"\.frame\(width: max\(([\d.]+), "
                          r"geo\.size\.width \* fraction\)\)",
                          _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the fill's minimum width")
        self.assertEqual(float(match.group(1)), theme.BAR_H)

    def test_the_readout_font_matches_size_row_meta(self):
        match = re.search(
            r"\.font\(\.system\(size: ([\d.]+)\)\.monospacedDigit\(\)\)",
            _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the readout's font")
        self.assertEqual(float(match.group(1)), theme.SIZE_ROW_META)

    def test_the_name_and_readout_sizes_are_a_real_step_apart(self):
        """The inverse of what this file used to assert.

        The name and the readout were pinned EQUAL while they shared one
        line, because a size step between two words on the same line reads
        as a hierarchy that isn't there. They are now a heading over its
        data, so the step IS the hierarchy — and collapsing it back would
        silently undo the reason the line was split.
        """
        self.assertGreater(theme.SIZE_ROW_HEAD, theme.SIZE_ROW_META)

    def test_the_caret_matches_pace_w_and_is_drawn_as_the_notch(self):
        text = _read(ROW_SOURCE)
        match = re.search(
            r"Rectangle\(\)\s+\.fill\(palette\.pace\)\s+"
            r"\.frame\(width: ([\d.]+)\)", text)
        self.assertIsNotNone(match, "could not find the caret Rectangle")
        self.assertEqual(float(match.group(1)), theme.PACE_W)
        half = re.search(r"let half: CGFloat = ([\d.]+)", text)
        self.assertIsNotNone(half, "could not find the caret's half-width")
        self.assertEqual(float(half.group(1)) * 2, theme.PACE_W,
                         "the caret centres itself on half its own width; a "
                         "stale half puts the notch off-centre by a hair at "
                         "every pace but 50%")


class TestMetricTitleParity(SwiftPresent):
    """model.metric_title's Swift twin. Structural rather than a value
    comparison — there is no Swift to execute here — so it pins the actual
    rules Metric.title encodes, not a string that happens to agree today."""

    def test_python_titles_every_key_shape_the_ramp_can_produce(self):
        # (key, label as cswap actually sets it, expected heading). The
        # label matters: a scoped bucket's key is "scoped:Fable" but its
        # label is already the model's name, and metric_title's whole
        # contract for those is to defer to it rather than parse the key.
        cases = [
            ("5h", "5h", "5-hour"),
            ("7d", "7d", "Weekly"),
            ("3d", "3d", "3-day"),
            ("24h", "24h", "24-hour"),
            ("spend", "Spend", "Spend"),
            ("scoped:Fable", "Fable", "Fable"),
            ("", "", ""),
            # Anything the rules do not recognise keeps cswap's own label
            # rather than being mangled by a rule not written for it.
            ("weekly", "Weekly window", "Weekly window"),
        ]
        for key, label, expected in cases:
            with self.subTest(key=key):
                metric = model.Metric(key=key, label=label, short=key,
                                      pct=0.0)
                self.assertEqual(model.metric_title(metric), expected)

    def test_the_swift_encodes_the_same_four_rules(self):
        text = _read(MODELS_SOURCE)
        for fragment in ('trimmed.hasPrefix("scoped:")',
                         'trimmed == "spend"',
                         r'#"^(\d+)([hd])$"#',
                         'amount == 7 ? "Weekly"',
                         '"\\(amount)-day"',
                         '"\\(amount)-hour"'):
            with self.subTest(rule=fragment):
                self.assertIn(fragment, text,
                              "Metric.title dropped a rule model.metric_title "
                              "still applies — the Mac would head a row with "
                              "a different word from every other front-end")


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
