from __future__ import annotations

import os
import unittest

from smartbar.core import sysmon


class TestConfig(unittest.TestCase):
    def setUp(self):
        for key in ("SMARTBAR_SYSMON", "SMARTBAR_SYSMON_HOT",
                    "SMARTBAR_SYSMON_INTERVAL", "SMARTBAR_SYSMON_AUTOKILL",
                    "SMARTBAR_SYSMON_NOTIFY"):
            os.environ.pop(key, None)

    def test_enabled_default_on(self):
        self.assertTrue(sysmon.enabled())

    def test_enabled_off(self):
        os.environ["SMARTBAR_SYSMON"] = "off"
        self.assertFalse(sysmon.enabled())

    def test_enabled_off_is_case_insensitive(self):
        os.environ["SMARTBAR_SYSMON"] = "OFF"
        self.assertFalse(sysmon.enabled())

    def test_hot_default_and_override(self):
        self.assertEqual(sysmon.hot_threshold(), 50.0)
        os.environ["SMARTBAR_SYSMON_HOT"] = "75"
        self.assertEqual(sysmon.hot_threshold(), 75.0)

    def test_hot_garbage_falls_back(self):
        os.environ["SMARTBAR_SYSMON_HOT"] = "not-a-number"
        self.assertEqual(sysmon.hot_threshold(), 50.0)

    def test_interval_default(self):
        self.assertEqual(sysmon.interval(), 60)

    def test_interval_floor_15(self):
        os.environ["SMARTBAR_SYSMON_INTERVAL"] = "5"
        self.assertEqual(sysmon.interval(), 15)

    def test_autokill_default_off(self):
        self.assertFalse(sysmon.autokill_enabled())

    def test_autokill_on(self):
        os.environ["SMARTBAR_SYSMON_AUTOKILL"] = "on"
        self.assertTrue(sysmon.autokill_enabled())

    def test_notify_default_on_and_off(self):
        self.assertTrue(sysmon.notify_enabled())
        os.environ["SMARTBAR_SYSMON_NOTIFY"] = "off"
        self.assertFalse(sysmon.notify_enabled())

    def test_proc_dataclass_fields(self):
        proc = sysmon.Proc(pid=1, ppid=1, uid=501, rss_kb=2048, elapsed=60,
                           cpu=12.5, args="/bin/foo --bar", start=1700000000)
        self.assertEqual(proc.pid, 1)
        self.assertEqual(proc.rss_kb, 2048)
        self.assertEqual(proc.start, 1700000000)


if __name__ == "__main__":
    unittest.main()
