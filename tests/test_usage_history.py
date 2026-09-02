"""Unit pins for the per-metric usage-history ring (the hover-reveal trend).

The macOS app polls cswap/Codex itself, so the front-ends record each poll's
per-metric % into this ring and this module owns only the SHAPE rules:
retention (SPAN_MINUTES) and when a sampling hole breaks the drawn line
(GAP_MINUTES). Swift's UsageHistory.swift mirrors these 1:1 — see
tests/test_usage_history_parity.py.
"""
from __future__ import annotations

import unittest

from smartbar.core import usage_history as uh


class TestRecord(unittest.TestCase):
    def test_first_sample_seeds_the_ring(self):
        self.assertEqual(uh.record([], 100, 45), [(100, 45)])

    def test_same_minute_replaces_last(self):
        # A poll can fire more than once a minute (open + timer); the minute
        # keeps one point, the latest value.
        self.assertEqual(uh.record([(100, 45)], 100, 60), [(100, 60)])

    def test_new_minute_appends(self):
        self.assertEqual(uh.record([(100, 45)], 101, 50),
                         [(100, 45), (101, 50)])

    def test_entries_older_than_span_are_dropped(self):
        # now_minute defaults to the sample minute; anything at or before
        # now-span falls off.
        self.assertEqual(uh.record([(0, 10)], 10081, 5, span=10080),
                         [(10081, 5)])

    def test_a_sample_exactly_span_old_is_dropped_but_one_newer_stays(self):
        ring = [(1, 10), (2, 20)]  # cutoff at now-span = 1, keep minute > 1
        self.assertEqual(uh.record(ring, 10081, 5, span=10080),
                         [(2, 20), (10081, 5)])

    def test_pct_is_clamped_and_rounded_to_int(self):
        self.assertEqual(uh.record([], 5, 130), [(5, 100)])
        self.assertEqual(uh.record([], 5, -3), [(5, 0)])
        self.assertEqual(uh.record([], 5, 45.6), [(5, 46)])

    def test_record_returns_a_new_list(self):
        ring = [(100, 45)]
        out = uh.record(ring, 101, 50)
        self.assertIsNot(out, ring)
        self.assertEqual(ring, [(100, 45)])  # caller's list untouched


class TestSeries(unittest.TestCase):
    def test_contiguous_samples_are_a_plain_list(self):
        self.assertEqual(uh.series([(1, 10), (2, 20), (3, 30)]),
                         [10, 20, 30])

    def test_a_hole_wider_than_gap_breaks_the_line(self):
        self.assertEqual(uh.series([(1, 10), (30, 20)], gap=15),
                         [10, None, 20])

    def test_gap_boundary_is_inclusive_no_break_at_exactly_gap(self):
        self.assertEqual(uh.series([(1, 10), (16, 20)], gap=15), [10, 20])
        self.assertEqual(uh.series([(1, 10), (17, 20)], gap=15),
                         [10, None, 20])

    def test_empty_ring_is_empty_series(self):
        self.assertEqual(uh.series([]), [])


class TestSummary(unittest.TestCase):
    def test_empty_ring(self):
        self.assertEqual(uh.summary([]), {"peak": 0, "last": 0, "points": 0})

    def test_peak_and_last(self):
        self.assertEqual(uh.summary([(1, 10), (2, 80), (3, 30)]),
                         {"peak": 80, "last": 30, "points": 3})


class TestConstants(unittest.TestCase):
    def test_span_is_seven_days_of_minutes(self):
        self.assertEqual(uh.SPAN_MINUTES, 7 * 24 * 60)

    def test_gap_clears_the_poll_cadence(self):
        # 180s idle poll = 3 min; the break threshold must sit well above it
        # so ordinary polling never fakes a break.
        self.assertGreater(uh.GAP_MINUTES, 3)


if __name__ == "__main__":
    unittest.main()
