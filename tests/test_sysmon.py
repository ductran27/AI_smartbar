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


class TestClassify(unittest.TestCase):
    """Rules are anchored on the executable path. Fixtures are real argv
    lines from this Mac's `ps` output on 2026-08-23 (the two orphaned
    headless Chromes, esbuild, the scanner shell that must NOT match)."""

    MY_UID = 501

    # Today's actual orphan (pid 22493): headless Chrome from a CDP script.
    CHROME_ORPHAN = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
        "--headless=new --disable-gpu --enable-unsafe-swiftshader "
        "--hide-scrollbars --remote-debugging-port=9603 "
        "--window-size=1512,900 --no-first-run "
        "--user-data-dir=/tmp/cdp-prof-9603 http://localhost:5173/")
    # A normal Chrome (real profile) — must never be junk.
    CHROME_NORMAL = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
        "--user-data-dir=/Users/ductran/Library/Application Support/"
        "Google/Chrome")
    ESBUILD = ("/Users/ductran/.local/vietnamair/node_modules/@esbuild/"
               "darwin-arm64/bin/esbuild --service=0.28.1 --ping")
    PUPPETEER = (
        "/Users/ductran/.cache/puppeteer/chrome/mac_arm-151.0.7922.77/"
        "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/"
        "Google Chrome for Testing --allow-pre-commit-input")
    ZSH_SNAPSHOT = ("/bin/zsh -c source /Users/ductran/.claude/"
                    "shell-snapshots/snapshot-zsh-1234.sh")
    VITE = "/usr/local/bin/node /x/node_modules/vite/bin/vite.js --host"
    HTTP_SERVER = "/usr/bin/python3 -m http.server 8931"
    CLAUDE = "/Users/ductran/.local/bin/claude"
    CODEX = "/usr/local/bin/codex --model gpt-5"
    # The scanner's OWN shell: argv mentions every pattern, exe is a shell.
    SCANNER = ("/bin/zsh -c ps -Axww | grep -E 'esbuild --service|"
               "cdp-prof-|--headless|Chrome for Testing'")

    def k(self, args, orphan, cpu=0.0, prev=0.0, uid=None):
        uid = self.MY_UID if uid is None else uid
        ppid = 1 if orphan else 400
        proc = sysmon.Proc(pid=1, ppid=ppid, uid=uid, rss_kb=0, elapsed=0,
                           cpu=cpu, args=args)
        return sysmon.classify(proc, orphan, cpu, prev, self.MY_UID)

    def test_headless_chrome_orphan_is_junk(self):
        self.assertEqual(self.k(self.CHROME_ORPHAN, True), "junk")

    def test_headless_chrome_with_live_parent_is_watch(self):
        self.assertEqual(self.k(self.CHROME_ORPHAN, False), "watch")

    def test_normal_chrome_is_never_junk(self):
        self.assertIsNone(self.k(self.CHROME_NORMAL, True))

    def test_esbuild_orphan_is_junk(self):
        self.assertEqual(self.k(self.ESBUILD, True), "junk")

    def test_esbuild_live_parent_is_watch(self):
        self.assertEqual(self.k(self.ESBUILD, False), "watch")

    def test_puppeteer_chrome_for_testing_orphan_is_junk(self):
        self.assertEqual(self.k(self.PUPPETEER, True), "junk")

    def test_zsh_shell_snapshot_orphan_is_junk(self):
        self.assertEqual(self.k(self.ZSH_SNAPSHOT, True), "junk")

    def test_orphan_dev_server_is_idle_never_junk(self):
        self.assertEqual(self.k(self.VITE, True), "idle")
        self.assertEqual(self.k(self.HTTP_SERVER, True), "idle")

    def test_claude_and_codex_are_sessions(self):
        self.assertEqual(self.k(self.CLAUDE, False), "session")
        self.assertEqual(self.k(self.CODEX, False), "session")

    def test_scanner_shell_mentioning_patterns_is_not_classified(self):
        # exe is a shell, not a rule target; the argv text must not match.
        self.assertIsNone(self.k(self.SCANNER, True))

    def test_hot_needs_two_samples_over_threshold(self):
        self.assertIsNone(self.k("/usr/bin/somebusyapp", False, cpu=80, prev=0))
        self.assertEqual(
            self.k("/usr/bin/somebusyapp", False, cpu=80, prev=80), "hot")

    def test_other_users_process_is_system(self):
        self.assertEqual(self.k("/usr/sbin/foo", False, uid=0), "system")

    def test_ordinary_idle_process_is_unclassified(self):
        self.assertIsNone(self.k("/usr/bin/pmset -g", False, cpu=1))


if __name__ == "__main__":
    unittest.main()
