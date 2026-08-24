"""Audit-driven updater pins (2026-08-24, batch B4).

The live failure behind most of these: the 08-22 history rewrite moved
tags on origin, `git fetch --tags` refused to clobber them, --quiet blanked
the error, and this Mac's updater failed silently every 6 h for five days.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from smartbar import update_git, update_runner
from smartbar.core import update


def state(**kwargs):
    defaults = {"head": "sha-old", "branch": "", "dirty": False,
                "unpushed": 0, "tags": ["v0.3.0"], "head_tags": [],
                "remote_main": "sha-remote", "version": "0.3.0"}
    defaults.update(kwargs)
    return update.RepoState(**defaults)


class TestFetchSurvivesMovedTags(unittest.TestCase):
    def test_fetch_forces_tag_updates_and_prunes(self):
        calls = []
        with mock.patch.object(update_git, "git",
                               lambda *a, **k: calls.append(a)):
            update_git.fetch()
        args = calls[0]
        self.assertIn("--force", args)
        self.assertIn("--prune-tags", args)

    def test_git_error_names_the_exit_code_when_stderr_is_empty(self):
        proc = subprocess.CompletedProcess([], returncode=128, stdout="",
                                           stderr="")
        with mock.patch.object(update_git.subprocess, "run",
                               return_value=proc), \
             mock.patch.object(update_git, "git_binary",
                               return_value="/usr/bin/git"):
            with self.assertRaises(update_git.GitError) as ctx:
                update_git.git("fetch")
        self.assertIn("exit 128", str(ctx.exception))


class TestFetchFailureIsNotSilent(unittest.TestCase):
    """Three consecutive failed fetches notify the user and leave an honest
    state file (the manual check used to answer 'Check busy' forever)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patches = [
            mock.patch.object(update_runner, "CACHE_DIR", self.tmp),
            mock.patch.object(update_runner, "STATE_FILE",
                              os.path.join(self.tmp, "update-state.json")),
            mock.patch.object(update_runner, "LOCK_FILE",
                              os.path.join(self.tmp, "update.lock")),
            mock.patch.object(update_runner, "LOG_FILE",
                              os.path.join(self.tmp, "update.log")),
            mock.patch.object(update_git, "fetch",
                              side_effect=update_git.GitError("git fetch "
                                                              "failed: exit 1")),
            # portable.lock keeps its handle alive for the process lifetime
            # (by design — real runs are separate processes); sequential
            # in-process run_once calls need a fresh dummy per call.
            mock.patch.object(update_runner.portable, "lock",
                              lambda path: object()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.notes = []
        p = mock.patch.object(update_runner, "notify",
                              lambda title, body: self.notes.append(title))
        p.start()
        self.addCleanup(p.stop)

    def test_third_consecutive_failure_notifies_once(self):
        for _ in range(4):
            self.assertEqual(update_runner.run_once(check_only=True), 1)
        self.assertEqual(len(self.notes), 1)
        self.assertIn("update", self.notes[0].lower())

    def test_state_records_the_failure_for_the_manual_check(self):
        update_runner.run_once(check_only=True)
        st = update_runner.load_state()
        self.assertTrue(st.get("checkedAt"))
        self.assertEqual(st.get("action"), "blocked")
        self.assertIn("exit 1", st.get("reason", ""))


class TestReleaseChannelTracksWhatWasBuilt(unittest.TestCase):
    """An interrupted apply leaves HEAD at the tag with the OLD app installed;
    'tag in head_tags' alone then reports 'already up to date' forever."""

    def test_a_tag_checkout_nothing_was_installed_for_is_not_current(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"], head="sha-tag"),
                                  applied_ref="sha-older")
        self.assertTrue(plan.should_apply)

    def test_a_tag_checkout_the_installers_ran_for_is_current(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"], head="sha-tag"),
                                  applied_ref="sha-tag")
        self.assertEqual(plan.action, update.CURRENT)

    def test_an_unrecorded_applied_ref_still_counts_as_built(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"], head="sha-tag"),
                                  applied_ref="")
        self.assertEqual(plan.action, update.CURRENT)


class TestResetSemantics(unittest.TestCase):
    def test_reset_reapplies_even_at_the_target(self):
        # README sells --reset as "discard drift and re-install from
        # scratch"; at the newest tag it used to be a silent no-op.
        plan = update.plan_update(state(head_tags=["v0.3.0"], head="sha-tag"),
                                  reset=True)
        self.assertTrue(plan.should_apply)

    def test_reset_parks_head_in_a_rescue_ref(self):
        calls = []
        with mock.patch.object(update_git, "git",
                               lambda *a, **k: calls.append(a) or ""):
            update_git.rescue_ref()
        update_refs = [c for c in calls if c and c[0] == "update-ref"]
        self.assertTrue(any("HEAD" in c for c in update_refs),
                        f"no HEAD rescue ref recorded: {calls}")


class TestRunInstallerHardening(unittest.TestCase):
    def test_interval_env_is_dropped_so_config_env_wins(self):
        # The agent's baked SMARTBAR_UPDATE_INTERVAL is the PREVIOUS value;
        # letting it through regenerated the timer with the old number.
        seen = {}

        class FakeProc:
            returncode = 0

            def communicate(self, timeout=None):
                return "", ""

        def fake_popen(argv, cwd=None, env=None, **kw):
            seen["env"] = env
            seen["argv"] = argv
            return FakeProc()
        with mock.patch.object(update_runner.subprocess, "Popen", fake_popen), \
             mock.patch.dict(os.environ, {"SMARTBAR_UPDATE_INTERVAL": "999",
                                          "SMARTBAR_UPDATE_CHANNEL": ""}):
            failure = update_runner.run_installer("update_agent",
                                                  channel="release")
        self.assertEqual(failure, "")
        self.assertNotIn("SMARTBAR_UPDATE_INTERVAL", seen["env"])
        self.assertEqual(seen["env"].get("SMARTBAR_UPDATE_CHANNEL"), "release")


class TestCronSpecForLongIntervals(unittest.TestCase):
    def test_a_day_is_once_a_day(self):
        self.assertEqual(update.cron_spec(86400), "17 3 * * *")

    def test_two_days_is_every_second_day(self):
        self.assertEqual(update.cron_spec(2 * 86400), "17 3 */2 * *")

    def test_sub_day_intervals_unchanged(self):
        self.assertEqual(update.cron_spec(21600), "17 */6 * * *")
        self.assertEqual(update.cron_spec(600), "*/10 * * * *")


class TestPollIntervalParsing(unittest.TestCase):
    def test_hosts_share_one_tolerant_parser(self):
        from smartbar.core.tray_controller import TrayController
        for raw, want in (("90.0", 90), ("", 60), ("banana", 60),
                          ("0", 15), ("inf", 60), ("120", 120)):
            with mock.patch.dict(os.environ, {"SMARTBAR_INTERVAL": raw}):
                self.assertEqual(TrayController.poll_interval_from_env(),
                                 want, raw)


class TestDisabledCheckIsHonest(unittest.TestCase):
    def test_check_outcome_disabled_wording(self):
        outcome = update.check_outcome(disabled=True)
        self.assertIn("off", outcome.label.lower())
        self.assertIn("SMARTBAR_UPDATE=off", outcome.body)

    def test_check_now_reports_disabled_not_busy(self):
        with mock.patch.dict(os.environ, {"SMARTBAR_UPDATE": "off"}):
            outcome = update_runner.check_now()
        self.assertIn("off", outcome.label.lower())
        self.assertNotIn("busy", outcome.label.lower())


if __name__ == "__main__":
    unittest.main()
