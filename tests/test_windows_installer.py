"""Cross-file invariants between install/windows.ps1 and the Python it drives.

install/windows.ps1 cannot be executed here — it needs a Windows host, and
this repo's CI runs its Windows job without one for the GUI half. That leaves
a whole class of defect completely uncovered: the installer and the Python
that probes it agree about a path or a string TODAY, and six months from now
one side is renamed and the other is not. Nothing fails, because nothing
imports a PowerShell script; the device simply stops recognising itself as
installed and re-runs the wrong installer forever.

So these tests read windows.ps1 as text and pin the handful of strings that
have to match something on the Python side. They are not a substitute for
running the installer — see docs/windows-bring-up.md for that — but they do
turn three silent-drift bugs into a red test on every push, on every OS.
"""
import os
import re
import subprocess
import sys
import unittest

from smartbar import update_runner
from smartbar.core import update

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "install", "windows.ps1")
LAUNCHER = os.path.join(REPO, "bin", "ai-smartbar")


def script_text():
    with open(SCRIPT, encoding="utf-8") as handle:
        return handle.read()


def script_code():
    """windows.ps1 with its comments stripped.

    Several of these tests assert that a construct is ABSENT. Searching the
    raw text for one is a trap: the comments deliberately name the things
    they warn against ("never add -TaskPath"), so a prose mention would fail
    a test that is really asking about code. Strip block comments first, then
    whole-line `#` comments — trailing comments are left alone, since none of
    the absent-construct checks below can be fooled by one.
    """
    text = re.sub(r"<#.*?#>", "", script_text(), flags=re.S)
    return "\n".join(line for line in text.splitlines()
                      if not line.lstrip().startswith("#"))


class TestTheInstallerExists(unittest.TestCase):
    def test_the_windows_installer_key_points_at_a_real_file(self):
        # update.INSTALLERS is what run_installer() joins onto REPO_ROOT, so a
        # typo here is only ever discovered on a real Windows box mid-update.
        relative = update.INSTALLERS["windows"]
        self.assertTrue(os.path.isfile(os.path.join(REPO, relative)),
                        "%s is referenced by INSTALLERS but does not exist"
                        % relative)


class TestTheChannelSurvivesItsOwnRoundTrip(unittest.TestCase):
    """The write half and the read-back half are one format in two places.

    windows.ps1 stores the update channel in the Scheduled Task's action
    arguments, because a task carries no environment block to hold it. It then
    parses that same string back out on the next install to avoid flipping a
    `main` device onto `release`. Those two halves live ~200 lines apart in one
    file and neither one fails visibly if they stop agreeing — the read just
    returns '' forever, silently defaulting every device to `release`. That is
    the exact bug install/linux.sh:44-61 exists to prevent, so it gets a test.
    """

    def read_back_pattern(self):
        found = re.search(r"-match\s+'(--channel[^']+)'", script_text())
        self.assertIsNotNone(found, "no --channel read-back regex in windows.ps1")
        return found.group(1)

    def written_arguments(self):
        found = re.search(r"\$arguments\s*=\s*'([^']+)'\s*-f\s*", script_text())
        self.assertIsNotNone(found, "no task-argument format string in windows.ps1")
        return found.group(1)

    def test_the_written_arguments_match_the_read_back_regex(self):
        template = self.written_arguments()
        pattern = self.read_back_pattern()
        for channel in ("release", "main"):
            # Reproduce PowerShell's -f formatting: {0} is the launcher path,
            # {1} the channel. A path with a space is the realistic case and
            # the one most likely to break a naive pattern.
            rendered = (template.replace("{0}", r"C:\Program Files\AI smartbar"
                                                r"\bin\ai-smartbar")
                                .replace("{1}", channel))
            match = re.search(pattern, rendered)
            self.assertIsNotNone(
                match, "read-back %r does not match written %r"
                       % (pattern, rendered))
            self.assertEqual(match.group(1), channel)

    def test_the_read_back_ignores_a_channel_it_does_not_recognise(self):
        # Mirrors the shell `case "$EXISTING" in release|main)` guard: a
        # corrupted task must fall through to the default, never propagate a
        # junk channel into a fresh registration.
        pattern = self.read_back_pattern()
        self.assertIsNone(re.search(pattern, '"C:\\x\\bin\\ai-smartbar" '
                                             '--update --channel nightly'))


class TestTheInstallerAndTheProbeAgreeOnPaths(unittest.TestCase):
    """present_installers() stats what windows.ps1 writes. Pin both names.

    If these drift, a Windows device reports itself as not-installed on every
    update pass. apply_targets() then returns nothing, the update applies no
    installer at all, and the device quietly stops updating its own launcher —
    with no error anywhere, because every individual step "succeeded".
    """

    def test_the_task_name_matches_the_file_the_probe_stats(self):
        found = re.search(r"\$TaskName\s*=\s*'([^']+)'", script_text())
        self.assertIsNotNone(found, "no $TaskName assignment in windows.ps1")
        probed = os.path.basename(update_runner._win_task_file())
        self.assertEqual(found.group(1), probed)

    def test_the_shortcut_name_matches_the_file_the_probe_stats(self):
        found = re.search(r"\$Shortcut\s*=\s*Join-Path[^\n]*'([^']+\.lnk)'",
                          script_text())
        self.assertIsNotNone(found, "no $Shortcut assignment in windows.ps1")
        probed = os.path.basename(update_runner._win_startup_shortcut())
        self.assertEqual(found.group(1), probed)

    def test_the_task_is_registered_at_the_root_where_the_probe_looks(self):
        # _win_task_file() stats %SystemRoot%\System32\Tasks\<name>, which only
        # holds for a task registered at the root. A -TaskPath would nest the
        # file one directory deeper and the probe would never find it.
        self.assertNotIn("-TaskPath", script_code())


class TestTheLauncherAcceptsWhatTheInstallerCalls(unittest.TestCase):
    """Every CLI form windows.ps1 invokes must actually parse.

    The installer shells out to the launcher three times. A flag it passes that
    argparse rejects exits 2, and on the Scheduled Task path that failure is
    invisible — the task just never updates anything.
    """

    def run_launcher(self, *arguments):
        return subprocess.run([sys.executable, LAUNCHER] + list(arguments),
                              capture_output=True, text=True,
                              env=dict(os.environ, SMARTBAR_CONFIG_DIR=REPO))

    def test_print_config_winenv_is_accepted(self):
        proc = self.run_launcher("--print-config", "winenv")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_update_interval_minutes_prints_a_whole_number(self):
        proc = self.run_launcher("--update-interval", "minutes")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip().isdigit(), proc.stdout)

    def test_the_channel_flag_accepts_both_real_channels(self):
        for channel in update.CHANNELS:
            proc = self.run_launcher("--channel", channel, "--help")
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_channel_flag_rejects_anything_else(self):
        # argparse's `choices` is the whole guard here: without it a typo in
        # the Scheduled Task would silently plan against the default channel.
        proc = self.run_launcher("--channel", "nightly", "--help")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--channel", proc.stderr)

    def test_every_launcher_call_in_the_installer_uses_a_known_flag(self):
        # Catches a flag being renamed in bin/ai-smartbar while windows.ps1
        # keeps calling the old name.
        proc = self.run_launcher("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in re.findall(r"\$Launcher\s+(--[a-z-]+)", script_text()):
            self.assertIn(flag, proc.stdout,
                          "windows.ps1 calls %s, which the launcher does not "
                          "accept" % flag)


if __name__ == "__main__":
    unittest.main()
