"""Audit-driven warmup pins (2026-08-24, batch B7).

The live failure: every unattended ping ran with the user's full settings
from cwd "/", firing their SessionStart/Stop hooks (a Discord webhook,
~10x/day) and leaving 111 transcripts (6 MB). And the post-ping "verify"
was structurally a false negative (7 successes vs 397 warnings in the real
log) because cswap's serve TTL never refetches that fast.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from smartbar import warmup_runner
from smartbar.core import device_config


class TestPingsAreHookFree(unittest.TestCase):
    def setUp(self):
        # ping_argv resolves the cswap binary; CI runners have none
        # installed, and this file must not depend on the dev box having
        # one (or on test_warmup_runner's module-level override leaking
        # in first).
        patcher = mock.patch.dict(os.environ,
                                  {"SMARTBAR_CSWAP": "/mock/bin/cswap"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_argv_excludes_user_settings(self):
        argv = warmup_runner.ping_argv(2, ["--model", "haiku"])
        self.assertIn("--setting-sources", argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "local")
        self.assertIn("--strict-mcp-config", argv)

    def test_ping_runs_in_a_dedicated_empty_cwd(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(warmup_runner.subprocess, "run", fake_run):
            warmup_runner.ping(1, "/mock/bin/claude")
        cwd = seen.get("cwd") or ""
        self.assertTrue(cwd.endswith("warmup-cwd"), cwd)
        self.assertTrue(os.path.isdir(cwd))

    def test_ping_decodes_utf8_regardless_of_locale(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(warmup_runner.subprocess, "run", fake_run):
            warmup_runner.ping(1, "/mock/bin/claude")
        self.assertEqual(seen.get("encoding"), "utf-8")
        self.assertEqual(seen.get("errors"), "replace")


class TestClaudeOverrideActuallyResolves(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch.object(warmup_runner, "CACHE_DIR",
                                    os.path.join(self.tmp, "cache"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _versioned_binary(self, name):
        real = os.path.join(self.tmp, name)
        with open(real, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(real, 0o755)
        return real

    def test_an_override_not_named_claude_gets_a_shim(self):
        # cswap resolves `claude` BY NAME on PATH; an override pointing at
        # a versioned binary silently did nothing. The shim's shape is the
        # host's: a symlink on POSIX, a `claude.cmd` on Windows (which is
        # what the Windows CI leg runs this on).
        real = self._versioned_binary("2.1.241")
        env = warmup_runner.env_with_claude_on_path(real)
        first = env["PATH"].split(os.pathsep)[0]
        shim = os.path.join(
            first, "claude.cmd" if os.name == "nt" else "claude")
        self.assertTrue(os.path.islink(shim) or os.path.isfile(shim),
                        f"no claude shim in {first}")

    def test_the_windows_shim_is_a_cmd_wrapper_naming_the_target(self):
        # Exercised in-process on every platform: only file I/O is involved.
        real = self._versioned_binary("claude-2.1.241.exe")
        with mock.patch.object(warmup_runner.sys, "platform", "win32"):
            env = warmup_runner.env_with_claude_on_path(real)
        first = env["PATH"].split(os.pathsep)[0]
        shim = os.path.join(first, "claude.cmd")
        with open(shim, encoding="utf-8", newline="") as fh:
            body = fh.read()
        self.assertEqual(body, f'@"{os.path.abspath(real)}" %*\r\n')
        # A second call with the same target leaves the file alone.
        before = os.stat(shim).st_mtime_ns
        with mock.patch.object(warmup_runner.sys, "platform", "win32"):
            warmup_runner.env_with_claude_on_path(real)
        self.assertEqual(os.stat(shim).st_mtime_ns, before)

    def test_windows_matches_the_name_through_pathext(self):
        with mock.patch.object(warmup_runner.sys, "platform", "win32"):
            for name in ("claude.exe", "claude.cmd", "Claude.EXE", "claude"):
                self.assertFalse(warmup_runner._needs_claude_shim(name), name)
            self.assertTrue(
                warmup_runner._needs_claude_shim("claude-2.1.241.exe"))

    def test_an_override_named_claude_needs_no_shim(self):
        override = "/opt/x/bin/claude"
        env = warmup_runner.env_with_claude_on_path(override)
        # abspath, because Windows reports it drive-qualified (D:\opt\x\bin).
        self.assertEqual(env["PATH"].split(os.pathsep)[0],
                         os.path.dirname(os.path.abspath(override)))
        self.assertFalse(os.path.exists(
            os.path.join(warmup_runner.CACHE_DIR, "claude-shim")))


class TestVerificationIsNextRun(unittest.TestCase):
    def test_success_logs_info_not_a_false_warning(self):
        # cswap's serve TTL (180s) means the new window CANNOT be visible
        # immediately; the old immediate re-fetch logged WARNING for nearly
        # every real success.
        from smartbar.core.model import Account, Metric, Snapshot
        acct = Account(number=1, email="a@x.com", active=True, ok=True,
                       status="ok",
                       fetched_at="2026-08-24T11:59:59Z",
                       metrics=[Metric(key="5h", label="5h", short="5h",
                                       pct=10.0, resets_at="")])
        warnings, infos = [], []
        with mock.patch.object(warmup_runner.cswap, "fetch",
                               return_value=Snapshot(accounts=[acct])), \
             mock.patch.object(warmup_runner, "claude_binary",
                               return_value="/mock/bin/claude"), \
             mock.patch.object(warmup_runner, "load_state", return_value={}), \
             mock.patch.object(warmup_runner, "save_state"), \
             mock.patch.object(warmup_runner.portable, "lock",
                               return_value=mock.Mock()), \
             mock.patch.object(warmup_runner.warmup, "should_warm",
                               return_value=(True, "")), \
             mock.patch.object(warmup_runner, "ping",
                               return_value=(True, "ok (haiku)")), \
             mock.patch.object(warmup_runner.log, "warning",
                               side_effect=lambda m, *a: warnings.append(m)), \
             mock.patch.object(warmup_runner.log, "info",
                               side_effect=lambda m, *a: infos.append(m)):
            warmup_runner.run_once()
        self.assertFalse([w for w in warnings if "not visible" in w])
        self.assertTrue([i for i in infos if "pinged" in i or "warmed" in i])


class TestNotificationEscaping(unittest.TestCase):
    def test_backslash_is_escaped_before_quotes(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["script"] = argv[argv.index("-e") + 1]
            return mock.Mock(returncode=0)
        with mock.patch.object(warmup_runner.sys, "platform", "darwin"), \
             mock.patch.object(warmup_runner.subprocess, "run", fake_run):
            warmup_runner.notify_failure("t", "path ends in \\")
        # An unescaped trailing backslash swallowed the closing quote and
        # the whole notification silently failed to compile.
        self.assertIn("\\\\", seen["script"])


class TestConfigParsing(unittest.TestCase):
    def test_a_bom_does_not_eat_the_first_key(self):
        settings, problems = device_config.parse(
            "﻿SMARTBAR_INTERVAL=90\n")
        self.assertEqual(settings.get("SMARTBAR_INTERVAL"), "90")

    def test_an_inline_comment_is_not_part_of_the_value(self):
        settings, _ = device_config.parse("SMARTBAR_INTERVAL=90  # faster\n")
        self.assertEqual(settings.get("SMARTBAR_INTERVAL"), "90")

    def test_a_hash_inside_a_value_with_no_space_survives(self):
        settings, _ = device_config.parse(
            "SMARTBAR_PRESENCE_LABEL=box#3\n")
        self.assertEqual(settings.get("SMARTBAR_PRESENCE_LABEL"), "box#3")


class TestPathOverridesExpandTilde(unittest.TestCase):
    def test_smartbar_claude_expands(self):
        with mock.patch.dict(os.environ,
                             {"SMARTBAR_CLAUDE": "~/bin/claude"}):
            self.assertEqual(warmup_runner.claude_binary(),
                             os.path.expanduser("~/bin/claude"))

    def test_smartbar_cswap_expands(self):
        from smartbar.core import cswap
        with mock.patch.dict(os.environ, {"SMARTBAR_CSWAP": "~/bin/cswap"}):
            self.assertEqual(cswap._binary(),
                             os.path.expanduser("~/bin/cswap"))


class TestStateRobustness(unittest.TestCase):
    def test_corrupt_state_is_logged_not_silent(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        bad = os.path.join(tmp, "warmup-state.json")
        with open(bad, "w") as fh:
            fh.write("{corrupt")
        noted = []
        with mock.patch.object(warmup_runner, "STATE_FILE", bad), \
             mock.patch.object(warmup_runner.log, "warning",
                               side_effect=lambda m, *a: noted.append(m)):
            state = warmup_runner.load_state()
        self.assertEqual(state, {"days": {}, "last": {}})
        self.assertTrue(noted, "an operator needs to know caps were reset")


if __name__ == "__main__":
    unittest.main()
