"""Windows-portability seams of smartbar/update_runner.py,
smartbar/presence_runner.py and smartbar/presence_client.py.

Phase 1 of the Windows port does not ask these three files to grow any new
user-facing behaviour — it asks them to still IMPORT on a platform with no
`fcntl`, and to get their cache/config directories and their exclusive locks
from the two shared shims (smartbar.core.paths, smartbar.core.portable)
instead of a hand-rolled `fcntl.flock` / `os.environ.get(...) or
os.path.expanduser(...)` copy. `bin/ai-smartbar` imports presence_runner
just for CONFIG_DIR, and `smartbar/linux/tray.py` imports update_runner just
for load_state() — a module-scope `import fcntl` in either one is enough to
break both of those importers on win32 even though neither of them ever
calls the function that needed fcntl in the first place. That is the one
property no ordinary unit test of run_once()'s business logic would ever
catch, so it gets its own file rather than living inside test_presence.py /
a hypothetical test_update_runner.py.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock

from smartbar import presence_client, presence_runner, update_runner
from smartbar.core import paths


@contextlib.contextmanager
def _reloaded(module_name, *, platform=None, env=None, fcntl_missing=False):
    """Reimport `module_name` under a temporary env/platform, then restore
    the real module object to sys.modules no matter what happens inside the
    `with` block.

    Restoring matters as much as reloading does here: test_presence.py
    does `from smartbar import presence_runner` fresh in nearly every test
    and monkeypatches its module-level STATE_FILE, so a reload left behind
    in sys.modules by this file (with a fake SMARTBAR_CACHE_DIR baked into
    its CACHE_DIR/CONFIG_DIR constants) would silently corrupt whichever
    test from that file happens to run afterward in the same process —
    exactly the kind of cross-test leak `python3 -m unittest discover`
    would only reveal by file-order flakiness, not by this file failing on
    its own.
    """
    saved_module = sys.modules.pop(module_name, None)
    saved_platform = paths.sys.platform
    saved_env = {name: os.environ.get(name) for name in (env or {})}
    saved_fcntl = sys.modules.get("fcntl", "not-set")
    if platform is not None:
        paths.sys.platform = platform
    for name, value in (env or {}).items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    if fcntl_missing:
        # The documented CPython mechanism for marking a module "known
        # missing": any `import fcntl` anywhere sees sys.modules["fcntl"]
        # is None and raises ImportError immediately, instead of Python
        # re-running the real (present-on-this-dev-machine) fcntl
        # extension. It is the only way to prove a module-scope import was
        # actually removed rather than merely unexercised by this test.
        sys.modules["fcntl"] = None
    try:
        yield importlib.import_module(module_name)
    finally:
        paths.sys.platform = saved_platform
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if saved_fcntl == "not-set":
            sys.modules.pop("fcntl", None)
        else:
            sys.modules["fcntl"] = saved_fcntl
        sys.modules.pop(module_name, None)
        if saved_module is not None:
            sys.modules[module_name] = saved_module


class TestImportSurvivesWithoutFcntl(unittest.TestCase):
    """The regression this whole file exists to pin: both runners used to
    do `import fcntl` at module scope, which is enough to make the whole
    module unimportable on win32 even for a caller that only wants
    CONFIG_DIR or load_state().
    """

    def test_update_runner_imports_when_fcntl_is_unavailable(self):
        with _reloaded("smartbar.update_runner", fcntl_missing=True) as mod:
            self.assertTrue(hasattr(mod, "run_once"))

    def test_presence_runner_imports_when_fcntl_is_unavailable(self):
        with _reloaded("smartbar.presence_runner", fcntl_missing=True) as mod:
            self.assertTrue(hasattr(mod, "run_once"))


class TestPathsIndirection(unittest.TestCase):
    """CACHE_DIR/CONFIG_DIR must come from smartbar.core.paths rather than
    a sixth hand-copied `os.environ.get(...) or os.path.expanduser(...)`
    expression — see paths.py's own docstring for how linux/tray.py
    silently dropped the SMARTBAR_CACHE_DIR override the last time this
    was copied instead of shared.
    """

    def test_update_runner_cache_dir_follows_the_override(self):
        env = {"SMARTBAR_CACHE_DIR": "/tmp/ai-smartbar-test-cache"}
        with _reloaded("smartbar.update_runner", env=env) as mod:
            self.assertEqual(mod.CACHE_DIR, "/tmp/ai-smartbar-test-cache")

    def test_presence_runner_dirs_follow_their_overrides(self):
        env = {"SMARTBAR_CACHE_DIR": "/tmp/ai-smartbar-test-cache",
               "SMARTBAR_CONFIG_DIR": "/tmp/ai-smartbar-test-config"}
        with _reloaded("smartbar.presence_runner", env=env) as mod:
            self.assertEqual(mod.CACHE_DIR, "/tmp/ai-smartbar-test-cache")
            self.assertEqual(mod.CONFIG_DIR, "/tmp/ai-smartbar-test-config")

    def test_update_runner_falls_back_to_localappdata_on_win32(self):
        local = r"C:\Users\duc\AppData\Local"
        env = {"SMARTBAR_CACHE_DIR": None, "LOCALAPPDATA": local}
        with _reloaded("smartbar.update_runner", platform="win32",
                       env=env) as mod:
            self.assertEqual(mod.CACHE_DIR,
                             os.path.join(local, "ai-smartbar"))

    def test_presence_runner_falls_back_to_appdata_on_win32(self):
        roaming = r"C:\Users\duc\AppData\Roaming"
        env = {"SMARTBAR_CONFIG_DIR": None, "APPDATA": roaming}
        with _reloaded("smartbar.presence_runner", platform="win32",
                       env=env) as mod:
            self.assertEqual(mod.CONFIG_DIR,
                             os.path.join(roaming, "ai-smartbar"))


class TestLockIndirection(unittest.TestCase):
    """run_once()/_lock() must take their exclusive lock through
    smartbar.core.portable.lock() so that "another run already holds it"
    keeps meaning exactly what it meant under bare fcntl.flock(): None
    back, skip quietly, return 0 — without ever touching git.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_presence_runner_lock_is_exclusive_through_the_shim(self):
        with _reloaded("smartbar.presence_runner",
                       env={"SMARTBAR_CACHE_DIR": self.tmp.name}) as mod:
            os.makedirs(mod.CACHE_DIR, exist_ok=True)
            first = mod._lock()
            self.addCleanup(lambda: first and first.close())
            self.assertIsNotNone(first)
            self.assertIsNone(mod._lock())

    def test_update_runner_skips_without_touching_git_when_lock_is_held(self):
        with _reloaded("smartbar.update_runner",
                       env={"SMARTBAR_CACHE_DIR": self.tmp.name}) as mod:
            never = AssertionError("should not reach git")
            with mock.patch.object(mod.portable, "lock",
                                   return_value=None) as fake_lock, \
                 mock.patch.object(mod.update_git, "fetch", side_effect=never):
                self.assertEqual(mod.run_once(), 0)
            fake_lock.assert_called_once_with(mod.LOCK_FILE)


