"""Tests for smartbar.core.cswap — parser + binary resolution (no network)."""
import json
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
