"""Tests for smartbar.core.update — release selection and safety policy.

These cover the decisions that can destroy work or brick a device, so they
are deliberately exhaustive: nothing here touches a real git repository.
"""
import os
import re
import unittest
from datetime import datetime, timedelta, timezone

from smartbar.core import model, update
from smartbar.core import popover_theme as theme

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
# A real-shaped sha: the short-ref rule only fires on 40 hex characters, so
# the fixtures elsewhere in this file ("sha-remote") deliberately do not match.
FULL_SHA = "da43ea0e53f58b3d4313d35a7255d2b14cd04fae"


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


class TestTheBundleIsBuiltNotJustCheckedOut(Env):
    """An update is checkout THEN install, so being on the target sha only
    proves the first half happened. A dev box that ran `git pull` by hand
    satisfies it without ever rebuilding, which is how a stale app bundle
    came to report itself up to date."""

    def synced(self, **kwargs):
        return update.plan_update(state(branch="main", head="sha-remote"),
                                  channel=update.CHANNEL_MAIN, **kwargs)

    def test_a_checkout_nothing_was_installed_for_is_not_current(self):
        plan = self.synced(applied_ref="sha-older")
        self.assertTrue(plan.should_apply)
        self.assertEqual(plan.target_ref, "sha-remote")

    def test_a_checkout_the_installers_ran_for_is_current(self):
        self.assertEqual(self.synced(applied_ref="sha-remote").action,
                         update.CURRENT)

    def test_an_unrecorded_applied_ref_is_treated_as_built(self):
        # A state file written before appliedRef existed says nothing about
        # the bundle. Reading that silence as "stale" would rebuild every
        # dev box once on upgrade for no reason; the first apply backfills
        # the field and every run after this one is decided on evidence.
        self.assertEqual(self.synced(applied_ref="").action, update.CURRENT)

    def test_the_release_channel_ignores_it(self):
        # There the target is a tag and appliedVersion already answers this,
        # so a stale sha must not drag a pinned device into a rebuild.
        plan = update.plan_update(state(head_tags=["v0.3.0"]),
                                  applied_ref="sha-older")
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

    def test_a_recorded_channel_is_used_before_the_default(self):
        """The regression `fallback` exists for.

        The channel is baked into the update AGENT, deliberately not into
        config.env (device_config.RESERVED), so ONLY the agent's own process
        inherits it. The popover's manual check is a child of the app — a
        different agent, no such variable — so it planned on `release` for a
        device configured `main`, and then wrote that plan into the state
        file both of them read. Same checkout, same minute, opposite answers:
        "0.11.0 available" to the user, "already up to date" to the log.
        """
        self.assertEqual(update.channel(fallback="main"), update.CHANNEL_MAIN)

    def test_an_explicit_variable_still_outranks_a_recorded_channel(self):
        # How --channel and the agent's own plist keep overriding it.
        os.environ["SMARTBAR_UPDATE_CHANNEL"] = "release"
        self.assertEqual(update.channel(fallback="main"),
                         update.CHANNEL_RELEASE)

    def test_a_junk_variable_does_not_veto_the_recorded_channel(self):
        # Unreadable is the same as unset; it must not shove the device back
        # onto the default when the device's real answer is known.
        os.environ["SMARTBAR_UPDATE_CHANNEL"] = "nonsense"
        self.assertEqual(update.channel(fallback="main"), update.CHANNEL_MAIN)

    def test_an_unusable_recorded_channel_still_reaches_the_default(self):
        for junk in ("", None, "   ", "nonsense", "releases"):
            with self.subTest(fallback=junk):
                self.assertEqual(update.channel(fallback=junk),
                                 update.CHANNEL_RELEASE)

    def test_a_recorded_channel_is_normalised_like_the_variable(self):
        # It comes back out of JSON that some older build wrote, so it gets
        # exactly the tolerance the environment variable has always had.
        self.assertEqual(update.channel(fallback=" MAIN "), update.CHANNEL_MAIN)

    def test_what_ui_state_records_is_what_channel_reads_back(self):
        """The two halves of the fix, joined. A round-trip through the state
        file is the whole mechanism: if either side renames the key or
        normalises differently, the manual check silently resumes guessing."""
        payload = update.ui_state(update.plan_update(state()), "0.2.0", NOW,
                                  channel=update.CHANNEL_MAIN)
        self.assertEqual(update.channel(fallback=payload["channel"]),
                         update.CHANNEL_MAIN)

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

    def test_applied_ref_is_recorded(self):
        plan = update.plan_update(state(head_tags=["v0.3.0"]))
        payload = update.ui_state(plan, "0.3.0", NOW, applied="0.3.0",
                                  applied_ref="sha-built")
        self.assertEqual(payload["appliedRef"], "sha-built")

    def test_no_applied_ref_key_when_the_caller_passed_none(self):
        # Same reason as "channel" below: an absent key means "never
        # recorded", which plan_update reads as "assume built". Writing ""
        # would be indistinguishable from it at the read end, so it must
        # stay absent rather than empty.
        payload = update.ui_state(update.plan_update(state()), "0.2.0", NOW)
        self.assertNotIn("appliedRef", payload)

    def test_the_channel_is_left_where_the_next_run_can_find_it(self):
        plan = update.plan_update(state())
        payload = update.ui_state(plan, "0.2.0", NOW,
                                  channel=update.CHANNEL_MAIN)
        self.assertEqual(payload["channel"], update.CHANNEL_MAIN)

    def test_no_channel_key_when_the_caller_did_not_resolve_one(self):
        # An older state file simply has no "channel". Writing "" instead of
        # omitting it would be indistinguishable at the read end from a
        # device that really had recorded something.
        payload = update.ui_state(update.plan_update(state()), "0.2.0", NOW)
        self.assertNotIn("channel", payload)


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

    def test_the_main_channel_target_is_offered_as_a_short_sha(self):
        """channel=main aims at a COMMIT, not a release: ui_state leaves
        pendingVersion empty and fills pendingRef. Reading only the version
        field is how every front-end came to tell a main-channel device it
        was up to date while its own updater had already decided to rebuild
        it — no button, no badge, on that channel at all."""
        payload = update.ui_state(
            update.plan_update(state(branch="main", remote_main=FULL_SHA),
                               channel=update.CHANNEL_MAIN),
            "1.0.1", NOW)
        self.assertEqual(payload["action"], update.UPDATE)
        self.assertEqual(payload["pendingVersion"], "")
        self.assertEqual(payload["pendingRef"], FULL_SHA)
        self.assertEqual(update.pending_version(payload), FULL_SHA[:7])

    def test_the_version_still_wins_when_both_are_present(self):
        self.assertEqual(
            update.pending_version({"pendingVersion": "0.4.0",
                                    "pendingRef": FULL_SHA}), "0.4.0")

    def test_a_tag_is_offered_whole(self):
        # pendingRef holds a TAG on channel=release. The fallback should not
        # reach it there, but if it ever does, "v0.11.0" must not be served
        # as "v0.11." — the one thing worse than not abbreviating.
        self.assertEqual(update.short_ref("v0.11.0"), "v0.11.0")
        self.assertEqual(update.pending_version({"pendingRef": "v0.11.0"}),
                         "v0.11.0")

    def test_nothing_pending_stays_empty(self):
        # ui_state writes pendingRef only when there is something to apply,
        # so "no update" must survive the new fallback untouched.
        self.assertEqual(update.pending_version({"pendingRef": ""}), "")
        self.assertEqual(update.pending_version({"pendingRef": None}), "")
        self.assertEqual(update.pending_version({"pendingRef": 3}), "")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWIFT_UPDATE = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                            "UpdateStatus.swift")