class TestPresenceClientSpawnsDetached(unittest.TestCase):
    """_spawn() must route through portable.spawn_detached rather than a
    bare `subprocess.Popen(..., start_new_session=True)` — `start_new_session`
    is not a valid Popen keyword at all on win32, so passing it directly
    would raise there instead of silently doing the wrong thing.
    """

    def test_spawn_delegates_and_never_passes_start_new_session_itself(self):
        calls = []

        class FakeStdin:
            def write(self, data):
                self.written = data

            def close(self):
                pass

        class FakeProc:
            def __init__(self):
                self.stdin = FakeStdin()

        def fake_spawn_detached(argv, **kwargs):
            calls.append((argv, kwargs))
            return FakeProc()

        with mock.patch.object(presence_client.portable, "spawn_detached",
                               side_effect=fake_spawn_detached) as fake:
            presence_client._spawn(["--presence-beat"], "hello")

        fake.assert_called_once()
        argv, kwargs = calls[0]
        self.assertEqual(argv, [sys.executable, presence_client.LAUNCHER,
                                "--presence-beat"])
        # spawn_detached owns the platform-specific detach mechanism now —
        # _spawn must not also pass its own start_new_session, which would
        # be redundant on POSIX and a TypeError on win32.
        self.assertNotIn("start_new_session", kwargs)


