"""Smoke tests for the cairo painters — skipped where pycairo is absent.

The layout is covered by test_popover_layout; what these add is that the
painter survives every state it can be handed. That matters because the
machine this is developed on has no GTK and no tray: without them, a crash
in an unusual branch (no data, an error string, a hovered button) would only
surface on the user's Linux box. Install pycairo to run them here.
"""
import io
import os
import tempfile
import unittest

try:
    import cairo
except ImportError:                                  # pragma: no cover
    cairo = None

from smartbar.core import popover_layout as layout
from smartbar.core import popover_theme as t


def _demo():
    from smartbar.paint.popover_preview import demo_snapshot
    return demo_snapshot()


_SYS_DEMO = {
    "sampledAt": "21:24",
    "machine": {"caption": "16 cores · 64 GB · load 1.0 · 2.0 · 3.0"},
    "cpu": {"pct": 13, "cores": [40, 30, 10, 0, 5, 0],
            "caption": "13 claude · 1141 procs"},
    "history": {"pct": [10, None, 84, 91, 12], "peakText": "peak 91%",
                "lastPct": 12},
    "mem": {"pct": 54.9, "caption": "34.8 / 64 GB · 2.3 GB compressed"},
    "leftovers": {"chip": "1 burning · 5.8 cores", "more": 0,
                  "foot": "Auto-kill off · junk rules: 5",
                  "rows": [{"token": "100:1000", "kind": "junk",
                            "name": "Google Chrome (headless)",
                            "sub": "pid 100 · cdp-prof-9603",
                            "meta": "orphan · 6 h · 580%", "burning": True,
                            "cores": 5.8, "mem": 440, "age": 21600}]},
    "busy": {"caption": "≥ 50% CPU over two samples", "rows": [
        {"token": "400:1", "kind": "session", "name": "claude", "sub": "×12",
         "count": 12, "cpu": 40, "mem": 9000, "meta": "40% · 8.8 GB",
         "killable": False}]},
}


@unittest.skipIf(cairo is None, "pycairo not installed")
class TestFitTruncationMode(unittest.TestCase):
    """popover_draw._fit's two modes, mirroring SwiftUI's two truncation
    behaviours — see Label.mode's comment in popover_theme.py for which
    Text uses which. Both must actually fit the text they return, or a
    "successful" truncation could still overflow its column."""

    def _ctx(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 100)
        ctx = cairo.Context(surface)
        ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                             cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(t.SIZE_ROW_LABEL)
        return ctx

    def test_tail_is_the_default_and_drops_the_end(self):
        # Exactly how many characters survive is a font-metrics question —
        # CI's default sans-serif isn't macOS's, so this pins the BEHAVIOUR
        # (an ellipsis replacing a real suffix, keeping a real prefix)
        # rather than an exact character count. Uses a string long enough to
        # overflow LABEL_W regardless of its current value — "Bengalfox"
        # itself now fits (see test_bengalfox_fits_without_truncation), so
        # it can no longer exercise this path.
        from smartbar.paint import popover_draw
        ctx = self._ctx()
        text = "Supercalifragilisticexpialidocious"
        fitted = popover_draw._fit(ctx, text, t.LABEL_W)
        self.assertTrue(fitted.endswith("…"))
        self.assertTrue(text.startswith(fitted[:-1]))
        self.assertLess(len(fitted), len(text))

    def test_text_that_already_fits_is_returned_unchanged(self):
        from smartbar.paint import popover_draw
        ctx = self._ctx()
        for mode in ("tail", "middle"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    popover_draw._fit(ctx, "7d", t.LABEL_W, mode), "7d")

    def test_bengalfox_fits_without_truncation(self):
        # The real Codex scoped rate-limit name that motivated LABEL_W's
        # widening (see its comment in popover_theme.py) — the row has
        # spare width, so this renders in full rather than as "Beng…".
        from smartbar.paint import popover_draw
        ctx = self._ctx()
        self.assertEqual(popover_draw._fit(ctx, "Bengalfox", t.LABEL_W),
                         "Bengalfox")

    def test_middle_mode_keeps_head_and_tail(self):
        # Same font-metrics caveat as above: assert the SHAPE (a real
        # prefix, an ellipsis, a real suffix) rather than exact characters.
        from smartbar.paint import popover_draw
        ctx = self._ctx()
        text = "Supercalifragilisticexpialidocious"
        fitted = popover_draw._fit(ctx, text, t.LABEL_W, "middle")
        head, sep, tail = fitted.partition("…")
        self.assertEqual(sep, "…")
        self.assertTrue(head, "middle mode should keep a real prefix")
        self.assertTrue(tail, "middle mode should keep a real suffix")
        self.assertTrue(text.startswith(head))
        self.assertTrue(text.endswith(tail))

    def test_every_fitted_result_actually_fits(self):
        from smartbar.paint import popover_draw
        ctx = self._ctx()
        text = "Supercalifragilisticexpialidocious"
        for mode in ("tail", "middle"):
            fitted = popover_draw._fit(ctx, text, t.LABEL_W, mode)
            self.assertLessEqual(ctx.text_extents(fitted).x_advance,
                                 t.LABEL_W)


