"""Tests for smartbar/linux/tray.py and smartbar/linux/popover_window.py.

Neither module can be imported on the machine this suite normally runs on --
`gi` (PyGObject) is not installed here -- so a fake `gi`/`gi.repository`
(Gtk/Gdk/GLib/AyatanaAppIndicator3) goes into sys.modules first, the same
approach tests/test_windows_tray.py uses for tkinter/pystray. Unlike that
file, cairo itself is NOT faked: pycairo IS installed in this environment,
so smartbar.paint.tray_icon/popover_draw import and run for real underneath
the fake toolkit.

What is pinned here, and why:

  1. Both modules import cleanly under the fake gi, and Popover actually
     subclasses the stubbed Gtk.Window (a real class, not a MagicMock --
     Python cannot use a bare Mock instance as a base class).
  2. _popover_layout() forwards action_error/refreshing/stale_reason to
     popover_layout.build() -- these three keyword-only parameters exist
     precisely so a front-end can wire them, and a front-end that builds
     the kwargs by hand is exactly the kind of thing that silently drifts.
  3. The "sticky until the next attempt" contract for action_error
     (mirrors UsageStore.swift:15-16): a switch/remove failure sets it,
     armed BEFORE the network call so a stale error never survives a new
     attempt in flight, cleared by the "dismiss-error" hit.
  4. Every UI mutation a switch/remove failure makes happens through
     GLib.idle_add rather than directly on the worker thread -- GLib.idle_add
     is a plain recorder here (it does NOT auto-run its callback), so a
     regression that pokes self.action_error straight from run() would
     leave action_error empty until the test manually drains the recorder,
     which is exactly what TestActionErrorGoesThroughIdleAdd checks for.
  5. The refreshing flag: raised synchronously by _start_fetch, cleared by
     _apply_snapshot/_apply_error ONLY when the generation still matches --
     a superseded (late, pre-switch) fetch landing must not clear a NEWER
     fetch's own in-flight flag.
  6. The optimistic ACTIVE flip on switch (mirrors UsageStore.swift:
     159-166): the account list flips and self.generation is bumped
     synchronously, before the background cswap.switch() call even starts,
     so a slow pre-switch fetch that lands afterwards is dropped by the
     existing generation guard instead of clobbering the guess.
  7. Per-hit tooltips (FINDING 8). The panel is one DrawingArea, so GTK
     has no widget tree to hang a tooltip on: set_has_tooltip(True) plus a
     query-tooltip handler that hit-tests the pointer itself is the only
     way in. An earlier version of this file also pinned a _clamp_y
     helper against FINDING 9 (an overtall panel pushing its own header
     off the TOP of the screen). That helper is gone: 4c20b69 replaced
     pointer-following placement with top-right + drag + a remembered
     origin, so pin_origin now returns `top + margin` and the header
     cannot leave the work area by construction.
  8. FINDING 9's other half -- an overtall panel's FOOTER running past
     the bottom of the work area -- and the scrolling viewport that now
     catches it, matching the Windows panel. What is worth pinning is
     not the cap arithmetic but the coordinate translation: the PAINT is
     what scrolls (one ctx.translate in _on_draw), so a click, a hover
     and a tooltip on a scrolled panel all have to go back through
     _content_y or they land on whatever sits at the untranslated y.

Deliberately NOT covered: real AppIndicator/menu rendering, real cairo
painting of the popover, and Wayland's "leave placement alone" branch --
none of that needs anything this file's changes touch.

Every GUI-stubbed test snapshots the WHOLE of sys.modules and restores it
verbatim afterwards -- see GuiStubbedTestCase's own docstring (copied from
test_windows_tray.py) for why restoring only the gi.* keys is not enough:
smartbar.paint.tray_icon/popover_draw's own `import cairo` would otherwise
stay bound to whatever sys.modules["cairo"] held the first time either
module was ever imported in this process.
"""
from __future__ import annotations

import contextlib
import importlib
import inspect
import sys
import types
import unittest
from unittest import mock

from smartbar.core import model
from smartbar.core import popover_layout as layout


class _RecordingTooltip:
    """Stand-in for Gtk.Tooltip: records whatever set_text was handed."""

    def __init__(self):
        self.text = None

    def set_text(self, text):
        self.text = text


def snapshot_with_a_dead_credential():
    """One card whose stored credential is dead, so its switch button is
    the DISABLED hit whose tooltip is the only explanation of why."""
    return model.Snapshot(accounts=[
        model.Account(number=1, email="dead@example.com", ok=False,
                      status="relogin_required", metrics=[])])


