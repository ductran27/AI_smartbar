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
    def test_an_override_not_named_claude_gets_a_shim(self):
        # cswap resolves `claude` BY NAME on PATH; an override pointing at
        # a versioned binary silently did nothing.
        import tempfile
        real = os.path.join(tempfile.mkdtemp(), "2.1.241")
        with open(real, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(real, 0o755)
        env = warmup_runner.env_with_claude_on_path(real)
        first = env["PATH"].split(os.pathsep)[0]
        shim = os.path.join(first, "claude")
        self.assertTrue(os.path.islink(shim) or os.path.isfile(shim),
                        f"no claude shim in {first}")

    def test_an_override_named_claude_needs_no_shim(self):
        env = warmup_runner.env_with_claude_on_path("/opt/x/bin/claude")
        self.assertEqual(env["PATH"].split(os.pathsep)[0], "/opt/x/bin")


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
