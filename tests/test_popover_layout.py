"""Tests for smartbar.core.popover_layout — geometry and click targets.

The Linux panel is hand-painted, so nothing catches a misplaced button for
us: no widget toolkit, no layout engine, and no Linux box here to look at.
These tests are that safety net, and they pin the parity claim — the card
structure, bar geometry and wording all mirror PopoverView.swift.
"""
import unittest
from datetime import datetime, timedelta, timezone

from smartbar.core import model, popover_layout as layout
from smartbar.core import popover_theme as t

NOW = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)


def metric(key="5h", pct=40.0, label=None, resets_at="", countdown=""):
    return model.Metric(key=key, label=label or key, short=key, pct=pct,
                        resets_at=resets_at, countdown=countdown)


def account(number=1, email="a@example.com", active=False, metrics=None,
            ok=True, status="ok"):
    return model.Account(number=number, email=email, active=active, ok=ok,
                         status=status,
                         metrics=[metric()] if metrics is None else metrics)


def snap(*accounts):
    return model.Snapshot(accounts=list(accounts))


def labels(built):
    return [s.text for s in built.shapes if isinstance(s, t.Label)]


def boxes(built):
    return [s for s in built.shapes if isinstance(s, t.Box)]


def bars(built):
    """Bar track/fill boxes, picked by height so button boxes can't be
    mistaken for them (indices shift with the ACTIVE chip / switch button)."""
    return [b for b in boxes(built) if abs(b.h - t.BAR_H) < 0.01]


class TestStructure(unittest.TestCase):
    def test_width_is_the_shared_design_width(self):
        built = layout.build(snap(account()), now=NOW)
        self.assertEqual(built.width, t.WIDTH)   # same 330 as the macOS popover

    def test_height_grows_by_exactly_one_card_per_account(self):
        one = layout.build(snap(account()), now=NOW)
        two = layout.build(snap(account(), account(number=2)), now=NOW)
        self.assertAlmostEqual(two.height - one.height,
                               layout.card_height(account()) + t.CARD_GAP)

    def test_card_height_follows_the_row_count(self):
        three = account(metrics=[metric("5h"), metric("7d"),
                                 metric("scoped:Fable", label="Fable")])
        self.assertAlmostEqual(
            layout.card_height(three) - layout.card_height(account()),
            2 * (t.ROW_H + t.ROW_GAP))

    def test_header_and_footer_are_present(self):
        built = layout.build(snap(account()), version="1.2.3", now=NOW)
        self.assertIn("AI smartbar", labels(built))
        self.assertIn("v1.2.3", labels(built))

    def test_nothing_is_drawn_outside_the_panel(self):
        built = layout.build(snap(account(), account(number=2, active=True)),
                             version="1.2.3", pending_version="1.3.0", now=NOW)
        for box in boxes(built):
            self.assertGreaterEqual(round(box.x, 3), 0)
            self.assertLessEqual(round(box.x + box.w, 3), built.width)
            self.assertLessEqual(round(box.y + box.h, 3), built.height)


class TestCardContent(unittest.TestCase):
    def test_active_card_gets_the_chip_and_the_white_outline(self):
        built = layout.build(snap(account(active=True)), now=NOW)
        self.assertIn("ACTIVE", labels(built))
        card = boxes(built)[0]
        self.assertEqual(card.stroke, t.CARD_BORDER_ACTIVE)
        self.assertEqual(card.line_width, 1.5)

    def test_inactive_card_gets_a_switch_button_and_a_faint_outline(self):
        built = layout.build(snap(account(number=4)), now=NOW)
        self.assertIn("Make Active", labels(built))
        self.assertEqual(boxes(built)[0].stroke, t.CARD_BORDER)
        self.assertTrue(any(h.name == "switch:4" for h in built.hits))

    def test_active_card_has_no_switch_target(self):
        built = layout.build(snap(account(number=4, active=True)), now=NOW)
        self.assertFalse(any(h.name.startswith("switch") for h in built.hits))

    def test_metric_rows_render_used_percent_and_countdown(self):
        resets = (NOW + timedelta(hours=2, minutes=5)).isoformat()
        built = layout.build(
            snap(account(metrics=[metric(pct=79.0, resets_at=resets)])), now=NOW)
        self.assertIn("79% · 2h 5m", labels(built))

    def test_countdown_falls_back_to_the_fetch_time_string(self):
        built = layout.build(
            snap(account(metrics=[metric(pct=5.0, resets_at="junk",
                                         countdown="3h 1m")])), now=NOW)
        self.assertIn("5% · 3h 1m", labels(built))

    def test_bar_fill_is_proportional_and_clamped(self):
        for pct, expect_full in ((50.0, False), (100.0, True), (140.0, True)):
            built = layout.build(snap(account(metrics=[metric(pct=pct)])),
                                 now=NOW)
            track, fill = bars(built)
            if expect_full:
                self.assertAlmostEqual(fill.w, track.w)
            else:
                self.assertAlmostEqual(fill.w, track.w * 0.5)

    def test_a_spent_metric_is_painted_purple(self):
        built = layout.build(snap(account(metrics=[metric(pct=100.0)])), now=NOW)
        self.assertEqual(bars(built)[1].fill, t.status_rgba("full"))

    def test_zero_percent_draws_no_fill(self):
        built = layout.build(snap(account(metrics=[metric(pct=0.0)])), now=NOW)
        self.assertEqual(len(bars(built)), 1)   # track only, no fill


