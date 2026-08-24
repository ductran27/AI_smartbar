from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

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


class TestTreeAndKill(unittest.TestCase):
    """Process-tree grouping and the guarded kill token."""

    def table(self):
        # A headless-Chrome root (100) with a GPU helper (101) and a network
        # helper (102); an unrelated process (200); a claude session (300)
        # with an MCP child (301).
        procs = [
            sysmon.Proc(100, 1, 501, 50_000, 21600, 5.0,
                        "/Applications/Google Chrome.app/Contents/MacOS/"
                        "Google Chrome --headless --user-data-dir=/tmp/"
                        "cdp-prof-9603", start=1000),
            sysmon.Proc(101, 100, 501, 400_000, 21600, 575.0,
                        "/Applications/Google Chrome.app/.../Helpers/"
                        "Google Chrome Helper (GPU) --type=gpu-process",
                        start=1000),
            sysmon.Proc(102, 100, 501, 30_000, 21600, 1.0,
                        "Google Chrome Helper --type=utility", start=1000),
            sysmon.Proc(200, 1, 501, 1000, 10, 0.0, "/usr/bin/pmset",
                        start=2000),
            sysmon.Proc(300, 400, 501, 900_000, 3600, 5.0,
                        "/Users/ductran/.local/bin/claude", start=3000),
            sysmon.Proc(301, 300, 501, 100_000, 3600, 1.0,
                        "/usr/bin/node /x/mcp-server.js", start=3100),
        ]
        return {p.pid: p for p in procs}

    def test_kill_token_is_pid_and_start(self):
        proc = sysmon.Proc(100, 1, 501, 0, 0, 0.0, "x", start=1000)
        self.assertEqual(sysmon.kill_token(proc), "100:1000")

    def test_tree_pids_collects_descendants(self):
        table = self.table()
        self.assertEqual(sysmon.tree_pids(100, table), {100, 101, 102})
        self.assertEqual(sysmon.tree_pids(300, table), {300, 301})

    def test_tree_cpu_sums_the_subtree(self):
        table = self.table()
        # 5 + 575 + 1 — the headless root's real cost is in its GPU helper.
        self.assertEqual(sysmon.tree_cpu(100, table), 581.0)

    def test_tree_mem_sums_the_subtree_in_mb(self):
        table = self.table()
        # (50000 + 400000 + 30000) KB / 1024 ≈ 469 MB
        self.assertEqual(sysmon.tree_mem_mb(100, table), round(480_000 / 1024))

    def test_validate_kill_ok_for_own_orphan(self):
        table = self.table()
        ok, error = sysmon.validate_kill("100:1000", table, my_uid=501,
                                         own_pids={9999})
        self.assertTrue(ok)
        self.assertEqual(error, "")

    def test_validate_kill_refuses_unknown_pid(self):
        ok, error = sysmon.validate_kill("55555:1", self.table(), my_uid=501,
                                         own_pids=set())
        self.assertFalse(ok)
        self.assertIn("gone", error.lower())

    def test_validate_kill_refuses_start_time_mismatch(self):
        # PID reuse: same pid, different start time than the token names.
        ok, error = sysmon.validate_kill("100:999", self.table(), my_uid=501,
                                         own_pids=set())
        self.assertFalse(ok)
        self.assertIn("reused", error.lower())

    def test_validate_kill_refuses_other_user(self):
        table = self.table()
        table[100].uid = 0
        ok, error = sysmon.validate_kill("100:1000", table, my_uid=501,
                                         own_pids=set())
        self.assertFalse(ok)

    def test_validate_kill_refuses_a_session(self):
        ok, error = sysmon.validate_kill("300:3000", self.table(), my_uid=501,
                                         own_pids=set())
        self.assertFalse(ok)
        self.assertIn("session", error.lower())

    def test_validate_kill_refuses_own_process(self):
        table = self.table()
        ok, error = sysmon.validate_kill("100:1000", table, my_uid=501,
                                         own_pids={100})
        self.assertFalse(ok)

    def test_validate_kill_rejects_malformed_token(self):
        ok, error = sysmon.validate_kill("nonsense", self.table(), my_uid=501,
                                         own_pids=set())
        self.assertFalse(ok)


class TestDisplayHelpers(unittest.TestCase):
    def test_format_age(self):
        self.assertEqual(sysmon.format_age(30), "just now")
        self.assertEqual(sysmon.format_age(90), "1 m")
        self.assertEqual(sysmon.format_age(3600), "1 h")
        self.assertEqual(sysmon.format_age(6 * 3600), "6 h")
        self.assertEqual(sysmon.format_age(3 * 86400), "3 d")

    def test_display_name_headless_chrome(self):
        proc = sysmon.Proc(1, 1, 501, 0, 0, 0.0,
                           "/Applications/Google Chrome.app/Contents/MacOS/"
                           "Google Chrome --headless --user-data-dir=/tmp/"
                           "cdp-prof-9603")
        self.assertEqual(sysmon.display_name(proc), "Google Chrome (headless)")

    def test_display_name_esbuild(self):
        proc = sysmon.Proc(1, 1, 501, 0, 0, 0.0, "/x/bin/esbuild --service")
        self.assertEqual(sysmon.display_name(proc), "esbuild --service")

    def test_display_name_dev_server(self):
        proc = sysmon.Proc(1, 1, 501, 0, 0, 0.0,
                           "/usr/local/bin/node /x/serve-dist.mjs")
        self.assertEqual(sysmon.display_name(proc), "node serve-dist.mjs")

    def test_display_sub_cdp_profile(self):
        proc = sysmon.Proc(22493, 1, 501, 0, 0, 0.0,
                           "/Applications/Google Chrome.app/Contents/MacOS/"
                           "Google Chrome --headless --user-data-dir=/tmp/"
                           "cdp-prof-9603")
        self.assertEqual(sysmon.display_sub(proc), "pid 22493 · cdp-prof-9603")