class _RecordingArea:
    """Stand-in for the Gtk.DrawingArea: counts repaints."""

    def __init__(self):
        self.redraws = 0

    def queue_draw(self):
        self.redraws += 1


def snapshot_with_many_accounts(count=8):
    """Enough cards to overflow a real work area -- FINDING 9 measured 8
    accounts at 769pt of content and 12 at 1117pt, against the 984pt a
    1080p work area leaves once MAX_HEIGHT_MARGIN is taken off both ends."""
    return model.Snapshot(accounts=[
        model.Account(number=n, email="user%d@example.com" % n,
                      metrics=[model.Metric(key="5h", label="5h", short="5h",
                                            pct=10.0, countdown="1h 2m")])
        for n in range(1, count + 1)])


def _press_event(x, y):
    return types.SimpleNamespace(button=1, x=x, y=y, x_root=0.0, y_root=0.0,
                                 time=0)


def _wheel(direction, deltas=None):
    return types.SimpleNamespace(direction=direction,
                                 get_scroll_deltas=lambda: deltas)


def _install_gi_stubs():
    """Fake gi/gi.repository.{Gtk,Gdk,GLib,AyatanaAppIndicator3} into
    sys.modules. cairo is left alone -- it is really installed here."""
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_a, **_k: None
    sys.modules["gi"] = gi

    repository = types.ModuleType("gi.repository")
    sys.modules["gi.repository"] = repository
    gi.repository = repository

    class _FakeWidget:
        """A real class, not a MagicMock: Popover(Gtk.Window) subclasses
        this at class-definition time (import time), and a bare Mock()
        instance cannot be used as a base class -- "metaclass conflict"."""

        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            def _method(*a, **k):
                return None
            return _method

    class _Window(_FakeWidget):
        pass

    class _DrawingArea(_FakeWidget):
        pass

    class _MenuItem:
        def __init__(self, label=""):
            self.label = label
            self.sensitive = True
            self._callbacks = {}

        def connect(self, signal, callback, *args):
            self._callbacks[signal] = (callback, args)

        def set_sensitive(self, value):
            self.sensitive = value

        def activate(self):
            callback, args = self._callbacks["activate"]
            callback(self, *args)

    class _SeparatorMenuItem(_MenuItem):
        pass

    class _Menu:
        def __init__(self):
            self.items = []
            self._callbacks = {}

        def append(self, item):
            self.items.append(item)

        def connect(self, signal, callback, *args):
            self._callbacks[signal] = (callback, args)

        def show_all(self):
            pass

        def get_mapped(self):
            return False

    Gtk = types.ModuleType("gi.repository.Gtk")
    Gtk.Window = _Window
    Gtk.DrawingArea = _DrawingArea
    Gtk.Menu = _Menu
    Gtk.MenuItem = _MenuItem
    Gtk.SeparatorMenuItem = _SeparatorMenuItem
    Gtk.WindowType = types.SimpleNamespace(TOPLEVEL=0)
    Gtk.main = lambda: None
    Gtk.main_quit = lambda: None
    repository.Gtk = Gtk
    sys.modules["gi.repository.Gtk"] = Gtk

    class _Display:
        @staticmethod
        def get_default():
            return None

    Gdk = types.ModuleType("gi.repository.Gdk")
    Gdk.EventMask = types.SimpleNamespace(
        BUTTON_PRESS_MASK=1, BUTTON_RELEASE_MASK=2, POINTER_MOTION_MASK=4,
        LEAVE_NOTIFY_MASK=8, SCROLL_MASK=16, SMOOTH_SCROLL_MASK=32)
    Gdk.ScrollDirection = types.SimpleNamespace(UP=0, DOWN=1, SMOOTH=4)
    Gdk.WindowTypeHint = types.SimpleNamespace(DOCK=0, UTILITY=1)
    Gdk.KEY_Escape = 65307
    Gdk.Display = _Display
    repository.Gdk = Gdk
    sys.modules["gi.repository.Gdk"] = Gdk

    GLib = types.ModuleType("gi.repository.GLib")
    # A plain recorder, NOT an auto-runner: see the module docstring's
    # point 4 -- a test has to be able to tell "queued for the main loop"
    # apart from "ran inline on the worker thread".
    GLib.idle_add = mock.Mock(name="GLib.idle_add")
    GLib.timeout_add_seconds = mock.Mock(name="GLib.timeout_add_seconds",
                                         return_value=1)
    repository.GLib = GLib
    sys.modules["gi.repository.GLib"] = GLib

    class _Indicator:
        IndicatorCategory = types.SimpleNamespace(APPLICATION_STATUS=0)
        IndicatorStatus = types.SimpleNamespace(ACTIVE=1)

        @staticmethod
        def new(*a, **k):
            return mock.MagicMock(name="AppIndicator.Indicator")

    AppIndicator = types.ModuleType("gi.repository.AyatanaAppIndicator3")
    AppIndicator.Indicator = _Indicator
    AppIndicator.IndicatorCategory = _Indicator.IndicatorCategory
    AppIndicator.IndicatorStatus = _Indicator.IndicatorStatus
    repository.AyatanaAppIndicator3 = AppIndicator
    sys.modules["gi.repository.AyatanaAppIndicator3"] = AppIndicator

    return types.SimpleNamespace(Gtk=Gtk, Gdk=Gdk, GLib=GLib,
                                 AppIndicator=AppIndicator)