class TestUpdateNotifyOnWindows(unittest.TestCase):
    """notify()'s original else-branch execs `notify-send`, which does not
    exist on win32: before the win32 arm, that raised FileNotFoundError (an
    OSError), and the bare `except OSError: log.exception(...)` turned a
    notifier that never had a chance into one that looks identical to a
    real notifier breaking. The win32 arm must never reach for that binary,
    and SMARTBAR_UPDATE_NOTIFY=off must keep short-circuiting before any
    platform check runs at all.
    """

    def setUp(self):
        self.saved_platform = update_runner.sys.platform
        self.saved_notify = os.environ.get("SMARTBAR_UPDATE_NOTIFY")
        os.environ.pop("SMARTBAR_UPDATE_NOTIFY", None)

    def tearDown(self):
        update_runner.sys.platform = self.saved_platform
        if self.saved_notify is None:
            os.environ.pop("SMARTBAR_UPDATE_NOTIFY", None)
        else:
            os.environ["SMARTBAR_UPDATE_NOTIFY"] = self.saved_notify

    def test_win32_never_execs_notify_send(self):
        update_runner.sys.platform = "win32"
        with mock.patch.object(update_runner.subprocess, "run") as fake_run:
            update_runner.notify("title", "body")
        fake_run.assert_not_called()

    def test_notify_off_still_short_circuits_on_win32(self):
        update_runner.sys.platform = "win32"
        os.environ["SMARTBAR_UPDATE_NOTIFY"] = "off"
        with mock.patch.object(update_runner.subprocess, "run") as fake_run, \
             mock.patch.object(update_runner.log, "info") as fake_info:
            update_runner.notify("title", "body")
        fake_run.assert_not_called()
        fake_info.assert_not_called()


class TestPresentInstallersOnWindows(unittest.TestCase):
    """D6: present_installers()'s win32 arm. Before this arm existed, the
    only platform check in present_installers() was `if sys.platform !=
    "darwin":`, which is true on win32 too -- so a Windows device would get
    handed the LINUX shape and update_runner would keep re-running
    install/linux.sh on every single update pass forever. Everything below
    pins the new "windows" key by name and pins that the old "linux" key
    never comes back once sys.platform is win32.
    """

    def setUp(self):
        self.saved_platform = update_runner.sys.platform
        self.saved_env = {name: os.environ.get(name) for name in
                          ("SMARTBAR_UPDATE_TARGETS", "APPDATA", "SystemRoot")}
        os.environ.pop("SMARTBAR_UPDATE_TARGETS", None)
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["APPDATA"] = os.path.join(self.tmp.name, "Roaming")
        os.environ["SystemRoot"] = os.path.join(self.tmp.name, "Windows")
        update_runner.sys.platform = "win32"

    def tearDown(self):
        update_runner.sys.platform = self.saved_platform
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def test_neither_artifact_present_reports_windows_false(self):
        self.assertEqual(update_runner.present_installers(),
                         {"windows": False})

    def test_the_startup_shortcut_alone_is_enough(self):
        path = update_runner._win_startup_shortcut()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        self.assertEqual(update_runner.present_installers(),
                         {"windows": True})

    def test_the_scheduled_task_file_alone_is_enough(self):
        path = update_runner._win_task_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        self.assertEqual(update_runner.present_installers(),
                         {"windows": True})

    def test_the_shape_is_never_the_linux_one(self):
        # The regression this whole class exists to pin.
        result = update_runner.present_installers()
        self.assertEqual(set(result), {"windows"})
        self.assertNotIn("linux", result)

    def test_update_targets_override_still_wins_on_win32(self):
        os.environ["SMARTBAR_UPDATE_TARGETS"] = "linux"
        self.assertEqual(update_runner.present_installers(), {"linux": True})

    def test_the_override_also_accepts_windows_explicitly(self):
        os.environ["SMARTBAR_UPDATE_TARGETS"] = "windows"
        self.assertEqual(update_runner.present_installers(),
                         {"windows": True})


