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
import os
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
                                "--max-turns", "1"])
        # Regression: the claude binary path must NOT be smuggled in as an
        # argument — cswap resolves the binary itself.
        self.assertFalse(any(token.endswith("/claude") for token in argv))

    def test_plain_retry_has_no_model_flag(self):
        argv = warmup_runner.ping_argv(1, [])
        self.assertEqual(argv[3], "--")
        self.assertNotIn("--model", argv)


class TestEnvWithClaudeOnPath(Env):
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
    ACTUAL host OS provides (posixpath here, since this suite only ever runs
    on macOS/Linux), so every fake path a test builds goes through
    os.path.join/os.sep instead of hardcoded backslashes — which keeps
    os.path.dirname/abspath sane on the real host while still exercising the
    win32 branch.
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
    def test_prepends_the_windows_install_dirs_not_the_posix_ones(self):
        appdata = os.path.join(os.sep, "Users", "duc", "AppData", "Roaming")
        localappdata = os.path.join(os.sep, "Users", "duc", "AppData", "Local")
        os.environ["APPDATA"] = appdata
        os.environ["LOCALAPPDATA"] = localappdata
        os.environ["PATH"] = os.path.join(os.sep, "Windows", "System32")
        claude = os.path.join(appdata, "npm", "claude.cmd")
        env = warmup_runner.env_with_claude_on_path(claude)
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], os.path.join(appdata, "npm"))
        self.assertIn(os.path.join(localappdata, "Programs", "claude"), parts)
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


if __name__ == "__main__":
    unittest.main()