class GuiStubbedTestCase(unittest.TestCase):
    """Installs the fake gi stack for one test, then restores ALL of
    sys.modules -- not just the gi.* keys. See test_windows_tray.py's
    identical-in-spirit GuiStubbedTestCase for why a partial restore is
    not enough: smartbar.paint.tray_icon/popover_draw's own `import cairo`
    binds whatever module object is in sys.modules["cairo"] AT THAT MOMENT
    into their own globals permanently, and popover_layout/model are real,
    ordinary imports too that must not leak a fake-gi-flavoured copy of
    smartbar.linux.tray into a later test file's import of the same name.
    """

    def setUp(self):
        self._sys_modules_snapshot = dict(sys.modules)
        self.gi = _install_gi_stubs()

    def tearDown(self):
        for name in list(sys.modules):
            if name not in self._sys_modules_snapshot:
                del sys.modules[name]
        sys.modules.update(self._sys_modules_snapshot)


def _reimport(dotted_name):
    sys.modules.pop(dotted_name, None)
    return importlib.import_module(dotted_name)


class TestImportsCleanly(GuiStubbedTestCase):
    def test_tray_module_imports_and_defines_tray(self):
        mod = _reimport("smartbar.linux.tray")
        self.assertTrue(hasattr(mod, "Tray"))
        self.assertTrue(hasattr(mod, "main"))

    def test_popover_window_module_imports_and_defines_popover(self):
        mod = _reimport("smartbar.linux.popover_window")
        self.assertTrue(hasattr(mod, "Popover"))
        self.assertIn(self.gi.Gtk.Window, mod.Popover.__mro__)


def _bare_tray(mod, snapshot=None):
    """A Tray with no constructor side effects: no AppIndicator, no icon
    render, no thread -- just the plain-attribute state the methods under
    test actually read, mirroring test_windows_tray.py's Tray.__new__
    pattern."""
    tray = mod.Tray.__new__(mod.Tray)
    tray.snapshot = snapshot
    tray.provider = ""
    tray.confirm = ""
    tray.failures = 0
    tray.flip = False
    tray.generation = 0
    tray.menu = None
    tray.pending_menu = None
    tray.last_fetch_at = 0.0
    tray.last_error = ""
    tray.action_error = ""
    tray.refreshing = False
    tray.update_blocked = ""
    tray.update_pending = ""
    tray.open_item = None
    tray.presence_started = True
    tray.popover = mock.MagicMock(name="popover")
    tray.popover.get_visible.return_value = True
    tray.indicator = mock.MagicMock(name="indicator")
    return tray


def _account(number, email, active=False):
    return model.Account(number=number, email=email, active=active)


