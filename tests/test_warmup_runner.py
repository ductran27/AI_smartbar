"""Tests for smartbar.warmup_runner argv/env construction — no subprocesses.

These pin the two launchd bugs that silently broke v1 warmup:
- cswap resolves `claude` itself via PATH, so the subprocess env must
  carry a PATH containing the claude CLI (launchd hands agents a bare one);
- everything after `--` in `cswap run` is passed to claude as ARGUMENTS,
  so the claude binary path must never appear there.

The Windows half covers the same two concerns on that platform — `claude`
discovery under the install locations Windows actually uses, and the PATH
fallback — plus the one that matters most: that notify_failure picks its
mechanism by branching on the platform instead of falling through to a
POSIX-only tool. Falling through raises FileNotFoundError, an OSError
subclass that the blanket handler swallows, which is exactly how every
warmup failure notification on Windows vanished without a trace.
"""
import ntpath
import os
import sys
import tempfile
import unittest
from unittest import mock

from smartbar import warmup_runner


class Env(unittest.TestCase):
    def setUp(self):
        self.saved = {name: os.environ.get(name)
                      for name in ("SMARTBAR_CSWAP", "PATH")}
        os.environ["SMARTBAR_CSWAP"] = "/mock/bin/cswap"

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestPingArgv(Env):
    def test_post_dashdash_is_claude_args_only(self):
        argv = warmup_runner.ping_argv(2, ["--model", "haiku"])
        self.assertEqual(argv, ["/mock/bin/cswap", "run", "2", "--",
                                "--model", "haiku", "-p", ".",
                                "--max-turns", "1", "--strict-mcp-config"])
        # Regression: the claude binary path must NOT be smuggled in as an
        # argument — cswap resolves the binary itself.
        self.assertFalse(any(token.endswith("/claude") for token in argv))

    def test_plain_retry_has_no_model_flag(self):
        argv = warmup_runner.ping_argv(1, [])
        self.assertEqual(argv[3], "--")
        self.assertNotIn("--model", argv)


@unittest.skipIf(sys.platform == "win32",
                 "exercises the POSIX branch, which needs POSIX os.path")
class TestEnvWithClaudeOnPath(Env):
    """Pinned to a POSIX platform so these tests exercise
    env_with_claude_on_path's POSIX branch specifically.

    Without this, a CI runner that is genuinely Windows (sys.platform ==
    "win32" for real, not mocked) would silently take the win32 branch
    instead -- the POSIX fixtures below ("/some/where", "/opt/homebrew/bin",
    ...) would then be fed through the wrong half of the function entirely,
    not just normalised oddly.

    Pinning sys.platform is necessary but NOT sufficient on a genuinely
    Windows host, hence the skip. os.path is bound to ntpath at import time
    and no monkeypatch of sys.platform rebinds it, so
    os.path.abspath("/some/where/claude") yields "D:\\some\\where\\claude"
    and os.pathsep is ";", meaning a ":"-joined PATH fixture never splits.
    The branch under test is dead code on Windows -- sys.platform is really
    "win32" there, so the win32 arm always wins -- and it stays fully
    covered by the ubuntu and macOS legs of the matrix. Its Windows
    counterpart has its own class below.
    """
    def setUp(self):
        super().setUp()
        self.saved_platform = warmup_runner.sys.platform
        warmup_runner.sys.platform = "darwin"

    def tearDown(self):
        warmup_runner.sys.platform = self.saved_platform
        super().tearDown()

    def test_prepends_claude_dir_and_common_bins(self):
        os.environ["PATH"] = "/usr/bin:/bin"  # launchd's bare default
        env = warmup_runner.env_with_claude_on_path("/some/where/claude")
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], "/some/where")
        self.assertIn(os.path.expanduser("~/.local/bin"), parts)
        self.assertIn("/opt/homebrew/bin", parts)
        # The original PATH survives at the tail.
        self.assertIn("/usr/bin", parts)
        self.assertIn("/bin", parts)

    def test_deduplicates_and_keeps_order(self):
        os.environ["PATH"] = "/opt/homebrew/bin:/usr/bin"
        env = warmup_runner.env_with_claude_on_path("/opt/homebrew/bin/claude")
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts.count("/opt/homebrew/bin"), 1)
        self.assertEqual(parts[0], "/opt/homebrew/bin")

    def test_other_env_untouched(self):
        os.environ["PATH"] = "/usr/bin"
        env = warmup_runner.env_with_claude_on_path("/x/claude")
        self.assertEqual(env["SMARTBAR_CSWAP"], "/mock/bin/cswap")


