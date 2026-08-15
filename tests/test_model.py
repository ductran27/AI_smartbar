"""Tests for smartbar.core.model — pure logic, no I/O.

v3 semantics: every user-visible number is "% used" (the /usage scale);
the status ramp (green/yellow/low/critical/full) is judged on usage, and
pills/bars fill as tokens are spent. "gray" is off that ramp: it means
there is no measurement at all.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone

from smartbar.core import model


def metric(key="5h", pct=10.0, **kw):
    defaults = dict(label=key, short=key, resets_at="r1", countdown="1h 2m")
    defaults.update(kw)
    return model.Metric(key=key, pct=pct, **defaults)


def account(number=1, email="a@x.com", active=True, ok=True, status="ok",
            metrics=None):
    return model.Account(number=number, email=email, org="", active=active,
                         ok=ok, status=status,
                         metrics=metrics if metrics is not None else [])


class Env(unittest.TestCase):
    def setUp(self):
        for var in ("SMARTBAR_YELLOW", "SMARTBAR_LOW", "SMARTBAR_RED",
                    "SMARTBAR_TEST_THRESHOLD", "SMARTBAR_PANEL"):
            os.environ.pop(var, None)

    tearDown = setUp


class TestWorst(Env):
    def test_picks_highest_pct(self):
        a = account(metrics=[metric("5h", 24.0), metric("7d", 20.0),
                             metric("scoped:Fable", 28.0, short="F", label="Fable")])
        self.assertEqual(model.worst(a).pct, 28.0)

    def test_none_for_empty_or_missing(self):
        self.assertIsNone(model.worst(account(metrics=[])))
        self.assertIsNone(model.worst(None))


class TestColor(Env):
    def test_five_step_boundaries_on_used(self):
        self.assertEqual(model.color(0.0), "green")
        self.assertEqual(model.color(49.9), "green")
        self.assertEqual(model.color(50.0), "yellow")
        self.assertEqual(model.color(74.9), "yellow")
        self.assertEqual(model.color(75.0), "low")
        self.assertEqual(model.color(89.9), "low")
        self.assertEqual(model.color(90.0), "critical")
        self.assertEqual(model.color(99.9), "critical")
        self.assertEqual(model.color(100.0), "full")   # purple: spent
        self.assertEqual(model.color(105.0), "full")   # over-limit stays full

    def test_env_overrides_are_used_based(self):
        os.environ["SMARTBAR_YELLOW"] = "40"
        os.environ["SMARTBAR_LOW"] = "70"
        os.environ["SMARTBAR_RED"] = "95"
        self.assertEqual(model.color(45), "yellow")    # 45 used >= 40
        self.assertEqual(model.color(72), "low")       # 72 used >= 70
        self.assertEqual(model.color(96), "critical")  # 96 used >= 95
        self.assertEqual(model.color(30), "green")     # 30 used < 40

    def test_test_threshold_sets_all_three(self):
        os.environ["SMARTBAR_TEST_THRESHOLD"] = "10"
        self.assertEqual(model.color(10), "critical")  # 10 used >= 10 (red wins)
        self.assertEqual(model.color(9.9), "green")


class TestPanelPinned(Env):
    def test_popover_is_the_default(self):
        self.assertFalse(model.panel_pinned())

    def test_always_pins_the_panel(self):
        os.environ["SMARTBAR_PANEL"] = "always"
        self.assertTrue(model.panel_pinned())

    def test_case_and_whitespace_tolerated(self):
        for value in ("Always", "ALWAYS", "  always  "):
            os.environ["SMARTBAR_PANEL"] = value
            self.assertTrue(model.panel_pinned(), value)

    def test_anything_else_stays_a_popover(self):
        for value in ("", "popover", "0", "off", "true", "yes"):
            os.environ["SMARTBAR_PANEL"] = value
            self.assertFalse(model.panel_pinned(), value)


class TestBestSwitch(Env):
    def test_lowest_worst_among_others(self):
        snap = model.Snapshot(accounts=[
            account(1, "a@x.com", active=True, metrics=[metric("5h", 95)]),
            account(2, "b@x.com", active=False, metrics=[metric("5h", 62)]),
            account(3, "c@x.com", active=False, metrics=[metric("5h", 34)]),
        ])
        self.assertEqual(model.best_switch(snap).number, 3)

    def test_none_when_alone(self):
        snap = model.Snapshot(accounts=[account(1, active=True, metrics=[metric()])])
        self.assertIsNone(model.best_switch(snap))

    def test_skips_no_data_accounts(self):
        snap = model.Snapshot(accounts=[
            account(1, active=True, metrics=[metric()]),
            account(2, active=False, ok=False, status="unavailable", metrics=[]),
        ])
        self.assertIsNone(model.best_switch(snap))


class TestAccountStates(Env):
    def test_state_text_for_dead_and_odd_statuses(self):
        dead = account(2, active=False, ok=False, status="relogin_required")
        self.assertIn("Re-login required", model.state_text(dead))
        expired = account(1, ok=False, status="token_expired")
        self.assertIn("Token expired", model.state_text(expired))
        unknown = account(3, ok=False, status="weird_future_status")
        self.assertEqual(model.state_text(unknown), "No usage data")

    def test_state_text_ok_paths(self):
        self.assertEqual(model.state_text(account(metrics=[metric()])), "")
        self.assertEqual(model.state_text(account(metrics=[])), "No usage data")

    def test_switch_blocked_only_for_dead_credentials(self):
        self.assertTrue(model.switch_blocked(
            account(ok=False, status="relogin_required")))
        self.assertTrue(model.switch_blocked(
            account(ok=False, status="no_credentials")))
        self.assertFalse(model.switch_blocked(account()))
        self.assertFalse(model.switch_blocked(
            account(ok=False, status="token_expired")))

    def test_best_switch_skips_dead_credentials(self):
        snap = model.Snapshot(accounts=[
            account(1, active=True, metrics=[metric("5h", 95)]),
            account(2, active=False, ok=False, status="relogin_required"),
        ])
        self.assertIsNone(model.best_switch(snap))


class TestNeedsRegistration(Env):
    def test_no_active_slot_needs_registration(self):
        snap = model.Snapshot(accounts=[account(1, active=False, metrics=[metric()])])
        self.assertTrue(model.needs_registration(snap))

    def test_fresh_install_no_accounts_needs_registration(self):
        self.assertTrue(model.needs_registration(model.Snapshot()))

    def test_active_slot_does_not(self):
        snap = model.Snapshot(accounts=[account(1, active=True, metrics=[metric()])])
        self.assertFalse(model.needs_registration(snap))


class TestNeedsRecapture(Env):
    def test_active_dead_backup_needs_recapture(self):
        snap = model.Snapshot(accounts=[
            account(1, active=True, ok=False, status="relogin_required")])
        self.assertTrue(model.needs_recapture(snap))

    def test_healthy_active_does_not(self):
        snap = model.Snapshot(accounts=[account(1, active=True, metrics=[metric()])])
        self.assertFalse(model.needs_recapture(snap))

    def test_inactive_dead_backup_does_not(self):
        # An inactive dead slot can only be healed by signing in as it —
        # `cswap add` captures the LIVE login, so recapture would be a no-op.
        snap = model.Snapshot(accounts=[
            account(1, active=True, metrics=[metric()]),
            account(2, active=False, ok=False, status="relogin_required"),
        ])
        self.assertFalse(model.needs_recapture(snap))

    def test_no_active_account_does_not(self):
        self.assertFalse(model.needs_recapture(model.Snapshot()))


class TestPillStates(Env):
    def test_general_then_scoped_fractions_used(self):
        a = account(metrics=[metric("5h", 29.0), metric("7d", 21.0),
                             metric("scoped:Fable", 95.0, short="F", label="Fable")])
        self.assertEqual(model.pill_states(a),
                         [(0.29, "green"), (0.95, "critical")])

    def test_general_only_single_pill(self):
        a = account(metrics=[metric("5h", 72.0), metric("7d", 21.0)])
        self.assertEqual(model.pill_states(a), [(0.72, "yellow")])

    def test_enterprise_spend_is_an_account_wide_pill(self):
        a = account(metrics=[metric("spend", 14.0, label="Spend", short="$")])
        self.assertEqual(model.pill_states(a), [(0.14, "green")])
        self.assertEqual(model.icon_text(a), "$14")

    def test_two_scoped_models_three_pills(self):
        a = account(metrics=[metric("5h", 10.0),
                             metric("scoped:Fable", 40.0, short="F", label="Fable"),
                             metric("scoped:Opus", 80.0, short="O", label="Opus")])
        self.assertEqual(model.pill_states(a),
                         [(0.1, "green"), (0.4, "green"), (0.8, "low")])

    def test_over_limit_fraction_clamps_at_one(self):
        a = account(metrics=[metric("5h", 130.0)])
        self.assertEqual(model.pill_states(a), [(1.0, "full")])

    def test_empty_for_no_data(self):
        self.assertEqual(model.pill_states(account(metrics=[])), [])
        self.assertEqual(model.pill_states(None), [])


class TestFormatting(Env):
    def setUp(self):
        super().setUp()
        self.acct = account(1, "ios8build@gmail.com", metrics=[
            metric("5h", 24.0), metric("7d", 20.0),
            metric("scoped:Fable", 28.4, short="F", label="Fable"),
        ])

    def test_title_line_shows_used(self):
        self.assertEqual(model.title_line(self.acct),
                         "ios8build@gmail.com — 5h 24% · 7d 20% · F 28% used")

    def test_title_line_no_account(self):
        self.assertEqual(model.title_line(None), "AI smartbar — no active account")

    def test_title_line_dead_backup_names_the_fix(self):
        dead = account(1, "x@y.com", ok=False, status="relogin_required")
        self.assertIn("Re-login required", model.title_line(dead))

    def test_menu_row_active_and_inactive(self):
        self.assertTrue(model.menu_row(self.acct).startswith("● 1 ios8build@gmail.com"))
        other = account(2, "b@x.com", active=False, metrics=[metric("5h", 62)])
        self.assertEqual(model.menu_row(other), "○ 2 b@x.com   5h 62%")

    def test_menu_row_dead_backup(self):
        dead = account(2, "b@x.com", active=False, ok=False,
                       status="relogin_required")
        self.assertIn("Re-login required", model.menu_row(dead))

    def test_icon_text_worst_short_and_used(self):
        self.assertEqual(model.icon_text(self.acct), "F28")
        self.assertEqual(model.icon_text(account(metrics=[])), "?")

    def test_macos_title_two_segments_with_independent_dots(self):
        self.assertEqual(model.macos_title(self.acct), "🟢 5h24 · 🟢 F28")
        mixed = account(metrics=[metric("5h", 24), metric("scoped:Fable", 95, short="F")])
        self.assertEqual(model.macos_title(mixed), "🟢 5h24 · 🔴 F95")

    def test_macos_title_general_only_and_empty(self):
        nearly_spent = account(metrics=[metric("5h", 92)])
        self.assertEqual(model.macos_title(nearly_spent), "🔴 5h92")
        self.assertEqual(model.macos_title(None), "⚪ ?")

    def test_active_account_property(self):
        snap = model.Snapshot(accounts=[account(1, active=False), account(2, active=True)])
        self.assertEqual(snap.active_account.number, 2)


class TestGeneralScopedRows(Env):
    def setUp(self):
        super().setUp()
        self.acct = account(1, metrics=[
            metric("5h", 29.0), metric("7d", 21.0),
            metric("scoped:Fable", 95.0, short="F", label="Fable"),
        ])

    def test_general_worst_ignores_scoped(self):
        self.assertEqual(model.general_worst(self.acct).key, "5h")

    def test_scoped_worst_ignores_general(self):
        self.assertEqual(model.scoped_worst(self.acct).key, "scoped:Fable")

    def test_none_cases(self):
        self.assertIsNone(model.general_worst(None))
        self.assertIsNone(model.scoped_worst(account(metrics=[metric("5h", 10)])))
        self.assertIsNone(model.general_worst(
            account(metrics=[metric("scoped:Fable", 10, short="F")])))

    def test_icon_rows_used_text_with_independent_colors(self):
        self.assertEqual(model.icon_rows(self.acct),
                         [("5h29", "green"), ("F95", "critical")])

    def test_icon_rows_general_only(self):
        a = account(metrics=[metric("5h", 72.0), metric("7d", 21.0)])
        self.assertEqual(model.icon_rows(a), [("5h72", "yellow")])

    def test_icon_rows_no_data(self):
        self.assertEqual(model.icon_rows(account(metrics=[])), [("?", "gray")])
        self.assertEqual(model.icon_rows(None), [("?", "gray")])


class TestWindowSeconds(Env):
    """The pace caret only ever applies to a window whose LENGTH is known —
    a reset TIME alone (every metric has one) isn't enough."""

    def test_claudes_two_buckets(self):
        self.assertEqual(model.window_seconds("5h"), 18000.0)
        self.assertEqual(model.window_seconds("7d"), 604800.0)

    def test_a_codex_window_of_a_length_neither_provider_hardcodes(self):
        # core/codex.py._window_key emits this exact "<n>h"/"<n>d" shape for
        # any window_minutes it is handed, not just 5h/7d — window_seconds
        # has to understand the general shape, not two literals.
        self.assertEqual(model.window_seconds("3d"), 259200.0)
        self.assertEqual(model.window_seconds("2h"), 7200.0)

    def test_spend_and_scoped_have_no_stated_length(self):
        # cswap gives a reset TIME for these but no window-size number —
        # there is no "the Fable bucket is N days wide" anywhere in the
        # payload, so a caret here would have to guess.
        self.assertIsNone(model.window_seconds("spend"))
        self.assertIsNone(model.window_seconds("scoped:Fable"))

    def test_garbage_keys_are_also_none(self):
        self.assertIsNone(model.window_seconds(""))
        self.assertIsNone(model.window_seconds("weekly"))


