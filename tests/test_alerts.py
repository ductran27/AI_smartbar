"""Tests for smartbar.core.alerts — fire-once / re-arm state machine.

v2: alerts fire when a metric's % left drops to the red threshold
(default 10% left ≡ 90% used); copy speaks in "% left".
"""
import os
import unittest

from smartbar.core import model
from smartbar.core.alerts import AlertManager


def snap(active_pct, resets="r1", other_pct=None):
    accounts = [model.Account(number=1, email="a@x.com", active=True, metrics=[
        model.Metric(key="5h", label="5h", short="5h", pct=active_pct,
                     resets_at=resets, countdown="1h 12m", clock="")])]
    if other_pct is not None:
        accounts.append(model.Account(number=2, email="b@x.com", active=False, metrics=[
            model.Metric(key="5h", label="5h", short="5h", pct=other_pct,
                         resets_at="rx", countdown="", clock="")]))
    return model.Snapshot(accounts=accounts)


class TestAlerts(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SMARTBAR_TEST_THRESHOLD", None)
        self.mgr = AlertManager()

    def tearDown(self):
        os.environ.pop("SMARTBAR_TEST_THRESHOLD", None)

    def test_fires_once_at_threshold(self):
        alerts = self.mgr.check(snap(92))          # 8% left
        self.assertEqual(len(alerts), 1)
        self.assertIn("5h", alerts[0].title)
        self.assertIn("8% left", alerts[0].title)
        self.assertIn("Resets in 1h 12m", alerts[0].body)
        self.assertEqual(self.mgr.check(snap(93)), [])  # held, same window

    def test_fires_when_fully_empty(self):
        alerts = self.mgr.check(snap(100))         # 0% left -> gray, still alerts
        self.assertEqual(len(alerts), 1)
        self.assertIn("0% left", alerts[0].title)

    def test_rearm_on_drop_below(self):
        self.mgr.check(snap(92))
        self.mgr.check(snap(5))          # window reset -> plenty left again
        self.assertEqual(len(self.mgr.check(snap(91))), 1)

    def test_rearm_on_resets_at_change(self):
        self.mgr.check(snap(92, resets="r1"))
        alerts = self.mgr.check(snap(95, resets="r2"))  # new window, still low
        self.assertEqual(len(alerts), 1)

    def test_above_threshold_never_fires(self):
        self.assertEqual(self.mgr.check(snap(89.9)), [])  # 10.1% left

    def test_suggestion_names_best_other_account_in_left(self):
        alerts = self.mgr.check(snap(92, other_pct=34))
        self.assertIn("#2 b@x.com", alerts[0].body)
        self.assertIn("66% left", alerts[0].body)

    def test_no_other_account_message(self):
        alerts = self.mgr.check(snap(92))
        self.assertIn("No other account", alerts[0].body)

    def test_respects_test_threshold_env(self):
        os.environ["SMARTBAR_TEST_THRESHOLD"] = "30"
        self.assertEqual(len(self.mgr.check(snap(75))), 1)  # 25% left <= 30


if __name__ == "__main__":
    unittest.main()
