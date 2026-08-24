"""Tests for smartbar/linux/tray.py and smartbar/linux/popover_window.py.

Neither module can be imported on the machine this suite normally runs on --
`gi` (PyGObject) is not installed here -- so a fake `gi`/`gi.repository`
(Gtk/Gdk/GLib/AyatanaAppIndicator3) goes into sys.modules first, the same
approach tests/test_windows_tray.py uses for tkinter/pystray. Unlike that
file, cairo itself is NOT faked: pycairo IS installed in this environment,
so smartbar.paint.tray_icon/popover_draw import and run for real underneath
the fake toolkit.

The fetch/apply/alert/recapture/check-update state machine itself now lives
in smartbar.core.tray_controller (TrayController) and is pinned exactly
once, there, in tests/test_tray_controller.py -- see that file's own
docstring. What THIS file pins instead is the GTK-facing half: the TrayHost
contract's concrete binding, and whatever stays genuinely GTK-shaped and
therefore cannot move into the controller at all.

What is pinned here, and why:

  1. Both modules import cleanly under the fake gi, and Popover actually
     subclasses the stubbed Gtk.Window (a real class, not a MagicMock --
     Python cannot use a bare Mock instance as a base class).
  2. _popover_layout() forwards action_error/refreshing/stale_reason --
     now read off self.controller rather than plain instance attributes --
     to popover_layout.build(); a front-end that builds the kwargs by hand
     is exactly the kind of thing that silently drifts.
  3. TrayHost.call_on_ui_thread/schedule's concrete GTK binding:
     call_on_ui_thread must BE GLib.idle_add (not merely call it once and
     hope), and schedule must fire its callback exactly once through
     GLib.timeout_add_seconds without asking GLib to repeat it -- these
     are the highest-risk edits in the whole refactor (the controller
     depends on this seam for every worker-thread -> UI-thread touch a
     fetch, switch, remove, recapture or check-update makes).
  4. set_icon's on-disk filename alternation: AppIndicator.set_icon_full
     ignores a call whose icon name did not change, so a single fixed name
     would never repaint -- pinned by asserting two successive calls use
     different names drawn from exactly {state-a, state-b}.
  5. notify()'s urgency -> icon-name mapping (dialog-warning for
     'critical', dialog-information for 'normal') and its notify-send
     subprocess fallback when libnotify could not be initialised.
  6. rebuild_menu()'s pending-menu-until-hide swap: reassigning the menu
     out from under an open one closes it on some shells, so a mapped menu
     must hold the rebuild in self.pending_menu instead of installing it,
     and the 'hide' signal is what installs it afterwards.
  7. The optimistic ACTIVE flip's repaint (_flip_active_optimistically):
     genuinely host-bound per the design's own divergence note (Linux
     applies it synchronously; only the flip's platform-specific side --
     moving account.active, then repainting through set_icon/set_title/
     rebuild_menu/panel-refresh -- lives here; WHEN it runs is
     TrayController.on_switch's job, pinned in test_tray_controller.py).
  8. The dispatch table in _on_popover_action and the thin delegating
     call sites (_on_switch, _on_check_update, _on_refresh, _quit) reach
     the right controller call with the right arguments -- the decision
     logic behind each call is the controller's, pinned there instead.
  9. Per-hit tooltips (FINDING 8). The panel is one DrawingArea, so GTK
     has no widget tree to hang a tooltip on: set_has_tooltip(True) plus a
     query-tooltip handler that hit-tests the pointer itself is the only
     way in. An earlier version of this file also pinned a _clamp_y
     helper against FINDING 9 (an overtall panel pushing its own header
     off the TOP of the screen). That helper is gone: 4c20b69 replaced
     pointer-following placement with top-right + drag + a remembered
     origin, so pin_origin now returns `top + margin` and the header
     cannot leave the work area by construction.
  10. FINDING 9's other half -- an overtall panel's FOOTER running past
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
import inspect
import os
import tempfile
import types
import unittest
from unittest import mock

from smartbar.core import model
from smartbar.core import popover_layout as layout
from tests.support import stubs


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


class GuiStubbedTestCase(stubs.GuiStubbedTestCase):
    """Installs the fake gi stack for one test, then restores ALL of
    sys.modules -- not just the gi.* keys. See tests/support/stubs.py's
    GuiStubbedTestCase for why a partial restore is not enough:
    smartbar.paint.tray_icon/popover_draw's own `import cairo` binds
    whatever module object is in sys.modules["cairo"] AT THAT MOMENT into
    their own globals permanently, and popover_layout/model are real,
    ordinary imports too that must not leak a fake-gi-flavoured copy of
    smartbar.linux.tray into a later test file's import of the same name.
    """

    def setUp(self):
        # Before install_gi, not after: the guard is about the REAL cairo
        # install_gi deliberately does not fake, and skipping first leaves
        # sys.modules untouched on the way out (setUp raising means tearDown
        # never runs, so there must be no snapshot left half-taken).
        stubs.skip_without_pycairo()
        super().setUp()
        self.gi = stubs.install_gi()


_reimport = stubs.reimport


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
    test actually read, plus a REAL TrayController wired to this tray as
    its host (the controller itself is toolkit-free, so building one costs
    nothing here), mirroring how Tray.__init__ wires the two together."""
    tray = mod.Tray.__new__(mod.Tray)
    tray.controller = mod.TrayController(tray)
    tray.controller.snapshot = snapshot
    tray.provider = ""
    tray.confirm = ""
    tray.flip = False
    tray.menu = None
    tray.pending_menu = None
    tray.open_item = None
    tray.popover = mock.MagicMock(name="popover")
    tray.popover.get_visible.return_value = True
    tray.indicator = mock.MagicMock(name="indicator")
    tray._libnotify = None
    return tray