class TestPopoverLayoutWiring(GuiStubbedTestCase):
    """_popover_layout() must hand action_error/refreshing/stale_reason to
    popover_layout.build() -- the three keyword-only parameters that let
    this front-end actually surface a stale reason, a busy refresh glyph
    and a dismissible action-error banner."""

    def test_forwards_action_error_refreshing_and_stale_reason(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.action_error = "Switch failed: boom"
        tray.refreshing = True
        tray.last_error = "connection reset"
        with mock.patch.object(mod.popover_layout, "build") as build:
            mod.Tray._popover_layout(tray, hover="refresh")
        _args, kwargs = build.call_args
        self.assertEqual(kwargs["action_error"], "Switch failed: boom")
        self.assertIs(kwargs["refreshing"], True)
        self.assertEqual(kwargs["stale_reason"], "connection reset")

    def test_stale_reason_is_empty_once_a_fetch_has_succeeded(self):
        # last_error is cleared by _apply_snapshot on success (see below),
        # so a healthy panel must not show a stale reason left over from
        # some earlier, since-resolved failure.
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.last_error = ""
        with mock.patch.object(mod.popover_layout, "build") as build:
            mod.Tray._popover_layout(tray)
        self.assertEqual(build.call_args.kwargs["stale_reason"], "")


class TestDismissErrorHit(GuiStubbedTestCase):
    def test_dismiss_error_clears_the_sticky_banner(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.action_error = "Remove failed: in use"
        mod.Tray._on_popover_action(tray, "dismiss-error")
        self.assertEqual(tray.action_error, "")
        tray.popover.refresh_layout.assert_called_once()


class TestRefreshingFlag(GuiStubbedTestCase):
    """FINDING: the \u23f3 must dim and stop accepting clicks while a fetch
    is in flight, and must not un-dim early because a DIFFERENT
    (superseded) fetch's completion happened to land."""

    def test_start_fetch_raises_the_flag_before_the_worker_even_runs(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        with mock.patch.object(mod.threading, "Thread") as thread:
            mod.Tray._start_fetch(tray)
        thread.assert_called_once()
        self.assertTrue(tray.refreshing)

    def test_apply_snapshot_clears_the_flag_for_the_current_generation(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.refreshing = True
        tray.generation = 3
        snap = model.Snapshot(accounts=[])
        tray.alerts = mock.MagicMock()
        tray.alerts.check.return_value = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(mod, "presence"))
            stack.enter_context(mock.patch.object(mod, "plan"))
            stack.enter_context(
                mock.patch.object(mod.codex, "accounts", return_value=[]))
            stack.enter_context(mock.patch.object(mod, "presence_client"))
            stack.enter_context(
                mock.patch.object(tray, "_pending_update", return_value=""))
            stack.enter_context(mock.patch.object(tray, "_set_icon"))
            stack.enter_context(mock.patch.object(tray, "_refresh_menu"))
            stack.enter_context(mock.patch.object(tray, "_maybe_recapture"))
            mod.Tray._apply_snapshot(tray, snap, 3)
        self.assertFalse(tray.refreshing)

    def test_apply_snapshot_leaves_a_superseded_generations_flag_alone(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.refreshing = True   # a NEWER fetch is genuinely in flight
        tray.generation = 4
        result = mod.Tray._apply_snapshot(tray, model.Snapshot(), 3)
        self.assertFalse(result)
        self.assertTrue(tray.refreshing, "a stale callback must not clear "
                        "the flag a newer, still-running fetch owns")

    def test_apply_error_clears_the_flag_for_the_current_generation(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.refreshing = True
        tray.generation = 1
        with mock.patch.object(tray, "_refresh_menu"):
            mod.Tray._apply_error(tray, "boom", 1)
        self.assertFalse(tray.refreshing)
        self.assertEqual(tray.last_error, "boom")

    def test_apply_error_leaves_a_superseded_generations_flag_alone(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.refreshing = True
        tray.generation = 9
        result = mod.Tray._apply_error(tray, "boom", 8)
        self.assertFalse(result)
        self.assertTrue(tray.refreshing)


class TestActionErrorGoesThroughIdleAdd(GuiStubbedTestCase):
    """A switch/remove failure happens on a worker thread; the fix must
    route the resulting state mutation through GLib.idle_add, never touch
    self.action_error directly from that thread (see module docstring
    point 4)."""

    def _run_worker_target(self, mod, thread_mock):
        """threading.Thread(target=run, ...) was called once; invoke that
        `run` synchronously, as if it were the worker thread, without ever
        spinning up a real thread."""
        _args, kwargs = thread_mock.call_args
        kwargs["target"]()

    def test_switch_failure_is_sticky_via_idle_add_not_a_direct_write(self):
        mod = _reimport("smartbar.linux.tray")
        snap = model.Snapshot(accounts=[_account(1, "a@x.com", active=True),
                                        _account(2, "b@x.com")])
        tray = _bare_tray(mod, snapshot=snap)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                mod.cswap, "switch",
                side_effect=mod.cswap.CswapError("in use")))
            stack.enter_context(mock.patch.object(tray, "_set_icon"))
            stack.enter_context(mock.patch.object(tray, "_refresh_menu"))
            thread = stack.enter_context(
                mock.patch.object(mod.threading, "Thread"))
            idle_add = stack.enter_context(
                mock.patch.object(mod.GLib, "idle_add"))
            mod.Tray._on_switch(tray, None, 2)
            # The worker has not run yet: nothing may be visible until the
            # idle_add-queued callback is actually drained.
            self.assertEqual(tray.action_error, "")
            self._run_worker_target(mod, thread)
        idle_add.assert_called_once()
        callback, message = idle_add.call_args.args
        self.assertEqual(callback, tray._set_action_error)
        self.assertIn("in use", message)
        self.assertEqual(tray.action_error, "",
                         "must still be empty until the recorded idle_add "
                         "callback is actually invoked")
        callback(message)
        self.assertEqual(tray.action_error, "Switch failed: in use")

    def test_remove_failure_is_sticky_via_idle_add_not_a_direct_write(self):
        mod = _reimport("smartbar.linux.tray")
        snap = model.Snapshot(accounts=[_account(5, "c@x.com")])
        tray = _bare_tray(mod, snapshot=snap)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                mod.cswap, "remove_account",
                side_effect=mod.cswap.CswapError("no such")))
            thread = stack.enter_context(
                mock.patch.object(mod.threading, "Thread"))
            idle_add = stack.enter_context(
                mock.patch.object(mod.GLib, "idle_add"))
            mod.Tray._on_remove(tray, "claude:5")
            self._run_worker_target(mod, thread)
        callback, message = idle_add.call_args.args
        self.assertEqual(message, "Remove failed: no such")
        callback(message)
        self.assertEqual(tray.action_error, "Remove failed: no such")

    def test_a_new_switch_attempt_clears_any_previous_sticky_error(self):
        # "sticky until the next attempt" (UsageStore.swift:15-16): the OLD
        # error must be gone the instant a NEW attempt starts, not only
        # once the new attempt itself resolves.
        mod = _reimport("smartbar.linux.tray")
        snap = model.Snapshot(accounts=[_account(1, "a@x.com", active=True),
                                        _account(2, "b@x.com")])
        tray = _bare_tray(mod, snapshot=snap)
        tray.action_error = "Switch failed: stale old error"
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(mod.cswap, "switch"))
            stack.enter_context(mock.patch.object(tray, "_set_icon"))
            stack.enter_context(mock.patch.object(tray, "_refresh_menu"))
            stack.enter_context(mock.patch.object(mod.threading, "Thread"))
            mod.Tray._on_switch(tray, None, 2)
        self.assertEqual(tray.action_error, "")


class TestOptimisticSwitch(GuiStubbedTestCase):
    """UsageStore.swift:159-166's optimistic flip: the ACTIVE account (and
    therefore the icon/menu/panel) must move the instant the user clicks,
    not after the cswap round-trip -- guarded by the same generation bump
    that protects a real fetch from a stale result."""

    def test_flip_moves_active_synchronously_and_bumps_generation(self):
        mod = _reimport("smartbar.linux.tray")
        snap = model.Snapshot(accounts=[_account(1, "a@x.com", active=True),
                                        _account(2, "b@x.com")])
        tray = _bare_tray(mod, snapshot=snap)
        tray.generation = 7
        with contextlib.ExitStack() as stack:
            set_icon = stack.enter_context(
                mock.patch.object(tray, "_set_icon"))
            stack.enter_context(mock.patch.object(tray, "_refresh_menu"))
            stack.enter_context(mock.patch.object(mod.threading, "Thread"))
            mod.Tray._on_switch(tray, None, 2)
        self.assertTrue(snap.accounts[1].active)
        self.assertFalse(snap.accounts[0].active)
        self.assertEqual(snap.active_account.number, 2)
        self.assertEqual(tray.generation, 8, "must bump generation exactly "
                        "like UsageStore.swift's fetchGeneration += 1")
        self.assertFalse(tray.refreshing)
        set_icon.assert_called_once()
        tray.popover.refresh_layout.assert_called_once()

    def test_generation_bump_makes_a_stale_preswitch_fetch_get_dropped(self):
        """The whole point of bumping generation in the flip: a fetch that
        started BEFORE the click must not un-flip the optimistic guess when
        it lands after."""
        mod = _reimport("smartbar.linux.tray")
        snap = model.Snapshot(accounts=[_account(1, "a@x.com", active=True),
                                        _account(2, "b@x.com")])
        tray = _bare_tray(mod, snapshot=snap)
        tray.generation = 7   # a fetch already in flight captured THIS
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(tray, "_set_icon"))
            stack.enter_context(mock.patch.object(tray, "_refresh_menu"))
            stack.enter_context(mock.patch.object(mod.threading, "Thread"))
            mod.Tray._on_switch(tray, None, 2)
        stale_snapshot = model.Snapshot(
            accounts=[_account(1, "a@x.com", active=True),
                      _account(2, "b@x.com")])
        result = mod.Tray._apply_snapshot(tray, stale_snapshot, 7)
        self.assertFalse(result)
        # The optimistic guess (account 2 active) must survive untouched.
        self.assertIs(tray.snapshot, snap)
        self.assertEqual(tray.snapshot.active_account.number, 2)

    def test_no_snapshot_yet_is_a_no_op_not_a_crash(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod, snapshot=None)
        with mock.patch.object(mod.threading, "Thread"):
            mod.Tray._on_switch(tray, None, 2)   # must not raise
        self.assertEqual(tray.generation, 0)