class TestDataless(unittest.TestCase):
    def test_dead_credential_explains_itself_and_blocks_switching(self):
        built = layout.build(
            snap(account(metrics=[], ok=False, status="relogin_required")),
            now=NOW)
        self.assertTrue(any("Re-login required" in text for text in labels(built)))
        blocked = [h for h in built.hits if h.name == "switch:1"]
        self.assertEqual(len(blocked), 1)
        self.assertFalse(blocked[0].enabled)
        self.assertIsNone(built.hit(blocked[0].x + 2, blocked[0].y + 2))

    def test_dataless_dot_is_hollow(self):
        built = layout.build(snap(account(metrics=[])), now=NOW)
        dot = next(s for s in built.shapes if isinstance(s, t.Dot))
        self.assertTrue(dot.hollow)

    def test_measured_dot_is_solid(self):
        built = layout.build(snap(account()), now=NOW)
        dot = next(s for s in built.shapes if isinstance(s, t.Dot))
        self.assertFalse(dot.hollow)

    def test_no_accounts_and_unregistered_login_are_distinguished(self):
        self.assertIn(layout.NO_ACCOUNTS, labels(layout.build(snap(), now=NOW)))
        self.assertIn(layout.UNREGISTERED,
                      labels(layout.build(snap(account()), now=NOW)))

    def test_loading_and_error_states(self):
        self.assertIn("Loading usage…", labels(layout.build(None, now=NOW)))
        self.assertIn("cswap exploded",
                      labels(layout.build(None, error="cswap exploded", now=NOW)))


class TestHitTesting(unittest.TestCase):
    def built(self, **kwargs):
        return layout.build(snap(account(number=2), account(number=3, active=True)),
                            version="1.0.0", now=NOW, **kwargs)

    def test_named_targets_exist(self):
        names = {h.name for h in self.built().hits}
        self.assertEqual(names, {"refresh", "quit", "switch:2"})

    def test_update_target_appears_only_when_one_is_pending(self):
        self.assertNotIn("update", {h.name for h in self.built().hits})
        pending = self.built(pending_version="9.9.9")
        self.assertIn("update", {h.name for h in pending.hits})
        self.assertIn("Update to 9.9.9", labels(pending))

    def test_a_click_inside_a_target_resolves_to_it(self):
        built = self.built()
        for hit in built.hits:
            found = built.hit(hit.x + hit.w / 2, hit.y + hit.h / 2)
            self.assertIsNotNone(found, hit.name)
            self.assertEqual(found.name, hit.name)

    def test_a_click_on_empty_space_resolves_to_nothing(self):
        built = self.built()
        self.assertIsNone(built.hit(t.PAD + 2, built.height - 2))

    def test_targets_do_not_overlap(self):
        hits = self.built(pending_version="9.9.9").hits
        for i, first in enumerate(hits):
            for second in hits[i + 1:]:
                apart = (first.x + first.w <= second.x
                         or second.x + second.w <= first.x
                         or first.y + first.h <= second.y
                         or second.y + second.h <= first.y)
                self.assertTrue(apart, f"{first.name} overlaps {second.name}")

    def test_refresh_and_quit_sit_inside_the_header(self):
        for hit in self.built().hits:
            if hit.name in ("refresh", "quit"):
                self.assertLessEqual(hit.x + hit.w, t.WIDTH - t.PAD + 0.01)
                self.assertLess(hit.y, t.PAD + t.HEADER_H)


class TestHeaderAndFooter(unittest.TestCase):
    def test_updated_label_is_local_short_time(self):
        text = layout.updated_label("2026-07-24T18:00:00Z", NOW)
        self.assertTrue(text.startswith("Updated "), text)
        self.assertRegex(text, r"^Updated \d{1,2}:\d{2} (AM|PM)$")

    def test_updated_label_empty_without_a_stamp(self):
        self.assertEqual(layout.updated_label("", NOW), "")
        self.assertEqual(layout.updated_label("not-a-time", NOW), "")

    def test_stale_is_marked(self):
        built = layout.build(snap(account()), fetched_at="2026-07-24T18:00:00Z",
                             stale=True, now=NOW)
        self.assertIn("stale", labels(built))

    def test_blocked_update_is_surfaced_in_the_footer(self):
        built = layout.build(snap(account()), version="1.0.0",
                             blocked_reason="2 unpushed commit(s)", now=NOW)
        self.assertIn("v1.0.0 · update held", labels(built))


if __name__ == "__main__":
    unittest.main()