class WindowsPlatform(unittest.TestCase):
    """Fakes sys.platform == "win32" the way tests/test_presence.py does.

    Monkeypatches the module's own `sys.platform` attribute rather than the
    real interpreter platform, and restores it in tearDown. The `os.path.*`
    calls inside the code under test still bind whatever path module the
    ACTUAL host OS provides -- posixpath on macOS/Linux hosts, but real
    ntpath (with its native drive-letter resolution) if this suite is ever
    run natively on Windows. That's fine for the tests in this class: they
    build fake paths through os.path.join/os.sep so dirname/abspath stay
    sane either way. TestEnvWithClaudeOnPathWindows below needs the
    stronger guarantee of *always* getting ntpath, so it patches
    `warmup_runner.os.path` explicitly instead of relying on the host.
    """

    def setUp(self):
        self.saved_platform = warmup_runner.sys.platform
        self.saved_env = {name: os.environ.get(name)
                          for name in ("SMARTBAR_CLAUDE", "APPDATA",
                                       "LOCALAPPDATA", "PATHEXT",
                                       "SMARTBAR_WARMUP_NOTIFY")}
        os.environ.pop("SMARTBAR_CLAUDE", None)
        warmup_runner.sys.platform = "win32"

    def tearDown(self):
        warmup_runner.sys.platform = self.saved_platform
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestClaudeBinaryOnWindows(WindowsPlatform):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ.pop("APPDATA", None)
        os.environ.pop("LOCALAPPDATA", None)
        os.environ["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"

    def plant(self, *parts):
        """An empty file at tmp/<parts>; returns its path."""
        path = os.path.join(self.tmp.name, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def test_finds_claude_cmd_under_appdata_npm(self):
        planted = self.plant("npm", "claude.cmd")
        os.environ["APPDATA"] = self.tmp.name
        with mock.patch.object(warmup_runner.shutil, "which", return_value=None):
            self.assertEqual(warmup_runner.claude_binary(), planted)

    def test_falls_back_to_localappdata_programs_claude_exe(self):
        planted = self.plant("Programs", "claude", "claude.exe")
        os.environ["LOCALAPPDATA"] = self.tmp.name
        with mock.patch.object(warmup_runner.shutil, "which", return_value=None):
            self.assertEqual(warmup_runner.claude_binary(), planted)

    def test_returns_none_when_nothing_is_installed(self):
        os.environ["APPDATA"] = self.tmp.name
        with mock.patch.object(warmup_runner.shutil, "which", return_value=None):
            self.assertIsNone(warmup_runner.claude_binary())

    def test_the_env_override_still_wins_first(self):
        os.environ["SMARTBAR_CLAUDE"] = r"C:\wherever\claude.exe"
        with mock.patch.object(warmup_runner.shutil, "which", return_value=None):
            self.assertEqual(warmup_runner.claude_binary(),
                             r"C:\wherever\claude.exe")

    def test_shutil_which_still_wins_over_the_fallback_list(self):
        # SMARTBAR_CLAUDE unset but PATH already resolves it: the Windows
        # fallback list must not be consulted at all.
        with mock.patch.object(warmup_runner.shutil, "which",
                               return_value=r"C:\Windows\claude.exe"):
            self.assertEqual(warmup_runner.claude_binary(),
                             r"C:\Windows\claude.exe")


class TestEnvWithClaudeOnPathWindows(WindowsPlatform):
    """Also pins `warmup_runner.os.path` to the real `ntpath` module.

    env_with_claude_on_path() runs the incoming claude path through
    os.path.dirname(os.path.abspath(...)). On real Windows, abspath()
    resolves a drive-less rooted path (root but no drive letter) against
    the CURRENT DRIVE, because that's what Windows' GetFullPathNameW does
    with such a path. A fake APPDATA without a drive letter is not what
    real Windows ever hands a process -- %APPDATA% is always
    drive-qualified there -- so using a drive-qualified fixture here is
    the realistic choice, and it keeps abspath() a no-op instead of
    guessing. Pinning os.path to ntpath (rather than trusting the host)
    makes that hold on macOS/Linux dev machines too, not only on native
    Windows CI.
    """

    def setUp(self):
        super().setUp()
        self.os_path_patcher = mock.patch.object(warmup_runner.os, "path", ntpath)
        self.os_path_patcher.start()
        self.addCleanup(self.os_path_patcher.stop)
        # Real Windows joins PATH entries with ";", not ":" -- and a bare
        # ":" would collide with the drive-letter colon in "C:\Users\...".
        self.os_pathsep_patcher = mock.patch.object(warmup_runner.os, "pathsep", ";")
        self.os_pathsep_patcher.start()
        self.addCleanup(self.os_pathsep_patcher.stop)

    def test_prepends_the_windows_install_dirs_not_the_posix_ones(self):
        appdata = r"C:\Users\duc\AppData\Roaming"
        localappdata = r"C:\Users\duc\AppData\Local"
        os.environ["APPDATA"] = appdata
        os.environ["LOCALAPPDATA"] = localappdata
        os.environ["PATH"] = r"C:\Windows\System32"
        claude = warmup_runner.os.path.join(appdata, "npm", "claude.cmd")
        env = warmup_runner.env_with_claude_on_path(claude)
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], warmup_runner.os.path.join(appdata, "npm"))
        self.assertIn(warmup_runner.os.path.join(localappdata, "Programs", "claude"),
                      parts)
        # The POSIX-only install dirs must not leak in from the other branch.
        self.assertNotIn(os.path.expanduser("~/.local/bin"), parts)
        self.assertNotIn("/opt/homebrew/bin", parts)
        self.assertNotIn("/usr/local/bin", parts)