class TestPanelTooltips(GuiStubbedTestCase):
    """GTK's half of FINDING 8: the painted panel had no tooltips at all.

    The panel is one DrawingArea, so GTK has no widget tree to hang a
    tooltip on -- set_has_tooltip(True) plus a query-tooltip handler that
    hit-tests the pointer itself is the only way in. Pinned because the
    handler's two escape hatches are both easy to get backwards: returning
    False is what HIDES a tooltip already on screen (so "nothing here" must
    fall through, not return True with empty text), and keyboard mode has
    no pointer position to test against at all.
    """

    def setUp(self):
        super().setUp()
        self.module = _reimport("smartbar.linux.popover_window")
        self.popover = self.module.Popover.__new__(self.module.Popover)
        self.popover._scroll = 0.0    # unscrolled: widget y IS layout y
        self.popover.layout = layout.build(snapshot_with_a_dead_credential())
        self.tip = _RecordingTooltip()

    def _blocked_switch(self):
        return [h for h in self.popover.layout.hits
                if h.name.startswith("switch:")][0]

    def test_the_drawing_area_asks_for_tooltips_at_all(self):
        source = inspect.getsource(self.module.Popover.__init__)
        self.assertIn("set_has_tooltip(True)", source)
        self.assertIn('"query-tooltip"', source)

    def test_a_hovered_hit_answers_with_its_own_text(self):
        target = self._blocked_switch()
        shown = self.popover._on_query_tooltip(
            None, target.x + target.w / 2, target.y + target.h / 2,
            False, self.tip)
        self.assertTrue(shown)
        # The DISABLED button's reason, reached through tooltip_at rather
        # than hit() -- see popover_theme.Layout.tooltip_at.
        self.assertFalse(target.enabled)
        self.assertEqual(self.tip.text, target.tooltip)

    def test_empty_space_falls_through_so_gtk_hides_the_last_one(self):
        shown = self.popover._on_query_tooltip(None, -5, -5, False, self.tip)
        self.assertFalse(shown)
        self.assertIsNone(self.tip.text)

    def test_keyboard_mode_is_declined(self):
        target = self._blocked_switch()
        shown = self.popover._on_query_tooltip(
            None, target.x + target.w / 2, target.y + target.h / 2,
            True, self.tip)
        self.assertFalse(shown)
        self.assertIsNone(self.tip.text)

    def test_no_layout_yet_is_not_a_crash(self):
        self.popover.layout = None
        self.assertFalse(
            self.popover._on_query_tooltip(None, 5, 5, False, self.tip))


