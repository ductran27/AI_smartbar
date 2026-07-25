"""Plan badge semantics: mapping, reader, stamping, cross-language parity."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartbar.core import plan


class TestTierLabel(unittest.TestCase):
    def test_max_multipliers_come_from_the_tier_suffix(self):
        self.assertEqual(plan.tier_label("default_claude_max_20x"), "20x")
        self.assertEqual(plan.tier_label("default_claude_max_5x"), "5x")
        self.assertEqual(plan.tier_label("default_claude_max_1x"), "1x")

    def test_pro_free_team_come_from_tier_or_org_type(self):
        self.assertEqual(plan.tier_label("default_claude_pro"), "Pro")
        self.assertEqual(plan.tier_label(None, "claude_pro"), "Pro")
        self.assertEqual(plan.tier_label("", "claude_free"), "Free")
        self.assertEqual(plan.tier_label(None, "claude_enterprise"), "Team")
        self.assertEqual(plan.tier_label("team_something", None), "Team")

    def test_subscription_type_is_the_coarse_fallback(self):
        self.assertEqual(plan.tier_label(None, None, "max"), "Max")

    def test_unknown_means_no_badge(self):
        self.assertEqual(plan.tier_label(None, None, None), "")
        self.assertEqual(plan.tier_label("", "", ""), "")
        self.assertEqual(plan.tier_label("mystery_tier_x", "org?", ""), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
