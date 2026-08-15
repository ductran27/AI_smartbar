"""Tests for smartbar.core.reset_countdown_format — live countdown text."""
import os
import re
import unittest
from datetime import datetime, timedelta, timezone

import smartbar
from smartbar.core import warmup
from smartbar.core.reset_countdown_format import parse_iso, remaining_text

SWIFT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__))),
    "macos-swift", "Sources", "AISmartbar", "TimeRemaining.swift")

NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)


def at(**kwargs):
    return (NOW + timedelta(**kwargs)).isoformat()


class TestParseIso(unittest.TestCase):
    def test_z_suffix(self):
        self.assertEqual(parse_iso("2026-07-19T11:00:00Z").tzinfo, timezone.utc)

    def test_offset_and_microseconds(self):
        # cswap resetsAt carries microseconds + an explicit offset
        parsed = parse_iso("2026-07-20T05:40:00.162682+00:00")
        self.assertEqual(parsed.hour, 5)

    def test_naive_treated_as_utc(self):
        self.assertEqual(parse_iso("2026-07-19T11:00:00").tzinfo, timezone.utc)

    def test_empty_and_garbage(self):
        self.assertIsNone(parse_iso(""))
        self.assertIsNone(parse_iso("soon"))

    def test_warmup_reexport_is_same_function(self):
        self.assertIs(warmup.parse_iso, parse_iso)


class TestRemainingText(unittest.TestCase):
    def test_minutes_only(self):
        self.assertEqual(remaining_text(at(minutes=44, seconds=30), NOW), "44m")

    def test_hours_minutes(self):
        self.assertEqual(remaining_text(at(hours=1, minutes=44), NOW), "1h 44m")

    def test_exact_hour(self):
        self.assertEqual(remaining_text(at(hours=1), NOW), "1h 0m")

    def test_days_hours(self):
        self.assertEqual(remaining_text(at(days=6, hours=13, minutes=59), NOW),
                         "6d 13h")

    def test_exact_day(self):
        self.assertEqual(remaining_text(at(days=1), NOW), "1d 0h")

    def test_past_clamps_to_zero(self):
        self.assertEqual(remaining_text(at(minutes=-5), NOW), "0m")

    def test_unparseable_is_empty(self):
        self.assertEqual(remaining_text("", NOW), "")
        self.assertEqual(remaining_text("r1", NOW), "")


class TestSwiftCountdownParity(unittest.TestCase):
    """The countdown's shape exists twice, in two languages. Pin them together.

    TimeRemaining.swift says it is a "mirror of
    smartbar/core/reset_countdown_format.py", and the macOS popover ticks its
    countdowns from that mirror rather than from this module — so a change to
    a breakpoint or a separator on one side alone silently gives the Mac a
    different countdown from Linux and Windows for the same reset time.
    Nothing scraped TimeRemaining.swift at all before this.

    Compared as TEMPLATES, not sample outputs: normalising Swift's
    `\\(days)d \\(hours)h` to Python's `{days}d {hours}h` catches a changed
    separator, unit letter or field order, which sampling a few durations
    would miss unless the samples happened to straddle every boundary.
    """

    #: `if days > 0 { return "\\(days)d \\(hours)h" }` and the bare tail
    #: `return "\\(minutes)m"`.
    _GUARDED = re.compile(r'if (\w+) > 0 \{ return "([^"]+)" \}')
    _TAIL = re.compile(r'^\s*return "([^"]+)"\s*$', re.MULTILINE)
    #: Swift interpolation -> Python format field.
    _INTERP = re.compile(r"\\\((\w+)\)")

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(SWIFT_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")
        with open(SWIFT_SOURCE, encoding="utf-8") as handle:
            cls.source = handle.read()

    def swift_templates(self):
        """[(guard field or None, python-style template)] in Swift's order."""
        rules = [(field, self._INTERP.sub(r"{\1}", body))
                 for field, body in self._GUARDED.findall(self.source)]
        tails = [self._INTERP.sub(r"{\1}", body)
                 for body in self._TAIL.findall(self.source)]
        return rules + [(None, tails[-1])] if tails else rules

    def test_the_scraper_actually_found_the_ladder(self):
        self.assertEqual(len(self.swift_templates()), 3,
                         "expected the days / hours / minutes ladder")

    def test_the_ladder_matches_this_module_rung_for_rung(self):
        # The Python side, read off remaining_text's own three returns.
        self.assertEqual(self.swift_templates(), [
            ("days", "{days}d {hours}h"),
            ("hours", "{hours}h {minutes}m"),
            (None, "{minutes}m"),
        ], "TimeRemaining.swift's countdown ladder drifted from "
           "reset_countdown_format.remaining_text — the Mac would show a "
           "different countdown from Linux/Windows for the same reset time")

    def test_python_still_produces_exactly_those_templates(self):
        """The other half: the assertion above hard-codes what Python does,
        so this proves that hard-coding is still true rather than stale."""
        self.assertEqual(remaining_text(at(days=6, hours=13), NOW), "6d 13h")
        self.assertEqual(remaining_text(at(hours=1, minutes=44), NOW), "1h 44m")
        self.assertEqual(remaining_text(at(minutes=44), NOW), "44m")
        self.assertEqual(remaining_text(at(minutes=-5), NOW), "0m")


if __name__ == "__main__":
    unittest.main()
