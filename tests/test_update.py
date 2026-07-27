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

    def test_windows_is_present_and_included_when_present(self):
        self.assertEqual(update.apply_targets({"windows": True}), ["windows"])

    def test_windows_sits_with_the_other_ui_shapes_not_the_side_agents(self):
        # D5: Windows is a UI shape, so it goes after the other UI shapes
        # and strictly before the side agents (warmup) and the updater's
        # own agent (update_agent).
        order = update.APPLY_ORDER
        self.assertLess(order.index("linux"), order.index("windows"))
        self.assertLess(order.index("windows"), order.index("warmup"))
        self.assertLess(order.index("windows"), order.index("update_agent"))

    def test_all_five_ui_and_agent_shapes_apply_in_the_documented_order(self):
        targets = update.apply_targets({key: True for key in update.INSTALLERS})
        self.assertEqual(targets, ["macos_swift", "macos_python", "linux",
                                   "windows", "warmup", "update_agent"])


class TestWindowsInstaller(unittest.TestCase):
    """D5: the Windows install shape gets exactly the same treatment as the
    three that already exist — a script path in INSTALLERS, and a slot in
    APPLY_ORDER — so present_installers()/apply_targets() need no special
    case for it anywhere else in this module.
    """

    def test_windows_maps_to_the_powershell_installer(self):
        self.assertEqual(update.INSTALLERS["windows"], "install/windows.ps1")

    def test_windows_is_a_key_in_apply_order(self):
        self.assertIn("windows", update.APPLY_ORDER)


class TestMinutesSpec(unittest.TestCase):
    """minutes_spec() feeds a Task Scheduler repetition trigger, which has
    no sub-minute resolution — the Windows analogue of cron_spec.
    """

    def test_rounds_to_the_nearest_minute_same_as_cron_spec(self):
        self.assertEqual(update.minutes_spec(100), 2)   # 1.667 min -> 2
        self.assertEqual(update.minutes_spec(89), 1)     # 1.483 min -> 1
        self.assertEqual(update.minutes_spec(210), 4)    # 3.5 min -> 4

    def test_a_whole_number_of_minutes_is_unchanged(self):
        self.assertEqual(update.minutes_spec(300), 5)
        self.assertEqual(update.minutes_spec(21600), 360)

    def test_floored_at_one_minute(self):
        self.assertEqual(update.minutes_spec(0), 1)
        self.assertEqual(update.minutes_spec(1), 1)
        self.assertEqual(update.minutes_spec(29), 1)

    def test_matches_the_default_check_interval_the_installers_will_pass(self):
        # check_interval() already floors at MIN_CHECK_INTERVAL (300s), so
        # the 1-minute floor here is defensive, not the normal case — pin
        # the value the installers actually see.
        self.assertEqual(update.minutes_spec(update.MIN_CHECK_INTERVAL), 5)
        self.assertEqual(update.minutes_spec(update.DEFAULT_CHECK_INTERVAL), 360)

    def test_a_falsy_seconds_value_does_not_crash(self):
        # cron_spec treats a falsy seconds the same way ("or 0"); pin the
        # same tolerance here so a bad config.env value degrades to the
        # 1-minute floor instead of raising out of an installer.
        self.assertEqual(update.minutes_spec(0.0), 1)


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


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWIFT_UPDATE = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                            "UpdateStatus.swift")
SWIFT_OPTIONS = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                             "AppOptionsMenu.swift")
SWIFT_POPOVER = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                             "PopoverView.swift")
TRAY = os.path.join(REPO, "smartbar", "linux", "tray.py")