class TestNotifyFailureChoosesByPlatform(unittest.TestCase):
    """The bug this class exists to pin.

    Before a win32 arm existed, the else branch ran notify-send, which is
    not on Windows; subprocess.run raised FileNotFoundError (an OSError
    subclass) and notify_failure's blanket `except OSError` swallowed it, so
    every warmup failure notification there vanished with nothing in the
    log. These assert the mechanism is chosen BY PLATFORM, never by falling
    through to whatever the last else happens to be.
    """

    def setUp(self):
        self.saved_platform = warmup_runner.sys.platform
        self.saved_notify = os.environ.get("SMARTBAR_WARMUP_NOTIFY")
        os.environ.pop("SMARTBAR_WARMUP_NOTIFY", None)

    def tearDown(self):
        warmup_runner.sys.platform = self.saved_platform
        if self.saved_notify is None:
            os.environ.pop("SMARTBAR_WARMUP_NOTIFY", None)
        else:
            os.environ["SMARTBAR_WARMUP_NOTIFY"] = self.saved_notify

    def test_windows_uses_powershell_not_notify_send(self):
        warmup_runner.sys.platform = "win32"
        with mock.patch.object(warmup_runner.subprocess, "run") as run:
            warmup_runner.notify_failure("title", "body")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "powershell.exe")

    def test_the_windows_call_suppresses_the_console_window(self):
        warmup_runner.sys.platform = "win32"
        with mock.patch.object(warmup_runner.subprocess, "run") as run:
            warmup_runner.notify_failure("title", "body")
        self.assertIn("creationflags", run.call_args.kwargs)

    def test_windows_never_raises_even_when_powershell_is_missing(self):
        warmup_runner.sys.platform = "win32"
        # assertLogs both pins the swallow (it IS logged, not lost) and keeps
        # the deliberate traceback out of this suite's stderr.
        with mock.patch.object(warmup_runner.subprocess, "run",
                               side_effect=FileNotFoundError):
            with self.assertLogs(warmup_runner.log, level="ERROR") as caught:
                warmup_runner.notify_failure("title", "body")  # must not raise
        self.assertIn("notification failed", caught.output[0])

    def test_linux_still_uses_notify_send(self):
        warmup_runner.sys.platform = "linux"
        with mock.patch.object(warmup_runner.subprocess, "run") as run:
            warmup_runner.notify_failure("title", "body")
        self.assertEqual(run.call_args.args[0][0], "notify-send")

    def test_darwin_still_uses_osascript(self):
        warmup_runner.sys.platform = "darwin"
        with mock.patch.object(warmup_runner.subprocess, "run") as run:
            warmup_runner.notify_failure("title", "body")
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/osascript")

    def test_notify_off_short_circuits_on_windows_too(self):
        warmup_runner.sys.platform = "win32"
        os.environ["SMARTBAR_WARMUP_NOTIFY"] = "off"
        with mock.patch.object(warmup_runner.subprocess, "run") as run:
            warmup_runner.notify_failure("title", "body")
        run.assert_not_called()


class TestPingForwardsNoWindow(Env):
    """ping() must splat portable.no_window() into its subprocess.run call,
    so a windowed host never flashes a console for the ping.

    Proven by substituting a sentinel rather than faking a whole platform:
    portable's own platform branch is already covered by
    tests/test_portable.py, and the wiring is what this file owns.
    """

    def test_the_no_window_kwargs_reach_subprocess_run(self):
        sentinel = {"creationflags": 0x08000000}
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(warmup_runner.portable, "no_window",
                               return_value=sentinel), \
             mock.patch.object(warmup_runner.subprocess, "run",
                               return_value=done) as run:
            warmup_runner.ping(1, "/mock/bin/claude")
        self.assertEqual(run.call_args.kwargs.get("creationflags"), 0x08000000)


