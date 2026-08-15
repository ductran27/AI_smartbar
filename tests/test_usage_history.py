"""smartbar.core.usage_history: the 30-day strip's local store.

Every test passes `path=` explicitly into a tmp directory rather than
touching the real cache dir — see load()/record()/series()'s own `path`
parameter, which exists for exactly this kind of isolation.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from smartbar.core import model, usage_history as uh

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def snapshot(email="a@example.com", provider="claude", windows=None):
    windows = windows if windows is not None else {"7d": 42.0}
    metrics = [model.Metric(key=key, label=key, short=key, pct=pct)
              for key, pct in windows.items()]
    account = model.Account(number=1, email=email, provider=provider,
                            metrics=metrics)
    if provider == "claude":
        return model.Snapshot(accounts=[account])
    snap = model.Snapshot(accounts=[])
    snap.openai = [account]
    return snap


class UsageHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "usage-history.json")


class TestRoundTrip(UsageHistoryTest):
    def test_fresh_store_is_empty(self):
        self.assertEqual(uh.load(path=self.path), {"records": [], "version": 1})

    def test_record_then_load_round_trips(self):
        uh.record(snapshot(windows={"7d": 55.0, "spend": 10.0}), now=NOW,
                  path=self.path)
        store = uh.load(path=self.path)
        self.assertEqual(len(store["records"]), 1)
        entry = store["records"][0]
        self.assertEqual(entry["date"], "2026-08-15")
        self.assertEqual(entry["provider"], "claude")
        self.assertEqual(entry["email"], "a@example.com")
        self.assertEqual(entry["windows"], {"7d": 55.0, "spend": 10.0})

    def test_accounts_with_no_metrics_are_not_recorded(self):
        # A blocked/data-less account has nothing to say about "how close
        # to the ceiling" it ran — there is no reading to take a max of.
        empty = model.Snapshot(accounts=[
            model.Account(number=1, email="dead@example.com", metrics=[])])
        uh.record(empty, now=NOW, path=self.path)
        self.assertEqual(uh.load(path=self.path)["records"], [])


class TestSameDayMerge(UsageHistoryTest):
    def test_two_polls_same_day_merge_by_max_not_append(self):
        uh.record(snapshot(windows={"7d": 40.0}), now=NOW, path=self.path)
        later = NOW + timedelta(hours=3)
        uh.record(snapshot(windows={"7d": 61.0}), now=later, path=self.path)
        records = uh.load(path=self.path)["records"]
        self.assertEqual(len(records), 1)   # merged, never duplicated
        self.assertEqual(records[0]["windows"]["7d"], 61.0)

    def test_a_lower_later_reading_does_not_pull_the_max_back_down(self):
        uh.record(snapshot(windows={"7d": 80.0}), now=NOW, path=self.path)
        uh.record(snapshot(windows={"7d": 30.0}), now=NOW + timedelta(hours=1),
                  path=self.path)
        records = uh.load(path=self.path)["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["windows"]["7d"], 80.0)

    def test_openai_and_claude_accounts_with_the_same_email_stay_separate(self):
        uh.record(snapshot(email="a@x.com", provider="claude",
                           windows={"7d": 20.0}), now=NOW, path=self.path)
        uh.record(snapshot(email="a@x.com", provider="openai",
                           windows={"7d": 90.0}), now=NOW, path=self.path)
        records = uh.load(path=self.path)["records"]
        self.assertEqual(len(records), 2)
        by_provider = {r["provider"]: r["windows"]["7d"] for r in records}
        self.assertEqual(by_provider, {"claude": 20.0, "openai": 90.0})


class TestNinetyDayCap(UsageHistoryTest):
    def test_prunes_oldest_first_once_past_the_cap(self):
        for offset in range(95):
            day = NOW - timedelta(days=94 - offset)   # oldest first, 95 days
            uh.record(snapshot(windows={"7d": float(offset)}), now=day,
                      path=self.path)
        records = uh.load(path=self.path)["records"]
        self.assertEqual(len(records), uh.MAX_RECORDS)
        dates = sorted(r["date"] for r in records)
        # The 5 oldest of 95 days are gone; the surviving window starts on
        # day-offset 5 (1970-... arithmetic aside, just "5 days later").
        earliest_kept = (NOW - timedelta(days=94 - 5)).astimezone().strftime("%Y-%m-%d")
        latest_kept = NOW.astimezone().strftime("%Y-%m-%d")
        self.assertEqual(dates[0], earliest_kept)
        self.assertEqual(dates[-1], latest_kept)


class TestSeries(UsageHistoryTest):
    def test_returns_exactly_days_entries_ending_today_with_none_for_gaps(self):
        today = datetime.now(timezone.utc)
        yesterday = today - timedelta(days=1)
        five_days_ago = today - timedelta(days=5)
        uh.record(snapshot(windows={"7d": 11.0}), now=today, path=self.path)
        uh.record(snapshot(windows={"7d": 22.0}), now=yesterday, path=self.path)
        uh.record(snapshot(windows={"7d": 33.0}), now=five_days_ago,
                  path=self.path)
        out = uh.series("claude", "a@example.com", "7d", path=self.path)
        self.assertEqual(len(out), 30)
        self.assertEqual(out[-1], 11.0)     # today, last
        self.assertEqual(out[-2], 22.0)     # yesterday
        self.assertEqual(out[-6], 33.0)     # five days ago
        self.assertIsNone(out[-3])          # two days ago: never recorded
        self.assertIsNone(out[0])           # 30 days ago: never recorded

    def test_days_argument_changes_the_length(self):
        out = uh.series("claude", "a@example.com", "7d", days=7,
                        path=self.path)
        self.assertEqual(len(out), 7)
        self.assertTrue(all(v is None for v in out))

    def test_a_different_window_key_on_the_same_day_is_independent(self):
        uh.record(snapshot(windows={"7d": 40.0, "spend": 5.0}),
                  now=datetime.now(timezone.utc), path=self.path)
        self.assertEqual(
            uh.series("claude", "a@example.com", "7d", path=self.path)[-1], 40.0)
        self.assertEqual(
            uh.series("claude", "a@example.com", "spend", path=self.path)[-1], 5.0)


class TestCorruptFile(UsageHistoryTest):
    def test_unreadable_json_loads_as_an_empty_store(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not valid json")
        self.assertEqual(uh.load(path=self.path), {"records": [], "version": 1})

    def test_a_json_value_that_is_not_the_expected_shape_loads_as_empty(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump([1, 2, 3], handle)
        self.assertEqual(uh.load(path=self.path), {"records": [], "version": 1})

    def test_record_survives_a_corrupt_existing_file(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("garbage")
        uh.record(snapshot(), now=NOW, path=self.path)  # must not raise
        self.assertEqual(len(uh.load(path=self.path)["records"]), 1)


class TestAtomicWrite(UsageHistoryTest):
    def test_no_stray_tmp_file_after_a_normal_write(self):
        uh.record(snapshot(), now=NOW, path=self.path)
        leftovers = [name for name in os.listdir(self.tmp.name)
                     if name != "usage-history.json"]
        self.assertEqual(leftovers, [])

    def test_record_never_raises(self):
        # The module's own best-effort contract: a write failure must not
        # be able to take the caller's refresh down with it.
        with mock.patch.object(uh.os, "replace", side_effect=OSError("nope")):
            uh.record(snapshot(), now=NOW, path=self.path)  # must not raise

    def test_an_interrupted_write_leaves_the_old_file_intact_and_no_tmp(self):
        uh.record(snapshot(windows={"7d": 1.0}), now=NOW, path=self.path)
        baseline = open(self.path, encoding="utf-8").read()
        with mock.patch.object(uh.os, "replace", side_effect=OSError("nope")):
            uh.record(snapshot(windows={"7d": 99.0}),
                      now=NOW + timedelta(days=1), path=self.path)
        self.assertEqual(open(self.path, encoding="utf-8").read(), baseline)
        leftovers = [name for name in os.listdir(self.tmp.name)
                     if name != "usage-history.json"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
