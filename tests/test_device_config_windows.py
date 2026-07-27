"""device_config's Windows seams: the widened charset (D1), the winenv
renderer (D3), and bin/ai-smartbar's win32-only runtime loader (D2).

test_device_config.py already pins the byte-for-byte default (macOS/Linux)
behaviour of parse()/the other three renderers; this file exists so that
widening the charset for Windows never quietly widens it everywhere else,
which is the one mistake that would turn the plist/systemd/desktop safety
argument in device_config.py back into three separate escaping schemes.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from smartbar.core import device_config as cfg

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(REPO, "bin", "ai-smartbar")


class TestTheDefaultCharsetIsUnchanged(unittest.TestCase):
    """The whole safety argument for parse() rests on this: adding the
    `windows` keyword must not move the default-path behaviour by one bit.
    """

    BAD = 'SMARTBAR_X=a"b\nSMARTBAR_Y=a\\b\nSMARTBAR_Z=a`b\n' \
          "SMARTBAR_W=a$b\nSMARTBAR_V=a%b\n"

    def test_omitting_windows_matches_explicit_windows_false(self):
        self.assertEqual(cfg.parse(self.BAD), cfg.parse(self.BAD, windows=False))

    def test_the_full_charset_is_still_refused_by_default(self):
        settings, problems = cfg.parse(self.BAD)
        self.assertEqual(settings, {})
        self.assertEqual(len(problems), 5)


class TestWindowsWidensOnlyTheBackslashFamily(unittest.TestCase):
    def test_backslash_survives_only_with_windows_true(self):
        text = "SMARTBAR_CSWAP=C:\\Users\\duc\\cswap.exe\n"
        settings, problems = cfg.parse(text, windows=True)
        self.assertEqual(settings, {"SMARTBAR_CSWAP": r"C:\Users\duc\cswap.exe"})
        self.assertEqual(problems, [])

    def test_backslash_is_still_dropped_by_default(self):
        text = "SMARTBAR_CSWAP=C:\\Users\\duc\\cswap.exe\n"
        settings, problems = cfg.parse(text)
        self.assertEqual(settings, {})
        self.assertEqual(len(problems), 1)

    def test_quote_backtick_dollar_and_percent_also_survive_under_windows(self):
        # None of the three quoting contexts device_config.py exists for
        # (plist/systemd/desktop) apply on Windows, so none of these five
        # characters have anywhere left to be dangerous.
        for allowed in ('a"b', "a\\b", "a`b", "a$b", "a%b"):
            settings, problems = cfg.parse("SMARTBAR_X=%s\n" % allowed,
                                           windows=True)
            self.assertEqual(settings, {"SMARTBAR_X": allowed}, allowed)
            self.assertEqual(problems, [], allowed)

    def test_control_characters_are_dropped_in_both_modes(self):
        # NUL and friends cannot survive a Windows environment block at all,
        # so the one thing the two charsets must agree on is this.
        for bad in ("a\x00b", "a\x07b", "a\x1bb", "a\x7fb"):
            for windows in (False, True):
                settings, problems = cfg.parse("SMARTBAR_X=%s\n" % bad,
                                               windows=windows)
                self.assertEqual(settings, {}, (bad, windows))
                self.assertEqual(len(problems), 1, (bad, windows))


class TestTheProblemMessageNamesTheCharsetThatApplied(unittest.TestCase):
    """A Windows user must never be told backslash is banned when the
    charset that actually ran against their value does not ban it.
    """

    def test_default_message_names_all_five_characters(self):
        _, problems = cfg.parse("SMARTBAR_X=a\\b\n")
        self.assertEqual(len(problems), 1)
        self.assertIn(
            "quote, backslash, backtick, $, % or a control character",
            problems[0])

    def test_windows_message_names_only_control_characters(self):
        _, problems = cfg.parse("SMARTBAR_X=a\x00b\n", windows=True)
        self.assertEqual(len(problems), 1)
        self.assertIn("(a control character)", problems[0])
        self.assertNotIn("backslash", problems[0])
        self.assertNotIn("quote", problems[0])


class TestRenderWinenv(unittest.TestCase):
    SETTINGS = {"SMARTBAR_B": "two", "SMARTBAR_A": "one"}

    def test_leading_newline_no_trailing_newline_sorted_keys(self):
        # Same splice convention as render_plist/render_systemd: installers
        # glue this in through $(…), which eats a trailing newline.
        rendered = cfg.render_winenv(self.SETTINGS)
        self.assertEqual(rendered, "\nSMARTBAR_A=one\nSMARTBAR_B=two")
        self.assertTrue(rendered.startswith("\n"))
        self.assertFalse(rendered.endswith("\n"))

    def test_sort_order_does_not_depend_on_insertion_order(self):
        reordered = dict(reversed(list(self.SETTINGS.items())))
        self.assertEqual(cfg.render_winenv(self.SETTINGS),
                         cfg.render_winenv(reordered))

    def test_an_empty_config_renders_to_nothing_at_all(self):
        self.assertEqual(cfg.render_winenv({}), "")
        self.assertEqual(cfg.render("winenv", {}), "")

    def test_winenv_is_registered_under_render(self):
        self.assertIn("winenv", cfg.RENDERERS)
        self.assertIs(cfg.RENDERERS["winenv"], cfg.render_winenv)


class TestTheCliContractForWinenv(unittest.TestCase):
    """`--print-config winenv` is the CLI-testable surface of the widened
    Windows charset: it must accept what `exec`/`plist`/`systemd` refuse.
    """

    def run_it(self, fmt, config_dir):
        # Same sys.executable indirection as test_device_config.py's
        # TestTheCliContract: LAUNCHER has no extension, so exec-by-shebang
        # cannot dispatch it on Windows.
        return subprocess.run(
            [sys.executable, LAUNCHER, "--print-config", fmt],
            capture_output=True, text=True,
            env=dict(os.environ, SMARTBAR_CONFIG_DIR=config_dir))

    def test_winenv_accepts_a_real_windows_path(self):
        directory = tempfile.mkdtemp()
        try:
            with open(os.path.join(directory, cfg.FILENAME), "w") as handle:
                handle.write("SMARTBAR_CSWAP=C:\\Users\\duc\\cswap.exe\n")
            done = self.run_it("winenv", directory)
            self.assertEqual(done.returncode, 0)
            self.assertIn("SMARTBAR_CSWAP=C:\\Users\\duc\\cswap.exe",
                          done.stdout)
            self.assertEqual(done.stderr, "")
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_same_value_is_still_rejected_by_exec(self):
        # Pins that the CLI format, not the platform, decides the charset —
        # the D1 rule that the caller decides, not sys.platform read inside
        # the module.
        directory = tempfile.mkdtemp()
        try:
            with open(os.path.join(directory, cfg.FILENAME), "w") as handle:
                handle.write("SMARTBAR_CSWAP=C:\\Users\\duc\\cswap.exe\n")
            done = self.run_it("exec", directory)
            self.assertEqual(done.returncode, 0)
            self.assertNotIn("SMARTBAR_CSWAP", done.stdout)
            self.assertIn("cannot be passed safely", done.stderr)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class TestTheRuntimeLoaderD2(unittest.TestCase):
    """bin/ai-smartbar's win32-only `_load_windows_env()`, exercised as the
    real CLI would run it: a fresh subprocess, so the win32/darwin/linux
    branches can each be exhibited without ever mutating this test
    process's own sys.platform, and without needing pystray/tkinter/cairo
    installed (main()'s UI dispatch is never reached — only the loader is
    called).
    """

    def _observe(self, *, platform, config_dir, preset=None):
        """Run `_load_windows_env()` in a child interpreter and report what
        it left in os.environ["SMARTBAR_CSWAP"] ("<unset>" if nothing).

        runpy.run_path with a run_name other than "__main__" executes every
        module-level statement in bin/ai-smartbar (imports, def main(): ...)
        but skips its `if __name__ == "__main__": sys.exit(main())` guard,
        so this calls the exact same function the real CLI calls first,
        without spawning a tray.

        The two modules `_load_windows_env` itself imports are warmed here
        FIRST, under this host's real sys.platform, before that attribute is
        faked to "win32": smartbar.presence_runner pulls in stdlib tempfile
        -> shutil, and CPython's own shutil.py does `if sys.platform ==
        'win32': import _winapi` at MODULE level. Faking the platform before
        that first import would make a real macOS interpreter try to import
        a Windows-only stdlib extension and crash — a side effect of the
        fake, not of anything this test is trying to exercise.
        """
        script = (
            "import os, sys\n"
            "sys.path.insert(0, %r)\n"
            "import smartbar.presence_runner\n"
            "import smartbar.core.device_config\n"
            "import runpy\n"
            "preset = %r\n"
            "if preset is not None:\n"
            "    os.environ['SMARTBAR_CSWAP'] = preset\n"
            "ns = runpy.run_path(%r, run_name='not_main')\n"
            "sys.platform = %r\n"
            "ns['_load_windows_env']()\n"
            "print(os.environ.get('SMARTBAR_CSWAP', '<unset>'))\n"
        ) % (REPO, preset, LAUNCHER, platform)
        done = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True,
                              env=dict(os.environ, SMARTBAR_CONFIG_DIR=config_dir))
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def _config_dir_with(self, value):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        with open(os.path.join(directory, cfg.FILENAME), "w") as handle:
            handle.write("SMARTBAR_CSWAP=%s\n" % value)
        return directory

    def test_win32_loads_config_env_into_os_environ(self):
        directory = self._config_dir_with(r"C:\Users\duc\cswap.exe")
        out = self._observe(platform="win32", config_dir=directory)
        self.assertEqual(out, r"C:\Users\duc\cswap.exe")

    def test_win32_never_overrides_an_already_set_variable(self):
        directory = self._config_dir_with(r"C:\Users\duc\cswap.exe")
        out = self._observe(platform="win32", config_dir=directory,
                            preset=r"C:\already\set.exe")
        self.assertEqual(out, r"C:\already\set.exe")

    def test_darwin_is_a_no_op_even_with_a_real_config_file_present(self):
        directory = self._config_dir_with(r"C:\Users\duc\cswap.exe")
        out = self._observe(platform="darwin", config_dir=directory)
        self.assertEqual(out, "<unset>")

    def test_linux_is_a_no_op_too(self):
        directory = self._config_dir_with(r"C:\Users\duc\cswap.exe")
        out = self._observe(platform="linux", config_dir=directory)
        self.assertEqual(out, "<unset>")

    def test_win32_with_a_missing_config_file_is_silent(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        out = self._observe(platform="win32",
                            config_dir=os.path.join(directory, "absent"))
        self.assertEqual(out, "<unset>")


if __name__ == "__main__":
    unittest.main()