class TestPaceFraction(Env):
    NOW = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)

    def test_mid_window(self):
        # A 5h (18000s) bucket resetting in 1h: 4h of the 5h have elapsed.
        m = metric("5h", resets_at=(self.NOW + timedelta(hours=1)).isoformat())
        self.assertAlmostEqual(model.pace_fraction(m, self.NOW), 4 / 5)

    def test_just_after_the_window_opened(self):
        m = metric("7d", resets_at=(self.NOW + timedelta(days=7, seconds=-1))
                   .isoformat())
        pace = model.pace_fraction(m, self.NOW)
        self.assertGreaterEqual(pace, 0.0)
        self.assertLess(pace, 0.001)

    def test_just_before_reset(self):
        m = metric("5h", resets_at=(self.NOW + timedelta(seconds=1)).isoformat())
        self.assertGreater(model.pace_fraction(m, self.NOW), 0.999)

    def test_past_reset_is_none_not_clamped_to_one(self):
        # A window that has already ended has nothing left to pace against
        # — the fill/countdown recompute from the live clock, but a caret
        # frozen at "1.0" would be stale in a way those aren't.
        m = metric("5h", resets_at=(self.NOW - timedelta(minutes=1)).isoformat())
        self.assertIsNone(model.pace_fraction(m, self.NOW))

    def test_missing_or_unparseable_resets_at_is_none(self):
        self.assertIsNone(model.pace_fraction(metric("5h", resets_at=""), self.NOW))
        self.assertIsNone(
            model.pace_fraction(metric("5h", resets_at="junk"), self.NOW))

    def test_no_window_length_is_none_even_with_a_good_resets_at(self):
        m = metric("spend", resets_at=(self.NOW + timedelta(hours=1)).isoformat())
        self.assertIsNone(model.pace_fraction(m, self.NOW))
        m = metric("scoped:Fable",
                  resets_at=(self.NOW + timedelta(hours=1)).isoformat())
        self.assertIsNone(model.pace_fraction(m, self.NOW))

    def test_defaults_to_the_real_clock_when_now_is_omitted(self):
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.assertIsNotNone(model.pace_fraction(metric("5h", resets_at=soon)))


