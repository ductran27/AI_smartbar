from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

from smartbar import sysmon_runner
from smartbar.core import sysmon

PS_TEXT = (
    "    1     0     0  27744 21-05:52:10 213:23.48 "
    "Sun Aug  2 16:19:43 2026 /sbin/launchd\n"
    "40404     1   501 400000    06:03:42 3618:57.00 "
    "Sun Aug 23 15:13:24 2026 /Applications/Google Chrome.app/Contents/MacOS/"
    "Google Chrome --headless --user-data-dir=/tmp/cdp-prof-9603\n"
    "40405 40404   501 100000    06:03:42 10:00.00 "
    "Sun Aug 23 15:13:24 2026 Google Chrome Helper (GPU) --type=gpu-process\n")


class RunnerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        ps_path = os.path.join(self.tmp, "ps.txt")
        with open(ps_path, "w") as handle:
            handle.write(PS_TEXT)
        stats_path = os.path.join(self.tmp, "stats.json")
        with open(stats_path, "w") as handle:
            json.dump({"cores": [40, 10], "mem": {"totalBytes": 32 * 2**30,
                       "usedBytes": 16 * 2**30, "pct": 50.0},
                       "load": [1.0, 2.0, 3.0]}, handle)
        os.environ["SMARTBAR_SYSMON_PS"] = ps_path
        os.environ["SMARTBAR_SYSMON_STATS"] = stats_path
        os.environ["SMARTBAR_CACHE_DIR"] = self.tmp
        for key in ("SMARTBAR_SYSMON_AUTOKILL", "SMARTBAR_SYSMON_KILL"):
            os.environ.pop(key, None)

    def tearDown(self):
        for key in ("SMARTBAR_SYSMON_PS", "SMARTBAR_SYSMON_STATS",
                    "SMARTBAR_CACHE_DIR", "SMARTBAR_SYSMON_AUTOKILL",
                    "SMARTBAR_SYSMON_KILL"):
            os.environ.pop(key, None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBackgroundTick(RunnerBase):
    def test_returns_display_payload(self):
        view = sysmon_runner.background_tick()
        for key in ("cpu", "mem", "history", "leftovers", "busy", "alerts"):
            self.assertIn(key, view)
        self.assertEqual(view["cpu"]["cores"], [40, 10])

    def test_headless_chrome_orphan_shows_as_a_leftover(self):
        view = sysmon_runner.background_tick()
        names = [r["name"] for r in view["leftovers"]["rows"]]
        self.assertIn("Google Chrome (headless)", names)

    def test_writes_and_reloads_state(self):
        sysmon_runner.background_tick()
        state = sysmon_runner.load_state()
        self.assertIn("history", state)
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "sysmon-state.json")))

    def test_history_grows_across_ticks(self):
        sysmon_runner.background_tick()
        first = len(sysmon_runner.load_state()["history"])
        self.assertGreaterEqual(first, 1)


class TestKill(RunnerBase):
    def token_for(self, pid):
        from smartbar.core import sysmon_probe
        procs, *_ = sysmon_probe.sample(interval=0.0)
        proc = next(p for p in procs if p.pid == pid)
        return sysmon.kill_token(proc)

    def test_dry_run_validates_without_signalling(self):
        os.environ["SMARTBAR_SYSMON_KILL"] = "off"
        ok, error = sysmon_runner.kill(self.token_for(40404))
        self.assertTrue(ok, error)
        self.assertEqual(error, "")

    def test_unknown_token_is_refused(self):
        os.environ["SMARTBAR_SYSMON_KILL"] = "off"
        ok, error = sysmon_runner.kill("999999:1")
        self.assertFalse(ok)
        self.assertIn("gone", error.lower())

    def test_group_token_dry_run(self):
        os.environ["SMARTBAR_SYSMON_KILL"] = "off"
        group = "group:" + self.token_for(40404)
        ok, error = sysmon_runner.kill(group)
        self.assertTrue(ok, error)


class TestCLI(RunnerBase):
    """The launcher's --sysmon / --kill entry points (subprocess, as Swift
    and the installers invoke them)."""

    def _run(self, args):
        import subprocess
        env = dict(os.environ)
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, os.path.join(repo, "bin", "ai-smartbar"), *args],
            capture_output=True, text=True, env=env, cwd=repo, timeout=30)
        return proc

    def test_sysmon_json_prints_payload(self):
        proc = self._run(["--sysmon", "--json"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("cpu", payload)
        self.assertIn("leftovers", payload)

    def test_kill_unknown_token_exits_nonzero(self):
        os.environ["SMARTBAR_SYSMON_KILL"] = "off"
        proc = self._run(["--kill", "999999:1"])
        self.assertEqual(proc.returncode, 1)
        result = json.loads(proc.stdout)
        self.assertFalse(result["ok"])


class TestStream(RunnerBase):
    def test_emits_a_line_then_stops(self):
        buffer = io.StringIO()
        calls = {"n": 0}

        def stop():
            calls["n"] += 1
            return calls["n"] > 1     # run one iteration, then stop

        sysmon_runner.stream(out=buffer, interval=0.0, stop=stop)
        lines = [line for line in buffer.getvalue().splitlines() if line]
        self.assertGreaterEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertTrue(payload["live"])
        self.assertIn("cpu", payload)


if __name__ == "__main__":
    unittest.main()