class TestBothUIsShareOneAnswer(unittest.TestCase):
    """Neither UI may decide what a manual check means.

    They call `--check-update --json` and display what comes back. That is not
    tidiness: the honesty rule (exit 0 means "current" OR "another run holds
    the lock", so it may not be rendered as "up to date") is easy to get wrong
    once and impossible to keep right twice. This app has already shipped one
    Mac/Linux divergence of exactly this kind, in the presence staleness window.
    """

    def source(self, path):
        if not os.path.exists(path):
            self.skipTest(f"{path} not in this checkout")
        with open(path) as handle:
            return handle.read()

    def test_both_call_the_shared_entry_point(self):
        for path in (SWIFT_UPDATE, TRAY):
            text = self.source(path)
            self.assertIn("--check-update", text, path)
            self.assertIn("--json", text, path)

    def test_the_mac_does_not_reimplement_the_wording(self):
        # Every phrase check_outcome can produce must be absent from Swift —
        # if one appears, that side has started deciding for itself.
        swift = self.source(SWIFT_UPDATE)
        phrases = set()
        for kwargs in ({}, {"pending": "1.2.3"}, {"blocked": "dirty"},
                       {"failed": True}, {"ran": False}):
            outcome = update.check_outcome(**kwargs)
            phrases.update({outcome.label, outcome.body})
        for phrase in phrases:
            self.assertNotIn(phrase, swift,
                             f"UpdateStatus.swift hardcodes {phrase!r} instead "
                             "of showing what --check-update --json returned")

    def test_the_mac_never_infers_up_to_date_from_an_exit_code(self):
        # The specific mistake this design exists to prevent.
        swift = self.source(SWIFT_UPDATE)
        self.assertNotIn("terminationStatus", swift)


class TestMacOptionsMenu(unittest.TestCase):
    """Secondary app commands stay in one native, accessible More menu."""

    def source(self, path):
        if not os.path.exists(path):
            self.skipTest(f"{path} not in this checkout")
        with open(path) as handle:
            return handle.read()

    def test_header_replaces_the_power_button_with_the_more_menu(self):
        popover = self.source(SWIFT_POPOVER)
        self.assertIn("AppOptionsMenu()", popover)
        self.assertNotIn('Image(systemName: "power")', popover)

    def test_secondary_actions_are_grouped_in_the_menu(self):
        options = self.source(SWIFT_OPTIONS)
        for label in ("Check for Updates", "About AI smartbar",
                      "updates.currentVersion", "Quit AI smartbar"):
            self.assertIn(label, options)
        self.assertIn("Divider()", options)

    def test_version_moved_from_the_footer_to_about(self):
        options = self.source(SWIFT_OPTIONS)
        popover = self.source(SWIFT_POPOVER)
        self.assertIn(
            'Label("About AI smartbar · v\\(updates.currentVersion)"',
            options)
        self.assertNotIn('Text("v\\(updates.currentVersion)")', popover)

    def test_about_panel_credits_the_author_with_a_profile_link(self):
        options = self.source(SWIFT_OPTIONS)
        self.assertIn("Created by Duc Tran", options)
        self.assertIn("https://github.com/ductran27/", options)
        self.assertIn(".credits: credits", options)

    def test_the_icon_only_menu_has_a_name_and_hides_its_indicator(self):
        options = self.source(SWIFT_OPTIONS)
        self.assertIn('Label("More options", systemImage: "ellipsis.circle")',
                      options)
        # 28pt (was 44): user-requested — the 44pt frames padded the whole
        # header row out, leaving dead space between the title and the
        # provider tabs. Still a comfortable macOS pointer target.
        self.assertIn(".frame(width: 28, height: 28)", options)
        self.assertIn(".menuIndicator(.hidden)", options)
        self.assertIn('.accessibilityLabel("More options")', options)

    def test_popover_has_a_compact_top_inset_and_balanced_outer_margins(self):
        popover = self.source(SWIFT_POPOVER)
        self.assertIn(".padding(.horizontal, 11)", popover)
        self.assertIn(".padding(.bottom, 11)", popover)
        self.assertIn(".padding(.top, 5)", popover)
        self.assertNotIn(".padding(11)", popover)


