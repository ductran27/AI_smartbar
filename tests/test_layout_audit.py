"""Audit-driven layout/paint pins (2026-08-24, batch B9)."""
from __future__ import annotations

import unittest

from smartbar.core import model, popover_layout, popover_theme, sysmon
from smartbar.core.popover_layout import build

try:
    import cairo
except ImportError:      # pragma: no cover - the README promises skips
    cairo = None


def system_payload(burning_rows=0, burning_field=None, busy_rows=()):
    rows = [{"token": f"tree:{i}:1", "kind": "junk", "name": f"x{i}",
             "sub": "", "meta": "", "burning": True, "cores": 3.0,
             "mem": 1, "age": 100}
            for i in range(burning_rows)]
    left = {"chip": "", "rows": rows[:8], "more": max(0, len(rows) - 8),
            "foot": ""}
    if burning_field is not None:
        left["burning"] = burning_field
    return {"sampledAt": "12:00", "machine": {"caption": "m"},
            "cpu": {"pct": 10, "cores": [10], "caption": "c"},
            "history": {"pct": [None] * 60, "peakText": "peak 0%",
                        "lastPct": 0},
            "mem": {"pct": 50.0, "caption": "1 / 2 GB"},
            "leftovers": left,
            "busy": {"caption": "≥ 50% CPU", "rows": list(busy_rows)},
            "live": False}


def labels(layout):
    return [sh.text for sh in layout.shapes if hasattr(sh, "text")]


def snap(*accounts):
    return model.Snapshot(accounts=list(accounts))


class TestSystemTabCount(unittest.TestCase):
    def test_tab_count_uses_the_payloads_burning_field(self):
        # 9 burning orphans: 8 rows displayed, but the tab must say 9.
        layout = build(snap(model.Account(number=1, email="a@x.com",
                                          active=True, metrics=[])),
                       system=system_payload(9, burning_field=9))
        self.assertIn("System · 9", labels(layout))


class TestBusyCardEmptyText(unittest.TestCase):
    def test_idle_machine_does_not_borrow_the_leftovers_wording(self):
        layout = build(snap(), system=system_payload(), provider="system")
        texts = labels(layout)
        self.assertIn("Nothing busy right now.", texts)
        self.assertEqual(texts.count("Nothing left behind — every orphan "
                                     "is gone."), 1)


class TestSystemTabDoesNotHideTheClaudeState(unittest.TestCase):
    def test_first_run_guidance_shows_above_the_vitals(self):
        layout = build(snap(), system=system_payload())
        self.assertIn(popover_layout.NO_ACCOUNTS, labels(layout))

    def test_a_cswap_error_shows_above_the_vitals(self):
        layout = build(None, error="cswap exploded",
                       system=system_payload())
        self.assertIn("cswap exploded", labels(layout))


class TestPinOriginOnStackedMonitors(unittest.TestCase):
    def test_lower_monitor_does_not_push_the_panel_down(self):
        areas = [(0, 0, 2560, 1440), (2560, 600, 1920, 1080)]
        x, y = popover_layout.pin_origin(areas, (340, 600), 12)
        self.assertEqual(y, 12)                       # margin only
        self.assertEqual(x, 2560 - 340 - 12)

    def test_vertical_stack_uses_the_chosen_monitor_top(self):
        areas = [(0, 0, 2560, 1440), (0, 1440, 1920, 1080)]
        _x, y = popover_layout.pin_origin(areas, (340, 600), 12)
        self.assertEqual(y, 12)

    def test_a_real_menu_bar_strut_still_clears(self):
        # Single monitor whose work area starts under the menu bar.
        areas = [(0, 25, 1920, 1055)]
        _x, y = popover_layout.pin_origin(areas, (340, 600), 12)
        self.assertEqual(y, 25 + 12)


class TestThemeConstantsMatchTheModel(unittest.TestCase):
    def test_caps_are_the_same_numbers(self):
        self.assertEqual(popover_theme.SYS_MAX_CORES, sysmon.MAX_CORE_COLUMNS)
        self.assertEqual(popover_theme.SYS_HISTORY, sysmon.HISTORY_LEN)
        self.assertEqual(popover_theme.PROC_MAX_ROWS, sysmon.PROC_ROWS_CAP)


@unittest.skipIf(cairo is None, "pycairo not installed")
class TestWrapSplitsOverlongTokens(unittest.TestCase):
    def test_a_single_token_wider_than_the_slot_is_split(self):
        from smartbar.paint import popover_draw
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
        ctx = cairo.Context(surface)
        ctx.set_font_size(12)
        token = "/Users/me/.local/share/claude-swap/backups/slot-3.json:"
        lines = popover_draw._wrap(ctx, token + " permission denied",
                                   120.0, 3)
        for line in lines:
            self.assertLessEqual(ctx.text_extents(line).x_advance, 120.0 + 1)


if __name__ == "__main__":
    unittest.main()
