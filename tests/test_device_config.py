"""config.env: the one place a device's settings survive an update.

The renderers here write into three files with three unrelated quoting rules —
a LaunchAgent's plist XML, a systemd unit, and a .desktop Exec line that may
be re-emitted into a crontab. A value that escapes its quoting does not
misconfigure the app, it produces an agent that will not load at all, on a
device nobody is looking at. So these tests care much less about the happy
path than about what is REFUSED, and they check the output by handing it to
real parsers (plutil, shlex) rather than by matching strings.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from smartbar.core import device_config as cfg


class TestParsing(unittest.TestCase):
    def test_a_plain_file(self):
        settings, problems = cfg.parse(
            "# a comment\n\nSMARTBAR_INTERVAL=90\nSMARTBAR_PRESENCE=off\n")
        self.assertEqual(settings, {"SMARTBAR_INTERVAL": "90",
                                    "SMARTBAR_PRESENCE": "off"})
        self.assertEqual(problems, [])

    def test_export_and_quotes_are_tolerated(self):
        # Both are what someone used to shell profiles will actually type.
        settings, problems = cfg.parse(
            'export SMARTBAR_INTERVAL=60\nSMARTBAR_WARMUP_QUIET="23-05"\n'
            "SMARTBAR_YELLOW='40'\n")
        self.assertEqual(settings, {"SMARTBAR_INTERVAL": "60",
                                    "SMARTBAR_WARMUP_QUIET": "23-05",
                                    "SMARTBAR_YELLOW": "40"})
        self.assertEqual(problems, [])

    def test_whitespace_around_the_equals(self):
        settings, _ = cfg.parse("  SMARTBAR_INTERVAL  =  90  \n")
        self.assertEqual(settings, {"SMARTBAR_INTERVAL": "90"})

    def test_a_quoted_value_may_carry_a_trailing_inline_comment(self):
        # Quoting AND an inline "# comment" are each documented .env syntax;
        # used together they left the quote characters on the value, which
        # then tripped the charset check and silently dropped the setting.
        settings, problems = cfg.parse(
            'SMARTBAR_CSWAP="/usr/local/bin/cswap"  # custom path\n')
        self.assertEqual(settings, {"SMARTBAR_CSWAP": "/usr/local/bin/cswap"})
        self.assertEqual(problems, [])

    def test_a_hash_inside_a_quoted_value_stays_content(self):
        # The closing quote, not " #", bounds a quoted value: a # between the
        # quotes is data, not a comment.
        settings, _ = cfg.parse('SMARTBAR_PRESENCE_LABEL="box #3"\n')
        self.assertEqual(settings, {"SMARTBAR_PRESENCE_LABEL": "box #3"})

    def test_an_empty_value_is_a_value(self):
        # SMARTBAR_PRESENCE_LABEL= deliberately means "publish no name".
        settings, problems = cfg.parse("SMARTBAR_PRESENCE_LABEL=\n")
        self.assertEqual(settings, {"SMARTBAR_PRESENCE_LABEL": ""})
        self.assertEqual(problems, [])

    def test_the_last_assignment_wins(self):
        settings, _ = cfg.parse("SMARTBAR_INTERVAL=60\nSMARTBAR_INTERVAL=90\n")
        self.assertEqual(settings, {"SMARTBAR_INTERVAL": "90"})

    def test_one_bad_line_does_not_cost_the_others(self):
        settings, problems = cfg.parse(
            "SMARTBAR_INTERVAL=90\ngarbage\nSMARTBAR_PRESENCE=off\n")
        self.assertEqual(settings, {"SMARTBAR_INTERVAL": "90",
                                    "SMARTBAR_PRESENCE": "off"})
        self.assertEqual(len(problems), 1)
        self.assertIn("line 2", problems[0])

    def test_nothing_at_all(self):
        self.assertEqual(cfg.parse(""), ({}, []))
        self.assertEqual(cfg.parse(None), ({}, []))


class TestWhatIsRefused(unittest.TestCase):
    def test_only_this_apps_own_variables(self):
        # The point of the allowlist: these go into a launchd agent's
        # environment, so a config file that could set PATH or an injected
        # library is a privilege problem, not a configuration feature.
        for key in ("PATH", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "HOME",
                    "smartbar_interval", "SMARTBARINTERVAL", "SMART_BAR_X"):
            settings, problems = cfg.parse("%s=whatever\n" % key)
            self.assertEqual(settings, {}, "%s must not be honoured" % key)
            self.assertEqual(len(problems), 1)

    def test_keys_with_their_own_mechanism_are_reserved(self):
        for key in cfg.RESERVED:
            settings, problems = cfg.parse("%s=x\n" % key)
            self.assertEqual(settings, {})
            self.assertIn("installer", problems[0])

    def test_characters_that_would_break_a_generated_unit(self):
        # ": closes a plist string and a systemd/desktop quote.  \\ and ` and
        # $: shell and systemd expansion.  %: crontab turns it into a newline.
        # \t and NUL survive a line split, so they have to be caught here.
        for bad in ('a"b', "a\\b", "a`b", "a$b", "a%b", "a\tb", "a\x00b",
                    "a\x07b", "a\x1bb"):
            settings, problems = cfg.parse("SMARTBAR_X=%s\n" % bad)
            self.assertEqual(settings, {}, "%r must be refused" % bad)
            self.assertEqual(len(problems), 1)

    def test_a_line_break_cannot_reach_a_value_at_all(self):
        # Not a rejection but a property of the format, and worth pinning: the
        # file is line-based with no continuation syntax, so a value simply
        # ends at the line end and the remainder is an ordinary bad line. That
        # is what makes the multi-line injection the renderers would be unsafe
        # against unrepresentable, rather than merely filtered.
        settings, problems = cfg.parse('SMARTBAR_X=a\n</string><key>evil</key>\n')
        self.assertEqual(settings, {"SMARTBAR_X": "a"})
        self.assertEqual(len(problems), 1)
        self.assertIn("line 2", problems[0])

    def test_the_things_a_real_value_needs_are_still_allowed(self):
        # Refusing too much would be its own bug: paths with spaces, hour
        # ranges, percentages-as-numbers and emails all have to survive.
        for good in ("/opt/my tools/cswap", "23-05", "90", "off", "a@b.c", "a-b_c"):
            settings, problems = cfg.parse("SMARTBAR_X=%s\n" % good)
            self.assertEqual(settings, {"SMARTBAR_X": good})
            self.assertEqual(problems, [])


class TestRendering(unittest.TestCase):
    SETTINGS = {"SMARTBAR_B": "two", "SMARTBAR_A": "one"}

    def test_output_is_sorted_so_reinstalling_changes_nothing(self):
        # The installers rewrite these files on every update; unstable
        # ordering would churn a unit on every single pass.
        first = cfg.render_plist(self.SETTINGS)
        second = cfg.render_plist(dict(reversed(list(self.SETTINGS.items()))))
        self.assertEqual(first, second)
        self.assertLess(first.index("SMARTBAR_A"), first.index("SMARTBAR_B"))

    def test_an_empty_config_renders_to_nothing_at_all(self):
        # Every caller splices these straight into a unit file, so "no
        # settings" has to leave that file byte-identical to before.
        for fmt in cfg.RENDERERS:
            self.assertEqual(cfg.render(fmt, {}), "", fmt)

    def test_systemd_lines_are_quoted_as_one_assignment(self):
        rendered = cfg.render_systemd({"SMARTBAR_CSWAP": "/opt/my tools/cswap"})
        self.assertEqual(rendered,
                         '\nEnvironment="SMARTBAR_CSWAP=/opt/my tools/cswap"')

    def test_line_renderings_lead_with_the_newline(self):
        # The installers splice these in through $(…), and command
        # substitution STRIPS TRAILING NEWLINES — so a trailing-newline style
        # glues the next line of the unit onto the last rendered one. That
        # produced `Environment="…"ExecStart=…` and a systemd unit that did
        # nothing; a plist merely looked wrong, because XML ignores whitespace.
        for renderer in (cfg.render_plist, cfg.render_systemd):
            rendered = renderer(self.SETTINGS)
            self.assertTrue(rendered.startswith("\n"), renderer.__name__)
            self.assertFalse(rendered.endswith("\n"), renderer.__name__)

    def test_an_unknown_format_is_an_error_not_a_silent_empty(self):
        with self.assertRaises(ValueError):
            cfg.render("yaml", self.SETTINGS)


class TestTheOutputSurvivesRealParsers(unittest.TestCase):
    """Hand the rendered text to the things that will actually read it."""

    TRICKY = ("SMARTBAR_AMP=a & b\nSMARTBAR_LT=x < y > z\n"
              "SMARTBAR_SPACE=/opt/my tools/cswap\nSMARTBAR_EMPTY=\n")

    @unittest.skipUnless(shutil.which("plutil"), "plutil is macOS-only")
    def test_the_plist_lines_parse_and_the_values_round_trip(self):
        settings, problems = cfg.parse(self.TRICKY)
        self.assertEqual(problems, [])
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            # Spliced exactly the way the installers splice it: at the END of
            # the previous line, because $(…) ate the trailing newline.
            '<plist version="1.0">\n<dict>'
            + cfg.render_plist(settings) +
            '\n</dict>\n</plist>\n')
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "test.plist")
            with open(path, "w") as handle:
                handle.write(document)
            # -lint proves it is well formed; the JSON conversion proves the
            # ESCAPING is right, which linting alone would not catch.
            subprocess.run(["plutil", "-lint", path], check=True,
                           stdout=subprocess.DEVNULL)
            out = subprocess.run(["plutil", "-convert", "json", "-o", "-", path],
                                 check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(out.stdout), settings)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_exec_prefix_splits_into_the_argv_env_expects(self):
        import shlex
        settings, _ = cfg.parse(self.TRICKY)
        line = cfg.render_exec_prefix(settings) + "/usr/local/bin/ai-smartbar"
        argv = shlex.split(line)
        self.assertEqual(argv[0], "env")
        self.assertEqual(argv[-1], "/usr/local/bin/ai-smartbar")
        # Each assignment must arrive as exactly ONE argument, spaces and all,
        # or `env` reads the tail of a path as the command to run.
        self.assertEqual(sorted(argv[1:-1]),
                         sorted("%s=%s" % kv for kv in settings.items()))


class TestTheCliContract(unittest.TestCase):
    """What the installers actually call, including when there is no file."""

    LAUNCHER = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bin", "ai-smartbar")

    def run_it(self, fmt, config_dir):
        # LAUNCHER is an extension-less "#!/usr/bin/env python3" script: POSIX
        # exec dispatches on the shebang, Windows has no such mechanism, so
        # subprocess.run([LAUNCHER, ...]) fails there with WinError 193 ("%1
        # is not a valid Win32 application"). Going through sys.executable
        # sidesteps shebang dispatch on every platform (the precedent is
        # tests/e2e-update.sh's run_update()'s `python3 ./bin/ai-smartbar`) and still
        # exercises exactly the CLI contract this test asserts against.
        return subprocess.run(
            [sys.executable, self.LAUNCHER, "--print-config", fmt],
            capture_output=True, text=True,
            env=dict(os.environ, SMARTBAR_CONFIG_DIR=config_dir))

    def test_a_missing_config_is_silent_and_empty(self):
        # The common case by far — no device has this file until someone
        # writes one, and an installer must not print noise or fail.
        directory = tempfile.mkdtemp()
        try:
            for fmt in ("plist", "systemd", "exec"):
                done = self.run_it(fmt, os.path.join(directory, "absent"))
                self.assertEqual(done.returncode, 0, fmt)
                self.assertEqual(done.stdout, "", fmt)
                self.assertEqual(done.stderr, "", fmt)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_problems_go_to_stderr_and_settings_to_stdout(self):
        # stdout is spliced into a unit file, so a warning must never land
        # there; the installer still shows it to whoever is installing.
        directory = tempfile.mkdtemp()
        try:
            with open(os.path.join(directory, cfg.FILENAME), "w") as handle:
                handle.write("SMARTBAR_INTERVAL=90\nPATH=/tmp/evil\n")
            done = self.run_it("plist", directory)
            self.assertEqual(done.returncode, 0)
            self.assertIn("SMARTBAR_INTERVAL", done.stdout)
            self.assertNotIn("PATH", done.stdout)
            self.assertIn("not a SMARTBAR_* setting", done.stderr)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