class TestRunInstallerForPowerShell(unittest.TestCase):
    """D6: run_installer()'s `.ps1` branch. `os.access(path, os.X_OK)` is
    meaningless for a `.ps1` on Windows -- an existing, perfectly runnable
    script has no exec bit and would fail it every time -- so this branch
    checks os.path.isfile instead, and shells out through pwsh/powershell
    since a `.ps1` cannot be argv[0] the way a POSIX script can.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_repo_root = update_runner.update_git.REPO_ROOT
        update_runner.update_git.REPO_ROOT = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, "install"))
        self.script = os.path.join(self.tmp.name, "install", "windows.ps1")
        with open(self.script, "w") as handle:
            handle.write("# no chmod +x -- exec bit is not the point here\n")

    def tearDown(self):
        update_runner.update_git.REPO_ROOT = self.saved_repo_root
        self.tmp.cleanup()

    def _ok_run(self):
        return mock.patch.object(
            update_runner.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout="", stderr=""))

    def test_a_non_executable_ps1_still_runs(self):
        # The exact regression: os.access(..., os.X_OK) would reject this
        # file (no exec bit) even though PowerShell runs it fine.
        with mock.patch.object(update_runner.shutil, "which",
                               return_value="/usr/bin/pwsh"), self._ok_run():
            failure = update_runner.run_installer("windows")
        self.assertEqual(failure, "")

    def test_a_missing_ps1_fails_cleanly_without_raising(self):
        os.remove(self.script)
        failure = update_runner.run_installer("windows")
        self.assertIn("missing", failure)

    def test_argv_is_the_powershell_invocation_and_apply_env_is_set(self):
        with mock.patch.object(update_runner.shutil, "which",
                               return_value="/usr/bin/pwsh") as fake_run, \
             self._ok_run() as fake_subprocess_run:
            update_runner.run_installer("windows")
        argv = fake_subprocess_run.call_args.args[0]
        kwargs = fake_subprocess_run.call_args.kwargs
        self.assertEqual(argv, ["/usr/bin/pwsh", "-NoProfile",
                                "-ExecutionPolicy", "Bypass", "-File",
                                self.script])
        self.assertEqual(kwargs["env"]["SMARTBAR_UPDATE_APPLY"], "1")

    def test_pwsh_is_preferred_over_powershell_when_both_exist(self):
        def fake_which(name):
            return {"pwsh": "/usr/bin/pwsh",
                    "powershell": "/usr/bin/powershell"}.get(name)
        with mock.patch.object(update_runner.shutil, "which",
                               side_effect=fake_which), self._ok_run() as fake:
            update_runner.run_installer("windows")
        self.assertEqual(fake.call_args.args[0][0], "/usr/bin/pwsh")

    def test_falls_back_to_powershell_when_pwsh_is_absent(self):
        def fake_which(name):
            return {"powershell": "/usr/bin/powershell"}.get(name)
        with mock.patch.object(update_runner.shutil, "which",
                               side_effect=fake_which), self._ok_run() as fake:
            update_runner.run_installer("windows")
        self.assertEqual(fake.call_args.args[0][0], "/usr/bin/powershell")

    def test_neither_shell_present_fails_without_raising(self):
        with mock.patch.object(update_runner.shutil, "which",
                               return_value=None):
            failure = update_runner.run_installer("windows")
        self.assertIn("pwsh", failure)
        self.assertIn("powershell", failure)

    def test_does_not_consult_os_access_for_a_ps1_script(self):
        with mock.patch.object(update_runner.os, "access") as fake_access, \
             mock.patch.object(update_runner.shutil, "which",
                               return_value="/usr/bin/pwsh"), self._ok_run():
            update_runner.run_installer("windows")
        fake_access.assert_not_called()


class TestVerifyOnWindows(unittest.TestCase):
    """D6: verify()'s Windows arm is a deliberate no-op beyond the portable
    --version check -- see verify()'s own docstring for why no reliable
    stdlib-only tray-liveness check exists, and why silently trusting a
    healthy launcher beats inventing a check that could false-positive a
    rollback of a perfectly good update.
    """

    def setUp(self):
        self.saved_platform = update_runner.sys.platform

    def tearDown(self):
        update_runner.sys.platform = self.saved_platform

    def _ok_version_check(self):
        return mock.patch.object(
            update_runner.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout="", stderr=""))

    def test_a_healthy_launcher_is_not_rolled_back_for_lack_of_a_tray_check(self):
        update_runner.sys.platform = "win32"
        with self._ok_version_check():
            self.assertEqual(update_runner.verify(["windows"]), "")

    def test_it_logs_rather_than_silently_doing_nothing(self):
        update_runner.sys.platform = "win32"
        with self._ok_version_check(), \
             mock.patch.object(update_runner.log, "info") as fake_info:
            update_runner.verify(["windows"])
        fake_info.assert_called_once()

    def test_a_broken_launcher_still_fails_verify_on_windows(self):
        # The portable half (--version) must still catch a genuinely broken
        # update even though the tray-specific half is a no-op.
        update_runner.sys.platform = "win32"
        with mock.patch.object(
                update_runner.subprocess, "run",
                return_value=mock.Mock(returncode=1, stdout="", stderr="boom")):
            failure = update_runner.verify(["windows"])
        self.assertIn("exited 1", failure)

    def test_the_windows_liveness_arm_never_runs_off_windows(self):
        # "windows" can appear in targets off Windows only via the
        # SMARTBAR_UPDATE_TARGETS test override; the arm must be gated on
        # sys.platform, not merely on "windows" being present in targets.
        update_runner.sys.platform = "darwin"
        with self._ok_version_check(), \
             mock.patch.object(update_runner.log, "info") as fake_info:
            update_runner.verify(["windows"])
        fake_info.assert_not_called()


class TestKickstartOnWindows(unittest.TestCase):
    """D6: kickstart() calls os.getuid(), which win32 CPython does not
    define at all. Unreachable today (bundled is only True when
    "macos_swift" is a target, which never happens on win32) but guarded
    anyway, per the contract, in case that invariant ever changes.
    """

    def setUp(self):
        self.saved_platform = update_runner.sys.platform

    def tearDown(self):
        update_runner.sys.platform = self.saved_platform

    def test_win32_never_calls_launchctl_or_getuid(self):
        update_runner.sys.platform = "win32"
        with mock.patch.object(update_runner.subprocess, "run") as fake_run, \
             mock.patch.object(
                 update_runner.os, "getuid", create=True,
                 side_effect=AssertionError("no getuid on win32")):
            update_runner.kickstart("some.label")
        fake_run.assert_not_called()

    def test_win32_logs_a_warning_instead_of_crashing(self):
        update_runner.sys.platform = "win32"
        with mock.patch.object(update_runner.subprocess, "run"), \
             mock.patch.object(update_runner.log, "warning") as fake_warn:
            update_runner.kickstart("some.label")
        fake_warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
