"""Tests for smartbar/windows/popover_window.py.

None of this touches a real Win32 window, pycairo, or a display -- it
can't, run from macOS. Every ctypes.windll lookup this module makes
(_work_area_at, _monitor_workareas, _update_scale) already degrades to a
documented fallback when ctypes.windll itself does not exist -- true on
this Mac, exactly as it would be if a real call failed on Windows -- so
these tests drive behaviour through that same, already-real fallback path
(winfo_screenwidth/winfo_screenheight, or a monkeypatched
_monitor_workareas) rather than faking ctypes.windll itself.

What's pinned here, and why:

  1. _max_panel_height_px (FINDING 9): the popover path falls back to the
     stubbed screen size when the ctypes monitor lookup is unavailable,
     the pin path picks the ROOMIEST monitor (pin_origin's own tie-break,
     not just the first one found), and a lookup failure of any kind
     degrades to 0 ("do not clamp") rather than raising or guessing.
  2. refresh_layout actually USES that cap: short content is left alone
     and not made scrollable; tall content is clamped to the cap, the
     canvas grows a scrollregion covering the FULL content, and the
     mouse wheel gets bound -- then unbound and reset to the top the
     moment content shrinks back under the cap.
  3. Hit-testing reads coordinates back through canvas.canvasx()/
     canvasy() rather than the raw event.x/event.y -- the fix that keeps
     a click landing on the right layout coordinate while scrolled.
  4. The tooltip dwell timer (FINDING 8's Hit.tooltip, tray study item
     5): hovering a hit with a tooltip schedules a delayed show rather
     than an immediate one, moving off before the dwell fires cancels
     it, a hit with no tooltip text never schedules anything, and the
     tooltip Toplevel is torn down on leave/click/panel-hide.
  5. _position's defensive floor: even if a height cap somehow still
     overflows the work area, the header must never end up placed above
     the work area's top edge (FINDING 9's one hard requirement).

Every GUI-stubbed test snapshots the WHOLE of sys.modules in setUp and
restores it verbatim in tearDown -- see GuiStubbedTestCase's own
docstring in tests/test_windows_tray.py for why restoring only the stub
keys is not enough (popover_draw's own `import cairo` binds whatever
sys.modules["cairo"] holds at ITS first-ever import permanently into its
own module globals).
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from smartbar.core import model
from smartbar.core import popover_layout as layout
from tests.support import stubs


class _FakeWidget:
    """Enough of a tk widget surface for Popover(tk.Toplevel) to run its
    real code against: real geometry/bind/after bookkeeping instead of a
    black hole, so a test can observe what the code under test actually
    DID, not just that it did not crash."""

    _id_counter = 1000

    def __init__(self, master=None, **kwargs):
        self.master = master
        _FakeWidget._id_counter += 1
        self._id = _FakeWidget._id_counter
        self._bindings = {}
        self._geometry_spec = ""
        self._tk_state = "withdrawn"
        self.destroyed = False
        self._after_jobs = {}
        self._next_after_id = 1
        self.pointer = (0, 0)
        self.screen = (1920, 1080)

    def bind(self, sequence, func):
        self._bindings[sequence] = func

    def unbind(self, sequence):
        self._bindings.pop(sequence, None)

    def configure(self, **kwargs):
        pass

    config = configure

    def pack(self, **kwargs):
        pass

    def geometry(self, spec=None):
        if spec is None:
            return self._geometry_spec
        self._geometry_spec = spec
        return None

    def overrideredirect(self, *a):
        pass

    def wm_attributes(self, *a):
        pass

    def resizable(self, *a):
        pass

    def withdraw(self):
        self._tk_state = "withdrawn"

    def deiconify(self):
        self._tk_state = "normal"

    def lift(self):
        pass

    def focus_force(self):
        pass

    def state(self):
        return self._tk_state

    def protocol(self, name, func):
        self._bindings[name] = func

    def destroy(self):
        self.destroyed = True

    def winfo_id(self):
        return self._id

    def winfo_pointerx(self):
        return self.pointer[0]

    def winfo_pointery(self):
        return self.pointer[1]

    def winfo_screenwidth(self):
        return self.screen[0]

    def winfo_screenheight(self):
        return self.screen[1]

    def winfo_width(self):
        return 0

    def winfo_height(self):
        return 0

    # after()/after_cancel(), driven manually by tests via fire_after() --
    # there is no real Tk event loop here to fire them on a timer.
    def after(self, delay, callback, *args):
        job_id = self._next_after_id
        self._next_after_id += 1
        self._after_jobs[job_id] = (delay, callback, args)
        return job_id

    def after_cancel(self, job_id):
        self._after_jobs.pop(job_id, None)

    def fire_after(self, job_id):
        _delay, callback, args = self._after_jobs.pop(job_id)
        callback(*args)


class _FakeCanvas(_FakeWidget):
    """Adds the Canvas-only surface refresh_layout/_on_click/_on_motion
    actually use: canvasx/canvasy (translated by `y_offset`, a stand-in
    for real scroll position) and yview_scroll/yview_moveto."""

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.y_offset = 0
        self.scroll_calls = []

    def create_image(self, *a, **k):
        return 1

    def itemconfig(self, *a, **k):
        pass

    def canvasx(self, x):
        return x

    def canvasy(self, y):
        return y + self.y_offset

    def yview_scroll(self, number, what):
        self.scroll_calls.append((number, what))
        self.y_offset += number * 30   # mirrors yscrollincrement=30

    def yview_moveto(self, fraction):
        if fraction == 0.0:
            self.y_offset = 0


class _FakePhotoImage:
    """Records the source image handed to it, unlike stubs.FakeWidget's
    stand-in in test_windows_tray.py -- this file's tests assert on WHAT
    was drawn, not just that drawing didn't crash."""

    def __init__(self, image):
        self.image = image