def _account(number, email, active=False, status="ok"):
    return model.Account(number=number, email=email, active=active,
                         status=status, ok=(status == "ok"))


class TestPopoverLayoutWiring(GuiStubbedTestCase):
    """_popover_layout() must hand action_error/refreshing/stale_reason to
    popover_layout.build() -- the three keyword-only parameters that let
    this front-end actually surface a stale reason, a busy refresh glyph
    and a dismissible action-error banner. All three now live on
    self.controller rather than on the Tray instance directly."""

    def test_forwards_action_error_refreshing_and_stale_reason(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller.action_error = "Switch failed: boom"
        tray.controller.refreshing = True
        tray.controller.last_error = "connection reset"
        with mock.patch.object(mod.popover_layout, "build") as build:
            mod.Tray._popover_layout(tray, hover="refresh")
        _args, kwargs = build.call_args
        self.assertEqual(kwargs["action_error"], "Switch failed: boom")
        self.assertIs(kwargs["refreshing"], True)
        self.assertEqual(kwargs["stale_reason"], "connection reset")

    def test_stale_reason_is_empty_once_a_fetch_has_succeeded(self):
        # last_error is cleared by the controller's _apply_snapshot on
        # success, so a healthy panel must not show a stale reason left
        # over from some earlier, since-resolved failure.
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller.last_error = ""
        with mock.patch.object(mod.popover_layout, "build") as build:
            mod.Tray._popover_layout(tray)
        self.assertEqual(build.call_args.kwargs["stale_reason"], "")


class TestDismissErrorHit(GuiStubbedTestCase):
    def test_dismiss_error_clears_the_sticky_banner(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller.action_error = "Remove failed: in use"
        mod.Tray._on_popover_action(tray, "dismiss-error")
        self.assertEqual(tray.controller.action_error, "")
        tray.popover.refresh_layout.assert_called_once()


class TestTabActionUpdatesProviderAndLayout(GuiStubbedTestCase):
    """A "tab:..." hit must update self.provider, and the very next popover
    layout must be built with that provider -- mirroring linux/tray.py's
    _popover_layout's `provider=self.provider` passthrough (see
    tests/test_windows_tray.py's twin of this class). Proven by mutation: a
    dispatcher that recognises "tab:" but forgets to write self.provider
    would pass every hit-name-recognition test above while leaving the panel
    permanently stuck on whichever provider auto-resolves first.
    """

    def test_a_tab_hit_sets_provider_and_next_layout_uses_it(self):
        # The dispatcher recognises "tab:" by prefix and never enumerates
        # the provider names, so whatever follows the colon flows through
        # untouched -- this pins that passthrough.
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)

        mod.Tray._on_popover_action(tray, "tab:openai")
        self.assertEqual(tray.provider, "openai")

        # The next layout build must carry the provider just set above --
        # this is what actually makes the click switch panel tabs.
        with mock.patch.object(mod.popover_layout, "build") as fake_build:
            mod.Tray._popover_layout(tray, hover="quit")
        _args, kwargs = fake_build.call_args
        self.assertEqual(kwargs.get("provider"), "openai")


class TestOptimisticFlip(GuiStubbedTestCase):
    """UsageStore.switchTo's optimistic-flip block: the ACTIVE account (and
    therefore the icon/menu/panel) must move the instant the user clicks,
    not after the cswap round-trip. TrayController.on_switch owns WHEN this
    runs (pinned in test_tray_controller.py); _flip_active_optimistically
    is WHAT it does on Linux -- genuinely host-bound per the design's own
    divergence note, since the repaint is made through this host's own
    set_icon/set_title/rebuild_menu/panel-refresh methods."""

    def test_flip_moves_active_and_repaints_without_touching_generation(self):
        mod = _reimport("smartbar.linux.tray")
        snap = model.Snapshot(accounts=[_account(1, "a@x.com", active=True),
                                        _account(2, "b@x.com")])
        tray = _bare_tray(mod, snapshot=snap)
        tray.controller.generation = 7
        tray.controller.refreshing = True
        with contextlib.ExitStack() as stack:
            set_icon = stack.enter_context(mock.patch.object(tray, "set_icon"))
            set_title = stack.enter_context(mock.patch.object(tray, "set_title"))
            rebuild = stack.enter_context(mock.patch.object(tray, "rebuild_menu"))
            mod.Tray._flip_active_optimistically(tray, 2)
        self.assertTrue(snap.accounts[1].active)
        self.assertFalse(snap.accounts[0].active)
        self.assertEqual(snap.active_account.number, 2)
        # Deliberately UNCHANGED. This flip is repaint-only: the bump that
        # matches UsageStore.swift's `fetchGeneration += 1` is done by
        # TrayController.on_switch under its own lock, because a bare `+=`
        # here raced the switch worker once the flip started being marshalled
        # through GLib.idle_add. See that bump's comment in tray_controller.py
        # and test_tray_controller's own regression test for the ordering.
        self.assertEqual(tray.controller.generation, 7)
        self.assertFalse(tray.controller.refreshing)
        set_icon.assert_called_once()
        set_title.assert_called_once()
        rebuild.assert_called_once()
        tray.popover.refresh_layout.assert_called_once()

    def test_no_snapshot_yet_is_a_no_op_not_a_crash(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod, snapshot=None)
        mod.Tray._flip_active_optimistically(tray, 2)   # must not raise
        self.assertEqual(tray.controller.generation, 0)

    def test_a_hidden_popover_is_not_asked_to_refresh(self):
        mod = _reimport("smartbar.linux.tray")
        snap = model.Snapshot(accounts=[_account(1, "a@x.com", active=True),
                                        _account(2, "b@x.com")])
        tray = _bare_tray(mod, snapshot=snap)
        tray.popover.get_visible.return_value = False
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(tray, "set_icon"))
            stack.enter_context(mock.patch.object(tray, "set_title"))
            stack.enter_context(mock.patch.object(tray, "rebuild_menu"))
            mod.Tray._flip_active_optimistically(tray, 2)
        tray.popover.refresh_layout.assert_not_called()


