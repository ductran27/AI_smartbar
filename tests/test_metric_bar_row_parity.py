"""MetricBarRow's row geometry and the pace caret's math exist twice, in two
languages. Pin them together.

A metric row is two stacked lines — label/reset/pct, then the bar — and
every column and gap in it is duplicated as a SwiftUI frame/padding literal
in MetricBarRow.swift, alongside model.window_seconds/pace_fraction's twins
(metricWindowSeconds/paceFraction) in Models.swift. Same approach as
test_model_parity.py and test_popover_theme_parity.py: read the Swift as
SOURCE TEXT, so a value or formula drifting on one side fails the ordinary
unit suite with no Swift toolchain and no Xcode required.
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
    two lines from their individual constants. If those disagree, every card
    is drawn a few points taller or shorter than the space reserved for it —
    the rows still look right, and the CARD quietly overlaps the one below or
    leaves a gap, on every platform at once.
    """

    def test_the_two_lines_and_their_gap_sum_to_row_h(self):
        self.assertEqual(
            theme.ROW_LABEL_H + theme.ROW_LABEL_GAP + theme.BAR_H,
            theme.ROW_H)


class TestRowGeometryParity(SwiftPresent):
    """Every geometry number MetricBarRow.swift hardcodes as a SwiftUI
    literal, matched against its popover_theme.py twin."""

    def test_the_label_column_matches_size_row_label_and_label_w(self):
        text = _read(ROW_SOURCE)
        match = re.search(
            r"Text\(metric\.label\)\s+"
            r"\.font\(\.system\(size: ([\d.]+), weight: \.bold\)\)", text)
        self.assertIsNotNone(match, "could not find the label's font")
        self.assertEqual(float(match.group(1)), theme.SIZE_ROW_LABEL)
        width = re.search(
            r"\.frame\(width: ([\d.]+), alignment: \.leading\)", text)
        self.assertIsNotNone(width, "could not find the label's column")
        self.assertEqual(float(width.group(1)), theme.LABEL_W)

    def test_the_label_has_no_middle_truncation_override(self):
        """Text(metric.label) relies on SwiftUI's own default (.tail) — it
        is not the account-address line, the one Text in the app that opts
        into .truncationMode(.middle) (AccountCardView.swift). The painted
        front ends default Label.mode to "tail" to match; if this Text ever
        grows a .truncationMode(.middle) override, a long scoped name like
        "Bengalfox" would render "Be…ox" on macOS but keep "Beng…" on the
        painted platforms unless popover_layout.py's call site is updated
        to match — this test is the tripwire for that drift."""
        text = _read(ROW_SOURCE)
        # The slice ends at the label's OWN frame, spelled from LABEL_W
        # rather than hardcoded: this test is not about the column's width,
        # and a literal here means every future scale-up breaks a
        # truncation test for a reason that has nothing to do with
        # truncation (it has, twice).
        block = text[text.index("Text(metric.label)"):
                     text.index(f".frame(width: {theme.LABEL_W:g}")]
        self.assertNotIn("truncationMode", block)

    def test_both_gaps_on_the_label_line_match_bar_gap(self):
        """One number, used twice: a FIXED gap after the label column (the
        caption's leading padding) and the same value as the FLOOR before
        the percentage. If they drift apart the caption stops sitting a
        column's width from the name it qualifies."""
        text = _read(ROW_SOURCE)
        floor = re.search(r"Spacer\(minLength: ([\d.]+)\)", text)
        self.assertIsNotNone(floor, "could not find the label line's Spacer")
        self.assertEqual(float(floor.group(1)), theme.BAR_GAP)
        leading = re.search(r"\.padding\(\.leading, ([\d.]+)\)", text)
        self.assertIsNotNone(leading,
                             "could not find the caption's leading padding")
        self.assertEqual(float(leading.group(1)), theme.BAR_GAP)

    def test_the_row_stacks_its_two_lines_row_label_gap_apart(self):
        match = re.search(
            r"VStack\(alignment: \.leading, spacing: ([\d.]+)\)",
            _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the row's VStack")
        self.assertEqual(float(match.group(1)), theme.ROW_LABEL_GAP)

    def test_the_bars_height_matches_bar_h(self):
        match = re.search(r"\.frame\(height: ([\d.]+)\)\s+"
                          r"\.animation\(", _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the bar's frame")
        self.assertEqual(float(match.group(1)), theme.BAR_H)

    def test_the_minimum_fill_is_the_bars_own_height(self):
        """The floor stops a 1%-used bar being a sliver its own corner
        radius clips away. It is BAR_H, not a constant that merely equalled
        it when written — BAR_H has been resized before."""
        match = re.search(r"\.frame\(width: max\(([\d.]+), "
                          r"geo\.size\.width \* fraction\)\)",
                          _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the fill's minimum width")
        self.assertEqual(float(match.group(1)), theme.BAR_H)

    def test_the_percentage_is_the_only_right_anchored_column(self):
        """The countdown used to hold a second trailing frame here, which
        put a number over the bar's right END that was not about the bar —
        the misreading LABEL_W's comment records. One column now, and it
        belongs to the bar's own readout."""
        text = _read(ROW_SOURCE)
        widths = [float(v) for v in re.findall(
            r"\.frame\(width: ([\d.]+), alignment: \.trailing\)", text)]
        self.assertEqual(widths, [theme.VALUE_PCT_W])

    def test_the_percentage_font_matches_size_row_value(self):
        match = re.search(
            r"\.font\(\.system\(size: ([\d.]+), weight: \.bold\)"
            r"\.monospacedDigit\(\)\)", _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the percentage's font")
        self.assertEqual(float(match.group(1)), theme.SIZE_ROW_VALUE)

    def test_the_reset_caption_is_worded_and_shares_the_value_size(self):
        """A bare duration beside a percentage gets read as budget left
        rather than time left, so the caption says what it measures. It
        keeps the row's value SIZE — secondary ink and regular weight are
        what demote it, not a half-point step nobody can see."""
        text = _read(ROW_SOURCE)
        self.assertIn('"resets in \\(countdown)"', text)
        match = re.search(r"Text\(countdown\.isEmpty \? \"\" : "
                          r"\"resets in \\\(countdown\)\"\)\s+"
                          r"\.font\(\.system\(size: ([\d.]+)\)\)\s+"
                          r"\.foregroundStyle\(palette\.(\w+)\)", text)
        self.assertIsNotNone(match, "could not find the reset caption")
        self.assertEqual(float(match.group(1)), theme.SIZE_ROW_VALUE)
        self.assertEqual(match.group(2), "textSecondary")

    def test_the_label_and_value_sizes_stay_equal(self):
        """They sit on the SAME line, so a size step between them would read
        as a hierarchy that isn't there — they are one fact, not a heading
        over a body. The equality is the relationship the row depends on,
        which is why it is pinned rather than either number alone."""
        self.assertEqual(theme.SIZE_ROW_LABEL, theme.SIZE_ROW_VALUE)

    def test_the_caret_matches_pace_w_by_pace_h(self):
        text = _read(ROW_SOURCE)
        match = re.search(
            r"Rectangle\(\)\s+"
            r"\.fill\(palette\.pace\)\s+(?://[^\n]*\n\s+)*"
            r"\.frame\(width: ([\d.]+), height: ([\d.]+)\)", text)
        self.assertIsNotNone(match, "could not find the caret Rectangle")
        self.assertEqual(float(match.group(1)), theme.PACE_W)
        self.assertEqual(float(match.group(2)), theme.PACE_H)
        half = re.search(r"let half: CGFloat = ([\d.]+)", text)
        self.assertIsNotNone(half, "could not find the caret's half-width")
        self.assertEqual(float(half.group(1)) * 2, theme.PACE_W,
                         "the caret centres itself on half its own width; a "
                         "stale half puts the tick off-centre by a hair at "
                         "every pace but 50%")

    def test_the_caret_hangs_flush_under_the_bar(self):
        """SwiftUI centres a ZStack's children, so the tick's y offset has
        to be half the bar plus half the tick — the exact drop that puts its
        TOP on the bar's BOTTOM, matching _bar()'s `y + BAR_H`. Get this
        wrong by a couple of points and the mark either cuts the fill again
        (the whole reason it moved) or floats away from the bar it
        annotates."""
        match = re.search(r"\.offset\(x: center - half, y: ([\d.]+)\)",
                          _read(ROW_SOURCE))
        self.assertIsNotNone(match, "could not find the caret's offset")
        self.assertEqual(float(match.group(1)),
                         theme.BAR_H / 2 + theme.PACE_H / 2)

    def test_the_pace_tick_still_fits_in_the_gap_below_the_row(self):
        """Python-only. The tick is drawn OUTSIDE the row's reserved height,
        into the ROW_GAP that separates one row's bar from the next row's
        label — which is only free real estate while it stays shorter than
        that gap. Nothing else would catch a taller tick colliding with the
        row below; the rows would simply start touching."""
        self.assertLess(theme.PACE_H, theme.ROW_GAP)


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