class TestRunOnceGatesPerAccount(Env):
    """Each account is gated on ITS OWN measurement time.

    The bug this pins: run_once() read one snapshot-wide stamp — whichever
    account happened to carry the first usageFetchedAt — and handed it to
    should_warm() for every account. cswap refreshes each slot on its own
    plan, so that single value is routinely wrong for the others, in both
    directions:

      * a stale slot 1 made EVERY account skip with "snapshot stale", and
        warmup silently never ran (the invisible failure);
      * a fresh slot 1 made a slot whose reading was hours old warm anyway,
        judging window_idle() on data long out of date.

    Driven through the real run_once() rather than should_warm() directly,
    because the defect was never in the gate — it was in what the runner
    chose to hand it.
    """

    def _run(self, accounts):
        """run_once() over `accounts`, returning the slot numbers it pinged."""
        from smartbar.core.model import Snapshot
        pinged = []
        with mock.patch.object(warmup_runner.cswap, "fetch",
                               return_value=Snapshot(accounts=accounts)), \
             mock.patch.object(warmup_runner, "claude_binary",
                               return_value="/mock/bin/claude"), \
             mock.patch.object(warmup_runner, "load_state", return_value={}), \
             mock.patch.object(warmup_runner, "save_state"), \
             mock.patch.object(warmup_runner.portable, "lock",
                               return_value=mock.Mock()), \
             mock.patch.object(warmup_runner, "ping",
                               side_effect=lambda number, _claude: (
                                   pinged.append(number), (True, ""))[1]):
            warmup_runner.run_once()
        return pinged

    @staticmethod
    def _account(number, minutes_old, now):
        """An idle, warmable account whose reading is `minutes_old`."""
        from datetime import timedelta
        from smartbar.core.model import Account, Metric
        stamp = (now - timedelta(minutes=minutes_old)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return Account(number=number, email="a%s@x.com" % number,
                       active=number == 1, ok=True, status="ok",
                       fetched_at=stamp,
                       metrics=[Metric(key="5h", label="5h", short="5h",
                                       pct=10.0, resets_at="")])

    def test_a_stale_slot_no_longer_silences_the_fresh_ones(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        pinged = self._run([self._account(1, 45, now),    # stale reading
                            self._account(2, 1, now)])    # fresh reading
        self.assertEqual(pinged, [2], "the fresh account must still warm")

    def test_a_fresh_slot_no_longer_warms_a_stale_one(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        pinged = self._run([self._account(1, 1, now),      # fresh reading
                            self._account(2, 180, now)])   # 3h-old reading
        self.assertEqual(pinged, [1], "the stale account must not warm")

    def test_a_dead_credential_is_still_named_as_such_not_as_stale(self):
        """A slot with no usage data must report WHY, through the real wiring.

        cswap emits usageFetchedAt only alongside a non-null `usage`, so every
        dead-credential slot reaches should_warm with fetched_at None. When the
        staleness gate ran first, that made "re-login required" unreachable in
        production and warmup.log said "snapshot stale" instead — pointing an
        operator at cswap freshness for what is actually a dead credential.

        tests/test_warmup.py cannot catch this: it calls should_warm directly
        with an explicit fresh timestamp, which is exactly the value the real
        runner never has for such an account.
        """
        from datetime import datetime, timezone
        from smartbar.core.model import Account, Snapshot
        now = datetime.now(timezone.utc)
        dead = Account(number=2, email="dead@x.com", ok=False,
                       status="relogin_required", fetched_at="")  # no metrics
        reasons = []
        with mock.patch.object(warmup_runner.cswap, "fetch",
                               return_value=Snapshot(accounts=[
                                   self._account(1, 1, now), dead])), \
             mock.patch.object(warmup_runner, "claude_binary",
                               return_value="/mock/bin/claude"), \
             mock.patch.object(warmup_runner, "load_state", return_value={}), \
             mock.patch.object(warmup_runner, "save_state"), \
             mock.patch.object(warmup_runner.portable, "lock",
                               return_value=mock.Mock()), \
             mock.patch.object(warmup_runner, "ping",
                               return_value=(True, "")), \
             mock.patch.object(warmup_runner.log, "info",
                               side_effect=lambda msg, *a: reasons.append(
                                   msg % a if a else msg)):
            warmup_runner.run_once()
        skips = [r for r in reasons if r.startswith("skip #2")]
        self.assertTrue(skips, "the dead account should have been skipped")
        self.assertIn("re-login required", skips[0])
        self.assertNotIn("stale", skips[0])


if __name__ == "__main__":
    unittest.main()