SWIFT_OPTIONS = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                             "AppOptionsMenu.swift")
SWIFT_POPOVER = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                             "PopoverView.swift")
SWIFT_BUILD = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                           "BuildInfo.swift")
INSTALLER = os.path.join(REPO, "install", "macos-swift.sh")
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
        with open(path, encoding="utf-8") as handle:
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


class TestTheMacOffersMainChannelUpdatesToo(unittest.TestCase):
    """The Mac's reader must consult pendingRef, exactly as pending_version
    does above.

    A "stayed fixed" guard rather than a value comparison. The two readers
    are separate implementations of one rule, and the Swift half is the one
    that shipped wrong: it read pendingVersion alone, which channel=main
    never fills, so the upgrade button (PopoverView) and the badged menu-bar
    icon (AISmartbarApp) were both unreachable on that channel. Every colour,
    geometry and wording test passed throughout.
    """

    def source(self, path):
        if not os.path.exists(path):
            self.skipTest(f"{path} not in this checkout")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_the_reader_falls_back_to_the_pending_ref(self):
        self.assertIn('raw["pendingRef"]', self.source(SWIFT_UPDATE))

    def test_the_fallback_is_guarded_against_offering_the_running_build(self):
        # Against the sha the BUNDLE was built from, not the checkout's HEAD:
        # the checkout moves on a fetch, the bundle only on a rebuild, and it
        # is the rebuild being offered.
        self.assertIn("AppBuild.sha", self.source(SWIFT_UPDATE))

    def test_both_sides_abbreviate_to_the_same_width(self):
        match = re.search(r"static let abbrev = (\d+)", self.source(SWIFT_BUILD))
        self.assertIsNotNone(match, "AppBuild.abbrev is gone")
        self.assertEqual(int(match.group(1)), update.REF_ABBREV,
                         "the Mac would name a different commit prefix from "
                         "every other front-end")


