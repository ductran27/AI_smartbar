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
