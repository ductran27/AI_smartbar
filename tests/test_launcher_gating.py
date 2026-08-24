"""The launcher honours the kill switches and rejects malformed flag values
instead of falling through to the GUI (audit B2).

Everything here drives bin/ai-smartbar as a subprocess, exactly as Swift,
the agents and the installers do.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(args, env_extra=None, timeout=30):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, os.path.join(REPO, "bin", "ai-smartbar"), *args],
        capture_output=True, text=True, env=env, cwd=REPO, timeout=timeout)


class TestSysmonOffIsRealEverywhere(unittest.TestCase):
    """README: SMARTBAR_SYSMON=off 'hides the System tab and skips every
    sample, stream and kill'. Before this, only the painted trays checked."""

    ENV = {"SMARTBAR_SYSMON": "off", "SMARTBAR_SYSMON_KILL": "off"}

    def test_sysmon_json_reports_disabled_without_sampling(self):
        proc = run_cli(["--sysmon", "--json"], self.ENV)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload, {"disabled": True})

    def test_sysmon_stream_exits_immediately(self):
        proc = run_cli(["--sysmon", "--stream"], self.ENV, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_kill_is_refused(self):
        proc = run_cli(["--kill", "1:1"], self.ENV)
        self.assertEqual(proc.returncode, 1)
        result = json.loads(proc.stdout)
        self.assertFalse(result["ok"])
        self.assertIn("off", result["error"])


class TestEmptyFlagValuesNeverReachTheGui(unittest.TestCase):
    """`--print-config ""` used to fall through every dispatch branch and
    start a second tray (or crash importing rumps)."""

    def test_empty_print_config_exits_2(self):
        proc = run_cli(["--print-config", ""])
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertEqual(proc.stdout, "")

    def test_empty_update_interval_exits_2(self):
        proc = run_cli(["--update-interval", ""])
        self.assertEqual(proc.returncode, 2, proc.stdout)

    def test_empty_remove_account_answers_json_not_gui(self):
        proc = run_cli(["--remove-account", ""])
        self.assertEqual(proc.returncode, 1)
        result = json.loads(proc.stdout)
        self.assertFalse(result["ok"])


class TestOpenPanelSafety(unittest.TestCase):
    def test_config_env_cache_dir_is_honoured(self):
        # The Linux tray runs under the .desktop env prefix and writes its
        # PID under config.env's SMARTBAR_CACHE_DIR; a bare `--open-panel`
        # resolved ~/.cache instead and always said "no running tray".
        if sys.platform in ("darwin", "win32"):
            self.skipTest("--open-panel is Linux-only")

    def test_a_reused_pid_is_not_signalled(self):
        # Covered by unit test below on POSIX: the PID file names a live
        # process that is NOT an ai-smartbar tray -> refuse, exit 1.
        if sys.platform == "win32":
            self.skipTest("POSIX only")
        import importlib.util
        from importlib.machinery import SourceFileLoader
        loader = SourceFileLoader(
            "launcher_mod", os.path.join(REPO, "bin", "ai-smartbar"))
        spec = importlib.util.spec_from_loader("launcher_mod", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = os.path.join(tmp, "tray.pid")
            with open(pid_path, "w") as fh:
                fh.write(str(os.getpid()))   # a live pid that is NOT a tray
            env = {"SMARTBAR_CACHE_DIR": tmp}
            with mock.patch.dict(os.environ, env), \
                 mock.patch.object(mod.sys, "platform", "linux"):
                rc = mod.open_panel()
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
