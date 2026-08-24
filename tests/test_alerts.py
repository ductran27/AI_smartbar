"""Tests for smartbar.core.alerts — fire-once / re-arm state machine.

v3: alerts fire when a metric's % used climbs to the red threshold
(default 90% used); copy speaks in "% used".
"""
import os
import unittest

from smartbar.core import model
from smartbar.core.alerts import AlertManager


def snap(active_pct, resets="r1", other_pct=None):
    accounts = [model.Account(number=1, email="a@x.com", active=True, metrics=[
        model.Metric(key="5h", label="5h", short="5h", pct=active_pct,
                     resets_at=resets, countdown="1h 12m")])]
    if other_pct is not None:
        accounts.append(model.Account(number=2, email="b@x.com", active=False, metrics=[
            model.Metric(key="5h", label="5h", short="5h", pct=other_pct,
                         resets_at="rx", countdown="")]))
    return model.Snapshot(accounts=accounts)


class TestAlerts(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SMARTBAR_TEST_THRESHOLD", None)
        self.mgr = AlertManager()

    def tearDown(self):
        os.environ.pop("SMARTBAR_TEST_THRESHOLD", None)

    def test_fires_once_at_threshold(self):
        alerts = self.mgr.check(snap(92))
        self.assertEqual(len(alerts), 1)
        self.assertIn("5h", alerts[0].title)
        self.assertIn("92% used", alerts[0].title)
        self.assertIn("Resets in 1h 12m", alerts[0].body)
        self.assertEqual(self.mgr.check(snap(93)), [])  # held, same window

    def test_fires_when_fully_empty(self):
        alerts = self.mgr.check(snap(100))         # exhausted -> gray, still alerts
        self.assertEqual(len(alerts), 1)
        self.assertIn("100% used", alerts[0].title)

    def test_rearm_on_drop_below(self):
        self.mgr.check(snap(92))
        self.mgr.check(snap(5))          # window reset -> plenty of room again
        self.assertEqual(len(self.mgr.check(snap(91))), 1)

    def test_rearm_on_resets_at_change(self):
        self.mgr.check(snap(92, resets="r1"))
        alerts = self.mgr.check(snap(95, resets="r2"))  # new window, still low
        self.assertEqual(len(alerts), 1)

    def test_below_threshold_never_fires(self):
        self.assertEqual(self.mgr.check(snap(89.9)), [])

    def test_suggestion_names_best_other_account_in_used(self):
        alerts = self.mgr.check(snap(92, other_pct=34))
        self.assertIn("#2 b@x.com", alerts[0].body)
        self.assertIn("34% used", alerts[0].body)

    def test_no_other_account_message(self):
        alerts = self.mgr.check(snap(92))
        self.assertIn("No other account", alerts[0].body)

    def test_no_other_account_escalates_title(self):
        alerts = self.mgr.check(snap(92))
        self.assertIn("no accounts left", alerts[0].title)

    def test_suggestion_available_does_not_escalate_title(self):
        alerts = self.mgr.check(snap(92, other_pct=34))
        self.assertNotIn("no accounts left", alerts[0].title)

    def test_countdown_recomputed_live_from_resets_at(self):
        # A parseable resetsAt wins over the frozen fetch-time countdown.
        from datetime import datetime, timedelta, timezone
        resets = (datetime.now(timezone.utc)
                  + timedelta(hours=2, minutes=30, seconds=30)).isoformat()
        alerts = self.mgr.check(snap(92, resets=resets))
        self.assertIn("Resets in 2h 30m", alerts[0].body)

    def test_respects_test_threshold_env(self):
        os.environ["SMARTBAR_TEST_THRESHOLD"] = "70"
        self.assertEqual(len(self.mgr.check(snap(75))), 1)  # 75 used >= 70

    def test_subsecond_resets_at_drift_does_not_refire(self):
        # The API re-stamps resets_at with fresh sub-second values on every
        # real fetch (observed live: .617540 → .275084 → .916220 for ONE
        # window). Keyed on the raw string, a ≥90% account notified every
        # 1-3 minutes until the window closed.
        self.mgr.check(snap(92, resets="2026-08-24T17:00:00.617540+00:00"))
        self.assertEqual(
            self.mgr.check(snap(93, resets="2026-08-24T17:00:00.275084+00:00")),
            [])
        # Even across a minute boundary within the same window.
        self.assertEqual(
            self.mgr.check(snap(94, resets="2026-08-24T16:59:59.912345+00:00")),
            [])

    def test_a_genuinely_new_window_still_refires(self):
        self.mgr.check(snap(92, resets="2026-08-24T17:00:00+00:00"))
        alerts = self.mgr.check(snap(91, resets="2026-08-24T22:00:00+00:00"))
        self.assertEqual(len(alerts), 1)


if __name__ == "__main__":
    unittest.main()
