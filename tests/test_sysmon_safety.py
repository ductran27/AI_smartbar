"""Audit-driven safety and correctness pins for the System tab (2026-08-24).

Each class documents one audited defect. The dangerous one first: a kill
must never take down a live claude/codex session, the app itself, or a
process that merely shares a display name with the target.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest import mock

from smartbar import sysmon_runner
from smartbar.core import sysmon, sysmon_probe

UID = 501


def proc(pid, ppid, args, cpu=0.0, uid=UID, rss=10_000, elapsed=7200,
         start=1000):
    return sysmon.Proc(pid=pid, ppid=ppid, uid=uid, rss_kb=rss,
                       elapsed=elapsed, cpu=cpu, args=args, start=start)


def view_of(procs, prev=None, own=frozenset({99998, 99999}), history=()):
    return sysmon.build_view(procs, [50, 50], {"totalBytes": 2**34,
                             "usedBytes": 2**33, "pct": 50.0}, (1, 1, 1),
                             prev or {}, datetime(2026, 8, 24, 12, 0),
                             UID, set(own), list(history) or [None] * 60)


class TestLeftoverTokensAreTreeScoped(unittest.TestCase):
    def test_leftover_rows_carry_tree_tokens(self):
        # A leftover row means "kill the whole tree" — the token must SAY so,
        # because a Busy token with the same pid:start shape means only one
        # process. sysmon_runner.kill dispatches on the prefix.
        rows = view_of([proc(10, 1, "/opt/x/esbuild --service", cpu=90)])[
            "leftovers"]["rows"]
        self.assertEqual(rows[0]["token"], "tree:10:1000")

    def test_busy_tokens_stay_plain(self):
        prev = {20: 90.0}
        rows = view_of([proc(20, 5, "/Applications/Zalo.app/Contents/MacOS/Zalo",
                             cpu=90)], prev=prev)["busy"]["rows"]
        self.assertEqual(rows[0]["token"], "20:1000")


class TestBusyRespectsTheHotThreshold(unittest.TestCase):
    def test_a_14_percent_fold_is_not_busy(self):
        # The card caption promises ">= 50% CPU over two samples"; a 14%
        # iTerm must not appear there labelled hot.
        prev = {30: 14.0}
        view = view_of([proc(30, 1, "/Applications/iTerm.app/Contents/MacOS/iTerm2",
                             cpu=14)], prev=prev)
        self.assertEqual(view["busy"]["rows"], [])

    def test_hot_needs_both_samples(self):
        view = view_of([proc(31, 1, "/Applications/iTerm.app/Contents/MacOS/iTerm2",
                             cpu=90)], prev={31: 2.0})
        self.assertEqual(view["busy"]["rows"], [])

    def test_hot_in_both_samples_shows(self):
        view = view_of([proc(32, 1, "/Applications/iTerm.app/Contents/MacOS/iTerm2",
                             cpu=90)], prev={32: 80.0})
        names = [r["name"] for r in view["busy"]["rows"]]
        self.assertIn("iTerm", names)

    def test_sessions_above_threshold_show_unkillable(self):
        view = view_of([proc(33, 1, "/Users/dev/.local/bin/claude", cpu=90)],
                       prev={33: 90.0})
        row = view["busy"]["rows"][0]
        self.assertEqual(row["kind"], "session")
        self.assertFalse(row["killable"])

    def test_a_low_cpu_session_stays_off_the_busy_card(self):
        view = view_of([proc(34, 1, "/Users/dev/.local/bin/claude", cpu=12)],
                       prev={34: 12.0})
        self.assertEqual(view["busy"]["rows"], [])


class TestWatchedTreesStayOutOfBusy(unittest.TestCase):
    def test_live_headless_chrome_helpers_do_not_fold_into_the_real_browser(self):
        # A LIVE cdp session's GPU helper must not merge into the user's real
        # "Google Chrome" fold — the group token would include the real
        # browser's pid and Kill would close it.
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        procs = [
            proc(40, 39, "node /x/cdp.mjs", cpu=2),                    # live script
            proc(41, 40, chrome + " --headless --user-data-dir=/tmp/cdp-prof-1",
                 cpu=5),                                               # watch root
            proc(42, 41, chrome + " --type=gpu-process", cpu=500),     # helper
            proc(43, 1, chrome, cpu=90),                               # real browser
        ]
        view = view_of(procs, prev={42: 500.0, 43: 90.0})
        chrome_rows = [r for r in view["busy"]["rows"]
                       if r["name"] == "Google Chrome"]
        self.assertEqual(len(chrome_rows), 1)
        self.assertEqual(chrome_rows[0]["token"], "43:1000")


class TestSelfIsNeverListedNorKillable(unittest.TestCase):
    def test_own_sampler_child_is_not_a_busy_row(self):
        # The stream sampler is a sibling child of the app: ppid in own_pids.
        view = view_of([proc(50, 99999, "/usr/bin/python3 /x/bin/ai-smartbar --sysmon",
                             cpu=90)], prev={50: 90.0})
        self.assertEqual(view["busy"]["rows"], [])

    def test_ai_smartbar_by_exe_is_excluded_even_with_unrelated_ppid(self):
        view = view_of([proc(51, 1, "/usr/bin/python3 /x/bin/ai-smartbar --sysmon --stream",
                             cpu=90)], prev={51: 90.0})
        self.assertEqual(view["busy"]["rows"], [])
        self.assertEqual(view["leftovers"]["rows"], [])


class TestClassificationRules(unittest.TestCase):
    def test_a_path_component_named_next_is_not_a_dev_server(self):
        p = proc(60, 1, "node /Users/x/next-app/scripts/watch-uploads.js")
        self.assertIsNone(sysmon.classify(p, True, 0.0, 0.0, UID))

    def test_a_real_next_server_still_is(self):
        p = proc(61, 1, "node /x/node_modules/.bin/next start")
        self.assertEqual(sysmon.classify(p, True, 0.0, 0.0, UID), "idle")

    def test_npm_installed_claude_code_is_a_session(self):
        p = proc(62, 1,
                 "node /x/node_modules/@anthropic-ai/claude-code/cli.js")
        self.assertEqual(sysmon.classify(p, True, 0.0, 0.0, UID), "session")

    def test_npm_installed_codex_is_a_session(self):
        p = proc(63, 1, "node /x/node_modules/@openai/codex/bin/codex.js")
        self.assertEqual(sysmon.classify(p, True, 0.0, 0.0, UID), "session")


class TestAutokillSeesEveryJunkRow(unittest.TestCase):
    def test_junk_beyond_the_display_cap_is_still_tracked(self):
        procs = [proc(100 + i, 1, f"/opt/x/esbuild --service --id={i}", cpu=2)
                 for i in range(10)]
        view = view_of(procs)
        self.assertEqual(len(view["leftovers"]["rows"]), 8)      # display cap
        self.assertEqual(len(view["leftovers"]["junk"]), 10)     # policy set
        self.assertEqual(view["leftovers"]["more"], 2)

    def test_burning_count_comes_from_the_full_set(self):
        procs = [proc(200 + i, 1, f"/opt/x/esbuild --service --id={i}", cpu=300)
                 for i in range(9)]
        view = view_of(procs)
        self.assertEqual(view["leftovers"]["burning"], 9)

    def test_a_junk_tree_holding_a_session_is_not_autokill_safe(self):
        procs = [
            proc(70, 1, "/bin/zsh -c source /Users/dev/.claude/shell-snapshots/s.sh",
                 cpu=2),
            proc(71, 70, "/Users/dev/.local/bin/claude -p run", cpu=1),
        ]
        view = view_of(procs)
        junk = {j["token"]: j for j in view["leftovers"]["junk"]}
        self.assertFalse(junk["tree:70:1000"]["autokillSafe"])

    def test_a_junk_shell_running_a_dev_server_is_not_autokill_safe(self):
        procs = [
            proc(72, 1, "/bin/zsh -c source /Users/dev/.claude/shell-snapshots/s.sh",
                 cpu=2),
            proc(73, 72, "node /x/node_modules/.bin/vite preview", cpu=1),
        ]
        view = view_of(procs)
        junk = {j["token"]: j for j in view["leftovers"]["junk"]}
        self.assertFalse(junk["tree:72:1000"]["autokillSafe"])

    def test_autokill_targets_only_safe_junk(self):
        rows = [
            {"token": "tree:1:5", "kind": "junk", "autokillSafe": True},
            {"token": "tree:2:5", "kind": "junk", "autokillSafe": False},
        ]
        seen = {"tree:1:5": 0.0, "tree:2:5": 0.0}
        with mock.patch.dict(os.environ, {"SMARTBAR_SYSMON_AUTOKILL": "on"}):
            self.assertEqual(sysmon.autokill_targets(rows, seen, 10_000.0),
                             ["tree:1:5"])


class TestAlertKeysAreStable(unittest.TestCase):
    def test_burning_alert_key_does_not_embed_the_core_count(self):
        # Dedupe happens on `key`; the sampled core count flaps between
        # ticks (5 → 6 → 5 cores) and must not re-arm the notification.
        rows = [{"token": "tree:1:5", "kind": "junk", "burning": True,
                 "cores": 5.2, "name": "x", "age": 100}]
        rows_flap = [dict(rows[0], cores=6.1)]
        first = sysmon.alerts(rows, [])
        second = sysmon.alerts(rows_flap, [])
        self.assertEqual(first[0]["key"], second[0]["key"])

    def test_autokill_alert_carries_a_key(self):
        out = sysmon.alerts([], [{"name": "esbuild --service", "cores": 2.0,
                                  "age": 400, "token": "tree:9:9"}])
        self.assertTrue(out[0]["key"].startswith("killed:"))


class TestKillDispatch(unittest.TestCase):
    """sysmon_runner.kill semantics, against a stubbed table + dry-run seam."""

    def setUp(self):
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        self.table = {p.pid: p for p in [
            proc(10, 1, chrome + " --headless --user-data-dir=/tmp/cdp-prof-9",
                 cpu=5),
            proc(11, 10, chrome + " --type=gpu-process", cpu=500),
            proc(12, 10, "/Users/dev/.local/bin/claude -p x", cpu=1),
            proc(13, 12, "node /x/mcp-server.js", cpu=1),
            proc(20, 1, "/Applications/Firefox.app/Contents/MacOS/firefox",
                 cpu=60),
            proc(21, 20, "/Applications/Firefox.app/Contents/MacOS/plugin-container",
                 cpu=40),
        ]}
        self.signalled = []
        patches = [
            mock.patch.object(sysmon_runner, "_current_table",
                              lambda: self.table),
            mock.patch.object(sysmon_runner, "_my_uid", lambda: UID),
            mock.patch.object(sysmon_runner, "_own_pids",
                              lambda: {99998, 99999}),
            mock.patch.object(sysmon_runner, "_signal",
                              lambda pids, sig: self.signalled.append(
                                  (sorted(pids), sig))),
            mock.patch.object(sysmon_runner, "_alive", lambda pid: False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_tree_kill_spares_session_descendants(self):
        ok, error = sysmon_runner.kill("tree:10:1000")
        self.assertTrue(ok, error)
        pids = self.signalled[0][0]
        self.assertIn(10, pids)
        self.assertIn(11, pids)
        self.assertNotIn(12, pids, "claude session must survive a tree kill")
        self.assertNotIn(13, pids, "the session's MCP server must survive")

    def test_group_kill_signals_members_only_and_gone_is_success(self):
        # 21 is a member; its parent 20 is another member. No tree expansion:
        # exactly the two members are signalled, once, in one round.
        ok, error = sysmon_runner.kill("group:20:1000,21:1000")
        self.assertTrue(ok, error)
        self.assertEqual(len([s for s in self.signalled]), 1)
        self.assertEqual(self.signalled[0][0], [20, 21])

    def test_group_member_already_gone_is_not_a_failure(self):
        ok, error = sysmon_runner.kill("group:20:1000,999:1")
        self.assertTrue(ok, error)
        self.assertEqual(self.signalled[0][0], [20])

    def test_group_with_no_valid_member_reports_the_reason(self):
        ok, error = sysmon_runner.kill("group:999:1")
        self.assertFalse(ok)
        self.assertIn("gone", error)

    def test_plain_token_kills_exactly_one_process(self):
        ok, error = sysmon_runner.kill("20:1000")
        self.assertTrue(ok, error)
        self.assertEqual(self.signalled[0][0], [20])

    def test_a_session_member_of_a_group_is_skipped(self):
        ok, error = sysmon_runner.kill("group:20:1000,12:1000")
        self.assertTrue(ok, error)
        self.assertEqual(self.signalled[0][0], [20])


class TestRunnerOrdering(unittest.TestCase):
    def test_history_includes_the_current_minute(self):
        # The poll must append THIS tick's point before building the series —
        # the newest column was always None.
        import json
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ps = os.path.join(tmp, "ps.txt")
        with open(ps, "w") as fh:
            fh.write("    1     0     0  27744 21-05:52:10 213:23.48 "
                     "Sun Aug  2 16:19:43 2026 /sbin/launchd\n")
        stats = os.path.join(tmp, "stats.json")
        with open(stats, "w") as fh:
            json.dump({"cores": [40, 40], "mem": {"totalBytes": 1, "usedBytes": 1,
                       "pct": 50.0}, "load": [0, 0, 0]}, fh)
        env = {"SMARTBAR_SYSMON_PS": ps, "SMARTBAR_SYSMON_STATS": stats,
               "SMARTBAR_CACHE_DIR": tmp}
        with mock.patch.dict(os.environ, env):
            view = sysmon_runner.background_tick()
        self.assertEqual(view["history"]["pct"][-1], 40)
        self.assertEqual(view["history"]["lastPct"], 40)
        # The Mac honours SMARTBAR_SYSMON_INTERVAL through the payload.
        self.assertEqual(view["pollInterval"], sysmon.interval())


class TestProbeCorrectness(unittest.TestCase):
    def test_memsize_does_not_depend_on_path(self):
        if not sysmon_probe.sys.platform == "darwin":
            self.skipTest("darwin only")
        real = os.environ.get("PATH")
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            size = sysmon_probe._memsize()
        os.environ["PATH"] = real
        self.assertGreater(size, 2**30)

    def test_cpu_seconds_with_days(self):
        self.assertEqual(sysmon_probe.cpu_seconds("1-02:03:04"),
                         86400 + 2 * 3600 + 3 * 60 + 4)

    def test_vm_stat_used_matches_activity_monitor(self):
        text = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                "Pages free:                              100.\n"
                "Pages active:                           1000.\n"
                "Pages inactive:                          900.\n"
                "Pages purgeable:                         200.\n"
                "Anonymous pages:                        1500.\n"
                "Pages wired down:                        400.\n"
                "Pages occupied by compressor:            300.\n")
        mem = sysmon_probe.parse_vm_stat(text, 16384 * 4000)
        # Activity Monitor: anonymous - purgeable + wired + compressor.
        self.assertEqual(mem["usedBytes"], (1500 - 200 + 400 + 300) * 16384)

    def test_ps_runs_under_the_c_locale(self):
        calls = []

        def fake_check_output(cmd, text=True, env=None, **kwargs):
            # **kwargs: on Windows _run also passes portable.no_window()'s
            # creationflags; the fake must accept the real call shape.
            calls.append(env)
            return ""
        with mock.patch.object(sysmon_probe.subprocess, "check_output",
                               fake_check_output):
            sysmon_probe._run(["ps", "-Axwwo", "pid="])
        self.assertEqual(calls[0].get("LC_ALL"), "C")


class TestStreamCadence(unittest.TestCase):
    def test_one_line_per_interval_including_sample_width(self):
        sleeps = []
        with mock.patch.object(sysmon_runner, "_build",
                               lambda side_effects: ({"cpu": {}}, [], [])), \
             mock.patch.object(sysmon_runner.time, "sleep", sleeps.append):
            calls = {"n": 0}

            def stop():
                calls["n"] += 1
                return calls["n"] > 1
            sysmon_runner.stream(out=mock.Mock(), interval=1.0, stop=stop)
        self.assertEqual(sleeps, [0.5], "sleep must subtract the 0.5s sample")


if __name__ == "__main__":
    unittest.main()
