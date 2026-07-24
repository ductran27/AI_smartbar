"""Tests for smartbar.core.update — release selection and safety policy.

These cover the decisions that can destroy work or brick a device, so they
are deliberately exhaustive: nothing here touches a real git repository.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone

from smartbar.core import model, update

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def state(**kwargs):
    """A clean device sitting on release v0.2.0, overridable per test."""
    base = dict(head="sha-old", branch="", dirty=False, unpushed=0,
                tags=["v0.1.0", "v0.2.0", "v0.3.0"], head_tags=["v0.2.0"],
                remote_main="sha-remote", version="0.2.0")
    base.update(kwargs)
    return update.RepoState(**base)


class Env(unittest.TestCase):
    VARS = ("SMARTBAR_UPDATE", "SMARTBAR_UPDATE_CHANNEL",
            "SMARTBAR_UPDATE_INTERVAL")

    def setUp(self):
        for var in self.VARS:
            os.environ.pop(var, None)

    tearDown = setUp


class TestVersionParsing(unittest.TestCase):
    def test_parses_with_and_without_prefix(self):
        self.assertEqual(update.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(update.parse_version(" 1.2.3 "), (1, 2, 3))

    def test_rejects_junk(self):
        for junk in ("", None, "v1.2", "1.2.3.4", "v1.2.3-rc1", "latest", "vX.Y.Z"):
            self.assertIsNone(update.parse_version(junk), junk)

    def test_newest_tag_is_semver_ordered_not_lexicographic(self):
        # "v0.9.0" > "v0.10.0" lexicographically; semver says otherwise.
        self.assertEqual(update.newest_tag(["v0.9.0", "v0.10.0"]), "v0.10.0")
        self.assertEqual(update.newest_tag(["v1.0.0", "v0.99.99"]), "v1.0.0")

    def test_newest_tag_ignores_non_release_tags(self):
        self.assertEqual(
            update.newest_tag(["nightly", "v0.2.0", "v1.0.0-rc1", "backup"]),
            "v0.2.0")

    def test_newest_tag_without_any_release_tag(self):
        self.assertIsNone(update.newest_tag([]))
        self.assertIsNone(update.newest_tag(["nightly", "wip"]))


class TestReleaseChannel(Env):
    def test_newer_tag_is_applied_detached(self):
        plan = update.plan_update(state())
        self.assertTrue(plan.should_apply)
        self.assertEqual(plan.target_ref, "v0.3.0")
        self.assertEqual(plan.target_version, "0.3.0")
        self.assertTrue(plan.detach)

    def test_already_on_newest_tag_is_current(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"]))
        self.assertEqual(plan.action, update.CURRENT)
        self.assertFalse(plan.should_apply)

    def test_force_reapplies_the_current_target(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"]), force=True)
        self.assertTrue(plan.should_apply)
        self.assertEqual(plan.target_ref, "v0.3.0")

    def test_no_tags_blocks(self):
        plan = update.plan_update(state(tags=["nightly"]))
        self.assertEqual(plan.action, update.BLOCKED)
        self.assertIn("no vX.Y.Z", plan.reason)

    def test_never_downgrades_a_checkout_ahead_of_the_release_line(self):
        # Dev box between a version bump and its tag: 0.4.0 > newest v0.3.0.
        plan = update.plan_update(state(version="0.4.0", head_tags=[]))
        self.assertEqual(plan.action, update.CURRENT)
        self.assertIn("ahead of", plan.reason)

    def test_reset_may_walk_a_newer_checkout_back_to_the_release(self):
        plan = update.plan_update(state(version="0.4.0", head_tags=[]),
                                  reset=True)
        self.assertTrue(plan.should_apply)
        self.assertEqual(plan.target_ref, "v0.3.0")


class TestMainChannel(Env):
    def test_follows_origin_main(self):
        plan = update.plan_update(state(branch="main", head="sha-old"),
                                  channel=update.CHANNEL_MAIN)
        self.assertTrue(plan.should_apply)
        self.assertEqual(plan.target_ref, "sha-remote")
        self.assertFalse(plan.detach)   # fast-forward, never detached

    def test_synced_main_is_current(self):
        plan = update.plan_update(
            state(branch="main", head="sha-remote"),
            channel=update.CHANNEL_MAIN)
        self.assertEqual(plan.action, update.CURRENT)

    def test_detached_or_feature_branch_blocks(self):
        for branch in ("", "feature/x"):
            plan = update.plan_update(state(branch=branch),
                                      channel=update.CHANNEL_MAIN)
            self.assertEqual(plan.action, update.BLOCKED, branch)
            self.assertIn("channel=main", plan.reason)

    def test_unfetched_remote_blocks(self):
        plan = update.plan_update(state(branch="main", remote_main=""),
                                  channel=update.CHANNEL_MAIN)
        self.assertEqual(plan.action, update.BLOCKED)


class TestWorkInProgressIsSafe(Env):
    """The dev checkout must never lose work to a scheduled update."""

    def test_dirty_tree_blocks(self):
        plan = update.plan_update(state(dirty=True))
        self.assertEqual(plan.action, update.BLOCKED)
        self.assertIn("--reset", plan.reason)

    def test_unpushed_commits_block(self):
        plan = update.plan_update(state(branch="main", unpushed=2),
                                  channel=update.CHANNEL_MAIN)
        self.assertEqual(plan.action, update.BLOCKED)
        self.assertIn("unpushed", plan.reason)

    def test_force_alone_does_not_discard_work(self):
        plan = update.plan_update(state(dirty=True), force=True)
        self.assertEqual(plan.action, update.BLOCKED)

    def test_reset_overrides_both_guards(self):
        plan = update.plan_update(state(dirty=True, unpushed=3), reset=True)
        self.assertTrue(plan.should_apply)
        self.assertIn("reset to", plan.reason)

    def test_clean_device_already_current_reports_current_not_blocked(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"], dirty=True))
        self.assertEqual(plan.action, update.CURRENT)


class TestFailureBrake(Env):
    def test_brake_engages_after_max_failures(self):
        plan = update.plan_update(state(), failures=update.MAX_REF_FAILURES)
        self.assertEqual(plan.action, update.BLOCKED)
        self.assertIn("failed", plan.reason)

    def test_force_overrides_the_brake(self):
        plan = update.plan_update(state(), failures=99, force=True)
        self.assertTrue(plan.should_apply)

    def test_below_the_cap_still_retries(self):
        plan = update.plan_update(state(), failures=update.MAX_REF_FAILURES - 1)
        self.assertTrue(plan.should_apply)

    def test_record_and_count_are_day_bucketed(self):
        st = {}
        self.assertEqual(update.record_failure(st, "v0.3.0", NOW), 1)
        self.assertEqual(update.record_failure(st, "v0.3.0", NOW), 2)
        self.assertEqual(update.failure_count(st, "v0.3.0", NOW), 2)
        self.assertEqual(update.failure_count(st, "v0.9.9", NOW), 0)

    def test_a_new_day_clears_yesterdays_poison(self):
        st = {}
        update.record_failure(st, "v0.3.0", NOW)
        update.record_failure(st, "v0.3.0", NOW)
        tomorrow = NOW + timedelta(days=1)
        self.assertEqual(update.failure_count(st, "v0.3.0", tomorrow), 0)
        # and recording tomorrow prunes the stale bucket entirely
        update.record_failure(st, "v0.3.0", tomorrow)
        self.assertEqual(list(st["failures"]), ["2026-07-25"])

    def test_success_clears_the_streak(self):
        st = {}
        update.record_failure(st, "v0.3.0", NOW)
        update.clear_failures(st, "v0.3.0", NOW)
        self.assertEqual(update.failure_count(st, "v0.3.0", NOW), 0)
        update.clear_failures(st, "never-seen", NOW)  # must not raise


class TestEnvironment(Env):
    def test_update_can_be_switched_off(self):
        self.assertTrue(update.enabled())
        os.environ["SMARTBAR_UPDATE"] = "off"
        self.assertFalse(update.enabled())

    def test_channel_defaults_and_validates(self):
        self.assertEqual(update.channel(), update.CHANNEL_RELEASE)
        os.environ["SMARTBAR_UPDATE_CHANNEL"] = "main"
        self.assertEqual(update.channel(), update.CHANNEL_MAIN)
        os.environ["SMARTBAR_UPDATE_CHANNEL"] = "nonsense"
        self.assertEqual(update.channel(), update.CHANNEL_RELEASE)

    def test_interval_has_a_floor(self):
        self.assertEqual(update.check_interval(), update.DEFAULT_CHECK_INTERVAL)
        os.environ["SMARTBAR_UPDATE_INTERVAL"] = "10"
        self.assertEqual(update.check_interval(), 300.0)
        os.environ["SMARTBAR_UPDATE_INTERVAL"] = "junk"
        self.assertEqual(update.check_interval(), update.DEFAULT_CHECK_INTERVAL)


class TestApplyTargets(unittest.TestCase):
    def test_order_puts_the_ui_first_and_the_updater_last(self):
        targets = update.apply_targets(
            {"update_agent": True, "warmup": True, "macos_swift": True})
        self.assertEqual(targets, ["macos_swift", "warmup", "update_agent"])

    def test_absent_shapes_are_skipped(self):
        self.assertEqual(update.apply_targets({"linux": True}), ["linux"])
        self.assertEqual(update.apply_targets({}), [])

    def test_every_key_maps_to_an_installer_script(self):
        for key in update.APPLY_ORDER:
            self.assertIn(key, update.INSTALLERS)


class TestUiState(unittest.TestCase):
    def test_pending_update_is_advertised(self):
        plan = update.plan_update(state())
        payload = update.ui_state(plan, "0.2.0", NOW)
        self.assertEqual(payload["pendingVersion"], "0.3.0")
        self.assertEqual(payload["pendingRef"], "v0.3.0")
        self.assertEqual(payload["currentVersion"], "0.2.0")
        self.assertEqual(payload["checkedAt"], "2026-07-24T12:00:00Z")

    def test_nothing_pending_when_current(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"]))
        payload = update.ui_state(plan, "0.3.0", NOW)
        self.assertEqual(payload["pendingVersion"], "")
        self.assertEqual(payload["action"], update.CURRENT)

    def test_applied_version_is_recorded(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"]))
        payload = update.ui_state(plan, "0.3.0", NOW, applied="0.3.0")
        self.assertEqual(payload["appliedVersion"], "0.3.0")
        self.assertEqual(payload["appliedAt"], payload["checkedAt"])


class TestDotStyle(unittest.TestCase):
    """Gray used to mean both "exhausted" and "no data"."""

    def account(self, metrics, **kwargs):
        return model.Account(number=1, email="a@x.com", metrics=metrics, **kwargs)

    def test_exhausted_is_solid_purple(self):
        spent = [model.Metric(key="scoped:Fable", label="Fable", short="F",
                              pct=100.0)]
        acct = self.account(spent)
        self.assertEqual(model.dot_style(acct), "solid")
        self.assertEqual(model.color(model.worst(acct).pct), "full")

    def test_no_data_is_hollow(self):
        self.assertEqual(model.dot_style(self.account([])), "hollow")

    def test_dead_credential_is_hollow(self):
        acct = self.account([], ok=False, status="relogin_required")
        self.assertEqual(model.dot_style(acct), "hollow")
        self.assertTrue(model.switch_blocked(acct))

    def test_measured_account_is_solid(self):
        acct = self.account([model.Metric(key="5h", label="5h", short="5h",
                                          pct=49.0)])
        self.assertEqual(model.dot_style(acct), "solid")


class TestPendingVersion(unittest.TestCase):
    """What the UIs read to decide whether to badge the icon."""

    def test_reads_the_pending_version(self):
        self.assertEqual(update.pending_version({"pendingVersion": "0.4.0"}),
                         "0.4.0")

    def test_empty_when_current_absent_or_malformed(self):
        for state_dict in ({}, {"pendingVersion": ""}, {"pendingVersion": None},
                           {"pendingVersion": 3}, {"other": "0.4.0"}):
            self.assertEqual(update.pending_version(state_dict), "")

    def test_matches_what_ui_state_writes(self):
        plan = update.plan_update(state())
        self.assertEqual(update.pending_version(update.ui_state(plan, "0.2.0")),
                         "0.3.0")
        current = update.plan_update(state(head_tags=["v0.3.0"]))
        self.assertEqual(
            update.pending_version(update.ui_state(current, "0.3.0")), "")


if __name__ == "__main__":
    unittest.main()