@unittest.skipIf(cairo is None, "pycairo not installed")
class TestPopoverPainter(unittest.TestCase):
    def render(self, built):
        from smartbar.paint import popover_draw
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "popover.png")
            popover_draw.render_png(built, path, scale=1.0)
            self.assertGreater(os.path.getsize(path), 0)

    def test_every_card_state_in_one_panel(self):
        self.render(layout.build(_demo(), version="1.2.3",
                                 fetched_at="2026-07-24T18:00:00Z"))

    def test_pending_update_and_hovered_controls(self):
        for hover in ("", "update", "refresh", "switch:1"):
            self.render(layout.build(_demo(), version="1.2.3",
                                     pending_version="1.3.0", hover=hover))

    def test_loading_error_and_stale(self):
        self.render(layout.build(None))
        self.render(layout.build(None, error="cswap exploded"))
        self.render(layout.build(_demo(), stale=True,
                                 fetched_at="2026-07-24T18:00:00Z",
                                 blocked_reason="2 unpushed commit(s)"))

    def test_empty_and_unregistered_snapshots(self):
        from smartbar.core import model
        self.render(layout.build(model.Snapshot(accounts=[])))
        demo = _demo()
        for account in demo.accounts:
            account.active = False       # signed in, nothing registered yet
        self.render(layout.build(demo))

    def test_long_email_is_truncated_not_overflowed(self):
        demo = _demo()
        demo.accounts[0].email = "a" * 120 + "@example.com"
        self.render(layout.build(demo, version="1.2.3"))

    def test_remove_confirm_state_renders(self):
        # FINDING 2's two-row header (a possibly-wrapped question stacked
        # above its own Remove/Keep row) is new painted territory — a
        # normal card never draws a Label with max_lines > 1 at
        # SIZE_EMAIL, so this exercises _wrap() at that size for the
        # first time.
        demo = _demo()
        self.render(layout.build(demo, confirm="claude:1"))
        # An OpenAI card's pid suffix is its EMAIL, not its number (see
        # _card(): non-Claude accounts have no stable numeric slot), and
        # its tab must be selected for the card to be drawn at all.
        self.render(layout.build(demo, provider="openai",
                                 confirm="openai:former@openai.example"))

    def test_hovering_a_removable_card_renders(self):
        demo = _demo()
        self.render(layout.build(demo, hover="card:claude:1"))

    def test_action_error_banner_renders(self):
        # FINDING 6: the dismissible error banner is new painted territory
        # — nothing else draws a Label plus a "close" Glyph outside a card.
        demo = _demo()
        self.render(layout.build(demo, action_error="Switch failed: in use"))
        self.render(layout.build(demo, action_error="Switch failed: in use",
                                 hover="dismiss-error"))

    def test_refreshing_renders_the_dimmed_disabled_glyph(self):
        demo = _demo()
        self.render(layout.build(demo, refreshing=True))

    

    def test_stale_and_blocked_footer_render_with_their_reasons(self):
        # FINDING 7: stale_reason/blocked_reason don't change what's drawn
        # (the reason only ever shows via `tooltip`, never painted), but
        # exercise the branches that populate those Hits.
        demo = _demo()
        self.render(layout.build(demo, version="1.2.3", stale=True,
                                 fetched_at="2026-07-24T18:00:00Z",
                                 stale_reason="cswap timed out",
                                 blocked_reason="2 unpushed commit(s)"))