def _install_gui_stubs():
    stubs.install_tk(tk_cls=_FakeWidget, canvas_cls=_FakeCanvas,
                     label_cls=_FakeWidget)
    stubs.install_pil(photoimage_cls=_FakePhotoImage)
    stubs.install_cairo(extra={
        "FONT_WEIGHT_NORMAL": 0, "LINE_CAP_ROUND": 0, "OPERATOR_SOURCE": 0})


class GuiStubbedTestCase(stubs.GuiStubbedTestCase):
    """See tests/support/stubs.py's own class of the same name for why the
    WHOLE of sys.modules is snapshotted/restored, not just the stub keys:
    popover_draw's `import cairo` binds whatever sys.modules["cairo"] holds
    at its first-ever import permanently into its own globals."""

    def setUp(self):
        super().setUp()
        _install_gui_stubs()


_reimport = stubs.reimport


def _layout(theme_mod, width=330.0, height=400.0, hits=None):
    return theme_mod.Layout(width=width, height=height, shapes=[],
                            hits=list(hits or []))


def _rebuild(theme_mod, width=330.0, height=400.0, hits=None):
    def rebuild(_hover):
        return _layout(theme_mod, width, height, hits)
    return rebuild


class TestImportsCleanly(GuiStubbedTestCase):
    def test_popover_subclasses_the_stubbed_toplevel(self):
        mod = _reimport("smartbar.windows.popover_window")
        self.assertIn(sys.modules["tkinter"].Toplevel, mod.Popover.__mro__)


