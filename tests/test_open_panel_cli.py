"""Tests for bin/ai-smartbar's --open-panel: the CLI half of the
open-panel hotkey feature.

See docs/superpowers/specs/2026-08-16-open-panel-hotkey-design.md for the
full design and smartbar/linux/tray.py's PID_FILE / SIGUSR1 handler for the
tray side this talks to. Runs the real launcher as a subprocess -- the same
technique tests/test_device_config.py's TestTheCliContract and
tests/test_windows_installer.py's run_launcher already use for bin/
ai-smartbar, since it has no .py extension and is a "#!/usr/bin/env
python3" script meant to be run, not imported.

There is no live Linux tray in either sandbox this normally runs in (macOS
dev, Ubuntu CI), so "found a running tray, it opened" is never exercised
here for real -- what IS pinned is that the Linux path degrades cleanly
with no running tray, that a dead PID left behind by a crash is reported
rather than silently ignored, and that macOS/Windows refuse outright
instead of doing nothing quietly (each has its own real, in-process hotkey
instead -- see AISmartbarApp.swift's key monitor and windows/tray.py's
RegisterHotKey thread).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(REPO, "bin", "ai-smartbar")

_NOT_LINUX = sys.platform in ("darwin", "win32")


def _run(*args, cache_dir):
    return subprocess.run(
        [sys.executable, LAUNCHER, *args],
        capture_output=True, text=True,
        env=dict(os.environ, SMARTBAR_CACHE_DIR=cache_dir))


class TestOpenPanelFlag(unittest.TestCase):
    def test_the_flag_is_advertised_in_help(self):
        done = subprocess.run([sys.executable, LAUNCHER, "--help"],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--open-panel", done.stdout)

    def test_behaves_correctly_for_whichever_platform_runs_this_suite(self):
        """darwin/win32 refuse outright; every other POSIX (what CI's
        ubuntu leg and most Linux dev boxes are) takes the real code path
        and fails cleanly because nothing wrote a PID file into the empty
        SMARTBAR_CACHE_DIR this test points it at."""
        with tempfile.TemporaryDirectory() as tmp:
            done = _run("--open-panel", cache_dir=tmp)
        self.assertEqual(done.returncode, 1)
        if _NOT_LINUX:
            self.assertIn("Linux-only", done.stderr)
        else:
            self.assertIn("no running", done.stderr.lower())
            self.assertIn(os.path.join(tmp, "tray.pid"), done.stderr)


@unittest.skipIf(_NOT_LINUX, "exercises the Linux PID-signalling code path")
class TestLinuxSignalling(unittest.TestCase):
    """The real branch this feature exists for: a PID file naming a dead
    (or never-existent) process must be reported, not silently swallowed
    or mistaken for a live tray."""

    def test_a_pid_file_naming_a_dead_process_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = os.path.join(tmp, "tray.pid")
            # PID 1 recycling onto this exact number during a test run is
            # not a real risk on any of the hosts this suite runs on
            # (macOS dev boxes never take this branch; Ubuntu CI containers
            # do not run test processes as PID 1's actual owner), so a
            # very large, almost-certainly-unassigned PID is enough without
            # reaching for /proc.
            with open(pid_path, "w") as handle:
                handle.write("999999")
            done = _run("--open-panel", cache_dir=tmp)
        self.assertEqual(done.returncode, 1)
        self.assertIn("999999", done.stderr)
        self.assertIn("not running", done.stderr)

    def test_a_pid_file_with_garbage_content_is_reported_like_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = os.path.join(tmp, "tray.pid")
            with open(pid_path, "w") as handle:
                handle.write("not-a-pid")
            done = _run("--open-panel", cache_dir=tmp)
        self.assertEqual(done.returncode, 1)
        self.assertIn("no running", done.stderr.lower())


if __name__ == "__main__":
    unittest.main()
