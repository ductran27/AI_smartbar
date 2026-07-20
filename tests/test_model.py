"""Tests for smartbar.core.model — pure logic, no I/O."""
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
        for var in ("SMARTBAR_YELLOW", "SMARTBAR_RED", "SMARTBAR_TEST_THRESHOLD"):
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
    def test_thresholds(self):
        self.assertEqual(model.color(69.9), "green")
        self.assertEqual(model.color(70.0), "yellow")
        self.assertEqual(model.color(89.9), "yellow")
        self.assertEqual(model.color(90.0), "red")

    def test_env_overrides(self):
        os.environ["SMARTBAR_YELLOW"] = "50"
        os.environ["SMARTBAR_RED"] = "60"
        self.assertEqual(model.color(55), "yellow")
        self.assertEqual(model.color(60), "red")

    def test_test_threshold_sets_both(self):
        os.environ["SMARTBAR_TEST_THRESHOLD"] = "10"
        self.assertEqual(model.color(10), "red")
        self.assertEqual(model.color(9.9), "green")


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


class TestFormatting(Env):
    def setUp(self):
        super().setUp()
        self.acct = account(1, "ios8build@gmail.com", metrics=[
            metric("5h", 24.0), metric("7d", 20.0),
            metric("scoped:Fable", 28.4, short="F", label="Fable"),
        ])

    def test_title_line(self):
        self.assertEqual(model.title_line(self.acct),
                         "ios8build@gmail.com — 5h 24% · 7d 20% · F 28%")

    def test_title_line_no_account(self):
        self.assertEqual(model.title_line(None), "AI smartbar — no active account")

    def test_menu_row_active_and_inactive(self):
        self.assertTrue(model.menu_row(self.acct).startswith("● 1 ios8build@gmail.com"))
        other = account(2, "b@x.com", active=False, metrics=[metric("5h", 62)])
        self.assertEqual(model.menu_row(other), "○ 2 b@x.com   5h 62%")

    def test_icon_text_worst_short_and_int_pct(self):
        self.assertEqual(model.icon_text(self.acct), "F28")
        self.assertEqual(model.icon_text(account(metrics=[])), "?")

    def test_macos_title_two_segments_with_independent_dots(self):
        self.assertEqual(model.macos_title(self.acct), "🟢 5h24 · 🟢 F28")
        mixed = account(metrics=[metric("5h", 24), metric("scoped:Fable", 95, short="F")])
        self.assertEqual(model.macos_title(mixed), "🟢 5h24 · 🔴 F95")

    def test_macos_title_general_only_and_empty(self):
        red = account(metrics=[metric("5h", 92)])
        self.assertEqual(model.macos_title(red), "🔴 5h92")
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

    def test_icon_rows_two_rows_with_independent_colors(self):
        self.assertEqual(model.icon_rows(self.acct),
                         [("5h29", "green"), ("F95", "red")])

    def test_icon_rows_general_only(self):
        a = account(metrics=[metric("5h", 72.0), metric("7d", 21.0)])
        self.assertEqual(model.icon_rows(a), [("5h72", "yellow")])

    def test_icon_rows_no_data(self):
        self.assertEqual(model.icon_rows(account(metrics=[])), [("?", "gray")])
        self.assertEqual(model.icon_rows(None), [("?", "gray")])


if __name__ == "__main__":
    unittest.main()