class TestBuildView(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SMARTBAR_SYSMON_AUTOKILL", None)
        self.now = datetime(2026, 8, 23, 21, 24, tzinfo=timezone.utc)

    def procs(self):
        return [
            # headless Chrome orphan tree (root 100 + GPU helper 101)
            sysmon.Proc(100, 1, 501, 50_000, 21600, 5.0,
                        "/Applications/Google Chrome.app/Contents/MacOS/"
                        "Google Chrome --headless --user-data-dir=/tmp/"
                        "cdp-prof-9603", start=1000),
            sysmon.Proc(101, 100, 501, 400_000, 21600, 575.0,
                        "Google Chrome Helper (GPU) --type=gpu-process",
                        start=1000),
            # an orphaned dev server (idle)
            sysmon.Proc(210, 1, 501, 30_000, 3 * 86400, 0.0,
                        "/usr/local/bin/node /x/serve-dist.mjs", start=500),
            # two Firefox processes (independent apps, ppid 1) — a fold
            sysmon.Proc(300, 1, 501, 500_000, 100, 20.0,
                        "/Applications/Firefox.app/Contents/MacOS/firefox",
                        start=100),
            sysmon.Proc(301, 1, 501, 300_000, 100, 18.0,
                        "/Applications/Firefox.app/Contents/MacOS/firefox "
                        "-contentproc", start=100),
            # claude session + node MCP child
            sysmon.Proc(400, 1, 501, 900_000, 3600, 60.0,
                        "/Users/ductran/.local/bin/claude", start=3000),
            sysmon.Proc(401, 400, 501, 100_000, 3600, 55.0,
                        "/usr/local/bin/node /x/mcp-server.js", start=3100),
            # another user's process (system)
            sysmon.Proc(2, 1, 0, 100_000, 99999, 60.0,
                        "/System/Library/.../WindowServer", start=1),
        ]

    def view(self, prev=None):
        return sysmon.build_view(
            procs=self.procs(), cores=[40, 30, 10, 0], mem={
                "totalBytes": 32 * 2**30, "usedBytes": 17 * 2**30 + 2**29,
                "pct": 54.9, "compressedBytes": 3 * 2**30},
            load=(5.2, 8.2, 23.4), prev_cpu=prev or {}, now=self.now,
            my_uid=501, own_pids={9999}, history=[10, None, 84, 91, 12])

    def test_leftovers_lists_orphan_junk_and_idle(self):
        view = self.view()
        kinds = [r["kind"] for r in view["leftovers"]["rows"]]
        self.assertIn("junk", kinds)
        self.assertIn("idle", kinds)

    def test_headless_row_sums_helper_cpu_into_the_tree(self):
        row = next(r for r in self.view()["leftovers"]["rows"]
                   if r["kind"] == "junk")
        self.assertEqual(row["name"], "Google Chrome (headless)")
        self.assertEqual(row["token"], "100:1000")
        self.assertTrue(row["burning"])          # 5 + 575 >= 50
        self.assertIn("580%", row["meta"])       # tree cpu, one decimal dropped

    def test_idle_dev_server_is_not_burning(self):
        row = next(r for r in self.view()["leftovers"]["rows"]
                   if r["kind"] == "idle")
        self.assertFalse(row["burning"])
        self.assertIn("idle", row["meta"])

    def test_leftovers_chip_counts_burning_cores(self):
        self.assertIn("burning", self.view()["leftovers"]["chip"])

    def test_leftovers_sorted_burning_first(self):
        kinds = [r["kind"] for r in self.view()["leftovers"]["rows"]]
        self.assertEqual(kinds[0], "junk")       # burning before idle

    def test_busy_folds_same_name_processes(self):
        firefox = next(r for r in self.view()["busy"]["rows"]
                       if r["name"] == "Firefox")
        self.assertEqual(firefox["count"], 2)
        self.assertTrue(firefox["killable"])
        self.assertTrue(firefox["token"].startswith("group:"))

    def test_busy_session_is_not_killable(self):
        claude = next(r for r in self.view()["busy"]["rows"]
                      if r["kind"] == "session")
        self.assertFalse(claude["killable"])

    def test_busy_system_is_not_killable(self):
        row = next((r for r in self.view()["busy"]["rows"]
                    if r["kind"] == "system"), None)
        self.assertIsNotNone(row)
        self.assertFalse(row["killable"])

    def test_session_child_never_appears_as_a_leftover(self):
        tokens = [r["token"] for r in self.view()["leftovers"]["rows"]]
        self.assertNotIn("401:3100", tokens)     # the MCP child is a session

    def test_cpu_block(self):
        view = self.view()
        self.assertEqual(view["cpu"]["pct"], 20)   # mean(40,30,10,0)
        self.assertEqual(view["cpu"]["cores"], [40, 30, 10, 0])
        self.assertIn("procs", view["cpu"]["caption"])

    def test_mem_block(self):
        view = self.view()
        self.assertEqual(view["mem"]["pct"], 54.9)
        self.assertIn("GB", view["mem"]["caption"])

    def test_history_block(self):
        view = self.view()
        self.assertEqual(view["history"]["pct"], [10, None, 84, 91, 12])
        self.assertEqual(view["history"]["lastPct"], 12)
        self.assertIn("91", view["history"]["peakText"])

    def test_cores_capped_and_paired_beyond_max(self):
        many = list(range(64))
        view = sysmon.build_view(procs=[], cores=many, mem={
            "totalBytes": 1, "usedBytes": 0, "pct": 0.0}, load=(0, 0, 0),
            prev_cpu={}, now=self.now, my_uid=501, own_pids=set(), history=[])
        self.assertLessEqual(len(view["cpu"]["cores"]), sysmon.MAX_CORE_COLUMNS)

    def test_foot_reports_autokill_state(self):
        self.assertIn("Auto-kill off", self.view()["leftovers"]["foot"])


class TestHistoryRing(unittest.TestCase):
    def test_append_grows_then_caps_at_60(self):
        ring = []
        for minute in range(70):
            ring = sysmon.history_append(ring, minute, minute)
        self.assertEqual(len(ring), sysmon.HISTORY_LEN)
        self.assertEqual(ring[-1], (69, 69))

    def test_same_minute_updates_not_duplicates(self):
        ring = sysmon.history_append([], 100, 40)
        ring = sysmon.history_append(ring, 100, 55)
        self.assertEqual(ring, [(100, 55)])

    def test_a_gap_between_minutes_is_left_as_none(self):
        ring = sysmon.history_append([], 100, 40)
        ring = sysmon.history_append(ring, 103, 50)   # minutes 101,102 missed
        self.assertEqual(sysmon.history_series(ring, 103, span=4),
                         [40, None, None, 50])


class TestAutokillAndAlerts(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SMARTBAR_SYSMON_AUTOKILL", None)

    def rows(self):
        return [
            {"token": "100:1000", "kind": "junk", "name": "Google Chrome "
             "(headless)", "burning": True, "cores": 5.8, "age": 6 * 3600},
            {"token": "210:500", "kind": "idle", "name": "node serve-dist.mjs",
             "burning": False, "cores": 0.0, "age": 3 * 86400},
        ]

    def test_autokill_targets_only_aged_junk_when_enabled(self):
        os.environ["SMARTBAR_SYSMON_AUTOKILL"] = "on"
        targets = sysmon.autokill_targets(self.rows(), first_seen={
            "100:1000": 0.0}, now_monotonic=400.0)
        self.assertEqual(targets, ["100:1000"])   # junk, seen 400s ago

    def test_autokill_skips_junk_seen_under_5_min(self):
        os.environ["SMARTBAR_SYSMON_AUTOKILL"] = "on"
        targets = sysmon.autokill_targets(self.rows(), first_seen={
            "100:1000": 100.0}, now_monotonic=200.0)
        self.assertEqual(targets, [])   # only 100s old

    def test_autokill_never_touches_idle(self):
        os.environ["SMARTBAR_SYSMON_AUTOKILL"] = "on"
        targets = sysmon.autokill_targets(
            [self.rows()[1]], first_seen={"210:500": 0.0}, now_monotonic=9e9)
        self.assertEqual(targets, [])

    def test_autokill_disabled_returns_nothing(self):
        targets = sysmon.autokill_targets(self.rows(), first_seen={
            "100:1000": 0.0}, now_monotonic=9e9)
        self.assertEqual(targets, [])

    def test_alert_when_leftovers_burn_and_autokill_off(self):
        alerts = sysmon.alerts(self.rows(), autokilled=[])
        self.assertEqual(len(alerts), 1)
        self.assertIn("burning", alerts[0]["title"].lower())

    def test_no_alert_when_nothing_burns(self):
        idle_only = [self.rows()[1]]
        self.assertEqual(sysmon.alerts(idle_only, autokilled=[]), [])

    def test_alert_for_each_autokilled_process(self):
        killed = [{"name": "Google Chrome (headless)", "cores": 5.8,
                   "age": 6 * 3600}]
        alerts = sysmon.alerts([], autokilled=killed)
        self.assertEqual(len(alerts), 1)
        self.assertIn("Killed", alerts[0]["title"])
        self.assertIn("Google Chrome", alerts[0]["title"])


if __name__ == "__main__":
    unittest.main()
