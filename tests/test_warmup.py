"""Tests for smartbar.core.warmup — pure gate logic, no I/O."""
import os
import unittest
from datetime import datetime, timedelta, timezone

from smartbar.core import model, warmup

NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat()


def acct(email="a@x.com", ok=True, status="ok", resets_at="", pct=0.0,
         with_5h=True):
    metrics = []
    if with_5h:
        metrics.append(model.Metric(key="5h", label="5h", short="5h",
                                    pct=pct, resets_at=resets_at))
    return model.Account(number=1, email=email, ok=ok, status=status,
                         active=True, metrics=metrics)


def state(last=None, count=0):
    day = NOW.astimezone().strftime("%Y-%m-%d")
    st = {"days": {day: {}}, "last": {}}
    if count:
        st["days"][day]["a@x.com"] = count
    if last is not None:
        st["last"]["a@x.com"] = last.timestamp()
    return st


class Env(unittest.TestCase):
    def setUp(self):
        for var in ("SMARTBAR_WARMUP_DAILY_CAP", "SMARTBAR_WARMUP_QUIET"):
            os.environ.pop(var, None)

    tearDown = setUp


class TestWindowIdle(Env):
    def test_empty_resets_at_is_idle(self):
        self.assertTrue(warmup.window_idle(acct(resets_at=""), NOW))

    def test_past_resets_at_is_idle_even_with_stale_pct(self):
        past = iso(NOW - timedelta(minutes=10))
        self.assertTrue(warmup.window_idle(acct(resets_at=past, pct=62.0), NOW))

    def test_future_resets_at_is_running(self):
        future = iso(NOW + timedelta(hours=3))
        self.assertFalse(warmup.window_idle(acct(resets_at=future), NOW))

    def test_unparseable_resets_at_is_not_idle(self):
        self.assertFalse(warmup.window_idle(acct(resets_at="soon"), NOW))

    def test_no_5h_metric_or_not_ok_is_not_idle(self):
        self.assertFalse(warmup.window_idle(acct(with_5h=False), NOW))
        self.assertFalse(warmup.window_idle(acct(ok=False), NOW))
        self.assertFalse(warmup.window_idle(None, NOW))

    def test_naive_resets_at_treated_as_utc(self):
        naive_past = (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        self.assertTrue(warmup.window_idle(acct(resets_at=naive_past), NOW))

    def test_z_suffix_parses_on_all_pythons(self):
        # cswap emits "...Z"; launchd's system python3 can be 3.9 where
        # fromisoformat rejects the Z suffix.
        past_z = "2026-07-19T11:00:00Z"
        self.assertTrue(warmup.window_idle(acct(resets_at=past_z), NOW))
        self.assertEqual(warmup.parse_iso(past_z).tzinfo, timezone.utc)


def at_local_hour(hour):
    """A UTC-aware instant whose LOCAL clock reads `hour` on whatever host runs this.

    The tests below used NOW.replace(hour=...) on a UTC datetime, which only
    described the intended scenario on a machine whose local zone IS UTC --
    i.e. the CI runners, and not the machine this was written on.
    """
    return datetime(2026, 7, 19, hour, 0).astimezone().astimezone(timezone.utc)


class _SplitClock:
    """A datetime stand-in whose UTC hour and local hour deliberately differ.

    Needed because a real datetime cannot express that on a UTC host: there,
    at_local_hour() above produces an instant whose two hours are equal, so a
    test built from it passes whether or not the code localises, and the CI
    runners are all UTC. This stub pins the behaviour itself -- that
    in_quiet_hours() goes through astimezone() before reading .hour -- and it
    does so identically on every host.
    """

    def __init__(self, hour, local_hour):
        self.hour = hour
        self._local_hour = local_hour

    def astimezone(self):
        return _SplitClock(self._local_hour, self._local_hour)


class TestQuietHours(Env):
    def test_empty_never_quiet(self):
        self.assertFalse(warmup.in_quiet_hours("", at_local_hour(3)))

    def test_plain_range(self):
        self.assertTrue(warmup.in_quiet_hours("13-15", at_local_hour(13)))
        self.assertTrue(warmup.in_quiet_hours("13-15", at_local_hour(14)))
        self.assertFalse(warmup.in_quiet_hours("13-15", at_local_hour(15)))
        self.assertFalse(warmup.in_quiet_hours("13-15", at_local_hour(12)))

    def test_wraps_midnight(self):
        self.assertTrue(warmup.in_quiet_hours("23-05", at_local_hour(23)))
        self.assertTrue(warmup.in_quiet_hours("23-05", at_local_hour(2)))
        self.assertFalse(warmup.in_quiet_hours("23-05", at_local_hour(5)))
        self.assertFalse(warmup.in_quiet_hours("23-05", at_local_hour(12)))

    def test_garbage_never_quiet(self):
        self.assertFalse(warmup.in_quiet_hours("night", at_local_hour(3)))

    def test_reads_the_local_hour_and_not_the_utc_one(self):
        """The bug this prevents: "23-05" silencing the user's whole morning.

        warmup_runner passes datetime.now(timezone.utc), so reading now.hour
        made the setting mean UTC. On a UTC+7 device "23-05" then covered
        06:00-12:00 local and left 3am wide open -- the exact inverse of what
        the README says the setting does.
        """
        # 20:00 UTC is 03:00 the next morning at UTC+7: inside "23-05".
        self.assertTrue(warmup.in_quiet_hours("23-05", _SplitClock(20, 3)))
        # 23:00 UTC is 06:00 there: outside it, though the UTC hour says 23.
        self.assertFalse(warmup.in_quiet_hours("23-05", _SplitClock(23, 6)))
        # And the plain (non-wrapping) branch localises too.
        self.assertTrue(warmup.in_quiet_hours("13-15", _SplitClock(7, 14)))


class TestShouldWarm(Env):
    def test_idle_account_fresh_data_warms(self):
        ok, reason = warmup.should_warm(acct(), NOW, state(), NOW)
        self.assertTrue(ok, reason)

    def test_running_window_skips(self):
        future = iso(NOW + timedelta(hours=2))
        ok, reason = warmup.should_warm(acct(resets_at=future), NOW, state(), NOW)
        self.assertFalse(ok)
        self.assertIn("running", reason)

    def test_stale_snapshot_skips(self):
        fetched = NOW - timedelta(minutes=45)
        ok, reason = warmup.should_warm(acct(), NOW, state(), fetched)
        self.assertFalse(ok)
        self.assertIn("stale", reason)

    def test_unknown_fetch_time_skips(self):
        ok, reason = warmup.should_warm(acct(), NOW, state(), None)
        self.assertFalse(ok)
        self.assertIn("stale", reason)

    def test_cooldown_skips(self):
        st = state(last=NOW - timedelta(minutes=10))
        ok, reason = warmup.should_warm(acct(), NOW, st, NOW)
        self.assertFalse(ok)
        self.assertIn("cooldown", reason)

    def test_cooldown_expired_warms(self):
        st = state(last=NOW - timedelta(minutes=40))
        ok, _ = warmup.should_warm(acct(), NOW, st, NOW)
        self.assertTrue(ok)

    def test_daily_cap_skips(self):
        st = state(count=6)
        ok, reason = warmup.should_warm(acct(), NOW, st, NOW)
        self.assertFalse(ok)
        self.assertIn("cap", reason)

    def test_daily_cap_env_override(self):
        os.environ["SMARTBAR_WARMUP_DAILY_CAP"] = "2"
        ok, reason = warmup.should_warm(acct(), NOW, state(count=2), NOW)
        self.assertFalse(ok)
        self.assertIn("cap", reason)

    def test_quiet_hours_skip(self):
        os.environ["SMARTBAR_WARMUP_QUIET"] = "0-24"
        ok, reason = warmup.should_warm(acct(), NOW, state(), NOW)
        self.assertFalse(ok)
        self.assertIn("quiet", reason)

    def test_relogin_required_names_the_cause(self):
        dead = acct(ok=False, status="relogin_required", with_5h=False)
        ok, reason = warmup.should_warm(dead, NOW, state(), NOW)
        self.assertFalse(ok)
        self.assertIn("re-login", reason)

    def test_other_bad_status_named(self):
        odd = acct(ok=False, status="keychain_unavailable", with_5h=False)
        ok, reason = warmup.should_warm(odd, NOW, state(), NOW)
        self.assertFalse(ok)
        self.assertIn("keychain_unavailable", reason)

    def test_no_5h_data_distinct_from_running(self):
        ok, reason = warmup.should_warm(acct(with_5h=False), NOW, state(), NOW)
        self.assertFalse(ok)
        self.assertIn("no 5h", reason)

    def test_failure_streak_pauses_until_tomorrow(self):
        st = state()
        for _ in range(warmup.MAX_CONSECUTIVE_FAILURES):
            warmup.record_failure(st, "a@x.com", NOW)
        ok, reason = warmup.should_warm(acct(), NOW, st, NOW)
        self.assertFalse(ok)
        self.assertIn("paused", reason)
        # A new day clears the streak.
        tomorrow = NOW + timedelta(days=1)
        ok, _ = warmup.should_warm(acct(), tomorrow, st, tomorrow)
        self.assertTrue(ok)


class TestStateHelpers(Env):
    def test_record_attempt_counts_and_stamps(self):
        st = {"days": {}, "last": {}}
        warmup.record_attempt(st, "a@x.com", NOW)
        warmup.record_attempt(st, "a@x.com", NOW)
        day = NOW.astimezone().strftime("%Y-%m-%d")
        self.assertEqual(st["days"][day]["a@x.com"], 2)
        self.assertEqual(st["last"]["a@x.com"], NOW.timestamp())

    def test_prune_drops_old_days_and_unknown_emails(self):
        old_day = (NOW - timedelta(days=9)).astimezone().strftime("%Y-%m-%d")
        day = NOW.astimezone().strftime("%Y-%m-%d")
        st = {"days": {old_day: {"a@x.com": 3}, day: {"a@x.com": 1, "gone@x.com": 2}},
              "last": {"a@x.com": 1.0, "gone@x.com": 2.0},
              "fail": {"gone@x.com": {"count": 2, "day": day}}}
        warmup.prune_state(st, ["a@x.com"], NOW)
        self.assertNotIn(old_day, st["days"])
        self.assertNotIn("gone@x.com", st["days"][day])
        self.assertNotIn("gone@x.com", st["last"])
        self.assertNotIn("gone@x.com", st["fail"])
        self.assertEqual(st["days"][day]["a@x.com"], 1)

    def test_failure_streak_records_and_clears(self):
        st = {}
        self.assertEqual(warmup.record_failure(st, "a@x.com", NOW), 1)
        self.assertEqual(warmup.record_failure(st, "a@x.com", NOW), 2)
        self.assertEqual(warmup.consecutive_failures(st, "a@x.com", NOW), 2)
        # Yesterday's streak does not carry into today.
        tomorrow = NOW + timedelta(days=1)
        self.assertEqual(warmup.consecutive_failures(st, "a@x.com", tomorrow), 0)
        self.assertEqual(warmup.record_failure(st, "a@x.com", tomorrow), 1)
        warmup.record_success(st, "a@x.com")
        self.assertEqual(warmup.consecutive_failures(st, "a@x.com", tomorrow), 0)


if __name__ == "__main__":
    unittest.main()
