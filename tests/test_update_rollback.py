"""Tests for the macOS bundle rollback arm of update_runner.run_once().

Why this file exists: this is the code whose stated job is that a bad release
"must never leave a device with no menu bar at all", and nothing exercised it.
tests/e2e-update.sh pins SMARTBAR_UPDATE_TARGETS=linux, so `bundled` is always
False in the one end-to-end test that drives a real failing-installer cycle;
test_runner_portability.py's own docstring disclaims run_once()'s business
logic; and backup_bundle/restore_bundle/BUNDLE_BACKUP appeared nowhere under
tests/ at all. Two real defects lived there because of it — restore_bundle()
throwing away its own failure, and kickstart() being the only subprocess call
in the module with no try/except.

Nothing here touches a real bundle, launchctl or notifier.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from smartbar import update_runner
from smartbar.core import update


class Env(unittest.TestCase):
    """Snapshot the environment: update.enabled() and the notify kill switch
    are both read straight off os.environ at call time."""

    def setUp(self):
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))
        os.environ.pop("SMARTBAR_UPDATE", None)
        os.environ.pop("SMARTBAR_UPDATE_NOTIFY", None)


class BundleHome(Env):
    """APP_BUNDLE and BUNDLE_BACKUP are module constants bound to $HOME at
    import time, so they are patched rather than re-derived."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        # addCleanup, not tearDown: unittest runs tearDown BEFORE the cleanups
        # a test registers, and on Windows a still-open handle blocks the
        # delete with WinError 32.
        self.addCleanup(tmp.cleanup)
        self.bundle = os.path.join(tmp.name, "AI_smartbar.app")
        self.backup = self.bundle + ".prev"
        patcher = mock.patch.multiple(update_runner, APP_BUNDLE=self.bundle,
                                      BUNDLE_BACKUP=self.backup)
        patcher.start()
        self.addCleanup(patcher.stop)

    def plant(self, marker):
        os.makedirs(os.path.join(self.bundle, "Contents"), exist_ok=True)
        with open(os.path.join(self.bundle, "Contents", "marker"), "w",
                  encoding="utf-8") as handle:
            handle.write(marker)

    def marker(self):
        with open(os.path.join(self.bundle, "Contents", "marker"),
                  encoding="utf-8") as handle:
            return handle.read()


class TestBundleBackupAndRestore(BundleHome):
    def test_restore_puts_the_previous_bundle_back(self):
        self.plant("known-good")
        self.assertTrue(update_runner.backup_bundle())
        self.plant("bad-release")
        self.assertTrue(update_runner.restore_bundle())
        self.assertEqual(self.marker(), "known-good")

    def test_backup_says_no_when_there_is_no_bundle_yet(self):
        self.assertFalse(update_runner.backup_bundle())

    def test_restore_says_no_when_nothing_was_ever_backed_up(self):
        self.assertFalse(update_runner.restore_bundle())

    def test_a_failed_restore_reports_it_and_leaves_no_bundle_at_all(self):
        """The hazard that makes the bool return necessary.

        restore_bundle() deletes APP_BUNDLE before it moves the backup into
        place, so a move that fails leaves the device with nothing. It used to
        return None either way, and the caller kickstarted the LaunchAgent
        regardless — respawning a job whose program had just been deleted.
        """
        self.plant("known-good")
        self.assertTrue(update_runner.backup_bundle())
        with mock.patch.object(update_runner.shutil, "move",
                               side_effect=OSError("no space left on device")):
            self.assertFalse(update_runner.restore_bundle())
        self.assertFalse(os.path.isdir(self.bundle))