class TestTheBundleNamesTheCommitItWasBuiltFrom(unittest.TestCase):
    """About prints the release version, which moves only when release.sh
    cuts a tag — so on channel=main it can sit many commits behind the code
    actually running, and did. The build sha is what tells them apart, and it
    has to survive the trip from the installer, through Info.plist, to the
    label: a rename at any one of the three ends leaves About silently
    printing the version alone again."""

    def source(self, path):
        if not os.path.exists(path):
            self.skipTest(f"{path} not in this checkout")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_the_installer_stamps_the_key_the_app_reads(self):
        match = re.search(r'static let infoKey = "([^"]+)"',
                          self.source(SWIFT_BUILD))
        self.assertIsNotNone(match, "AppBuild.infoKey is gone")
        script = self.source(INSTALLER)
        self.assertIn("<key>%s</key><string>${BUILD_SHA}</string>"
                      % match.group(1), script)
        self.assertIn("rev-parse HEAD", script)

    def test_about_names_the_build_beside_the_version(self):
        self.assertIn("AppBuild.suffix", self.source(SWIFT_OPTIONS))

    def test_an_unknown_sha_degrades_to_the_version_alone(self):
        # Running unbundled (`swift run`) or from a checkout without git are
        # both normal; an empty pair of brackets after the version is not.
        self.assertIn("short.isEmpty", self.source(SWIFT_BUILD))


class TestMacOptionsMenu(unittest.TestCase):
    """Secondary app commands stay in one native, accessible More menu."""

    def source(self, path):
        if not os.path.exists(path):
            self.skipTest(f"{path} not in this checkout")
        with open(path, encoding="utf-8") as handle:
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
        # 38.5pt (was 44, then 28pt, then scaled ~15%, then a further 20%
        # for retina legibility): the 44pt frames padded the whole header
        # row out, leaving dead space between the title and the provider
        # tabs. Still a comfortable macOS pointer target.
        self.assertIn(".frame(width: 38.5, height: 38.5)", options)
        self.assertIn(".menuIndicator(.hidden)", options)
        self.assertIn('.accessibilityLabel("More options")', options)

    def test_popover_has_a_compact_top_inset_and_balanced_outer_margins(self):
        # The horizontal and bottom margins ARE the shared theme's PAD, so
        # they are spelled from it rather than hardcoded — a scale-up of
        # the whole table should not have to come back and edit a test
        # about margin BALANCE. The top inset is deliberately tighter than
        # PAD and has no constant of its own, so it stays a literal.
        popover = self.source(SWIFT_POPOVER)
        pad = f"{theme.PAD:g}"
        self.assertIn(f".padding(.horizontal, {pad})", popover)
        self.assertIn(f".padding(.bottom, {pad})", popover)
        self.assertIn(".padding(.top, 6.5)", popover)
        self.assertNotIn(f".padding({pad})", popover)


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

    def test_an_upgrade_to_the_running_version_is_not_announced(self):
        """"⬆ 0.11.0 available" beside a footer with no button.

        Every front-end refuses to offer an upgrade to the version it is
        already running — applying it restarts the app to arrive exactly
        where it started. So the announcement had nothing behind it: an inert
        status line that reads like an offer, and a notification naming a
        control that was never drawn. The suppression belongs here, with the
        wording, rather than in each UI where it can drift out of step.
        """
        outcome = update.check_outcome(pending="0.11.0", current="0.11.0")
        self.assertFalse(outcome.found)
        self.assertNotIn("available", outcome.label)
        self.assertIn("Up to date", outcome.label)

    def test_a_genuinely_newer_release_is_still_announced(self):
        outcome = update.check_outcome(pending="0.12.0", current="0.11.0")
        self.assertTrue(outcome.found)
        self.assertIn("0.12.0", outcome.label)

    def test_an_unknown_running_version_does_not_suppress_the_offer(self):
        # current="" means "not told", not "running nothing" — a state file
        # from an older build must not silence a real release.
        self.assertTrue(update.check_outcome(pending="0.12.0").found)

    def test_a_blocked_hold_still_wins_once_the_offer_is_suppressed(self):
        # pending == current removes the offer, so the next honest thing to
        # say is why the device is held, not a bare "up to date".
        outcome = update.check_outcome(pending="0.11.0", current="0.11.0",
                                       blocked="2 unpushed commit(s)")
        self.assertFalse(outcome.found)
        self.assertIn("2 unpushed commit(s)", outcome.body)

    def test_the_offer_names_a_control_rather_than_a_surface(self):
        """It used to read "Pick '⬆ Update to X' in the tray menu".

        The Mac app has never had a tray menu — its update control is a
        button in the popover footer — so the one surface that raises this
        notification sent the user hunting for a menu item that does not
        exist. The label all four front-ends really draw is "Update to X".
        """
        outcome = update.check_outcome(pending="0.12.0", current="0.11.0")
        self.assertNotIn("tray menu", outcome.body)
        self.assertIn("Update to 0.12.0", outcome.body)

    def test_every_outcome_is_something_a_menu_row_can_show(self):
        for kwargs in ({}, {"pending": "1.0.0"}, {"blocked": "dirty"},
                       {"failed": True}, {"ran": False}):
            outcome = update.check_outcome(**kwargs)
            for field in (outcome.label, outcome.title, outcome.body):
                self.assertTrue(field.strip(), kwargs)
            self.assertNotIn("\n", outcome.label, kwargs)


