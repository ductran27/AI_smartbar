"""Tests for smartbar.core.popover_layout — geometry and click targets.

The Linux panel is hand-painted, so nothing catches a misplaced button for
us: no widget toolkit, no layout engine, and no Linux box here to look at.
These tests are that safety net, and they pin the parity claim — the card
structure, bar geometry and wording all mirror PopoverView.swift.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

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


def rails(built):
    """Active-card leading-rail boxes, picked by fill color — the rail is
    the only box painted RAIL, so this can't be confused with a card box,
    a bar, or a button of the same width."""
    return [b for b in boxes(built) if b.fill == t.DARK.rail]


def chips(built):
    """Plan/device micro-chip boxes, picked by fill+height so a disabled
    Make Active button (also BUTTON_DISABLED-filled, but BUTTON_H tall)
    can't be mistaken for one."""
    return [b for b in boxes(built)
            if b.fill == t.DARK.button_disabled and abs(b.h - t.CHIP_H) < 0.01]


class TestPinOrigin(unittest.TestCase):
    """Where a pinned panel parks. Geometry only — no GTK involved."""

    def test_single_monitor_top_right_inside_the_workarea(self):
        # 1920x1053 usable below a 27px top panel
        self.assertEqual(layout.pin_origin([(0, 27, 1920, 1053)], (330, 181), 12),
                         (1920 - 330 - 12, 27 + 12))

    def test_ignores_a_smaller_primary_dummy_plug(self):
        # The real regression: a 1920x1080 headless dummy stacked at the same
        # origin as the 2560x1440 display the user actually looks at. Anchoring
        # to the dummy would park the panel mid-screen.
        dummy = (0, 27, 1920, 1053)
        real = (0, 0, 2560, 1440)
        x, y = layout.pin_origin([dummy, real], (330, 181), 12)
        self.assertEqual(x, 2560 - 330 - 12)   # right edge of the REAL screen
        self.assertEqual(y, 27 + 12)           # still clears the dummy's panel

    def test_order_does_not_matter(self):
        a, b = (0, 27, 1920, 1053), (0, 0, 2560, 1440)
        self.assertEqual(layout.pin_origin([a, b], (330, 181), 12),
                         layout.pin_origin([b, a], (330, 181), 12))

    def test_offset_monitor_keeps_its_own_x(self):
        left, right = (0, 24, 1280, 1000), (1280, 24, 2560, 1416)
        x, _y = layout.pin_origin([left, right], (330, 181), 12)
        self.assertEqual(x, 1280 + 2560 - 330 - 12)

    def test_no_usable_workarea_returns_none(self):
        self.assertIsNone(layout.pin_origin([], (330, 181), 12))
        self.assertIsNone(layout.pin_origin([(0, 0, 0, 0)], (330, 181), 12))


class TestRestoreOrigin(unittest.TestCase):
    """Whether a user-dragged spot is still honoured. Geometry only.

    The contract: a remembered origin survives exactly while enough of the
    panel's top edge — the grab area — lands inside some current work area;
    everything else falls back to the default corner (pin_origin).
    """
    AREA = (0, 27, 1920, 1053)
    SIZE = (330, 181)

    def test_a_spot_inside_the_workarea_is_kept(self):
        self.assertEqual(layout.restore_origin((600, 400), [self.AREA],
                                               self.SIZE), (600, 400))

    def test_nothing_saved_returns_none(self):
        self.assertIsNone(layout.restore_origin(None, [self.AREA], self.SIZE))

    def test_a_spot_on_a_monitor_that_is_gone_returns_none(self):
        # Saved on a second monitor at x=1920.. that is no longer plugged in.
        self.assertIsNone(layout.restore_origin((2200, 400), [self.AREA],
                                                self.SIZE))

    def test_a_spot_with_its_grab_edge_below_the_screen_returns_none(self):
        # Top edge 10px above the bottom: technically on-screen, unusable.
        y = 27 + 1053 - 10
        self.assertIsNone(layout.restore_origin((600, y), [self.AREA],
                                                self.SIZE))

    def test_a_spot_mostly_off_the_right_edge_returns_none(self):
        # Only 40px of the panel's 330 remain on-screen: not enough to grab.
        self.assertIsNone(layout.restore_origin((1920 - 40, 400), [self.AREA],
                                                self.SIZE))

    def test_a_spot_on_a_second_monitor_is_kept(self):
        second = (1920, 0, 2560, 1440)
        self.assertEqual(layout.restore_origin((3000, 700),
                                               [self.AREA, second],
                                               self.SIZE), (3000, 700))

    def test_floats_are_returned_as_ints(self):
        self.assertEqual(layout.restore_origin((600.7, 400.2), [self.AREA],
                                               self.SIZE), (600, 400))

    def test_garbage_returns_none_rather_than_raising(self):
        self.assertIsNone(layout.restore_origin(("x", "y"), [self.AREA],
                                                self.SIZE))
        self.assertIsNone(layout.restore_origin((600,), [self.AREA],
                                                self.SIZE))
        self.assertIsNone(layout.restore_origin((600, 400), [], self.SIZE))