class TestRollbackOnlyRestartsWhatIsThere(Env):
    """run_once()'s failure arm, with git and the installers stubbed out."""

    def drive(self, *, restored):
        """Run one failing update whose targets include the Swift bundle."""
        plan = update.UpdatePlan(action=update.UPDATE, target_ref="v9.9.9",
                                 target_version="9.9.9", reason="test")
        repo = mock.Mock(head="a" * 40, branch="main", version="9.9.8",
                         dirty=False)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patches = [
            mock.patch.object(update_runner, "CACHE_DIR", tmp.name),
            mock.patch.object(update_runner, "LOG_FILE",
                              os.path.join(tmp.name, "update.log")),
            mock.patch.object(update_runner, "load_state", return_value={}),
            mock.patch.object(update_runner, "save_state"),
            mock.patch.object(update_runner.portable, "lock",
                              return_value=mock.Mock()),
            mock.patch.object(update_runner.update_git, "fetch"),
            mock.patch.object(update_runner.update_git, "repo_state",
                              return_value=repo),
            mock.patch.object(update_runner.update_git, "checkout",
                              return_value=""),
            mock.patch.object(update_runner.update_git, "restore"),
            mock.patch.object(update_runner.update_git, "version_in_checkout",
                              return_value="9.9.8"),
            mock.patch.object(update_runner, "_plan", return_value=plan),
            mock.patch.object(update_runner, "present_installers",
                              return_value={"macos_swift": True}),
            # The installer failing is what sends run_once() down the arm
            # this whole class is about.
            mock.patch.object(update_runner, "run_installer",
                              return_value="build failed"),
            mock.patch.object(update_runner, "backup_bundle", return_value=True),
            mock.patch.object(update_runner, "restore_bundle",
                              return_value=restored),
            mock.patch.object(update_runner, "kickstart"),
            mock.patch.object(update_runner, "notify"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        code = update_runner.run_once()
        return code, update_runner.kickstart, update_runner.notify

    def test_a_restored_bundle_is_restarted(self):
        code, kickstart, notify = self.drive(restored=True)
        self.assertEqual(code, 1)
        kickstart.assert_called_once_with(update_runner.APP_LABEL)
        notify.assert_called_once()

    def test_a_bundle_that_could_not_be_restored_is_never_restarted(self):
        """The defect: kickstarting an agent whose program was just deleted.

        launchd would respawn it, find nothing to exec, and the user would be
        left with no menu bar — the exact outcome the backup exists to avoid.
        Failing loudly in the log and leaving the agent down is recoverable;
        a respawn loop against a missing binary is not obviously anything.
        """
        code, kickstart, notify = self.drive(restored=False)
        self.assertEqual(code, 1)
        kickstart.assert_not_called()
        # The failure is still recorded and still announced either way.
        notify.assert_called_once()


@unittest.skipIf(sys.platform == "win32",
                 "kickstart() returns before the subprocess on win32, and "
                 "os.getuid() does not exist there; the win32 arm has its "
                 "own coverage in test_runner_portability.py")
class TestKickstartNeverEscapes(Env):
    """kickstart() runs inside run_once()'s rollback arm, above the
    record_failure/save_state/notify that report the failed update. An
    exception escaping it loses all three."""

    def test_a_hung_launchctl_is_swallowed(self):
        with mock.patch.object(
                update_runner.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("launchctl", 60)) as run:
            update_runner.kickstart("com.example.app")  # must not raise
        run.assert_called_once()

    def test_a_missing_launchctl_is_swallowed(self):
        with mock.patch.object(update_runner.subprocess, "run",
                               side_effect=OSError("no such file")) as run:
            update_runner.kickstart("com.example.app")
        run.assert_called_once()


class TestNotifiersSurviveAHang(Env):
    """TimeoutExpired is a SubprocessError, NOT an OSError.

    Every notifier branch in both runners passes timeout=, so a hang is
    reachable; the bare `except OSError` these tests pin could only catch a
    notifier that failed to START.
    """

    @unittest.skipIf(sys.platform == "win32",
                     "the win32 arm has its own broad `except Exception` "
                     "inside _win32_notify, so the outer handler under test "
                     "is unreachable there")
    def test_update_notify_survives_a_hung_notifier(self):
        with mock.patch.object(
                update_runner.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("osascript", 10)) as run:
            update_runner.notify("title", "body")  # must not raise
        run.assert_called_once()

    def test_warmup_notify_failure_survives_a_hung_notifier(self):
        """No skip needed: warmup_runner's win32 branch shares the one try,
        so this holds on all three platforms."""
        from smartbar import warmup_runner
        with mock.patch.object(
                warmup_runner.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("notify-send", 10)) as run:
            warmup_runner.notify_failure("title", "body")
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
