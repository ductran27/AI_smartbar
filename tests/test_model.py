"""Tests for smartbar.core.model — pure logic, no I/O.

v2 semantics: every user-visible number is "% left"; the 5-step status
ramp (green/yellow/low/critical/gray) is judged on what's left.
"""
import os
import unittest

from smartbar.core import model


def metric(key="5h", pct=10.0, **kw):
    defaults = dict(label=key, short=key, resets_at="r1", countdown="1h 2m",
                    clock="Jul 20 00:39")
    defaults.update(kw)
    return model.Metric(key=key, pct=pct, **defaults)


def account(number=1, email="a@x.com", active=True, ok=True, metrics=None):
    return model.Account(number=number, email=email, org="", active=active,
                         ok=ok, metrics=metrics if metrics is not None else [])


class Env(unittest.TestCase):
    def setUp(self):
        for var in ("SMARTBAR_YELLOW", "SMARTBAR_LOW", "SMARTBAR_RED",
                    "SMARTBAR_TEST_THRESHOLD"):
            os.environ.pop(var, None)

    tearDown = setUp


class TestLeft(Env):
    def test_left_is_remaining(self):
        self.assertEqual(metric(pct=38.0).left, 62.0)
        self.assertEqual(metric(pct=0.0).left, 100.0)

    def test_left_clamps_at_zero(self):
        self.assertEqual(metric(pct=105.0).left, 0.0)


class TestWorst(Env):
    def test_picks_highest_pct(self):
        a = account(metrics=[metric("5h", 24.0), metric("7d", 20.0),
                             metric("scoped:Fable", 28.0, short="F", label="Fable")])
        self.assertEqual(model.worst(a).pct, 28.0)

    def test_none_for_empty_or_missing(self):
        self.assertIsNone(model.worst(account(metrics=[])))
        self.assertIsNone(model.worst(None))


class TestColor(Env):
    def test_five_step_boundaries_on_left(self):
        self.assertEqual(model.color(49.9), "green")      # 50.1% left
        self.assertEqual(model.color(50.0), "yellow")     # 50% left
        self.assertEqual(model.color(74.9), "yellow")     # 25.1% left
        self.assertEqual(model.color(75.0), "low")        # 25% left
        self.assertEqual(model.color(89.9), "low")        # 10.1% left
        self.assertEqual(model.color(90.0), "critical")   # 10% left
        self.assertEqual(model.color(99.9), "critical")   # 0.1% left
        self.assertEqual(model.color(100.0), "gray")      # empty
        self.assertEqual(model.color(105.0), "gray")      # over-limit clamps

    def test_env_overrides_are_remaining_based(self):
        os.environ["SMARTBAR_YELLOW"] = "60"
        os.environ["SMARTBAR_LOW"] = "30"
        os.environ["SMARTBAR_RED"] = "5"
        self.assertEqual(model.color(45), "yellow")   # 55 left <= 60
        self.assertEqual(model.color(72), "low")      # 28 left <= 30
        self.assertEqual(model.color(96), "critical")  # 4 left <= 5
        self.assertEqual(model.color(30), "green")    # 70 left

    def test_test_threshold_sets_all_three(self):
        os.environ["SMARTBAR_TEST_THRESHOLD"] = "10"
        self.assertEqual(model.color(90), "critical")  # 10 left <= 10
        self.assertEqual(model.color(89.9), "green")   # 10.1 left


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
            account(2, active=False, ok=False, metrics=[]),
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


class TestPillStates(Env):
    def test_general_then_scoped_fractions_left(self):
        a = account(metrics=[metric("5h", 29.0), metric("7d", 21.0),
                             metric("scoped:Fable", 95.0, short="F", label="Fable")])
        self.assertEqual(model.pill_states(a),
                         [(0.71, "green"), (0.05, "critical")])

    def test_general_only_single_pill(self):
        a = account(metrics=[metric("5h", 72.0), metric("7d", 21.0)])
        self.assertEqual(model.pill_states(a), [(0.28, "yellow")])

    def test_two_scoped_models_three_pills(self):
        a = account(metrics=[metric("5h", 10.0),
                             metric("scoped:Fable", 40.0, short="F", label="Fable"),
                             metric("scoped:Opus", 80.0, short="O", label="Opus")])
        self.assertEqual(model.pill_states(a),
                         [(0.9, "green"), (0.6, "green"), (0.2, "low")])

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

    def test_title_line_shows_left(self):
        self.assertEqual(model.title_line(self.acct),
                         "ios8build@gmail.com — 5h 76% · 7d 80% · F 72%")

    def test_title_line_no_account(self):
        self.assertEqual(model.title_line(None), "AI smartbar — no active account")

    def test_menu_row_active_and_inactive(self):
        self.assertTrue(model.menu_row(self.acct).startswith("● 1 ios8build@gmail.com"))
        other = account(2, "b@x.com", active=False, metrics=[metric("5h", 62)])
        self.assertEqual(model.menu_row(other), "○ 2 b@x.com   5h 38%")

    def test_icon_text_worst_short_and_left(self):
        self.assertEqual(model.icon_text(self.acct), "F72")
        self.assertEqual(model.icon_text(account(metrics=[])), "?")

    def test_macos_title_two_segments_with_independent_dots(self):
        self.assertEqual(model.macos_title(self.acct), "🟢 5h76 · 🟢 F72")
        mixed = account(metrics=[metric("5h", 24), metric("scoped:Fable", 95, short="F")])
        self.assertEqual(model.macos_title(mixed), "🟢 5h76 · 🔴 F5")

    def test_macos_title_general_only_and_empty(self):
        nearly_spent = account(metrics=[metric("5h", 92)])
        self.assertEqual(model.macos_title(nearly_spent), "🔴 5h8")
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

    def test_icon_rows_left_text_with_independent_colors(self):
        self.assertEqual(model.icon_rows(self.acct),
                         [("5h71", "green"), ("F5", "critical")])

    def test_icon_rows_general_only(self):
        a = account(metrics=[metric("5h", 72.0), metric("7d", 21.0)])
        self.assertEqual(model.icon_rows(a), [("5h28", "yellow")])

    def test_icon_rows_no_data(self):
        self.assertEqual(model.icon_rows(account(metrics=[])), [("?", "gray")])
        self.assertEqual(model.icon_rows(None), [("?", "gray")])


if __name__ == "__main__":
    unittest.main()