class TestSetIconFlip(GuiStubbedTestCase):
    """AppIndicator.set_icon_full ignores a call whose name did not change
    from the last one -- a single fixed name would never repaint."""

    def test_alternates_between_exactly_two_names(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        with mock.patch.object(mod, "render_pills"):
            mod.Tray.set_icon(tray, [], False)
            first = tray.indicator.set_icon_full.call_args.args[0]
            mod.Tray.set_icon(tray, [], False)
            second = tray.indicator.set_icon_full.call_args.args[0]
        self.assertNotEqual(first, second)
        self.assertEqual({first, second}, {"state-a", "state-b"})

    def test_forwards_states_and_update_pending_to_render_pills(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        states = [(0.5, "green")]
        with mock.patch.object(mod, "render_pills") as render:
            mod.Tray.set_icon(tray, states, True)
        args, kwargs = render.call_args
        self.assertEqual(args[0], states)
        self.assertTrue(kwargs["update_pending"])


class TestSetTitle(GuiStubbedTestCase):
    def test_forwards_to_the_indicator(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        mod.Tray.set_title(tray, "AI smartbar — 42%")
        tray.indicator.set_title.assert_called_once_with("AI smartbar — 42%")


class TestThreadHandoff(GuiStubbedTestCase):
    """TrayController depends on host.call_on_ui_thread/host.schedule for
    every worker-thread -> UI-thread touch a fetch, switch, remove,
    recapture or check-update makes -- the module docstring calls these
    the highest-risk edits in the whole refactor, since they used to be
    3 separate GLib.idle_add call sites and are now one shared seam."""

    def test_call_on_ui_thread_is_glib_idle_add(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        callback = mock.Mock()
        mod.Tray.call_on_ui_thread(tray, callback, 1, 2)
        mod.GLib.idle_add.assert_called_once_with(callback, 1, 2)

    def test_schedule_fires_once_through_timeout_add_seconds(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        callback = mock.Mock()
        mod.Tray.schedule(tray, 20, callback, "token")
        mod.GLib.timeout_add_seconds.assert_called_once()
        seconds, fire = mod.GLib.timeout_add_seconds.call_args.args
        self.assertEqual(seconds, 20)
        result = fire()
        callback.assert_called_once_with("token")
        self.assertFalse(result, "a one-shot timer must not ask GLib to "
                        "repeat it")


class TestNotify(GuiStubbedTestCase):
    """urgency's icon-name mapping and the notify-send fallback when
    libnotify could not be initialised (see _init_notify)."""

    def test_critical_urgency_uses_the_warning_icon_via_libnotify(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray._libnotify = mock.MagicMock()
        alert = types.SimpleNamespace(title="Low", body="80% used")
        mod.Tray.notify(tray, alert, "critical")
        tray._libnotify.Notification.new.assert_called_once_with(
            "Low", "80% used", "dialog-warning")
        tray._libnotify.Notification.new.return_value.show.assert_called_once()

    def test_normal_urgency_uses_the_information_icon(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray._libnotify = mock.MagicMock()
        alert = types.SimpleNamespace(title="AI smartbar", body="Up to date")
        mod.Tray.notify(tray, alert, "normal")
        args = tray._libnotify.Notification.new.call_args.args
        self.assertEqual(args[2], "dialog-information")

    def test_falls_back_to_notify_send_when_libnotify_is_unavailable(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        alert = types.SimpleNamespace(title="Low", body="80% used")
        # Fire-and-forget Popen (audit B11): a synchronous run() blocked the
        # GTK loop while the notification daemon was being activated.
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            mod.Tray.notify(tray, alert, "critical")
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0],
                         ["notify-send", "-u", "critical", "Low", "80% used"])
        self.assertTrue(popen.call_args.kwargs.get("start_new_session"))

    def test_a_notification_failure_is_swallowed_not_raised(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        alert = types.SimpleNamespace(title="Low", body="80% used")
        with mock.patch.object(mod.subprocess, "Popen",
                               side_effect=OSError("boom")):
            mod.Tray.notify(tray, alert, "critical")   # must not raise


class TestCheckUpdateArgv(GuiStubbedTestCase):
    def test_returns_the_launcher_with_check_update_json(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        self.assertEqual(mod.Tray.check_update_argv(tray),
                         [mod.LAUNCHER, "--check-update", "--json"])


class TestPanelTriadDelegatesToPopover(GuiStubbedTestCase):
    def test_has_panel_true_when_a_popover_exists(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        self.assertTrue(tray.has_panel)

    def test_has_panel_false_when_no_popover_could_be_built(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.popover = None
        self.assertFalse(tray.has_panel)

    def test_show_hide_visible_and_refresh_all_forward_to_the_popover(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        mod.Tray.show_panel(tray)
        tray.popover.show_panel.assert_called_once()
        mod.Tray.hide_panel(tray)
        tray.popover.hide_panel.assert_called_once()
        tray.popover.get_visible.return_value = True
        self.assertTrue(mod.Tray.panel_visible(tray))
        mod.Tray.refresh_panel(tray)
        tray.popover.refresh_layout.assert_called_once()


class TestRebuildMenuPendingSwap(GuiStubbedTestCase):
    """Swapping the menu out from under the pointer closes it on some
    shells: a mapped (open) menu must hold the rebuild in pending_menu
    instead of installing it, and the 'hide' signal installs it later."""

    def test_an_open_menu_holds_the_rebuild_until_it_hides(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        old_menu = mock.MagicMock(name="old menu")
        old_menu.get_mapped.return_value = True
        tray.menu = old_menu
        new_menu = mock.MagicMock(name="new menu")
        with mock.patch.object(tray, "_build_menu", return_value=new_menu):
            mod.Tray.rebuild_menu(tray)
        self.assertIs(tray.menu, old_menu, "must not swap out from under "
                      "an open menu")
        self.assertIs(tray.pending_menu, new_menu)

    def test_a_closed_menu_installs_the_rebuild_immediately(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        old_menu = mock.MagicMock(name="old menu")
        old_menu.get_mapped.return_value = False
        tray.menu = old_menu
        new_menu = mock.MagicMock(name="new menu")
        with mock.patch.object(tray, "_build_menu", return_value=new_menu), \
             mock.patch.object(tray, "_install_menu") as install:
            mod.Tray.rebuild_menu(tray)
        install.assert_called_once_with(new_menu)

    def test_hiding_installs_a_pending_rebuild(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        pending = mock.MagicMock(name="pending menu")
        tray.pending_menu = pending
        with mock.patch.object(tray, "_install_menu") as install:
            mod.Tray._on_menu_hide(tray, None)
        install.assert_called_once_with(pending)

    def test_hiding_with_nothing_pending_is_a_no_op(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.pending_menu = None
        with mock.patch.object(tray, "_install_menu") as install:
            mod.Tray._on_menu_hide(tray, None)
        install.assert_not_called()


class TestControllerDelegation(GuiStubbedTestCase):
    """These call sites are pure delegation onto the controller -- the
    decision logic behind each one is pinned once in
    tests/test_tray_controller.py. What matters here is only that the
    toolkit-facing method reaches the RIGHT controller call with the
    right arguments."""

    def test_on_switch_delegates_with_the_flip_callable(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller = mock.MagicMock(name="controller")
        mod.Tray._on_switch(tray, None, 3)
        tray.controller.on_switch.assert_called_once_with(
            3, tray._flip_active_optimistically)

    def test_on_check_update_delegates(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller = mock.MagicMock(name="controller")
        mod.Tray._on_check_update(tray, None)
        tray.controller._on_check_update.assert_called_once_with()

    def test_on_refresh_delegates(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller = mock.MagicMock(name="controller")
        mod.Tray._on_refresh(tray, None)
        tray.controller._start_fetch.assert_called_once_with()

    def test_refresh_popover_action_delegates(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller = mock.MagicMock(name="controller")
        mod.Tray._on_popover_action(tray, "refresh")
        tray.controller._start_fetch.assert_called_once_with()

    def test_confirm_remove_action_delegates_to_on_remove(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller = mock.MagicMock(name="controller")
        tray.confirm = "claude:5"
        mod.Tray._on_popover_action(tray, "confirm-remove:claude:5")
        tray.controller.on_remove.assert_called_once_with("claude:5")
        self.assertEqual(tray.confirm, "")

    def test_switch_action_delegates_to_on_switch(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        with mock.patch.object(tray, "_on_switch") as on_switch:
            mod.Tray._on_popover_action(tray, "switch:2")
        on_switch.assert_called_once_with(None, 2)

    def test_kill_hit_arms_the_in_row_confirm(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        mod.Tray._on_popover_action(tray, "kill:100:1000")
        self.assertEqual(tray.confirm, "100:1000")

    def test_confirm_kill_delegates_to_on_kill_and_clears_confirm(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller = mock.MagicMock(name="controller")
        tray.confirm = "100:1000"
        mod.Tray._on_popover_action(tray, "confirm-kill:100:1000")
        tray.controller.on_kill.assert_called_once_with("100:1000")
        self.assertEqual(tray.confirm, "")

    def test_cancel_kill_clears_confirm(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.confirm = "100:1000"
        mod.Tray._on_popover_action(tray, "cancel-kill")
        self.assertEqual(tray.confirm, "")

    def test_popover_layout_forwards_the_system_payload(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.controller.system = {"leftovers": {"rows": []},
                                  "busy": {"rows": []}}
        with mock.patch.object(mod.popover_layout, "build") as build:
            mod.Tray._popover_layout(tray)
        self.assertIs(build.call_args.kwargs["system"], tray.controller.system)


class TestQuit(GuiStubbedTestCase):
    def test_leaves_presence_before_stopping_the_loop(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        calls = []
        with mock.patch.object(mod.presence_client, "leave",
                               side_effect=lambda: calls.append("leave")), \
             mock.patch.object(mod.Gtk, "main_quit",
                               side_effect=lambda: calls.append("quit")):
            mod.Tray._quit(tray)
        self.assertEqual(calls, ["leave", "quit"])

    def test_removes_the_pid_file_so_a_stale_pid_is_never_signalled(self):
        # Regression target: --open-panel reads PID_FILE and signals
        # whatever PID it names. Leaving it behind after a clean quit would
        # eventually point at some unrelated process the OS recycled that
        # PID onto.
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        with mock.patch.object(mod, "_remove_pid_file") as remove, \
             mock.patch.object(mod.presence_client, "leave"), \
             mock.patch.object(mod.Gtk, "main_quit"):
            mod.Tray._quit(tray)
        remove.assert_called_once_with()


class TestOpenPanelSignal(GuiStubbedTestCase):
    """SIGUSR1 (via GLib.unix_signal_add, wired in main()) is the CLI hotkey
    helper's (bin/ai-smartbar --open-panel) way into an already-running
    tray -- see PID_FILE's own comment and the design doc. What matters
    here: the handler reaches the exact same _on_open a menu click or
    middle-click does (not a parallel, drifting code path), and it never
    stops GLib from calling it again."""

    def test_delegates_to_on_open_and_keeps_watching(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        with mock.patch.object(tray, "_on_open") as on_open:
            result = mod.Tray._on_open_panel_signal(tray)
        on_open.assert_called_once_with(None)
        self.assertTrue(result, "returning a falsy value tells GLib to "
                        "stop watching for SIGUSR1 after the first one")

    def test_a_missing_popover_does_not_crash_the_handler(self):
        mod = _reimport("smartbar.linux.tray")
        tray = _bare_tray(mod)
        tray.popover = None
        result = mod.Tray._on_open_panel_signal(tray)   # must not raise
        self.assertTrue(result)


class TestPidFile(GuiStubbedTestCase):
    """_write_pid_file/_remove_pid_file: the on-disk half of the open-panel
    hotkey's Linux CLI path. Both are best-effort (never fatal — a tray
    that can't manage its PID file still works from the icon itself), so
    what is worth pinning is the happy path's actual content and the
    quiet-degrade-on-OSError shape, not any GTK/AppIndicator behaviour."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_write_then_remove_round_trips_this_processes_pid(self):
        mod = _reimport("smartbar.linux.tray")
        pid_path = os.path.join(self.tmp.name, "tray.pid")
        with mock.patch.object(mod, "PID_FILE", pid_path):
            mod._write_pid_file()
            with open(pid_path) as handle:
                self.assertEqual(handle.read(), str(mod.os.getpid()))
            mod._remove_pid_file()
            self.assertFalse(os.path.exists(pid_path))

    def test_remove_without_a_prior_write_does_not_raise(self):
        mod = _reimport("smartbar.linux.tray")
        pid_path = os.path.join(self.tmp.name, "never-written.pid")
        with mock.patch.object(mod, "PID_FILE", pid_path):
            mod._remove_pid_file()   # must not raise

    def test_write_failure_is_logged_not_raised(self):
        # An unwritable CACHE_DIR (permissions, read-only filesystem, a
        # race with a directory that vanished) must degrade the CLI hotkey
        # helper, not the tray itself.
        mod = _reimport("smartbar.linux.tray")
        bogus_path = os.path.join(self.tmp.name, "no-such-dir", "tray.pid")
        with mock.patch.object(mod, "PID_FILE", bogus_path), \
             mock.patch.object(mod.log, "exception") as fake_exception:
            mod._write_pid_file()   # must not raise
        fake_exception.assert_called_once()


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