class TestMaxPanelHeightPx(GuiStubbedTestCase):
    """FINDING 9's own height budget: how much room a panel is allowed to
    take before the rest must scroll instead of walking off-screen."""

    def test_falls_back_to_screen_size_for_a_popover(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover = mod.Popover(lambda hover: None, lambda name: None)
        popover.pointer = (500, 300)
        popover.screen = (1920, 1080)
        # Force BOTH native lookup paths to fail. Relying on ctypes.windll
        # being absent only exercised this fallback on non-Windows hosts, and
        # made the assertion depend on a real Actions runner's monitor size.
        user32 = types.SimpleNamespace(
            MonitorFromPoint=mock.Mock(side_effect=OSError("forced failure")),
            SystemParametersInfoW=mock.Mock(return_value=0),
        )
        with mock.patch.object(
                mod.ctypes, "windll",
                types.SimpleNamespace(user32=user32), create=True):
            cap = popover._max_panel_height_px()
        self.assertEqual(cap, 1080 - 2 * mod.MAX_HEIGHT_MARGIN)
        user32.MonitorFromPoint.assert_called_once()
        user32.SystemParametersInfoW.assert_called_once()

    def test_returns_zero_when_the_pointer_lookup_itself_raises(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover = mod.Popover(lambda hover: None, lambda name: None)
        popover.winfo_pointerx = mock.Mock(side_effect=RuntimeError("boom"))
        self.assertEqual(popover._max_panel_height_px(), 0)

    def test_pin_uses_the_roomiest_monitor_not_the_first(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover = mod.Popover(lambda hover: None, lambda name: None,
                              pinned=True)
        # A small primary monitor listed first, a bigger secondary second
        # -- pin_origin's own tie-break (largest area) must win, not
        # whichever one EnumDisplayMonitors happened to report first.
        popover._monitor_workareas = lambda: [
            (0, 0, 800, 600), (800, 0, 2560, 1440)]
        cap = popover._max_panel_height_px()
        self.assertEqual(cap, 1440 - 2 * mod.MAX_HEIGHT_MARGIN)

    def test_pin_returns_zero_with_no_monitor_workareas(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover = mod.Popover(lambda hover: None, lambda name: None,
                              pinned=True)
        popover._monitor_workareas = lambda: []
        self.assertEqual(popover._max_panel_height_px(), 0)


class TestRefreshLayoutHeightCap(GuiStubbedTestCase):
    """refresh_layout must actually use _max_panel_height_px's answer:
    clamp the window/canvas viewport, keep the FULL content in the
    scrollregion, and only bind the wheel while there is something to
    scroll."""

    def _popover(self, mod):
        popover = mod.Popover(lambda hover: None, lambda name: None)
        return popover

    def test_short_content_is_not_clamped_or_scrollable(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        popover._max_panel_height_px = lambda: 2000   # plenty of room
        popover.rebuild = _rebuild(theme_mod, height=400.0)
        popover.refresh_layout()
        self.assertEqual(popover.geometry(), "330x400")
        self.assertNotIn("<MouseWheel>", popover.canvas._bindings)

    def test_tall_content_is_clamped_and_becomes_scrollable(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        popover._max_panel_height_px = lambda: 500
        popover.rebuild = _rebuild(theme_mod, height=1100.0)
        popover.refresh_layout()
        self.assertEqual(popover.geometry(), "330x500")
        self.assertIn("<MouseWheel>", popover.canvas._bindings)

    def test_shrinking_back_below_the_cap_unbinds_wheel_and_resets_scroll(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        popover._max_panel_height_px = lambda: 500
        popover.rebuild = _rebuild(theme_mod, height=1100.0)
        popover.refresh_layout()
        popover.canvas.y_offset = 240   # the user scrolled partway down
        popover.rebuild = _rebuild(theme_mod, height=400.0)
        popover.refresh_layout()
        self.assertNotIn("<MouseWheel>", popover.canvas._bindings)
        self.assertEqual(popover.canvas.y_offset, 0)

    def test_a_failed_height_lookup_leaves_the_panel_uncapped(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        popover._max_panel_height_px = lambda: 0   # "could not determine"
        popover.rebuild = _rebuild(theme_mod, height=1100.0)
        popover.refresh_layout()
        self.assertEqual(popover.geometry(), "330x1100")
        self.assertNotIn("<MouseWheel>", popover.canvas._bindings)


class TestMouseWheelScroll(GuiStubbedTestCase):
    def test_wheel_notch_scrolls_by_the_configured_increment_direction(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover = mod.Popover(lambda hover: None, lambda name: None)
        popover._on_scroll(types.SimpleNamespace(delta=-120))
        self.assertEqual(popover.canvas.scroll_calls, [(1, "units")])


class TestHitTestingUsesCanvasCoordinates(GuiStubbedTestCase):
    """A click/hover must land on the layout coordinate under the
    pointer's position WITHIN THE SCROLLED CONTENT, not the raw
    viewport-relative event.x/event.y -- FINDING 9's scrolling only works
    if hit-testing accounts for it too."""

    def _popover_with_hit(self, mod):
        theme_mod = mod.t
        hit = theme_mod.Hit("refresh", 10.0, 500.0, 20.0, 20.0)
        popover = mod.Popover(lambda hover: None, lambda name: None)
        popover.layout = _layout(theme_mod, hits=[hit])
        return popover, hit

    def test_click_translates_through_canvasx_canvasy_before_hit_testing(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover, hit = self._popover_with_hit(mod)
        popover.canvas.y_offset = 490   # the hit is scrolled into view
        actions = []
        popover.on_action = actions.append
        popover._on_click(types.SimpleNamespace(x=15, y=15))
        self.assertEqual(actions, ["refresh"])

    def test_click_at_the_same_raw_xy_misses_when_unscrolled(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover, hit = self._popover_with_hit(mod)
        actions = []
        popover.on_action = actions.append
        popover._on_click(types.SimpleNamespace(x=15, y=15))
        self.assertEqual(actions, [])

    def test_motion_translates_through_canvasx_canvasy_before_hit_testing(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover, hit = self._popover_with_hit(mod)
        popover.canvas.y_offset = 490
        popover.refresh_layout = mock.Mock()
        popover._reset_tooltip = mock.Mock()
        popover._on_motion(types.SimpleNamespace(x=15, y=15))
        self.assertEqual(popover.hover, "refresh")


class TestTooltipDwell(GuiStubbedTestCase):
    """tkinter has no tooltip widget; Popover fakes one with a delayed
    Toplevel (FINDING 8's Hit.tooltip). Hover-dwell, not instant, and torn
    down again on leave/click/panel-hide.

    The dwell keys on the tooltip TEXT rather than the Hit, because the
    text now comes from Layout.tooltip_at() -- which, unlike hit(), also
    answers for a DISABLED hit, and a disabled control is the one most in
    need of explaining itself (see TestDisabledHitsStillExplainThemselves
    below and popover_theme.Layout.tooltip_at)."""

    def _popover(self, mod):
        return mod.Popover(lambda hover: None, lambda name: None)

    def test_hovering_a_tooltip_hit_schedules_a_dwell_timer(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        hit = theme_mod.Hit("refresh", 0, 0, 10, 10, tooltip="Refresh now")
        popover._reset_tooltip(hit.tooltip)
        self.assertIsNotNone(popover._tooltip_after_id)
        self.assertIsNone(popover._tooltip_win)   # not shown yet -- dwelling

    def test_the_dwell_timer_firing_shows_a_toplevel_with_the_hits_text(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        hit = theme_mod.Hit("refresh", 0, 0, 10, 10, tooltip="Refresh now")
        popover._reset_tooltip(hit.tooltip)
        job_id = popover._tooltip_after_id
        popover.fire_after(job_id)
        self.assertIsNotNone(popover._tooltip_win)

    def test_a_hit_with_no_tooltip_never_schedules_anything(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        hit = theme_mod.Hit("card:claude:1", 0, 0, 10, 10)   # tooltip=""
        popover._reset_tooltip(hit.tooltip)
        self.assertIsNone(popover._tooltip_after_id)
        self.assertIsNone(popover._tooltip_win)

    def test_moving_off_the_hit_before_the_dwell_fires_cancels_it(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        hit = theme_mod.Hit("refresh", 0, 0, 10, 10, tooltip="Refresh now")
        popover._reset_tooltip(hit.tooltip)
        first_job_id = popover._tooltip_after_id
        popover._reset_tooltip("")   # hover moved off before it fired
        self.assertIsNone(popover._tooltip_after_id)
        self.assertNotIn(first_job_id, popover._after_jobs)

    def test_a_stale_dwell_timer_that_fires_after_hover_moved_on_is_a_no_op(self):
        # A pending after() job cannot always be cancelled in time on real
        # Tk (a callback already queued for this tick still runs) -- the
        # `tip != self._tooltip_hit` guard in _show_tooltip is what
        # actually prevents a late-firing timer from showing a tooltip for
        # a hit the pointer has already left.
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        hit_a = theme_mod.Hit("refresh", 0, 0, 10, 10, tooltip="Refresh now")
        hit_b = theme_mod.Hit("quit", 0, 0, 10, 10, tooltip="Quit")
        popover._reset_tooltip(hit_a.tooltip)
        stale_job_id = popover._tooltip_after_id
        stale_callback = popover._after_jobs[stale_job_id]
        popover._tooltip_hit = hit_b.tooltip   # hover moved on
        stale_callback[1](*stale_callback[2])   # fire the STALE job directly
        self.assertIsNone(popover._tooltip_win)

    def test_leaving_the_canvas_hides_any_visible_tooltip(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        hit = theme_mod.Hit("refresh", 0, 0, 10, 10, tooltip="Refresh now")
        popover._reset_tooltip(hit.tooltip)
        popover.fire_after(popover._tooltip_after_id)
        self.assertIsNotNone(popover._tooltip_win)
        popover._on_leave(None)
        self.assertIsNone(popover._tooltip_win)

    def test_a_click_hides_any_visible_tooltip(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        hit = theme_mod.Hit("refresh", 0, 0, 10, 10, tooltip="Refresh now")
        popover._reset_tooltip(hit.tooltip)
        popover.fire_after(popover._tooltip_after_id)
        self.assertIsNotNone(popover._tooltip_win)
        popover._on_click(types.SimpleNamespace(x=0, y=0))
        self.assertIsNone(popover._tooltip_win)

    def test_hiding_the_panel_hides_any_visible_tooltip(self):
        mod = _reimport("smartbar.windows.popover_window")
        theme_mod = mod.t
        popover = self._popover(mod)
        hit = theme_mod.Hit("refresh", 0, 0, 10, 10, tooltip="Refresh now")
        popover._reset_tooltip(hit.tooltip)
        popover.fire_after(popover._tooltip_after_id)
        self.assertIsNotNone(popover._tooltip_win)
        popover.hide_panel()
        self.assertIsNone(popover._tooltip_win)



class TestDisabledHitsStillExplainThemselves(GuiStubbedTestCase):
    """_on_motion must read tooltips through tooltip_at, not through hit().

    hit() refuses a disabled Hit, so a pointer resting on the blocked
    "Make Active" button resolves to the account card underneath -- whose
    tooltip is "". Routing the dwell through hit() therefore made the one
    tooltip that explains WHY the button will not respond unreachable,
    while SwiftUI shows it via .help() on its own .disabled() button.
    """

    def test_hovering_a_blocked_switch_button_still_gets_its_reason(self):
        mod = _reimport("smartbar.windows.popover_window")
        built = layout.build(model.Snapshot(accounts=[
            model.Account(number=1, email="dead@example.com", ok=False,
                          status="relogin_required", metrics=[])]))
        popover = mod.Popover(lambda hover: built, lambda name: None)
        popover.layout = built
        popover._scale = 1.0
        # The repaint half of _on_motion needs real Win32 DPI calls that do
        # not exist here; this test is about the tooltip lookup only.
        target = [h for h in built.hits if h.name.startswith("switch:")][0]
        with mock.patch.object(popover, "refresh_layout"):
            self.assertFalse(target.enabled)
            popover._on_motion(types.SimpleNamespace(
                x=target.x + target.w / 2, y=target.y + target.h / 2))
        self.assertEqual(popover._tooltip_hit, target.tooltip)
        self.assertIn("Stored credential is dead", popover._tooltip_hit)


class TestPositionDefensiveFloor(GuiStubbedTestCase):
    """Even if a height cap somehow still overflows the work area, the
    header must never end up placed above the work area's top edge --
    FINDING 9's one hard requirement, independent of the cap itself."""

    def test_the_header_never_lands_above_the_work_area_top(self):
        mod = _reimport("smartbar.windows.popover_window")
        popover = mod.Popover(lambda hover: None, lambda name: None)
        popover.pointer = (100, 50)   # near the very top of the screen
        popover.screen = (1920, 1080)
        popover._size = (330, 5000)   # an implausibly tall, uncapped panel
        popover._position()
        _x, y = popover.geometry().lstrip("+").split("+")
        self.assertGreaterEqual(int(y), 0)   # never above the work area's y=0


if __name__ == "__main__":
    unittest.main()