class TestStructure(unittest.TestCase):
    def test_width_is_the_shared_design_width(self):
        built = layout.build(snap(account()), now=NOW)
        self.assertEqual(built.width, t.WIDTH)   # same 330 as the macOS popover

    def test_height_grows_by_exactly_one_card_per_account(self):
        # Two accounts vs three, not one vs two: both sides of THIS
        # comparison sit above the tab row's own >1-account threshold (see
        # TestProviderTabs), so the delta measured here is purely the
        # per-card growth, not partly the tab row switching on.
        two = layout.build(snap(account(), account(number=2)), now=NOW)
        three = layout.build(
            snap(account(), account(number=2), account(number=3)), now=NOW)
        self.assertAlmostEqual(three.height - two.height,
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
    def test_every_card_gets_the_same_hairline_border(self):
        # The 1.5pt pure-white outline that used to single out the active
        # card is gone — active and inactive cards share one quiet hairline
        # now; the rail (below) is what marks "active" instead.
        active = layout.build(snap(account(active=True)), now=NOW)
        inactive = layout.build(snap(account(number=4)), now=NOW)
        self.assertIn("ACTIVE", labels(active))
        self.assertEqual(boxes(active)[0].stroke, t.DARK.card_border)
        self.assertEqual(boxes(active)[0].line_width, 1.0)
        self.assertEqual(boxes(inactive)[0].stroke, t.DARK.card_border)
        self.assertEqual(boxes(inactive)[0].line_width, 1.0)

    def test_inactive_card_gets_a_switch_button(self):
        built = layout.build(snap(account(number=4)), now=NOW)
        self.assertIn("Make Active", labels(built))
        self.assertTrue(any(h.name == "switch:4" for h in built.hits))

    def test_active_card_has_no_switch_target(self):
        built = layout.build(snap(account(number=4, active=True)), now=NOW)
        self.assertFalse(any(h.name.startswith("switch") for h in built.hits))

    def test_active_card_gets_exactly_one_rail_at_its_left_edge(self):
        built = layout.build(snap(account(active=True)), now=NOW)
        card = boxes(built)[0]
        rail_boxes = rails(built)
        self.assertEqual(len(rail_boxes), 1)
        self.assertAlmostEqual(rail_boxes[0].x, card.x)
        self.assertAlmostEqual(rail_boxes[0].w, t.RAIL_W)
        self.assertAlmostEqual(rail_boxes[0].h, card.h - t.RAIL_INSET * 2)

    def test_non_active_card_gets_no_rail(self):
        built = layout.build(snap(account(number=4)), now=NOW)
        self.assertEqual(rails(built), [])

    def test_only_the_active_card_among_several_gets_a_rail(self):
        built = layout.build(
            snap(account(number=2), account(number=3, active=True)), now=NOW)
        self.assertEqual(len(rails(built)), 1)

    def test_the_device_count_becomes_a_micro_chip(self):
        card = account(email="a@example.com")
        card.devices = 2
        built = layout.build(snap(card), now=NOW)
        self.assertIn("a@example.com", labels(built))
        self.assertIn("(2)", labels(built))
        self.assertEqual(len(chips(built)), 1)

    def test_the_plan_and_device_badges_share_one_chip(self):
        card = account(email="a@example.com")
        card.plan = "20x"
        card.devices = 2
        built = layout.build(snap(card), now=NOW)
        self.assertIn("a@example.com", labels(built))
        self.assertIn("20x (2)", labels(built))
        self.assertEqual(len(chips(built)), 1)

    def test_no_chip_when_there_is_nothing_to_say(self):
        # No plan and no devices: account_badge() is "", and the card must
        # draw no chip at all rather than an empty pill.
        built = layout.build(snap(account()), now=NOW)
        self.assertIn("a@example.com", labels(built))
        self.assertEqual(chips(built), [])

    def test_the_chip_shrinks_the_address_budget(self):
        plain = layout.build(snap(account(number=2)), now=NOW)
        badged = account(number=2)
        badged.plan = "20x"
        with_chip = layout.build(snap(badged), now=NOW)
        plain_address = next(s for s in plain.shapes
                             if isinstance(s, t.Label) and "@" in s.text)
        chipped_address = next(s for s in with_chip.shapes
                               if isinstance(s, t.Label) and "@" in s.text)
        self.assertLess(chipped_address.max_width, plain_address.max_width)

    def test_the_badge_chip_cannot_run_into_the_active_chip(self):
        # The address's reserved width, and the badge chip's position, both
        # have to clear the ACTIVE chip — or a long address / wide badge
        # would be drawn straight through it.
        card = account(email="a" * 60 + "@example.com", active=True)
        card.devices = 3
        built = layout.build(snap(card), now=NOW)
        address = next(s for s in built.shapes
                       if isinstance(s, t.Label) and s.text.startswith("aaa"))
        badge = chips(built)[0]
        active_chip = next(s for s in boxes(built)
                           if s.fill == t.DARK.status_rgba("green"))
        self.assertLessEqual(address.x + address.max_width, badge.x)
        self.assertLessEqual(badge.x + badge.w, active_chip.x)

    def test_metric_rows_render_used_percent_and_countdown(self):
        resets = (NOW + timedelta(hours=2, minutes=5)).isoformat()
        built = layout.build(
            snap(account(metrics=[metric(pct=79.0, resets_at=resets)])), now=NOW)
        # Two separate labels, each right-anchored in its own reserved
        # sub-column — see test_percent_position_is_stable_across_countdown_
        # lengths for why keeping them apart matters.
        self.assertIn("79%", labels(built))
        # No " · " separator: the clock glyph sits in the gap that
        # separator used to occupy, so the string keeps only its lead space.
        self.assertIn(" 2h 5m", labels(built))

    def test_countdown_falls_back_to_the_fetch_time_string(self):
        built = layout.build(
            snap(account(metrics=[metric(pct=5.0, resets_at="junk",
                                         countdown="3h 1m")])), now=NOW)
        self.assertIn("5%", labels(built))
        self.assertIn(" 3h 1m", labels(built))

    def test_a_countdown_gets_a_clock_glyph_immediately_left_of_it(self):
        resets = (NOW + timedelta(hours=2, minutes=5)).isoformat()
        built = layout.build(
            snap(account(metrics=[metric(pct=79.0, resets_at=resets)])),
            now=NOW)
        clocks = [s for s in built.shapes
                 if isinstance(s, t.Glyph) and s.kind == "clock"]
        self.assertEqual(len(clocks), 1)
        countdown_label = next(s for s in built.shapes
                               if isinstance(s, t.Label) and s.text == " 2h 5m")
        text_edge = countdown_label.x - t.text_width(
            countdown_label.text, t.SIZE_ROW_VALUE, mono=True)
        self.assertLess(clocks[0].cx, text_edge)

    def test_a_row_with_no_countdown_draws_no_clock_glyph(self):
        built = layout.build(snap(account(metrics=[metric(pct=50.0)])),
                             now=NOW)
        self.assertFalse(any(isinstance(s, t.Glyph) and s.kind == "clock"
                             for s in built.shapes))

    def test_percent_position_is_stable_across_countdown_lengths(self):
        # FINDING 3: the percentage used to be right-anchored as part of
        # ONE string with the countdown, so it visibly slid sideways every
        # time the countdown's length changed (e.g. "1h 0m" -> "59m"). The
        # two are independently right-anchored in their own reserved
        # sub-columns now, so the percentage's x cannot move however long
        # the countdown runs.
        short = layout.build(
            snap(account(metrics=[metric(pct=79.0, countdown="9m")])),
            now=NOW)
        long_ = layout.build(
            snap(account(metrics=[metric(pct=79.0, countdown="6d 23h")])),
            now=NOW)
        pct_short = next(s for s in short.shapes
                         if isinstance(s, t.Label) and s.text == "79%")
        pct_long = next(s for s in long_.shapes
                        if isinstance(s, t.Label) and s.text == "79%")
        self.assertEqual(pct_short.x, pct_long.x)

    def test_the_bar_spans_the_cards_full_inner_width(self):
        # The end of FINDING 3's arithmetic. The bar used to share its line
        # with the label column and two reserved value sub-columns (VALUE_W
        # alone held 104pt for a value no realistic string needed); it has
        # its own line now, so it runs edge to edge of the card's inner
        # width while the label and value keep their columns above it.
        built = layout.build(snap(account()), now=NOW)
        track, _fill = bars(built)
        inner_l = t.PAD + t.CARD_PAD_H
        inner_r = t.WIDTH - t.PAD - t.CARD_PAD_H
        self.assertAlmostEqual(track.x, inner_l)
        self.assertAlmostEqual(track.w, inner_r - inner_l)

    def test_a_caret_marks_pace_for_a_metric_with_a_known_window(self):
        resets = (NOW + timedelta(hours=1)).isoformat()
        built = layout.build(
            snap(account(metrics=[metric(key="7d", resets_at=resets)])),
            now=NOW)
        # Filtered on width as well as fill: the notch is drawn in the
        # card's own colour (that is what makes it read as a hole rather
        # than a stripe), so `fill == pace` alone also matches every card
        # background on the panel.
        carets = [b for b in boxes(built)
                  if b.fill == t.DARK.pace and b.w == t.PACE_W]
        self.assertEqual(len(carets), 1)
        self.assertAlmostEqual(carets[0].w, t.PACE_W)
        self.assertAlmostEqual(carets[0].h, t.BAR_H)

    def test_no_caret_for_a_window_with_no_stated_length(self):
        # "spend" carries a reset TIME (same as 5h/7d) but no window-size
        # number, so it must draw no caret at all rather than a guessed one
        # — see model.window_seconds's docstring.
        resets = (NOW + timedelta(hours=1)).isoformat()
        built = layout.build(
            snap(account(metrics=[metric(key="spend", resets_at=resets)])),
            now=NOW)
        self.assertFalse(any(b.fill == t.DARK.pace and b.w == t.PACE_W
                             for b in boxes(built)))

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
        self.assertEqual(bars(built)[1].fill, t.DARK.status_rgba("full"))

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
        # The disabled button never resolves; the click falls through to
        # the card's non-action hover region instead of any control.
        self.assertEqual(built.hit(blocked[0].x + 2, blocked[0].y + 2).name,
                         "card:claude:1")

    def test_a_blocked_account_gets_a_warn_glyph_and_the_text_indents_past_it(self):
        built = layout.build(
            snap(account(metrics=[], ok=False, status="relogin_required")),
            now=NOW)
        warn = next(s for s in built.shapes
                   if isinstance(s, t.Glyph) and s.kind == "warn")
        state = next(s for s in built.shapes if isinstance(s, t.Label)
                    and "Re-login required" in s.text)
        self.assertLess(warn.cx, state.x)
        self.assertEqual(warn.color, state.color)

    def test_an_unblocked_dataless_account_gets_no_warn_glyph(self):
        built = layout.build(snap(account(metrics=[])), now=NOW)
        self.assertFalse(any(isinstance(s, t.Glyph) and s.kind == "warn"
                             for s in built.shapes))

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

    def test_a_long_error_message_wraps_instead_of_overflowing(self):
        long_error = "cswap exploded: " + "x" * 80
        built = layout.build(None, error=long_error, now=NOW)
        label = next(s for s in built.shapes
                     if isinstance(s, t.Label) and s.text == long_error)
        self.assertEqual(label.max_lines, 2)

    def test_a_short_error_message_stays_one_line(self):
        built = layout.build(None, error="offline", now=NOW)
        label = next(s for s in built.shapes
                     if isinstance(s, t.Label) and s.text == "offline")
        self.assertEqual(label.max_lines, 1)


class TestFirstRunMessage(unittest.TestCase):
    """FINDING 1: NO_ACCOUNTS/UNREGISTERED need 2 lines, not SwiftUI's
    implicit 1, or popover_draw._fit middle-truncates them into nonsense —
    the first thing a brand-new user sees."""

    def test_no_accounts_message_wraps_to_two_lines(self):
        built = layout.build(snap(), now=NOW)
        label = next(s for s in built.shapes
                     if isinstance(s, t.Label) and s.text == layout.NO_ACCOUNTS)
        self.assertEqual(label.max_lines, 2)

    def test_unregistered_message_fits_on_one_line(self):
        # Shorter than NO_ACCOUNTS and comfortably inside the slot both by
        # the estimate and by real cairo measurement — pinned so a future
        # wording change that makes this ALSO need 2 lines doesn't
        # silently reserve the wrong height (the FINDING 1 trap, in the
        # opposite direction: too MUCH height, an empty gap).
        built = layout.build(snap(account()), now=NOW)
        label = next(s for s in built.shapes
                     if isinstance(s, t.Label) and s.text == layout.UNREGISTERED)
        self.assertEqual(label.max_lines, 1)

    def test_the_reserved_height_grows_to_match_the_second_line(self):
        # Without the matching height growth, the next section down would
        # sit on top of the caption's wrapped second line.
        built = layout.build(snap(), now=NOW)
        expected = (t.PAD + t.HEADER_H + t.SECTION_GAP + t.STATE_ROW_H
                    + t.STATE_LINE_H + t.CARD_GAP + t.SECTION_GAP
                    + t.FOOTER_H + t.PAD)
        self.assertAlmostEqual(built.height, expected)


class TestHitTesting(unittest.TestCase):
    def built(self, **kwargs):
        return layout.build(snap(account(number=2), account(number=3, active=True)),
                            version="1.0.0", now=NOW, **kwargs)

    def test_named_targets_exist(self):
        names = {h.name for h in self.built().hits}
        # card:* regions are hover trackers (they make the remove ✕ appear
        # in the painted UIs), not buttons — clicks on them do nothing.
        # No tab hits: both accounts here are Claude, and the tab row only
        # appears when both providers have accounts (see TestProviderTabs).
        self.assertEqual(names, {"refresh", "quit", "switch:2",
                                 "card:claude:2", "card:claude:3"})

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
        # card:* regions deliberately CONTAIN their card's buttons (they are
        # hover containers, resolved by topmost-wins), so only the real
        # controls are held to the no-overlap rule.
        hits = [h for h in self.built(pending_version="9.9.9").hits
                if not h.name.startswith("card:")]
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


def openai_account(number=1, email="gpt@example.com", active=True,
                   metrics=None, ok=True, status="ok"):
    acct = account(number=number, email=email, active=active,
                   metrics=metrics, ok=ok, status=status)
    acct.provider = "openai"
    return acct


class TestAppearance(unittest.TestCase):
    """The panel follows the system appearance, so `build` takes a Scheme.

    Only colour may depend on it: every hit rect, every card height and the
    overall size have to be identical in both, because a front-end measures
    the panel once and because a review of one appearance is meant to be a
    review of the other's geometry too.
    """

    def _both(self, **kwargs):
        # Both providers, so the tab row is on screen too — it is the one
        # place an accent fill appears, and the one most likely to be
        # hardcoded to a single appearance.
        made = snap(account(active=True))
        made.openai = [openai_account()]
        return (layout.build(made, now=NOW, scheme=t.DARK, **kwargs),
                layout.build(made, now=NOW, scheme=t.LIGHT, **kwargs))

    def test_geometry_is_identical_in_both_appearances(self):
        dark, light = self._both()
        self.assertEqual((dark.width, dark.height),
                         (light.width, light.height))
        self.assertEqual([(h.name, h.x, h.y, h.w, h.h, h.enabled)
                          for h in dark.hits],
                         [(h.name, h.x, h.y, h.w, h.h, h.enabled)
                          for h in light.hits])

    def test_the_same_text_is_drawn_in_both_appearances(self):
        dark, light = self._both()
        self.assertEqual(labels(dark), labels(light))

    def test_the_two_appearances_actually_paint_differently(self):
        """The guard on everything above: if `scheme` were quietly ignored,
        every assertion in this class would pass on two identical layouts."""
        dark, light = self._both()
        self.assertNotEqual([s.color for s in dark.shapes
                             if isinstance(s, t.Label)],
                            [s.color for s in light.shapes
                             if isinstance(s, t.Label)])

    def test_the_layout_carries_its_own_window_ground(self):
        # So a painter cannot be handed a light layout and a dark background.
        dark, light = self._both()
        self.assertEqual(dark.background, t.DARK.window_bg)
        self.assertEqual(light.background, t.LIGHT.window_bg)

    def test_a_scheme_can_be_named_instead_of_passed(self):
        # Front-ends pass through whatever the host calls its appearance.
        by_name = layout.build(snap(account(active=True)), now=NOW,
                               scheme="light")
        self.assertEqual(by_name.background, t.LIGHT.window_bg)

    def test_an_unknown_appearance_falls_back_to_dark(self):
        # An unrecognised name has to land somewhere, and dark is where this
        # panel spent its whole life before light existed.
        for name in ("", "midnight", None):
            with self.subTest(name=name):
                built = layout.build(snap(account(active=True)), now=NOW,
                                     scheme=name)
                self.assertEqual(built.background, t.DARK.window_bg)

    def test_the_default_is_still_dark(self):
        # An un-migrated caller (the Linux and Windows front-ends both call
        # build() with no scheme) must render exactly as it did before the
        # parameter existed.
        built = layout.build(snap(account(active=True)), now=NOW)
        self.assertEqual(built.background, t.DARK.window_bg)


class TestProviderTabs(unittest.TestCase):
    def snap_both(self):
        s = snap(account(active=True))
        s.openai = [openai_account()]
        return s

    def test_claude_only_layout_is_byte_identical_to_before(self):
        built = layout.build(snap(account(active=True)), now=NOW)
        self.assertFalse(any(h.name.startswith("tab:") for h in built.hits))
        self.assertNotIn("OpenAI", labels(built))

    def test_tab_row_appears_only_with_both_providers(self):
        both = layout.build(self.snap_both(), now=NOW)
        names = {h.name for h in both.hits}
        self.assertIn("tab:claude", names)
        self.assertIn("tab:openai", names)
        plain = layout.build(snap(account(active=True)), now=NOW)
        # The row costs its own height plus only the tight TAB_TOP_GAP:
        # it rides the header, it is not a full SECTION_GAP-separated
        # section of its own.
        self.assertAlmostEqual(both.height - plain.height,
                               t.TAB_H + t.TAB_TOP_GAP)

    

    def test_default_selection_is_claude(self):
        built = layout.build(self.snap_both(), now=NOW)
        self.assertIn("a@example.com", labels(built))
        self.assertNotIn("gpt@example.com", labels(built))

    def test_openai_tab_lists_openai_cards_with_no_switch_targets(self):
        built = layout.build(self.snap_both(), provider="openai", now=NOW)
        self.assertIn("gpt@example.com", labels(built))
        self.assertNotIn("a@example.com", labels(built))
        self.assertFalse(any(h.name.startswith("switch") for h in built.hits))
        self.assertIn("ACTIVE", labels(built))   # the live login's chip

    def test_openai_only_machine_gets_no_tab_row_and_no_claude_banner(self):
        # Two OpenAI accounts, zero Claude ones: one provider means one
        # pill, so there is no tab row at all -- and the "sign in to Claude
        # Code" banner belongs to the Claude tab, which is not what this
        # machine auto-resolves to.
        s = snap()   # no Claude accounts at all
        s.openai = [openai_account(),
                    openai_account(number=2, email="old@example.com",
                                   active=False, ok=False,
                                   status="signed_out", metrics=[])]
        built = layout.build(s, now=NOW)   # auto-resolves to the OpenAI tab
        self.assertFalse(any(h.name.startswith("tab:") for h in built.hits))
        self.assertNotIn(layout.NO_ACCOUNTS, labels(built))
        self.assertTrue(any("Signed out" in text for text in labels(built)))

    def test_openai_plan_badge_becomes_a_micro_chip(self):
        card = openai_account()
        card.plan = "Pro Lite"
        s = snap()
        s.openai = [card]
        built = layout.build(s, provider="openai", now=NOW)
        self.assertIn("gpt@example.com", labels(built))
        self.assertIn("Pro Lite", labels(built))
        self.assertEqual(len(chips(built)), 1)

    def test_each_tab_carries_exactly_one_provider_mark(self):
        # The mark sits ABOVE the label, not instead of it: this pins
        # "exactly one", not "at least one", so a stray extra glyph (or a
        # kind mismatch that duplicates one tab's mark on the other) would
        # fail here too.
        built = layout.build(self.snap_both(), now=NOW)
        marks = [s for s in built.shapes
                if isinstance(s, t.Glyph) and s.kind in ("claude", "openai")]
        self.assertEqual({m.kind for m in marks}, {"claude", "openai"})
        self.assertEqual(len(marks), 2)
        claude_label = next(s for s in built.shapes
                            if isinstance(s, t.Label) and s.text == "Claude")
        claude_mark = next(m for m in marks if m.kind == "claude")
        # Side by side, so they share a baseline and the mark sits to the
        # LEFT of its label — never on top of it, and never instead of it.
        self.assertAlmostEqual(claude_mark.cy, claude_label.y)
        self.assertLess(claude_mark.cx, claude_label.x)

    def test_a_single_provider_machine_gets_no_tab_marks_either(self):
        # No tab row at all here (see test_claude_only_layout_is_byte_
        # identical_to_before) — so there is nothing to carry a mark.
        built = layout.build(snap(account(active=True)), now=NOW)
        self.assertFalse(any(isinstance(s, t.Glyph)
                             and s.kind in ("claude", "openai")
                             for s in built.shapes))


class TestRemoveAffordance(unittest.TestCase):
    """The hover ✕ and the in-card confirm — the remove flow's geometry.

    The rules pinned here: the ✕ exists only while the pointer is on a
    non-active card; confirming swaps ONLY the header row (same height, the
    bars stay); the active account is never removable, even if a stale
    confirm token names it. Mirrors AccountCardView's hover/confirm states.
    """

    def openai_snap(self):
        s = snap()
        s.openai = [openai_account(),
                    openai_account(number=2, email="old@x.com", active=False,
                                   ok=False, status="signed_out", metrics=[])]
        return s

    def test_no_remove_target_without_hover(self):
        built = layout.build(snap(account(number=2)), now=NOW)
        self.assertFalse(any(h.name.startswith("remove:")
                             for h in built.hits))
        self.assertFalse(any(isinstance(s, t.Glyph) and s.kind == "close"
                             for s in built.shapes))

    def test_every_card_carries_a_hover_region(self):
        built = layout.build(snap(account(number=2),
                                  account(number=3, active=True)), now=NOW)
        names = {h.name for h in built.hits}
        self.assertIn("card:claude:2", names)
        self.assertIn("card:claude:3", names)

    def test_hovering_a_card_reveals_its_remove_target(self):
        built = layout.build(snap(account(number=2)),
                             hover="card:claude:2", now=NOW)
        self.assertIn("remove:claude:2", {h.name for h in built.hits})
        self.assertTrue(any(isinstance(s, t.Glyph) and s.kind == "close"
                            for s in built.shapes))

    def test_hovering_the_switch_button_keeps_the_cross_visible(self):
        # The pointer is still on the card while it crosses the button.
        built = layout.build(snap(account(number=2)), hover="switch:2",
                             now=NOW)
        self.assertIn("remove:claude:2", {h.name for h in built.hits})

    def test_the_active_card_is_never_removable(self):
        built = layout.build(snap(account(active=True)),
                             hover="card:claude:1", now=NOW)
        self.assertFalse(any(h.name.startswith("remove:")
                             for h in built.hits))

    def test_the_cross_shrinks_the_address_budget_not_the_controls(self):
        hovered = layout.build(snap(account(number=2)),
                               hover="card:claude:2", now=NOW)
        cross = next(h for h in hovered.hits if h.name == "remove:claude:2")
        switch = next(h for h in hovered.hits if h.name == "switch:2")
        address = next(s for s in hovered.shapes
                       if isinstance(s, t.Label) and "@" in s.text)
        self.assertLessEqual(address.x + address.max_width, cross.x)
        self.assertLessEqual(cross.x + cross.w, switch.x)

    def test_confirm_grows_the_card_to_show_the_full_identity(self):
        # FINDING 2: the old header put the question and the Remove/Keep
        # buttons on ONE row, so "Remove <email>?" had to middle-truncate
        # on any realistic address. The new header stacks the (never
        # middle-truncated) question above its own button row instead — a
        # deliberate one-time height change on an explicit user action,
        # not the "same height always" property the old docstring pinned.
        plain = layout.build(snap(account(number=2)), now=NOW)
        confirm = layout.build(snap(account(number=2)), confirm="claude:2",
                               now=NOW)
        self.assertAlmostEqual(confirm.height - plain.height,
                               t.CARD_INNER_GAP + t.BUTTON_H)
        self.assertIn("Remove a@example.com?", labels(confirm))
        names = {h.name for h in confirm.hits}
        self.assertIn("confirm-remove:claude:2", names)
        self.assertIn("cancel-remove", names)
        self.assertNotIn("switch:2", names)
        self.assertNotIn("Make Active", labels(confirm))
        # the metric bars survive the question
        self.assertEqual(len(bars(confirm)), len(bars(plain)))

    def test_the_confirm_question_never_needs_more_than_its_reserved_lines(self):
        # CONSTRAINT from FINDING 2: the full account label — including the
        # plan badge and device count, not just the bare address — must
        # stay readable, un-truncated, for a realistic address. Stack a
        # long one with both badges to stress the reserved line budget.
        card = account(number=2, email="nguyentran4896@gmail.com")
        card.plan = "20x"
        card.devices = 3
        built = layout.build(snap(card), confirm="claude:2", now=NOW)
        label = next(s for s in built.shapes
                     if isinstance(s, t.Label) and s.text.startswith("Remove"))
        self.assertEqual(
            label.text, "Remove nguyentran4896@gmail.com · 20x (3)?")
        needed = layout._lines_for_width(label.text, label.max_width,
                                         size=t.SIZE_EMAIL, bold=True,
                                         cap=t.CONFIRM_MAX_LINES)
        self.assertLessEqual(needed, label.max_lines)

    def test_confirm_remove_button_is_destructive_red(self):
        confirm = layout.build(snap(account(number=2)), confirm="claude:2",
                               now=NOW)
        remove_hit = next(h for h in confirm.hits
                          if h.name == "confirm-remove:claude:2")
        fills = [b.fill for b in boxes(confirm)
                 if abs(b.x - remove_hit.x) < 0.01
                 and abs(b.y - remove_hit.y) < 0.01]
        self.assertIn(t.DARK.danger, fills)

    def test_a_stale_confirm_for_the_active_card_is_ignored(self):
        built = layout.build(snap(account(active=True)), confirm="claude:1",
                             now=NOW)
        self.assertIn("ACTIVE", labels(built))
        self.assertFalse(any(h.name.startswith("confirm-remove")
                             for h in built.hits))

    def test_openai_cards_are_identified_by_email(self):
        built = layout.build(self.openai_snap(),
                             hover="card:openai:old@x.com", now=NOW)
        self.assertIn("remove:openai:old@x.com", {h.name for h in built.hits})

    def test_the_live_codex_login_is_never_removable(self):
        built = layout.build(self.openai_snap(),
                             hover="card:openai:gpt@example.com", now=NOW)
        self.assertFalse(any(h.name.startswith("remove:")
                             for h in built.hits))

    def test_openai_confirm_names_the_right_card(self):
        built = layout.build(self.openai_snap(), confirm="openai:old@x.com",
                             now=NOW)
        self.assertIn("Remove old@x.com?", labels(built))
        self.assertIn("confirm-remove:openai:old@x.com",
                      {h.name for h in built.hits})

    def test_buttons_win_hit_testing_over_their_card_region(self):
        built = layout.build(snap(account(number=2)), now=NOW)
        switch = next(h for h in built.hits if h.name == "switch:2")
        self.assertEqual(built.hit(switch.x + switch.w / 2,
                                   switch.y + switch.h / 2).name, "switch:2")

    def test_hovering_does_not_reflow_the_address(self):
        # FINDING 4: the ✕ gutter used to be reserved only while
        # hovering, so a long address re-wrapped — and could re-truncate —
        # the instant the pointer arrived. It must be reserved
        # unconditionally on any removable card.
        plain = layout.build(snap(account(number=2)), now=NOW)
        hovered = layout.build(snap(account(number=2)),
                               hover="card:claude:2", now=NOW)
        address_plain = next(s for s in plain.shapes
                             if isinstance(s, t.Label) and "@" in s.text)
        address_hovered = next(s for s in hovered.shapes
                               if isinstance(s, t.Label) and "@" in s.text)
        self.assertEqual(address_plain.max_width, address_hovered.max_width)


class TestHeaderAndFooter(unittest.TestCase):
    def test_updated_label_is_local_short_time(self):
        # hour24 pinned explicitly: the auto-detected convention (FINDING
        # 5b) depends on the machine running the test, and this test is
        # about the TIME VALUE, not which convention won.
        text = layout.updated_label("2026-07-24T18:00:00Z", NOW, hour24=False)
        self.assertTrue(text.startswith("Updated "), text)
        self.assertRegex(text, r"^Updated \d{1,2}:\d{2} (AM|PM)$")

    def test_updated_label_honors_an_explicit_24_hour_override(self):
        # FINDING 5b: a user on a 24-hour clock must not always be shown
        # 12-hour time. hour24=True forces the other convention outright,
        # independent of whatever the host machine's locale guesses.
        text = layout.updated_label("2026-07-24T18:00:00Z", NOW, hour24=True)
        self.assertRegex(text, r"^Updated \d{2}:\d{2}$")

    def test_updated_label_empty_without_a_stamp(self):
        self.assertEqual(layout.updated_label("", NOW), "")
        self.assertEqual(layout.updated_label("not-a-time", NOW), "")

    def test_stale_is_marked(self):
        built = layout.build(snap(account()), fetched_at="2026-07-24T18:00:00Z",
                             stale=True, now=NOW)
        self.assertIn("stale", labels(built))

    def test_stale_marker_never_sits_under_the_updated_label(self):
        # FINDING 5a: both offsets are now DERIVED from the same
        # text_width() estimate rather than one hardcoded and one
        # estimated — pin that they stay consistent with each other.
        built = layout.build(snap(account()), fetched_at="2026-07-24T18:00:00Z",
                             stale=True, now=NOW)
        updated = next(s for s in built.shapes if isinstance(s, t.Label)
                       and s.text.startswith("Updated"))
        stale = next(s for s in built.shapes
                     if isinstance(s, t.Label) and s.text == "stale")
        self.assertGreaterEqual(
            stale.x, updated.x + t.text_width(updated.text, t.SIZE_CAPTION))

    def test_blocked_update_is_surfaced_in_the_footer(self):
        built = layout.build(snap(account()), version="1.0.0",
                             blocked_reason="2 unpushed commit(s)", now=NOW)
        self.assertIn("v1.0.0 · update held", labels(built))

    def test_a_blocked_update_prefixes_the_footer_with_a_pause_glyph(self):
        built = layout.build(snap(account()), version="1.0.0",
                             blocked_reason="2 unpushed commit(s)", now=NOW)
        pause = next(s for s in built.shapes
                    if isinstance(s, t.Glyph) and s.kind == "pause")
        label = next(s for s in built.shapes if isinstance(s, t.Label)
                    and s.text == "v1.0.0 · update held")
        self.assertLess(pause.cx, label.x)

    def test_no_blocked_update_means_no_pause_glyph(self):
        built = layout.build(snap(account()), version="1.0.0", now=NOW)
        self.assertFalse(any(isinstance(s, t.Glyph) and s.kind == "pause"
                             for s in built.shapes))


class TestClockConvention(unittest.TestCase):
    """FINDING 5b: auto-detect the locale's clock convention, but let an
    explicit override win, and never crash when detection is impossible."""

    def test_smartbar_clock_env_override_wins_outright(self):
        with mock.patch.dict(os.environ, {"SMARTBAR_CLOCK": "24"}):
            self.assertTrue(layout.prefers_24_hour_clock())
        with mock.patch.dict(os.environ, {"SMARTBAR_CLOCK": "12"}):
            self.assertFalse(layout.prefers_24_hour_clock())

    def test_an_unrecognised_override_falls_through_to_the_guess(self):
        with mock.patch.dict(os.environ, {"SMARTBAR_CLOCK": "banana"}):
            self.assertIn(layout.prefers_24_hour_clock(), (True, False))

    def test_the_guess_never_raises(self):
        # Best-effort by contract: whatever the host's locale looks like,
        # this must return a bool, never throw.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMARTBAR_CLOCK", None)
            self.assertIsInstance(layout.prefers_24_hour_clock(), bool)


class TestActionFeedback(unittest.TestCase):
    """FINDING 6: a switch/remove failure used to vanish silently (tray.py
    logs and refetches; nothing on screen changes). action_error renders a
    dismissible banner; refreshing dims and disables the ⟳ hit so a second
    click cannot queue a second fetch."""

    def test_default_call_has_no_banner_or_busy_state(self):
        # Byte-for-byte: an un-migrated caller (no action_error/refreshing
        # keyword at all) must render exactly as it did before either param
        # existed.
        built = layout.build(snap(account(active=True)), now=NOW)
        self.assertFalse(any(h.name == "dismiss-error" for h in built.hits))
        refresh = next(h for h in built.hits if h.name == "refresh")
        self.assertTrue(refresh.enabled)
        glyph = next(s for s in built.shapes
                    if isinstance(s, t.Glyph) and s.kind == "refresh")
        # Header chrome sits a step back from the cards it frames and
        # brightens to full ink only under the pointer.
        self.assertEqual(glyph.color, t.DARK.text_secondary)

    def test_action_error_renders_as_a_dismissible_banner(self):
        built = layout.build(snap(account(active=True)),
                             action_error="Switch failed: in use", now=NOW)
        self.assertIn("Switch failed: in use", labels(built))
        dismiss = next(h for h in built.hits if h.name == "dismiss-error")
        self.assertTrue(dismiss.enabled)
        self.assertEqual(dismiss.tooltip, "Dismiss")

    def test_action_error_sits_above_the_account_list(self):
        # Mirrors PopoverView.body's actionError banner — under the header/tabs, above
        # whichever provider's cards are showing.
        built = layout.build(snap(account(number=2)),
                             action_error="Remove failed: in use", now=NOW)
        banner = next(s for s in built.shapes if isinstance(s, t.Label)
                     and s.text == "Remove failed: in use")
        card = next(h for h in built.hits if h.name.startswith("card:"))
        self.assertLess(banner.y, card.y)

    def test_a_long_action_error_wraps_and_grows_the_panel(self):
        plain = layout.build(snap(account(active=True)), now=NOW)
        long_error = "Switch failed: " + "connection refused " * 6
        built = layout.build(snap(account(active=True)),
                             action_error=long_error, now=NOW)
        self.assertGreater(built.height, plain.height)

    def test_refreshing_dims_and_disables_only_the_refresh_glyph(self):
        built = layout.build(snap(account(active=True)), refreshing=True,
                             now=NOW)
        refresh = next(h for h in built.hits if h.name == "refresh")
        quit_ = next(h for h in built.hits if h.name == "quit")
        self.assertFalse(refresh.enabled)
        self.assertTrue(quit_.enabled)
        glyphs = {s.kind: s.color for s in built.shapes
                 if isinstance(s, t.Glyph) and s.kind in ("refresh", "quit")}
        self.assertEqual(glyphs["refresh"], t.DARK.text_tertiary)
        self.assertEqual(glyphs["quit"], t.DARK.text_secondary)

    def test_a_click_on_the_disabled_refresh_hit_resolves_to_nothing(self):
        # enabled=False, not just dimmed — a real second click while a
        # fetch is in flight must not resolve to "refresh" at all.
        built = layout.build(snap(account(active=True)), refreshing=True,
                             now=NOW)
        refresh = next(h for h in built.hits if h.name == "refresh")
        found = built.hit(refresh.x + refresh.w / 2, refresh.y + refresh.h / 2)
        self.assertIsNone(found)


class TestTooltips(unittest.TestCase):
    """FINDING 8: every actionable hit, plus the "stale" and "update-held"
    non-action hits, carry a tooltip worded from the matching
    PopoverView.swift / AccountCardView.swift `.help()` string, so both
    platforms explain themselves the same way."""

    def test_refresh_and_quit(self):
        built = layout.build(snap(account(active=True)), now=NOW)
        tips = {h.name: h.tooltip for h in built.hits}
        self.assertEqual(tips["refresh"], "Refresh now")
        self.assertEqual(tips["quit"], "Quit AI smartbar")

    def test_update_button(self):
        built = layout.build(snap(account(active=True)),
                             pending_version="9.9.9", now=NOW)
        update = next(h for h in built.hits if h.name == "update")
        self.assertEqual(update.tooltip,
                         "Fetch, rebuild and restart AI smartbar")

    def test_switch_button_names_the_target_account(self):
        built = layout.build(snap(account(number=2)), now=NOW)
        switch = next(h for h in built.hits if h.name == "switch:2")
        self.assertEqual(switch.tooltip, "Switch Claude Code to a@example.com")

    def test_a_dead_credential_explains_why_switching_is_blocked(self):
        blocked = account(number=2, status="relogin_required")
        built = layout.build(snap(blocked), now=NOW)
        switch = next(h for h in built.hits if h.name == "switch:2")
        self.assertIn("switching would log Claude Code out", switch.tooltip)
        self.assertIn(model.state_text(blocked), switch.tooltip)

    def test_remove_names_the_account(self):
        built = layout.build(snap(account(number=2)), hover="card:claude:2",
                             now=NOW)
        remove = next(h for h in built.hits if h.name == "remove:claude:2")
        self.assertEqual(remove.tooltip,
                         "Remove a@example.com from AI smartbar")

    def test_confirm_remove_explains_what_it_deletes_for_claude(self):
        built = layout.build(snap(account(number=2)), confirm="claude:2",
                             now=NOW)
        confirm = next(h for h in built.hits
                      if h.name == "confirm-remove:claude:2")
        self.assertIn("claude-swap's stored credential backup for slot 2",
                      confirm.tooltip)

    def test_confirm_remove_explains_what_it_deletes_for_openai(self):
        s = snap()
        s.openai = [openai_account(number=2, email="old@x.com", active=False,
                                   ok=False, status="signed_out", metrics=[])]
        built = layout.build(s, provider="openai", confirm="openai:old@x.com",
                             now=NOW)
        confirm = next(h for h in built.hits
                      if h.name == "confirm-remove:openai:old@x.com")
        self.assertIn("Codex brings it back", confirm.tooltip)

    def test_cancel_remove_offers_to_keep_the_account(self):
        built = layout.build(snap(account(number=2)), confirm="claude:2",
                             now=NOW)
        cancel = next(h for h in built.hits if h.name == "cancel-remove")
        self.assertEqual(cancel.tooltip, "Keep this account")

    def test_tab_tooltips_name_the_provider_they_switch_to(self):
        both = snap(account(active=True))
        both.openai = [openai_account()]
        built = layout.build(both, now=NOW)
        tips = {h.name: h.tooltip for h in built.hits
               if h.name.startswith("tab:")}
        self.assertEqual(tips["tab:claude"], "Show Claude accounts")
        self.assertEqual(tips["tab:openai"], "Show OpenAI accounts")

    def test_stale_and_update_held_surface_the_actual_reason(self):
        # FINDING 7: both reasons used to be computed and thrown away.
        built = layout.build(snap(account(active=True)), version="1.0.0",
                             stale=True, stale_reason="cswap timed out",
                             blocked_reason="2 unpushed commit(s)", now=NOW)
        tips = {h.name: h.tooltip for h in built.hits}
        self.assertEqual(tips["stale"], "cswap timed out")
        self.assertEqual(tips["update-held"],
                         "Update held back: 2 unpushed commit(s)")

    def test_stale_falls_back_to_a_generic_reason_without_one(self):
        built = layout.build(snap(account(active=True)), stale=True, now=NOW)
        stale = next(h for h in built.hits if h.name == "stale")
        self.assertEqual(stale.tooltip,
                         "last refresh failed; showing old data")

    def test_no_update_held_hit_when_nothing_is_blocked(self):
        built = layout.build(snap(account(active=True)), version="1.0.0",
                             now=NOW)
        self.assertFalse(any(h.name == "update-held" for h in built.hits))


class TestDisabledControlsStillExplainThemselves(unittest.TestCase):
    """Layout.tooltip_at must answer for DISABLED hits too.

    The blocked "Make Active" button is the single most valuable tooltip in
    the panel: it is the only thing that says why the button will not
    respond. It is also a disabled Hit, and Hit.contains() refuses those,
    so routing tooltips through Layout.hit() authored that string and then
    made it unreachable on both painted front-ends (SwiftUI keeps a .help()
    on its own .disabled() button, so macOS showed it and Linux/Windows
    could not). Pinned here because a front-end reaching for the obvious
    `layout.hit(x, y).tooltip` would silently reintroduce it.
    """

    def _blocked_layout(self):
        return layout.build(snap(account(email="dead@example.com", ok=False,
                                         status="relogin_required",
                                         metrics=[])))

    def test_a_disabled_switch_button_still_has_a_reachable_tooltip(self):
        built = self._blocked_layout()
        switch = [h for h in built.hits if h.name.startswith("switch:")]
        self.assertEqual(len(switch), 1)
        target = switch[0]
        self.assertFalse(target.enabled, "a dead credential stays blocked")
        self.assertTrue(target.tooltip)
        point = (target.x + target.w / 2, target.y + target.h / 2)
        # The bug, precisely: hit() skips the disabled button and lands on
        # the card's own hover region underneath, whose tooltip is "" — so
        # a front-end reading hit().tooltip gets silence, not the reason.
        under = built.hit(*point)
        self.assertEqual(under.name, "card:claude:1")
        self.assertEqual(under.tooltip, "")
        self.assertEqual(built.tooltip_at(*point), target.tooltip)

    def test_the_blocked_tooltip_says_why_rather_than_just_that(self):
        built = self._blocked_layout()
        target = [h for h in built.hits if h.name.startswith("switch:")][0]
        self.assertIn("Stored credential is dead", target.tooltip)

    def test_empty_space_has_no_tooltip(self):
        self.assertEqual(self._blocked_layout().tooltip_at(-5, -5), "")


if __name__ == "__main__":
    unittest.main()