class TestPaletteParity(unittest.TestCase):
    """Renderers look colors up BY NAME, so a status missing from either table
    is a runtime crash in a UI we may not be able to run — the cairo Linux
    badge does `COLORS[color_name]` on every poll."""

    def test_every_status_color_has_a_glyph_and_an_rgb(self):
        for pct in (0.0, 49.9, 50.0, 74.9, 75.0, 89.9, 90.0, 99.9, 100.0, 150.0):
            name = model.color(pct)
            self.assertIn(name, model.DOT, f"{pct}% -> {name!r} has no glyph")
            self.assertIn(name, model.RGB, f"{pct}% -> {name!r} has no RGB")

    def test_the_no_data_status_is_covered_too(self):
        # icon_rows/worst fall back to "gray" without ever calling color().
        self.assertIn("gray", model.DOT)
        self.assertIn("gray", model.RGB)

    def test_both_tables_cover_the_same_statuses(self):
        self.assertEqual(set(model.DOT), set(model.RGB))

    def test_rgb_values_are_unit_triples(self):
        for name, rgb in model.RGB.items():
            self.assertEqual(len(rgb), 3, name)
            for channel in rgb:
                self.assertTrue(0.0 <= channel <= 1.0, f"{name}: {rgb}")

    def test_full_is_visually_distinct_from_the_reds_and_from_gray(self):
        for other in ("low", "critical", "gray"):
            self.assertNotEqual(model.RGB["full"], model.RGB[other], other)
            self.assertNotEqual(model.DOT["full"], model.DOT[other], other)


class TestProviderModel(unittest.TestCase):
    def test_provider_defaults_keep_every_existing_constructor_working(self):
        self.assertEqual(account().provider, "claude")
        self.assertEqual(model.Snapshot().openai, [])

    def test_signed_out_accounts_explain_themselves(self):
        acct = account(ok=False, status="signed_out", metrics=[])
        self.assertEqual(model.state_text(acct),
                         "Signed out — usage from its last session")

    def test_openai_list_never_leaks_into_claude_semantics(self):
        snap = model.Snapshot()
        chatgpt = account(active=True)
        chatgpt.provider = "openai"
        snap.openai.append(chatgpt)
        # An active ChatGPT login must not satisfy Claude's active-account
        # logic (registration, recapture, alerts all key off it).
        self.assertIsNone(snap.active_account)
        self.assertTrue(model.needs_registration(snap))


if __name__ == "__main__":
    unittest.main()
