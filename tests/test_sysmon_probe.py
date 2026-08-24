from __future__ import annotations

import sys
import unittest

from smartbar.core import sysmon_probe as probe

# Real macOS `ps` and `vm_stat` output captured on this Mac 2026-08-23.
PS_TEXT = (
    "    1     0     0  27744 21-05:52:10 213:23.48 "
    "Sun Aug  2 16:19:43 2026 /sbin/launchd\n"
    "  364     1     0 101456 21-05:51:41 170:26.87 "
    "Sun Aug  2 16:20:12 2026 /usr/libexec/logd\n"
    "22499 22493   501 400000    06:03:42 3618:57.00 "
    "Sun Aug 23 15:13:24 2026 /Applications/Google Chrome.app/Contents/"
    "Frameworks/Google Chrome Helper (GPU) --type=gpu-process\n")

VM_STAT = (
    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
    "Pages free:                                    36205.\n"
    "Pages active:                                1763043.\n"
    "Pages inactive:                              1736470.\n"
    "Pages wired down:                             498623.\n"
    "Pages occupied by compressor:                 149694.\n")


class TestDurationParsers(unittest.TestCase):
    def test_cpu_seconds_minutes_over_60(self):
        # ps TIME is [[hh:]mm:]ss.cs and minutes are NOT capped at 60.
        self.assertAlmostEqual(probe.cpu_seconds("213:23.48"),
                               213 * 60 + 23.48, places=2)

    def test_cpu_seconds_with_hours(self):
        self.assertAlmostEqual(probe.cpu_seconds("1:02:03.00"),
                               3600 + 2 * 60 + 3, places=2)

    def test_cpu_seconds_seconds_only(self):
        self.assertAlmostEqual(probe.cpu_seconds("12.34"), 12.34, places=2)

    def test_etime_seconds_days(self):
        self.assertEqual(probe.etime_seconds("21-05:52:10"),
                         21 * 86400 + 5 * 3600 + 52 * 60 + 10)

    def test_etime_seconds_hms(self):
        self.assertEqual(probe.etime_seconds("06:03:42"),
                         6 * 3600 + 3 * 60 + 42)

    def test_etime_seconds_ms(self):
        self.assertEqual(probe.etime_seconds("52:10"), 52 * 60 + 10)


class TestLstart(unittest.TestCase):
    def test_ordering_is_preserved(self):
        # Local-timezone dependent, so test ORDER + positivity, not an exact
        # epoch (which would vary by the test machine's TZ).
        earlier = probe.parse_lstart("Sun Aug  2 16:19:43 2026")
        later = probe.parse_lstart("Sun Aug 23 15:13:24 2026")
        self.assertGreater(earlier, 0)
        self.assertGreater(later, earlier)

    def test_garbage_is_zero(self):
        self.assertEqual(probe.parse_lstart("not a date"), 0)


class TestParsePs(unittest.TestCase):
    def rows(self):
        return probe.parse_ps(PS_TEXT)

    def test_parses_every_row(self):
        self.assertEqual(len(self.rows()), 3)

    def test_columns_and_args_with_spaces(self):
        gpu = self.rows()[2]
        self.assertEqual(gpu["pid"], 22499)
        self.assertEqual(gpu["ppid"], 22493)
        self.assertEqual(gpu["uid"], 501)
        self.assertEqual(gpu["rss_kb"], 400000)
        self.assertEqual(gpu["elapsed"], 6 * 3600 + 3 * 60 + 42)
        self.assertIn("Google Chrome Helper (GPU)", gpu["args"])
        self.assertGreater(gpu["start"], 0)

    def test_cpu_percent_from_two_samples(self):
        # 10s of CPU burned over a 2s wall window = 500% (a busy multicore
        # process — exactly the headless-Chrome GPU helper case).
        prev = {22499: 100.0}
        cur = {22499: 110.0}
        self.assertAlmostEqual(probe.cpu_percent(22499, prev, cur, wall=2.0),
                               500.0, places=1)

    def test_cpu_percent_unknown_previous_is_zero(self):
        self.assertEqual(probe.cpu_percent(999, {}, {999: 5.0}, wall=1.0), 0.0)


class TestVmStat(unittest.TestCase):
    def test_used_is_active_plus_wired_plus_compressor(self):
        total = 68719476736
        mem = probe.parse_vm_stat(VM_STAT, total)
        page = 16384
        expected = (1763043 + 498623 + 149694) * page
        self.assertEqual(mem["usedBytes"], expected)
        self.assertEqual(mem["totalBytes"], total)
        self.assertEqual(mem["compressedBytes"], 149694 * page)
        self.assertAlmostEqual(mem["pct"], 100 * expected / total, places=1)


class TestLiveSampleSmoke(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "Mach ctypes is macOS-only")
    def test_core_ticks_returns_per_core_tuples(self):
        ticks = probe.core_ticks()
        self.assertIsNotNone(ticks)
        self.assertGreater(len(ticks), 0)
        self.assertEqual(len(ticks[0]), 4)   # user, system, idle, nice


if __name__ == "__main__":
    unittest.main()
