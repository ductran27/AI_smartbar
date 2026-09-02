"""Cross-language pins for the usage-history trend (source-scrape, no Swift
toolchain), sibling to test_sysmon_parity.

The macOS app records usage history itself (Swift), so UsageHistory.swift is a
1:1 port of core/usage_history.py rather than a decoder of core's payload.
These pins hold the two together on the parts that would silently corrupt the
drawn line if they drifted: the retention span, the gap-break threshold, and
the comparison that turns a sampling hole into a break. They also pin that both
provider stores still feed the ring and the row still reads it.
"""
from __future__ import annotations

import pathlib
import unittest

from smartbar.core import usage_history as uh

REPO = pathlib.Path(__file__).resolve().parent.parent
SWIFT_DIR = REPO / "macos-swift" / "Sources" / "AISmartbar"


def _swift(name):
    return (SWIFT_DIR / name).read_text(encoding="utf-8")


class TestConstantsAgree(unittest.TestCase):
    def test_span_is_a_seven_day_window_both_sides(self):
        self.assertEqual(uh.SPAN_MINUTES, 7 * 24 * 60)
        # Swift writes the same expression, so the number is obviously a week
        # on both sides and can't drift to a bare 10080 that means nothing.
        self.assertIn("spanMinutes = 7 * 24 * 60", _swift("UsageHistory.swift"))

    def test_gap_threshold_agrees(self):
        self.assertEqual(uh.GAP_MINUTES, 15)
        self.assertIn(f"gapMinutes = {uh.GAP_MINUTES}",
                      _swift("UsageHistory.swift"))


class TestGapBreakRuleIsMirrored(unittest.TestCase):
    def test_swift_breaks_the_line_on_a_strictly_wider_hole(self):
        # Python: `minute - prev > gap`. Swift must use the same strict `>`
        # against gapMinutes, or a boundary hole would break on one platform
        # and connect on the other (pinned numerically in test_usage_history).
        self.assertIn("minute - prev > Self.gapMinutes",
                      _swift("UsageHistory.swift"))


class TestBothStoresFeedTheRing(unittest.TestCase):
    def test_claude_poll_records_history(self):
        self.assertIn("UsageHistory.shared.record(", _swift("UsageStore.swift"))

    def test_openai_poll_records_history(self):
        self.assertIn("UsageHistory.shared.record(", _swift("OpenAIStatus.swift"))

    def test_the_row_reveals_the_ring_on_hover(self):
        self.assertIn("UsageHistory.shared.series(", _swift("MetricBarRow.swift"))


if __name__ == "__main__":
    unittest.main()
