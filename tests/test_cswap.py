"""Tests for smartbar.core.cswap — parser + binary resolution (no network)."""
import json
import os
import unittest
from unittest import mock

from smartbar.core import cswap

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cswap_list.json")


class TestParse(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE) as f:
            self.raw = f.read()

    def test_parses_real_fixture(self):
        snap = cswap.parse_snapshot(self.raw)
        self.assertEqual(snap.schema_warning, "")
        self.assertGreaterEqual(len(snap.accounts), 1)
        acct = snap.active_account
        self.assertTrue(acct.ok)
        keys = [m.key for m in acct.metrics]
        self.assertIn("5h", keys)
        self.assertIn("7d", keys)
        self.assertTrue(any(k.startswith("scoped:") for k in keys))
        fable = [m for m in acct.metrics if m.key == "scoped:Fable"][0]
        self.assertEqual(fable.short, "F")
        self.assertEqual(fable.label, "Fable")
        self.assertTrue(fable.countdown)  # preformatted string carried through

    def test_unknown_schema_version_warns_but_parses(self):
        data = json.loads(self.raw)
        data["schemaVersion"] = 2
        snap = cswap.parse_snapshot(json.dumps(data))
        self.assertIn("schemaVersion", snap.schema_warning)
        self.assertGreaterEqual(len(snap.accounts), 1)

    def test_missing_usage_tolerated(self):
        data = json.loads(self.raw)
        del data["accounts"][0]["usage"]
        data["accounts"][0]["usageStatus"] = "error"
        snap = cswap.parse_snapshot(json.dumps(data))
        self.assertFalse(snap.accounts[0].ok)
        self.assertEqual(snap.accounts[0].metrics, [])

    def test_parses_enterprise_spend_budget(self):
        data = json.loads(self.raw)
        data["accounts"][0]["usage"] = {
            "spend": {
                "used": 48.45,
                "limit": 350.0,
                "pct": 13.842857142857143,
                "currency": "USD",
            }
        }
        snap = cswap.parse_snapshot(json.dumps(data))
        spend = snap.active_account.metrics[0]
        self.assertEqual(spend.key, "spend")
        self.assertEqual(spend.label, "Spend")
        self.assertEqual(spend.short, "$")
        self.assertAlmostEqual(spend.pct, 13.842857142857143)

    def test_usage_status_carried_through(self):
        data = json.loads(self.raw)
        data["accounts"][0]["usage"] = None
        data["accounts"][0]["usageStatus"] = "relogin_required"
        snap = cswap.parse_snapshot(json.dumps(data))
        acct = snap.accounts[0]
        self.assertEqual(acct.status, "relogin_required")
        self.assertFalse(acct.ok)

    def test_missing_status_defaults_empty(self):
        data = json.loads(self.raw)
        del data["accounts"][0]["usageStatus"]
        snap = cswap.parse_snapshot(json.dumps(data))
        self.assertEqual(snap.accounts[0].status, "")
        self.assertFalse(snap.accounts[0].ok)

    def test_invalid_json_raises(self):
        with self.assertRaises(cswap.CswapError):
            cswap.parse_snapshot("not json {")


OLD = "2026-07-20T01:00:00Z"
MID = "2026-07-20T02:00:00Z"
NEW = "2026-07-20T03:00:00Z"


def _payload(*stamps):
    """A snapshot whose accounts carry the given (usageFetchedAt, active)."""
    return json.dumps({"schemaVersion": 1, "accounts": [
        {"number": n, "email": "a%s@x.com" % n, "active": active,
         "usageStatus": "ok", "usageFetchedAt": stamp,
         "usage": {"fiveHour": {"pct": 10.0}}}
        for n, (stamp, active) in enumerate(stamps, start=1)]})


class TestPerAccountFetchTime(unittest.TestCase):
    """cswap refreshes each slot on its own plan, so one payload carries
    several different measurement times. Collapsing them to one value is what
    broke warmup: every account was gated on whichever stamp happened to come
    first (see tests/test_warmup_runner.py's TestRunOnceGatesPerAccount)."""

    def test_each_account_keeps_its_own_stamp(self):
        snap = cswap.parse_snapshot(_payload((OLD, False), (NEW, True)))
        self.assertEqual([a.fetched_at for a in snap.accounts], [OLD, NEW])

    def test_absent_stamp_is_empty_not_borrowed(self):
        data = json.loads(_payload((OLD, False), (NEW, True)))
        del data["accounts"][1]["usageFetchedAt"]
        snap = cswap.parse_snapshot(json.dumps(data))
        self.assertEqual([a.fetched_at for a in snap.accounts], [OLD, ""])


class TestSnapshotStamp(unittest.TestCase):
    """The popover's "Updated" line — the active account's measurement time,
    matching the Swift app's Snapshot.dataDate."""

    def test_prefers_the_active_account_over_an_earlier_slot(self):
        snap = cswap.parse_snapshot(_payload((OLD, False), (NEW, True)))
        self.assertEqual(snap.fetched_at, NEW)

    def test_falls_back_to_the_newest_when_no_slot_is_active(self):
        snap = cswap.parse_snapshot(_payload((OLD, False), (NEW, False),
                                             (MID, False)))
        self.assertEqual(snap.fetched_at, NEW)

    def test_active_slot_without_a_stamp_falls_back(self):
        data = json.loads(_payload((OLD, False), (NEW, True)))
        del data["accounts"][1]["usageFetchedAt"]
        snap = cswap.parse_snapshot(json.dumps(data))
        self.assertEqual(snap.fetched_at, OLD)

    def test_no_stamps_at_all_is_empty(self):
        self.assertEqual(cswap.snapshot_stamp([]), "")