@unittest.skipIf(cairo is None, "pycairo not installed")
class TestGlyphDispatchCoversLayout(unittest.TestCase):
    """The test that matters most in stage 04: every Glyph.kind the layout
    can actually emit has to be a kind popover_draw._draw_glyph handles by
    name. Without this, a forgotten or typo'd kind doesn't fail loudly —
    _draw_glyph's fallback silently paints it as the power icon instead
    (see its own docstring), and nobody notices until a screenshot from
    someone else's Linux box looks wrong.
    """

    def _emitted_kinds(self):
        demo = _demo()
        builds = [
            # The header, both tabs, a hovered card's ✕, a blocked card's
            # warn line, every metric row's clock, and the footer's pause
            # — one build for the states that coexist without contradicting
            # each other (a hover and a hovered card share one build; the
            # dismissible error banner needs its own, since nothing else
            # renders it).
            layout.build(demo, version="1.2.3", pending_version="1.3.0",
                        blocked_reason="2 unpushed commit(s)",
                        hover="card:claude:1"),
            layout.build(demo, action_error="Switch failed: in use",
                        hover="dismiss-error"),
            # The System tab: its "system" tab mark and the kill ✕ on a
            # hovered leftover row.
            layout.build(demo, provider="system", system=_SYS_DEMO,
                        hover="row:100:1000"),
        ]
        kinds = set()
        for built in builds:
            kinds |= {s.kind for s in built.shapes if isinstance(s, t.Glyph)}
        return kinds

    def test_every_emitted_kind_has_a_drawer(self):
        from smartbar.paint import popover_draw
        emitted = self._emitted_kinds()
        self.assertTrue(emitted, "the demo build emitted no glyphs at all — "
                        "this test would pass vacuously")
        missing = emitted - set(popover_draw._GLYPH_DRAWERS)
        self.assertEqual(missing, set(),
                         f"{missing} would silently fall through to the "
                         "power icon instead of failing loudly")

    def test_the_demo_still_exercises_every_stage_04_kind(self):
        # Guards the test above against going quiet for the wrong reason:
        # if the demo snapshot ever stopped covering one of stage 04's new
        # kinds, "nothing is missing" would stop meaning "nothing broke".
        self.assertEqual(
            {"claude", "openai", "warn", "pause", "system"}
            - self._emitted_kinds(), set())


@unittest.skipIf(cairo is None, "pycairo not installed")
class TestTrayBadge(unittest.TestCase):
    def render(self, states, pending=False):
        from smartbar.paint.tray_icon import render_pills
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "icon.png")
            render_pills(states, path, update_pending=pending)
            self.assertGreater(os.path.getsize(path), 0)

    def test_pills_for_each_status(self):
        for name in ("green", "yellow", "low", "critical", "full", "gray"):
            self.render([(0.5, name), (1.0, name)])

    def test_no_data_draws_the_hollow_question_mark(self):
        self.render([])
        self.render([], pending=True)

    def test_update_badge_widens_the_icon(self):
        self.render([(0.3, "green")], pending=True)


@unittest.skipIf(cairo is None, "pycairo not installed")
class TestTrayBadgeScaling(unittest.TestCase):
    """`scale` and the file-like target, both added for the Windows tray.

    AppIndicator takes a PNG by filename and scales it to the panel height
    itself, so this always drew one fixed 96px-tall bitmap. pystray takes a
    PIL.Image at the size it will actually display, and does not resample
    for you — so the badge has to be rasterised at the target height, from
    a buffer rather than a temp file.
    """

    def size(self, states, **kwargs):
        from smartbar.paint.tray_icon import render_pills
        buffer = io.BytesIO()
        render_pills(states, buffer, **kwargs)
        buffer.seek(0)
        surface = cairo.ImageSurface.create_from_png(buffer)
        return surface.get_width(), surface.get_height()

    def test_a_file_object_works_in_place_of_a_path(self):
        from smartbar.paint.tray_icon import render_pills
        buffer = io.BytesIO()
        self.assertIs(render_pills([(0.5, "green")], buffer), buffer)
        self.assertGreater(buffer.tell(), 0)

    def test_a_file_object_and_a_path_produce_the_same_bytes(self):
        from smartbar.paint.tray_icon import render_pills
        states = [(0.42, "green"), (0.87, "critical")]
        buffer = io.BytesIO()
        render_pills(states, buffer)
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "icon.png")
            render_pills(states, path)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), buffer.getvalue())

    def test_the_default_scale_is_the_historical_96px_bitmap(self):
        # Pinned: every existing Linux device renders at this size, and the
        # whole point of defaulting scale=1.0 is that they keep doing so.
        self.assertEqual(self.size([(0.5, "green"), (0.5, "green")])[1], 96)

    def test_scale_gives_the_caller_the_height_it_asked_for(self):
        for height in (16, 20, 24, 32):
            width, got = self.size([(0.5, "green")], scale=height / 96)
            self.assertEqual(got, height)
            self.assertGreater(width, 0)

    def test_nothing_collapses_to_zero_at_the_smallest_tray_size(self):
        # px() floors at 1 on purpose: at 16px a naive round() would give a
        # zero-width pill, which cairo draws as nothing at all rather than
        # failing — an empty tray icon nobody could debug from a screenshot.
        width, height = self.size([], scale=16 / 96, update_pending=True)
        self.assertEqual(height, 16)
        self.assertGreater(width, height)   # the badge widens the frame


if __name__ == "__main__":
    unittest.main()