class TestPanelViewport(GuiStubbedTestCase):
    """FINDING 9's other half: an overtall panel ran its footer past the
    bottom of the work area with no way to reach it.

    The window is capped at _max_panel_height() and the wheel slides the
    paint underneath. Everything here exists because the scroll offset
    lives in exactly one place (_on_draw's ctx.translate) and therefore has
    to be undone in exactly one place too (_content_y) -- and a hit test
    that forgets is not a crash, it is a click on the wrong button.
    """

    def setUp(self):
        super().setUp()
        self.module = _reimport("smartbar.linux.popover_window")
        self.popover = self.module.Popover.__new__(self.module.Popover)
        self.popover.hover = ""
        self.popover.pinned = False
        self.popover.layout = None
        self.popover._scroll = 0.0
        self.popover._overflow = 0
        self.popover._size = (0, 0)
        self.popover._press = None
        self.popover._dragging = False
        self.popover._placed = None
        self.popover._saved = None
        self.popover._x11 = False       # Wayland branch: _position does nothing
        self.popover._tick_id = 1
        self.popover.area = _RecordingArea()
        self.resized = []
        self.popover.resize = lambda w, h: self.resized.append((w, h))
        self.tall = self._arm(snapshot_with_many_accounts())
        self.tall_height = int(round(self.tall.height))

    def _arm(self, snapshot):
        """Point rebuild() at a fresh Layout for this snapshot."""
        built = layout.build(snapshot)
        self.popover.rebuild = lambda _hover: built
        return built

    @contextlib.contextmanager
    def _screen(self, areas):
        """Pretend the desktop is exactly these work areas."""
        with mock.patch.object(self.module.Gdk.Display, "get_default",
                               staticmethod(lambda: object())), \
             mock.patch.object(self.module.Popover, "_workareas",
                               staticmethod(lambda _display: areas)):
            yield

    def _cap_for(self, height):
        return height - 2 * self.module.MAX_HEIGHT_MARGIN

    # --- the cap ------------------------------------------------------------
    def test_content_that_fits_the_screen_is_left_alone(self):
        """A panel that fits must not become scrollable: an offset that can
        only ever be 0 would still make every hit test pay for it."""
        with self._screen([(0, 0, 1920, 1080)]):
            self.popover.refresh_layout()
        self.assertLess(self.tall_height, self._cap_for(1080))
        self.assertEqual(self.resized[-1][1], self.tall_height)
        self.assertEqual(self.popover._overflow, 0)

    def test_content_taller_than_the_work_area_is_capped_and_scrollable(self):
        with self._screen([(0, 0, 1920, 400)]):
            self.popover.refresh_layout()
        self.assertEqual(self.resized[-1][1], self._cap_for(400))
        self.assertEqual(self.popover._overflow,
                         self.tall_height - self._cap_for(400))

    def test_a_failed_screen_lookup_leaves_the_panel_uncapped(self):
        """No monitors, no guessing: degrade to the old behaviour rather
        than invent a screen size and clip content nobody can scroll to."""
        def boom(_display):
            raise RuntimeError("no monitors here")

        with mock.patch.object(self.module.Gdk.Display, "get_default",
                               staticmethod(lambda: object())), \
             mock.patch.object(self.module.Popover, "_workareas",
                               staticmethod(boom)), \
             mock.patch.object(self.module.log, "exception"):
            self.popover.refresh_layout()
        self.assertEqual(self.resized[-1][1], self.tall_height)
        self.assertEqual(self.popover._overflow, 0)

    def test_no_display_at_all_leaves_the_panel_uncapped(self):
        self.assertEqual(self.popover._max_panel_height(), 0)

    def test_the_cap_uses_the_monitor_the_panel_was_dragged_onto(self):
        """Not the roomiest one: a panel remembered onto the small second
        monitor has to fit THAT screen, not the big one it is not on."""
        self.popover._saved = (2000, 10)
        with self._screen([(0, 0, 1920, 1080), (1920, 0, 1280, 400)]):
            self.assertEqual(self.popover._max_panel_height(),
                             self._cap_for(400))

    def test_with_nowhere_placed_yet_the_cap_uses_the_roomiest_monitor(self):
        """pin_origin's own tie-break, because that is where an unplaced
        panel is about to be parked."""
        with self._screen([(1920, 0, 1280, 400), (0, 0, 1920, 1080)]):
            self.assertEqual(self.popover._max_panel_height(),
                             self._cap_for(1080))

    # --- the wheel ----------------------------------------------------------
    def test_the_wheel_moves_the_viewport_and_stops_at_the_bottom(self):
        self.popover._overflow = 100
        for _ in range(2):
            self.popover._on_scroll(None, _wheel(self.module.Gdk.
                                                 ScrollDirection.DOWN))
        self.assertEqual(self.popover._scroll,
                         2.0 * self.module.SCROLL_STEP)
        for _ in range(10):
            self.popover._on_scroll(None, _wheel(self.module.Gdk.
                                                 ScrollDirection.DOWN))
        self.assertEqual(self.popover._scroll, 100.0)

    def test_the_wheel_stops_at_the_top_rather_than_going_negative(self):
        self.popover._overflow = 100
        self.popover._scroll = 20.0
        for _ in range(5):
            self.popover._on_scroll(None, _wheel(self.module.Gdk.
                                                 ScrollDirection.UP))
        self.assertEqual(self.popover._scroll, 0.0)

    def test_a_touchpads_smooth_deltas_scroll_too(self):
        """A wheel sends UP/DOWN; a touchpad sends SMOOTH with the numbers
        in get_scroll_deltas(). Reading only one leaves the other device
        scrolling nothing at all."""
        self.popover._overflow = 500
        self.popover._on_scroll(None, _wheel(
            self.module.Gdk.ScrollDirection.SMOOTH, (True, 0.0, 2.0)))
        self.assertEqual(self.popover._scroll,
                         2.0 * self.module.SCROLL_STEP)

    def test_smooth_deltas_that_are_not_available_scroll_nothing(self):
        self.popover._overflow = 500
        handled = self.popover._on_scroll(None, _wheel(
            self.module.Gdk.ScrollDirection.SMOOTH, (False, 0.0, 0.0)))
        self.assertFalse(handled)
        self.assertEqual(self.popover._scroll, 0.0)

    def test_a_wheel_over_a_panel_that_fits_is_not_swallowed(self):
        """Returning True on a panel with nothing to scroll would quietly
        eat wheel events that belong to whatever is underneath."""
        self.popover._overflow = 0
        handled = self.popover._on_scroll(None, _wheel(self.module.Gdk.
                                                       ScrollDirection.DOWN))
        self.assertFalse(handled)

    # --- the coordinate translation ----------------------------------------
    def test_the_paint_is_translated_by_the_scroll_offset(self):
        """Negative: scrolling DOWN moves the content UP past the window's
        top edge. The sign is one character and inverts the whole feature."""
        self.popover.layout = self.tall
        self.popover._scroll = 77.0
        moves = []
        ctx = types.SimpleNamespace(
            translate=lambda dx, dy: moves.append((dx, dy)))
        with mock.patch.object(self.module.popover_draw, "draw"):
            self.popover._on_draw(None, ctx)
        self.assertEqual(moves, [(0, -77.0)])

    def test_a_click_after_scrolling_lands_on_what_the_user_can_see(self):
        self.popover.layout = self.tall
        deep = self._deep_hit()
        self.popover._scroll = 200.0
        self.popover._on_press(
            None, _press_event(deep.x + 2, deep.y + deep.h / 2 - 200.0))
        self.assertEqual(self.popover._press[4], deep.name)

    def test_the_same_pointer_position_means_something_else_unscrolled(self):
        """The other half of the test above: without it, a _content_y that
        returned y unchanged would still pass if the two rects overlapped."""
        self.popover.layout = self.tall
        deep = self._deep_hit()
        self.popover._scroll = 0.0
        self.popover._on_press(
            None, _press_event(deep.x + 2, deep.y + deep.h / 2 - 200.0))
        self.assertNotEqual(self.popover._press[4], deep.name)

    def test_a_tooltip_after_scrolling_describes_what_is_under_the_pointer(self):
        built = layout.build(snapshot_with_a_dead_credential())
        self.popover.layout = built
        target = [h for h in built.hits if h.name.startswith("switch:")][0]
        tip = _RecordingTooltip()
        # Unscrolled, that same pointer position is 40px above the button.
        self.assertFalse(self.popover._on_query_tooltip(
            None, target.x + target.w / 2,
            target.y + target.h / 2 - 40.0, False, tip))
        self.assertIsNone(tip.text)
        self.popover._scroll = 40.0
        self.assertTrue(self.popover._on_query_tooltip(
            None, target.x + target.w / 2,
            target.y + target.h / 2 - 40.0, False, tip))
        self.assertEqual(tip.text, target.tooltip)

    def _deep_hit(self):
        """A hit far enough down the tall layout that a 200px scroll cannot
        leave it overlapping where it started."""
        return [h for h in self.tall.hits
                if h.name.startswith("card:") and h.y > 400][-1]

    # --- keeping the offset in range ---------------------------------------
    def test_reopening_the_panel_scrolls_back_to_the_top(self):
        self.popover._scroll = 120.0
        with self._screen([(0, 0, 1920, 400)]):
            self.popover.show_panel()
        self.assertGreater(self.popover._overflow, 120)   # it COULD have stayed
        self.assertEqual(self.popover._scroll, 0.0)

    def test_shrinking_content_pulls_the_viewport_back_into_range(self):
        """Scrolled to the bottom, then an account disappears: leaving the
        offset alone would park the window on blank space past the end."""
        with self._screen([(0, 0, 1920, 400)]):
            self.popover.refresh_layout()
            self.popover._scroll = float(self.popover._overflow)
            was = self.popover._scroll
            self._arm(snapshot_with_many_accounts(2))
            self.popover.refresh_layout()
        self.assertLess(self.popover._scroll, was)
        self.assertLessEqual(self.popover._scroll, float(self.popover._overflow))


if __name__ == "__main__":
    unittest.main()
