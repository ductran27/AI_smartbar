"""Audit-driven presence pins (2026-08-24, batch B5).

The live failure: publish() pushed the remote HEAD sha, which a clone that
has not fetched since main moved does NOT have -> "bad object" on every
beat. This Mac's presence.log holds ~191 such failed beats for one week.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from smartbar import presence_git, presence_runner, update_git
from smartbar.core import presence


class TestPublishTargetsAnObjectWeHave(unittest.TestCase):
    def test_read_remote_returns_advertised_candidates(self):
        out = ("aaa1111111111111111111111111111111111111\tHEAD\n"
               "bbb2222222222222222222222222222222222222\trefs/heads/main\n"
               "ccc3333333333333333333333333333333333333\trefs/tags/v1.0.0\n"
               "ddd4444444444444444444444444444444444444\t"
               "refs/smartbar/p1/dev1/mac-x/1000/-\n")
        proc = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch.object(presence_git, "_run", return_value=proc):
            result = presence_git.read_remote()
        self.assertIsNotNone(result)
        candidates, refs = result
        self.assertIn("aaa1111111111111111111111111111111111111", candidates)
        self.assertIn("bbb2222222222222222222222222222222222222", candidates)
        self.assertIn("ccc3333333333333333333333333333333333333", candidates)
        self.assertEqual(refs, ["refs/smartbar/p1/dev1/mac-x/1000/-"])

    def test_publish_uses_the_first_candidate_the_clone_has(self):
        pushes = []

        def fake_run(args, timeout=25):
            if args[0] == "cat-file":
                # only the TAG object exists locally
                ok = args[-1].startswith("ccc")
                return subprocess.CompletedProcess([], 0 if ok else 1,
                                                   stdout="", stderr="")
            pushes.append(args)
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(presence_git, "_run", side_effect=fake_run):
            ok = presence_git.publish(
                ["aaa1", "bbb2", "ccc3333333333333333333333333333333333333"],
                "refs/smartbar/p1/dev1/mac-x/1000/-", [])
        self.assertTrue(ok)
        self.assertTrue(pushes and pushes[0][0] == "push")
        self.assertIn("ccc3333333333333333333333333333333333333:"
                      "refs/smartbar/p1/dev1/mac-x/1000/-", pushes[0])

    def test_publish_with_no_local_candidate_fails_with_a_log_not_a_push(self):
        def fake_run(args, timeout=25):
            if args[0] == "cat-file":
                return subprocess.CompletedProcess([], 1, stdout="", stderr="")
            raise AssertionError(f"pushed anyway: {args}")
        with mock.patch.object(presence_git, "_run", side_effect=fake_run):
            ok = presence_git.publish(["aaa1"], "refs/smartbar/p1/x/y/1/-", [])
        self.assertFalse(ok)


class TestNoGuiCredentialPrompts(unittest.TestCase):
    def test_env_disables_every_askpass_route(self):
        env = update_git.env()
        self.assertEqual(env.get("GIT_ASKPASS"), "")
        self.assertEqual(env.get("SSH_ASKPASS_REQUIRE"), "never")
        self.assertEqual(env.get("GCM_INTERACTIVE"), "never")
        self.assertIn("BatchMode=yes", env.get("GIT_SSH_COMMAND", ""))


class TestStalenessWindowProtectsOtherDevices(unittest.TestCase):
    def tearDown(self):
        for key in ("SMARTBAR_PRESENCE_INTERVAL", "SMARTBAR_PRESENCE_TTL"):
            os.environ.pop(key, None)

    def test_a_fast_local_interval_does_not_shrink_the_window(self):
        # The window judges OTHER devices' 300s cadence; one device setting
        # INTERVAL=60 used to declare every default device dead for 120s of
        # each cycle (ttl 180 < 300).
        os.environ["SMARTBAR_PRESENCE_INTERVAL"] = "60"
        self.assertGreaterEqual(presence.ttl(), 3 * presence.DEFAULT_INTERVAL)

    def test_an_explicit_tiny_ttl_is_floored_too(self):
        os.environ["SMARTBAR_PRESENCE_TTL"] = "120"
        self.assertGreaterEqual(presence.ttl(), 3 * presence.DEFAULT_INTERVAL)

    def test_non_finite_values_do_not_crash_the_tray(self):
        os.environ["SMARTBAR_PRESENCE_INTERVAL"] = "inf"
        self.assertTrue(presence.interval() < float("inf"))
        int(presence.interval())    # OverflowError killed every tray


class TestKillSwitchSpellings(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SMARTBAR_PRESENCE", None)

    def test_common_falsy_spellings_disable(self):
        for value in ("off", "0", "false", "no", "OFF", "False"):
            os.environ["SMARTBAR_PRESENCE"] = value
            self.assertFalse(presence.enabled(), value)

    def test_anything_else_enables(self):
        os.environ["SMARTBAR_PRESENCE"] = "on"
        self.assertTrue(presence.enabled())


class TestVolatileIdentityDoesNotPublish(unittest.TestCase):
    def test_unpersistable_id_returns_empty(self):
        with mock.patch.object(presence_runner.os, "makedirs",
                               side_effect=OSError("read-only")), \
             mock.patch.object(presence_runner, "ID_FILE",
                               "/nonexistent-dir/device-id"):
            self.assertEqual(presence_runner.device_id(), "")


class TestLeaveWaitsForTheLock(unittest.TestCase):
    def test_leave_retries_a_busy_lock(self):
        calls = {"n": 0}

        def flaky_lock():
            calls["n"] += 1
            return None if calls["n"] < 3 else object()
        with mock.patch.object(presence_runner, "_lock", flaky_lock), \
             mock.patch.object(presence_runner.time, "sleep", lambda s: None), \
             mock.patch.object(presence_runner, "load_state",
                               return_value={}), \
             mock.patch.object(presence_runner.presence, "enabled",
                               return_value=True), \
             mock.patch.object(presence_runner, "_setup_log", lambda: None), \
             mock.patch.object(presence_runner.os, "makedirs",
                               lambda *a, **k: None):
            rc = presence_runner.run_once(leave=True)
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(calls["n"], 3)


class TestMacHostnameIsTheStableOne(unittest.TestCase):
    def test_darwin_prefers_scutil_localhostname(self):
        with mock.patch.object(presence_runner.sys, "platform", "darwin"), \
             mock.patch.object(presence_runner.subprocess, "check_output",
                               return_value="Ducs-MacBook-Pro\n") as fake, \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMARTBAR_PRESENCE_LABEL", None)
            label = presence_runner.device_label()
        fake.assert_called_once()
        self.assertIn("ducs-macbook-pro", label)


class TestStateTempFilesNeverLeak(unittest.TestCase):
    def test_failed_replace_unlinks_the_temp(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(presence_runner, "CACHE_DIR", tmp), \
             mock.patch.object(presence_runner, "STATE_FILE",
                               os.path.join(tmp, "presence-state.json")), \
             mock.patch.object(presence_runner.os, "replace",
                               side_effect=OSError("locked")):
            presence_runner.save_state({"a": 1})
        leftovers = [f for f in os.listdir(tmp)
                     if f.startswith(".presence-state-")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