class TestScheduledInterval(unittest.TestCase):
    """What the installers turn into a StartInterval / timer / crontab line."""

    def setUp(self):
        self.saved = os.environ.get("SMARTBAR_UPDATE_INTERVAL")
        os.environ.pop("SMARTBAR_UPDATE_INTERVAL", None)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("SMARTBAR_UPDATE_INTERVAL", None)
        else:
            os.environ["SMARTBAR_UPDATE_INTERVAL"] = self.saved

    def test_config_env_is_used_when_the_environment_is_silent(self):
        # The whole point: config.env survives an update, so a device can keep
        # a cadence it chose. Previously Linux ignored the setting entirely.
        self.assertEqual(update.check_interval(fallback="1800"), 1800.0)

    def test_an_explicit_environment_beats_config_env(self):
        os.environ["SMARTBAR_UPDATE_INTERVAL"] = "7200"
        self.assertEqual(update.check_interval(fallback="1800"), 7200.0)

    def test_the_floor_applies_to_both_sources(self):
        self.assertEqual(update.check_interval(fallback="10"),
                         update.MIN_CHECK_INTERVAL)
        os.environ["SMARTBAR_UPDATE_INTERVAL"] = "10"
        self.assertEqual(update.check_interval(fallback="99999"),
                         update.MIN_CHECK_INTERVAL)

    def test_nonsense_falls_through_rather_than_crashing_an_installer(self):
        os.environ["SMARTBAR_UPDATE_INTERVAL"] = "banana"
        self.assertEqual(update.check_interval(fallback="1800"), 1800.0)
        self.assertEqual(update.check_interval(fallback="also junk"),
                         update.DEFAULT_CHECK_INTERVAL)

    def test_the_default_cron_line_is_exactly_what_linux_used_to_hardcode(self):
        # Guards against the cadence quietly changing for every existing Linux
        # device the moment they take this release.
        self.assertEqual(update.cron_spec(update.DEFAULT_CHECK_INTERVAL),
                         "17 */6 * * *")

    def test_sub_hourly_becomes_a_minute_step(self):
        self.assertEqual(update.cron_spec(1800), "*/30 * * * *")
        self.assertEqual(update.cron_spec(update.MIN_CHECK_INTERVAL),
                         "*/5 * * * *")

    def test_every_spec_is_one_cron_accepts(self):
        # Five fields, and the step never exceeds what its field allows —
        # crontab rejects the whole file otherwise, silently ending updates.
        for seconds in (0, 1, 300, 3600, 21600, 86400, 86400 * 30):
            spec = update.cron_spec(seconds)
            fields = spec.split()
            self.assertEqual(len(fields), 5, spec)
            minute, hour = fields[0], fields[1]
            if minute.startswith("*/"):
                self.assertLessEqual(int(minute[2:]), 59, spec)
            else:
                self.assertLessEqual(int(minute), 59, spec)
            if hour.startswith("*/"):
                self.assertLessEqual(int(hour[2:]), 23, spec)
                self.assertGreaterEqual(int(hour[2:]), 1, spec)


class TestManualCheckOutcome(unittest.TestCase):
    """What the tray says after the user asked, by hand, for a check.

    The tray itself is GTK and untestable here, so the wording and — more
    importantly — the honesty rules live in core where they can be pinned.
    """

    def test_a_release_that_is_waiting_names_itself(self):
        outcome = update.check_outcome(pending="0.6.2")
        self.assertTrue(outcome.found)
        self.assertIn("0.6.2", outcome.label)
        self.assertIn("0.6.2", outcome.body)

    def test_nothing_waiting_says_so_plainly(self):
        outcome = update.check_outcome()
        self.assertFalse(outcome.found)
        self.assertIn("Up to date", outcome.label)

    def test_a_check_that_never_ran_is_not_reported_as_up_to_date(self):
        # The one that matters. run_once() returns 0 BOTH when a device is
        # genuinely current and when another update run holds the lock, so a
        # naive "exit 0 means current" would tell the user an outright lie at
        # exactly the moment the updater was busy doing something.
        outcome = update.check_outcome(ran=False)
        self.assertFalse(outcome.found)
        self.assertNotIn("Up to date", outcome.label)
        self.assertIn("progress", outcome.body)

    def test_a_failed_check_outranks_everything_else(self):
        # If the fetch itself failed, whatever the stale state file says about
        # a pending release is not news we just learned.
        outcome = update.check_outcome(pending="0.6.2", failed=True, ran=False)
        self.assertFalse(outcome.found)
        self.assertIn("failed", outcome.label.lower())
        self.assertIn("update.log", outcome.body)

    def test_held_back_explains_why_rather_than_offering_a_button(self):
        outcome = update.check_outcome(blocked="2 unpushed commit(s)")
        self.assertFalse(outcome.found)          # nothing to click
        self.assertIn("2 unpushed commit(s)", outcome.body)

    def test_every_outcome_is_something_a_menu_row_can_show(self):
        for kwargs in ({}, {"pending": "1.0.0"}, {"blocked": "dirty"},
                       {"failed": True}, {"ran": False}):
            outcome = update.check_outcome(**kwargs)
            for field in (outcome.label, outcome.title, outcome.body):
                self.assertTrue(field.strip(), kwargs)
            self.assertNotIn("\n", outcome.label, kwargs)


if __name__ == "__main__":
    unittest.main()
