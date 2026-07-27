"""Tests for the cswap fresh-primer plumbing (venv resolution + fallback)."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from smartbar.core import cswap

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cswap_list.json")


def write_script(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w") as handle:
        handle.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {name: os.environ.pop(name, None)
                      for name in ("SMARTBAR_CSWAP", "SMARTBAR_CSWAP_PYTHON")}
        cswap._combined_unsupported = False  # reset the per-process latch

    def tearDown(self):
        self.tmp.cleanup()
        cswap._combined_unsupported = False
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestVenvPython(Env):
    def test_parses_pipx_launcher_exec_line(self):
        # sys.executable stands in for the venv python: the parsed path must
        # exist, and a real interpreter always does.
        #
        # This parses a POSIX shell launcher, which is POSIX-only by
        # nature: venv_python()'s win32 branch never even looks at the
        # launcher text (Windows installs a compiled PE stub, nothing to
        # regex out of it), so this must fake a non-win32 platform to
        # deterministically hit the branch under test -- otherwise it only
        # passes when the suite happens to run on a POSIX host, and fails
        # for real on Windows CI where sys.platform is genuinely "win32".
        # A POSIX-shaped interpreter path, not sys.executable: on Windows
        # that is C:\\...\\python.exe, which the launcher regex
        # (r"'(/[^']*/bin/python[^']*)'") can never match, so this asserted
        # None and failed for a reason that has nothing to do with parsing.
        # The existence probe is stubbed for the same reason -- no Windows
        # filesystem can produce /opt/pipx/... -- and only for that exact
        # path, so a regex that matched something else would still fail.
        venv = "/opt/pipx/venvs/claude-swap/bin/python3"
        launcher = write_script(self.tmp.name, "cswap",
                                "#!/bin/sh\n"
                                f"'''exec' '{venv}' \"$0\" \"$@\"\n"
                                "' '''\n")
        os.environ["SMARTBAR_CSWAP"] = launcher
        saved_platform = cswap.sys.platform
        cswap.sys.platform = "linux"
        try:
            with mock.patch.object(cswap.os.path, "exists",
                                   side_effect=lambda p: p == venv):
                self.assertEqual(cswap.venv_python(), venv)
        finally:
            cswap.sys.platform = saved_platform

    def test_no_python_line_is_none(self):
        plain = write_script(self.tmp.name, "cswap", "#!/bin/sh\nexit 0\n")
        os.environ["SMARTBAR_CSWAP"] = plain
        self.assertIsNone(cswap.venv_python())

    def test_env_override_wins(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = "/custom/python3"
        self.assertEqual(cswap.venv_python(), "/custom/python3")

    def test_missing_binary_is_none(self):
        os.environ["SMARTBAR_CSWAP"] = os.path.join(self.tmp.name, "absent")
        self.assertIsNone(cswap.venv_python())


class TestVenvPythonWindows(Env):
    """Windows has no launcher text to parse — cswap.exe is a PE stub — so
    venv_python() probes the well-known pipx/uv tool-venv layouts instead.

    These fake HOME *and* USERPROFILE so the "~/.local/..." candidates
    resolve inside a throwaway tempdir rather than the real user profile,
    and fake sys.platform the way tests/test_presence.py:74-104 already
    does. Both env vars matter: os.path.expanduser() is bound to whatever
    path module the REAL host OS provides, not the faked sys.platform --
    posixpath (reads HOME) when this suite runs on macOS/Linux, ntpath
    (reads USERPROFILE, never HOME) when it actually runs on Windows CI.
    Faking only HOME made these pass here for the wrong reason: on real
    Windows expanduser() would silently ignore it and resolve against the
    CI box's real profile instead of the planted tempdir.
    """

    def setUp(self):
        super().setUp()
        self.saved_platform = cswap.sys.platform
        cswap.sys.platform = "win32"
        self.saved_home = os.environ.get("HOME")
        self.saved_userprofile = os.environ.get("USERPROFILE")
        os.environ["HOME"] = self.tmp.name
        os.environ["USERPROFILE"] = self.tmp.name

    def tearDown(self):
        cswap.sys.platform = self.saved_platform
        if self.saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.saved_home
        if self.saved_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = self.saved_userprofile
        super().tearDown()

    def plant(self, *parts):
        """An empty python.exe at tmp/<parts>/python.exe; returns its path."""
        venv_dir = os.path.join(self.tmp.name, *parts)
        os.makedirs(venv_dir)
        planted = os.path.join(venv_dir, "python.exe")
        with open(planted, "w") as handle:
            handle.write("")
        return planted

    def test_finds_a_planted_pipx_venv_python(self):
        planted = self.plant(".local", "pipx", "venvs", "claude-swap", "Scripts")
        # normpath, not a raw equality: on real Windows, expanduser()
        # splices a backslash-separated USERPROFILE onto venv_python()'s
        # hardcoded forward-slash literal, so the genuine return value is a
        # mixed-separator string that still names this exact file but never
        # string-matches `planted` (built purely through os.path.join).
        self.assertEqual(os.path.normpath(cswap.venv_python()),
                         os.path.normpath(planted))

    def test_finds_a_planted_uv_venv_python_when_pipx_is_absent(self):
        planted = self.plant(".local", "share", "uv", "tools", "claude-swap",
                             "Scripts")
        self.assertEqual(os.path.normpath(cswap.venv_python()),
                         os.path.normpath(planted))

    def test_none_when_nothing_is_planted(self):
        self.assertIsNone(cswap.venv_python())

    def test_the_env_override_still_wins_on_windows(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = r"C:\custom\python.exe"
        self.assertEqual(cswap.venv_python(), r"C:\custom\python.exe")


class TestSubprocessDecoding(Env):
    """Every subprocess.run here has to decode as UTF-8 with replacement.

    Falling through to locale.getpreferredencoding() — cp1252 on a stock
    Windows — lets a non-ASCII email or organizationName raise
    UnicodeDecodeError from inside subprocess.run itself, which is not
    somewhere _run()'s `except OSError` reaches. They must also splat
    portable.no_window(), or a GUI host flashes a console on every poll.
    """

    def test_run_decodes_as_utf8_with_replacement(self):
        # subprocess.run is stubbed rather than wrapped around a real exec of
        # a planted "#!/bin/sh" file: Windows' CreateProcess honours no
        # shebang and finds no PATHEXT match for an extensionless name, so
        # the real call raises before the kwargs this test is about can be
        # read. Those kwargs are the entire subject here -- _run()'s
        # OS-level exec mechanics are not -- so a CompletedProcess stub
        # proves the same thing on all three platforms.
        os.environ["SMARTBAR_CSWAP"] = write_script(
            self.tmp.name, "cswap", "#!/bin/sh\necho '{}'\n")
        completed = subprocess.CompletedProcess(
            args=["cswap", "list", "--json"], returncode=0, stdout="{}",
            stderr="")
        with mock.patch.object(cswap.subprocess, "run",
                               return_value=completed) as spy:
            self.assertEqual(cswap._run(["list", "--json"]), "{}")
        kwargs = spy.call_args[1]
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertEqual(kwargs.get("errors"), "replace")

    def test_prime_fresh_decodes_as_utf8_with_replacement(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "PRIMER_CODE", "import sys; sys.exit(0)"), \
             mock.patch.object(cswap.subprocess, "run",
                               wraps=cswap.subprocess.run) as spy:
            cswap.prime_fresh()
        kwargs = spy.call_args[1]
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertEqual(kwargs.get("errors"), "replace")

    def test_fetch_combined_decodes_as_utf8_with_replacement(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "COMBINED_CODE",
                               "import sys; sys.exit(97)"), \
             mock.patch.object(cswap.subprocess, "run",
                               wraps=cswap.subprocess.run) as spy:
            cswap.fetch_combined()
        kwargs = spy.call_args[1]
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertEqual(kwargs.get("errors"), "replace")

    def test_no_window_kwargs_are_splatted_on_a_faked_windows(self):
        # A faked win32 makes portable.no_window() hand back a real
        # creationflags kwarg, which the REAL subprocess.run on this POSIX
        # host rejects outright ("creationflags is only supported on Windows
        # platforms") — so unlike the tests above, this mock must NOT wrap
        # through to it.
        saved_platform = cswap.sys.platform
        cswap.sys.platform = "win32"
        try:
            os.environ["SMARTBAR_CSWAP"] = write_script(
                self.tmp.name, "cswap", "#!/bin/sh\necho '{}'\n")
            done = subprocess.CompletedProcess(args=["cswap"], returncode=0,
                                               stdout="{}", stderr="")
            with mock.patch.object(cswap.subprocess, "run",
                                   return_value=done) as spy:
                cswap._run(["list", "--json"])
        finally:
            cswap.sys.platform = saved_platform
        self.assertIn("creationflags", spy.call_args[1])


class TestPrimer(Env):
    def test_primer_code_compiles(self):
        compile(cswap.PRIMER_CODE, "<primer>", "exec")

    def test_prime_fresh_false_without_interpreter(self):
        os.environ["SMARTBAR_CSWAP"] = write_script(self.tmp.name, "cswap",
                                                    "#!/bin/sh\nexit 0\n")
        self.assertFalse(cswap.prime_fresh())

    def test_prime_fresh_false_on_broken_interpreter(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = os.path.join(self.tmp.name, "nope")
        self.assertFalse(cswap.prime_fresh())

    def test_prime_fresh_true_on_clean_run(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "PRIMER_CODE", "import sys; sys.exit(0)"):
            self.assertTrue(cswap.prime_fresh())

    def test_fetch_fresh_survives_missing_primer(self):
        with open(FIXTURE, encoding="utf-8") as handle:
            payload = handle.read()
        # No SMARTBAR_CSWAP/SMARTBAR_CSWAP_PYTHON planted, so combined and
        # the primer are both skipped for lack of a venv python and fetch()
        # falls through to the binary list path. _run() is mocked directly
        # rather than exec'ing a planted shell-script stand-in for cswap:
        # a bare text file with a "#!/bin/sh" shebang has no meaning to
        # Windows' CreateProcess (no .exe, no PATHEXT match), so a real
        # subprocess call would fail there with "not a valid Win32
        # application" even though the point of this test is fetch()'s
        # fallback branching, not _run()'s OS-level exec mechanics -- that
        # part is covered on its own by TestSubprocessDecoding.
        # venv_python is pinned rather than left to the ambient machine.
        # The comment above promises "no venv python", but nothing enforced
        # it: on a box with claude-swap pipx-installed, venv_python() finds a
        # real interpreter, fetch() takes the primer path, and this fails for
        # a reason unrelated to what it tests. CI only passed because a fresh
        # runner has no cswap on it.
        with mock.patch.object(cswap, "venv_python", return_value=None), \
                mock.patch.object(cswap, "_run", return_value=payload) as run:
            snap = cswap.fetch(fresh=True)
        run.assert_called_once_with(["list", "--json"])
        self.assertTrue(snap.accounts)


class TestCombined(Env):
    def test_combined_code_compiles(self):
        compile(cswap.COMBINED_CODE, "<combined>", "exec")

    def test_fetch_fresh_uses_combined_output(self):
        # The combined program's stdout IS the snapshot: no binary list run.
        with open(FIXTURE) as handle:
            payload = handle.read()
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        os.environ["SMARTBAR_CSWAP"] = os.path.join(self.tmp.name, "absent")
        fake = ("import sys, json\n"
                f"sys.stdout.write({payload!r})\n")
        with mock.patch.object(cswap, "COMBINED_CODE", fake):
            snap = cswap.fetch(fresh=True)
        self.assertTrue(snap.accounts)  # absent binary proves no fallback ran

    def test_exit_97_latches_unsupported(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "COMBINED_CODE", "import sys; sys.exit(97)"):
            self.assertIsNone(cswap.fetch_combined())
        self.assertTrue(cswap._combined_unsupported)
        # Latched: not even attempted again (a crashing program would throw
        # if it ran — the latch means we never get that far).
        with mock.patch.object(cswap, "COMBINED_CODE", "import sys; sys.exit(0)"):
            self.assertIsNone(cswap.fetch_combined())

    def test_nonzero_exit_falls_back_without_latching(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "COMBINED_CODE", "import sys; sys.exit(1)"):
            self.assertIsNone(cswap.fetch_combined())
        self.assertFalse(cswap._combined_unsupported)

    def test_fetch_fresh_falls_back_to_binary_on_combined_failure(self):
        with open(FIXTURE) as handle:
            payload = handle.read()
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        # Same reasoning as test_fetch_fresh_survives_missing_primer above:
        # only the fallback _run() call is mocked. fetch_combined() still
        # really execs `sys.executable -c COMBINED_CODE` -- a genuine,
        # cross-platform-launchable interpreter, unlike a planted shebang
        # script standing in for the cswap binary.
        with mock.patch.object(cswap, "COMBINED_CODE", "import sys; sys.exit(97)"), \
                mock.patch.object(cswap, "PRIMER_CODE", "import sys; sys.exit(0)"), \
                mock.patch.object(cswap, "_run", return_value=payload) as run:
            snap = cswap.fetch(fresh=True)
        run.assert_called_once_with(["list", "--json"])
        self.assertTrue(snap.accounts)


if __name__ == "__main__":
    unittest.main()
