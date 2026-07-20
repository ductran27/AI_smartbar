"""Tests for smartbar.core.reset_countdown_format — live countdown text."""
import unittest
from datetime import datetime, timedelta, timezone

from smartbar.core import warmup
from smartbar.core.reset_countdown_format import parse_iso, remaining_text

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


if __name__ == "__main__":
    unittest.main()