class TestTheOfferNamesAControlThatExists(unittest.TestCase):
    """check_outcome tells the user to pick "Update to X". Pin that all five
    surfaces really draw that label.

    The wording lives in one place precisely so it can be trusted by four
    front-ends at once — which only holds while they all still render it. The
    previous text named the tray menu, was true for three of them, and was a
    dead end on the fourth for as long as the Swift app has existed.
    """

    SURFACES = (
        os.path.join(REPO, "smartbar", "macos", "menubar.py"),
        os.path.join(REPO, "smartbar", "linux", "tray.py"),
        os.path.join(REPO, "smartbar", "windows", "tray.py"),
        os.path.join(REPO, "smartbar", "core", "popover_layout.py"),
        SWIFT_POPOVER,
    )

    def test_every_front_end_draws_the_label_the_notification_names(self):
        for path in self.SURFACES:
            with self.subTest(surface=os.path.basename(path)):
                if not os.path.exists(path):
                    self.skipTest(f"{path} not in this checkout")
                with open(path, encoding="utf-8") as handle:
                    self.assertIn("Update to ", handle.read(),
                                  "check_outcome tells the user to pick "
                                  "“Update to X” and this surface no longer "
                                  "renders anything by that name")


class TestTheUpdateButtonCannotSilentlyDoNothing(unittest.TestCase):
    """installUpdate() switches the spinner on BEFORE it launches anything.

    It has two launch arms — kickstart the update agent, or run the updater
    detached from a known repoRoot — and a device with neither fell through
    both, having already committed to "Updating…". Nothing ran, nothing could
    report that nothing ran, and the state only cleared at the 10-minute
    grace. A button that starts nothing has to say so.
    """

    def source(self, path):
        if not os.path.exists(path):
            self.skipTest(f"{path} not in this checkout")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_the_spawn_helper_reports_whether_it_launched(self):
        # Scoped to spawn's own body on purpose: runCheck() also spells
        # `try? process.run()`, and there it is correct — that one returns
        # [:] on failure, so the outcome is not discarded.
        swift = self.source(SWIFT_UPDATE)
        body = re.search(r"static func spawn\(.*?\n    \}", swift, re.DOTALL)
        self.assertIsNotNone(body, "spawn() has been renamed or reshaped")
        self.assertIn("-> Bool", body.group(0),
                      "spawn() no longer reports whether the process started")
        self.assertNotIn("try? process.run()", body.group(0),
                         "spawn() is swallowing whether the process started; "
                         "installUpdate() cannot tell a launch from a no-op")

    def test_the_launch_result_is_actually_branched_on(self):
        self.assertIn("if Self.startUpdater(", self.source(SWIFT_UPDATE),
                      "installUpdate() no longer checks whether anything "
                      "started — a click that launches nothing will pin the "
                      "spinner for the full grace period again")

    def test_the_footer_draws_what_a_failed_launch_records(self):
        # The other half: a flag nothing renders is the same silence with
        # more code behind it. Scoped to the footer's own body — merely
        # appearing somewhere in the file (in showsFooter, say) is not the
        # same as being drawn.
        popover = self.source(SWIFT_POPOVER)
        body = re.search(r"private var footer: some View \{(.*?)\n    \}",
                         popover, re.DOTALL)
        self.assertIsNotNone(body, "the footer has been renamed or reshaped")
        self.assertIn("updates.launchError", body.group(1),
                      "UpdateStatus records a failed launch that the popover "
                      "never shows, so the click looks like a no-op again")

    def test_a_failed_launch_is_not_hidden_behind_the_footer_gate(self):
        popover = self.source(SWIFT_POPOVER)
        gate = re.search(r"private var showsFooter: Bool \{(.*?)\n    \}",
                         popover, re.DOTALL)
        self.assertIsNotNone(gate, "showsFooter has been renamed or reshaped")
        self.assertIn("launchError", gate.group(1),
                      "the footer can record a launch failure and then "
                      "decline to draw itself, which is how the message "
                      "would never be seen")


if __name__ == "__main__":
    unittest.main()