def _snap(*accounts):
    from smartbar.core.model import Snapshot
    return Snapshot(accounts=list(accounts))


def _account(number, active=False, email="a@x.com"):
    from smartbar.core.model import Account
    return Account(number=number, email=email, active=active)


class TestRemoveAccount(unittest.TestCase):
    """remove_account never touches real slots: fetch and _run are mocked."""

    def test_pipes_the_confirmation_and_sends_the_slot_number(self):
        # cswap's CLI has no --yes flag, so the [y/N] prompt is answered on
        # stdin; the NUMBER (never the email) avoids its interactive
        # ambiguity prompt when one address fills two slots.
        with mock.patch.object(cswap, "fetch",
                               return_value=_snap(_account(2))), \
             mock.patch.object(cswap, "_run") as run:
            cswap.remove_account(2)
        run.assert_called_once_with(["remove", "2"], feed="y\n")

    def test_refuses_the_active_slot_before_running_anything(self):
        with mock.patch.object(cswap, "fetch",
                               return_value=_snap(_account(1, active=True))), \
             mock.patch.object(cswap, "_run") as run:
            with self.assertRaises(cswap.CswapError):
                cswap.remove_account(1)
        run.assert_not_called()

    def test_unknown_slot_raises_without_running_anything(self):
        with mock.patch.object(cswap, "fetch", return_value=_snap()), \
             mock.patch.object(cswap, "_run") as run:
            with self.assertRaises(cswap.CswapError):
                cswap.remove_account(9)
        run.assert_not_called()

    def test_a_cswap_failure_propagates(self):
        # e.g. cswap's own live-session refusal — surfaced, not swallowed.
        with mock.patch.object(cswap, "fetch",
                               return_value=_snap(_account(2))), \
             mock.patch.object(cswap, "_run",
                               side_effect=cswap.CswapError("live session")):
            with self.assertRaises(cswap.CswapError):
                cswap.remove_account(2)

    def test_run_feeds_stdin_when_asked(self):
        with mock.patch.object(cswap.subprocess, "run") as sub, \
             mock.patch.object(cswap, "_binary", return_value="/x/cswap"):
            sub.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            cswap._run(["remove", "2"], feed="y\n")
        self.assertEqual(sub.call_args.kwargs.get("input"), "y\n")

    def test_run_without_feed_stays_exactly_as_before(self):
        with mock.patch.object(cswap.subprocess, "run") as sub, \
             mock.patch.object(cswap, "_binary", return_value="/x/cswap"):
            sub.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            cswap._run(["list", "--json"])
        self.assertNotIn("input", sub.call_args.kwargs)


class TestBinary(unittest.TestCase):
    def test_env_override_wins(self):
        os.environ["SMARTBAR_CSWAP"] = "/nonexistent/cswap"
        try:
            self.assertEqual(cswap._binary(), "/nonexistent/cswap")
        finally:
            del os.environ["SMARTBAR_CSWAP"]


class TestBinaryWindowsBatchGuard(unittest.TestCase):
    """Windows silently reruns a resolved .bat/.cmd through cmd.exe, which
    re-parses the whole argv (the "BatBadBut" class of bug) — a fourth
    quoting context SMARTBAR_CSWAP's value never passes through on POSIX.
    CPython has not closed this at the subprocess layer as of 3.13
    (list2cmdline() still only implements MSVCRT-style escaping). Pins the
    decision from the port-review gap: refuse the resolved extension rather
    than try to out-escape cmd.exe.
    """

    def test_batch_extension_refused_on_windows(self):
        os.environ["SMARTBAR_CSWAP"] = "/nonexistent/cswap.bat"
        try:
            with mock.patch.object(cswap.sys, "platform", "win32"):
                with self.assertRaises(cswap.CswapError):
                    cswap._binary()
        finally:
            del os.environ["SMARTBAR_CSWAP"]

    def test_cmd_extension_refused_on_windows_case_insensitive(self):
        os.environ["SMARTBAR_CSWAP"] = r"C:\tools\cswap.CMD"
        try:
            with mock.patch.object(cswap.sys, "platform", "win32"):
                with self.assertRaises(cswap.CswapError):
                    cswap._binary()
        finally:
            del os.environ["SMARTBAR_CSWAP"]

    def test_batch_extension_allowed_off_windows(self):
        # POSIX has no equivalent OS-level shell respawn for these
        # extensions, so the same value is inert there — matches
        # device_config.py's _BAD_VALUE_WIN comment.
        os.environ["SMARTBAR_CSWAP"] = "/nonexistent/cswap.bat"
        try:
            with mock.patch.object(cswap.sys, "platform", "darwin"):
                self.assertEqual(cswap._binary(), "/nonexistent/cswap.bat")
        finally:
            del os.environ["SMARTBAR_CSWAP"]

    def test_exe_extension_allowed_on_windows(self):
        os.environ["SMARTBAR_CSWAP"] = r"C:\tools\cswap.exe"
        try:
            with mock.patch.object(cswap.sys, "platform", "win32"):
                self.assertEqual(cswap._binary(), r"C:\tools\cswap.exe")
        finally:
            del os.environ["SMARTBAR_CSWAP"]


if __name__ == "__main__":
    unittest.main()
