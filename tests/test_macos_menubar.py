"""Tests for smartbar/macos/menubar.py, with rumps stubbed out.

The fetch/apply/alert/recapture/check-update STATE MACHINE this file used
to own directly now lives in smartbar.core.tray_controller (TrayController)
and is pinned once, for all three front-ends, in
tests/test_tray_controller.py. What is left here is the TOOLKIT BINDING:
does a click route to the right controller call, does a menu row carry the
right label/callback, does SmartBarApp's own implementation of the
TrayHost contract (set_icon/set_title/rebuild_menu/call_on_ui_thread/
schedule/notify/check_update_argv) do the rumps-specific thing it claims
to. It cannot be imported on the machine this suite normally runs on
(rumps is macOS-only and not installed), so a fake rumps goes into
sys.modules first — the same approach tests/test_windows_tray.py uses for
pystray/tkinter.

What is pinned here, and why:

  1. The module imports at all under a fake rumps.
  2. call_on_ui_thread/schedule/_drain_ui: the worker -> UI queue+timer poll
     rumps' lack of an idle_add equivalent forces, including that
     _drain_ui keeps draining after one queued callback raises — otherwise
     a single bad update would strand every later fetch's result behind
     it.
  3. set_icon is a genuine no-op (this file has never rendered pixel icon
     state) and set_title ignores the generic text it is handed and
     renders model.macos_title from the controller's own state instead —
     see menubar.py's own module docstring for why.
  4. notify maps a shared Alert(title, body) onto rumps.notification's
     3-field (title, subtitle, body) call with subtitle always "", and
     swallows a failing call so it can never abort whatever the caller
     does next.
  5. _rebuild_menu turns controller state (accounts, openai, update_pending
     /update_blocked, the check row, a sticky switch_error) into the right
     rumps.MenuItem rows with the right callback-or-None.
  6. The switch flow (_make_switch/_apply_switch_error) is DELIBERATELY
     NOT routed through TrayController.on_switch/_set_action_error — see
     menubar.py's own module docstring for the blocker this hits (that
     shared path neither notifies nor rebuilds the menu, which this
     front-end's panel-less UI depends on for a failure to be visible at
     all). Covered here in full, since nothing in test_tray_controller.py
     exercises it.

Deliberately NOT covered here (pinned once in tests/test_tray_controller.py
instead): the generation guard, _apply_snapshot/_apply_error's field
mutations and call ordering, the check-update three-state stickiness and
its stale-token guards, RecapturePolicy pacing, and _pending_update's
delegation to update_runner.

Deliberately NOT covered: anything about a real NSStatusBar, real menu
rendering, or NSTimer scheduling. A test built on the fake rumps would be
exercising the fake.
"""
from __future__ import annotations

import importlib
import os
import sys
import unittest
from unittest import mock

import smartbar
from tests.support import stubs

SWIFT_STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(smartbar.__file__)),
                                "macos-swift", "Sources", "AISmartbar", "UsageStore.swift")


class _FakeApp:
    """A real class, not a MagicMock: SmartBarApp subclasses rumps.App at
    class-definition time, and a MagicMock instance cannot be a base class."""

    def __init__(self, title="", quit_button=None):
        self.title = title
        self.menu = []


class _FakeTimer:
    """Records rather than schedules. Tests drive the callbacks by hand."""

    started = []

    def __init__(self, callback, interval):
        self.callback = callback
        self.interval = interval

    def start(self):
        _FakeTimer.started.append(self)

    def stop(self):
        pass


class _FakeMenuItem:
    def __init__(self, title, callback=None):
        self.title = title
        self.callback = callback


def _install_fake_rumps():
    return stubs.install_rumps(app_cls=_FakeApp, timer_cls=_FakeTimer,
                               menuitem_cls=_FakeMenuItem)


class MenubarTestCase(unittest.TestCase):
    """Snapshots the WHOLE of sys.modules, not just the "rumps" key.

    Importing menubar drags in smartbar.macos.menubar itself plus anything it
    imports for the first time; restoring only "rumps" would leave a module
    behind that was compiled against the fake.
    """

    def setUp(self):
        self._modules = dict(sys.modules)
        self.addCleanup(lambda: (sys.modules.clear(),
                                 sys.modules.update(self._modules)))
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(),
                                 os.environ.update(self._env)))
        os.environ["SMARTBAR_PRESENCE"] = "off"
        _FakeTimer.started = []
        self.rumps = _install_fake_rumps()
        sys.modules.pop("smartbar.macos.menubar", None)
        self.menubar = importlib.import_module("smartbar.macos.menubar")

    def build(self):
        """A SmartBarApp whose constructor starts no threads and reads no state."""
        with mock.patch.object(self.menubar.threading, "Thread") as thread, \
             mock.patch("smartbar.update_runner.pending_for_ui",
                        return_value=("", "")):
            app = self.menubar.SmartBarApp()
        # Nothing may have actually run: the constructor's own _tick must be
        # a spawned thread, never inline work.
        thread.assert_called()
        return app


def account(number, email="a@x.com", active=False, status="ok", metrics=None):
    from smartbar.core import model
    return model.Account(number=number, email=email, active=active, status=status,
                         ok=(status == "ok"),
                         metrics=metrics if metrics is not None else [])


def snapshot(*accounts):
    from smartbar.core import model
    snap = model.Snapshot()
    snap.accounts = list(accounts) or [account(1, "a@x.com", active=True)]
    return snap


class TestImportsAndBuilds(MenubarTestCase):
    def test_the_module_imports_under_a_fake_rumps(self):
        self.assertTrue(hasattr(self.menubar, "SmartBarApp"))
        self.assertTrue(hasattr(self.menubar, "log"))

    def test_it_has_a_logger_configured_to_a_file_in_main(self):
        """The bug this prevents: a front-end that swallows in total silence.

        menubar.py used to import no logging at all, so every except left
        nothing behind on the one platform whose header admits it has never
        run on real hardware.
        """
        import logging
        self.assertIsInstance(self.menubar.log, logging.Logger)
        self.assertTrue(self.menubar.LOG_FILE.endswith("tray.log"))

    def test_building_wires_a_tray_controller_as_its_own_host(self):
        from smartbar.core.tray_controller import TrayController
        app = self.build()
        self.assertIsInstance(app.controller, TrayController)
        self.assertIs(app.controller.host, app)


# --- TrayHost: thread -> UI handoff -----------------------------------------

class TestThreadHandoff(MenubarTestCase):
    def test_call_on_ui_thread_queues_rather_than_runs_inline(self):
        app = self.build()
        seen = []
        app.call_on_ui_thread(seen.append, "queued")
        self.assertEqual(seen, [])
        app._drain_ui(None)
        self.assertEqual(seen, ["queued"])

    def test_one_raising_callback_does_not_strand_the_rest(self):
        """Otherwise a single bad update freezes every later fetch's result."""
        app = self.build()
        seen = []
        app.call_on_ui_thread(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        app.call_on_ui_thread(seen.append, "second")
        app._drain_ui(None)
        self.assertEqual(seen, ["second"])
        self.assertTrue(app._ui_queue.empty())

    def test_schedule_fires_call_on_ui_thread_after_a_real_delay(self):
        """schedule() has to reach the UI thread through the same queued
        path as everything else — a bare threading.Timer target would
        touch app state straight from the timer thread."""
        app = self.build()
        with mock.patch.object(self.menubar.threading, "Timer") as timer_cls:
            app.schedule(20, app.call_on_ui_thread, "unused")
        timer_cls.assert_called_once()
        args, kwargs = timer_cls.call_args
        self.assertEqual(args[0], 20)
        self.assertEqual(kwargs["args"][0], app.call_on_ui_thread)
        self.assertTrue(timer_cls.return_value.daemon)
        timer_cls.return_value.start.assert_called_once()


# --- TrayHost: notify --------------------------------------------------------

class TestNotify(MenubarTestCase):
    def test_maps_a_shared_alert_onto_the_three_field_rumps_call(self):
        from smartbar.core.alerts import Alert
        app = self.build()
        app.notify(Alert(title="AI smartbar update", body="1.2.3 is ready."))
        self.rumps.notification.assert_called_once_with(
            "AI smartbar update", "", "1.2.3 is ready.")

    def test_a_failing_notification_is_caught_and_logged_not_raised(self):
        from smartbar.core.alerts import Alert
        app = self.build()
        self.rumps.notification.side_effect = RuntimeError("no bundle id")
        app.notify(Alert(title="t", body="b"))   # must not raise


# --- TrayHost: icon / title ---------------------------------------------------

class TestSetIcon(MenubarTestCase):
    def test_set_icon_is_a_documented_no_op(self):
        """This file has never rendered pixel icon state; the controller
        still calls this uniformly across all three hosts."""
        app = self.build()
        before = app.title
        app.set_icon([(0.5, "green")], True)   # must not raise
        self.assertEqual(app.title, before)


class TestSetTitle(MenubarTestCase):
    """set_title ignores the generic `text` it is handed and renders
    model.macos_title from the controller's own state instead -- the
    short glyph+text form the menu bar (the only visible surface here)
    needs, unlike Linux's tooltip or Windows' szTip."""

    def test_below_the_failure_threshold_it_renders_macos_title(self):
        from smartbar.core import model
        app = self.build()
        app.controller.failures = 0
        app.controller.snapshot = snapshot(account(1, "a@x.com", active=True))
        app.set_title("ignored generic text")
        self.assertEqual(app.title, model.macos_title(
            app.controller.snapshot.active_account))

    def test_no_snapshot_yet_renders_macos_title_of_none(self):
        from smartbar.core import model
        app = self.build()
        app.controller.failures = 0
        app.controller.snapshot = None
        app.set_title("ignored")
        self.assertEqual(app.title, model.macos_title(None))

    def test_at_the_failure_threshold_it_shows_the_unknown_glyph_instead(self):
        app = self.build()
        app.controller.failures = 3
        app.controller.snapshot = snapshot(account(1, "a@x.com", active=True))
        app.set_title("AI smartbar — cswap error: boom")
        self.assertEqual(app.title, "⚪ ?")


class TestRebuildMenuDelegates(MenubarTestCase):
    def test_rebuild_menu_calls_the_same_builder_the_constructor_used(self):
        app = self.build()
        with mock.patch.object(type(app), "_rebuild_menu") as inner:
            app.rebuild_menu()
        inner.assert_called_once_with()


# --- TrayHost: the manual check row -----------------------------------------

class TestCheckUpdateArgv(MenubarTestCase):
    def test_no_sys_executable_prefix_unlike_windows(self):
        """POSIX shebang execution needs no interpreter prefix, unlike
        windows/tray.py's check_update_argv."""
        app = self.build()
        self.assertEqual(app.check_update_argv(),
                         [self.menubar.LAUNCHER, "--check-update", "--json"])

    def test_the_check_row_click_delegates_to_the_controller(self):
        app = self.build()
        with mock.patch.object(app.controller, "_on_check_update") as on_check:
            app._on_check_update(None)
        on_check.assert_called_once_with()


class TestTicksDelegateToController(MenubarTestCase):
    def test_the_recurring_tick_delegates(self):
        app = self.build()
        with mock.patch.object(app.controller, "_tick") as tick:
            app._tick(None)
        tick.assert_called_once_with()

    def test_the_presence_tick_beats_with_the_controllers_snapshot(self):
        app = self.build()
        app.controller.snapshot = "sentinel-snapshot"
        with mock.patch.object(self.menubar.presence_client, "beat") as beat:
            app._presence_tick(None)
        beat.assert_called_once_with("sentinel-snapshot")


# --- the controller/host seam ------------------------------------------------

class TestTheRealApplyPathRunsAgainstThisHost(MenubarTestCase):
    """The ONE deliberate exception to this file's "the state machine is
    pinned in test_tray_controller.py, not here" rule.

    Everything else here mocks controller._tick/_start_fetch, and
    test_tray_controller.py drives the real state machine against a FakeHost
    that SUBCLASSES TrayHost. Between them, the real controller never once
    ran against this real host — and that gap hid a live bug: SmartBarApp
    did not declare has_panel, hosts satisfy TrayHost by duck typing so the
    class default could not reach it, and _apply_snapshot's
    `if self.host.has_panel` raised AttributeError on every macOS fetch.
    _drain_ui swallows a failing queued callback by design, so nothing
    crashed: the icon/title/menu written just above that line kept working
    while the limit-alert loop and _maybe_recapture below it silently never
    ran. What is asserted here is therefore not the ordering (that is the
    controller's own test) but REACHABILITY — that the last two steps still
    happen when the real host is on the other end.

    tests/test_tray_host_conformance.py pins the same seam structurally,
    by member set, for all three hosts.
    """

    def _apply(self, app, alerts):
        c = app.controller
        with mock.patch.object(c.alerts, "check", return_value=alerts), \
             mock.patch.object(c, "_maybe_recapture") as recapture, \
             mock.patch.object(self.menubar.presence_client, "beat"), \
             mock.patch("smartbar.presence_client.counts", return_value={}), \
             mock.patch("smartbar.core.plan.plans_by_email", return_value={}), \
             mock.patch("smartbar.core.codex.accounts", return_value=[]), \
             mock.patch("smartbar.update_runner.pending_for_ui",
                        return_value=("", "")):
            c._apply_snapshot(snapshot(), c.generation)
        return recapture

    def test_a_snapshot_reaches_the_alert_loop_and_recapture(self):
        from smartbar.core.alerts import Alert
        app = self.build()
        recapture = self._apply(app, [Alert(title="t", body="b")])
        self.rumps.notification.assert_called_once()
        recapture.assert_called_once()

    def test_a_snapshot_with_no_alerts_still_reaches_recapture(self):
        app = self.build()
        recapture = self._apply(app, [])
        self.rumps.notification.assert_not_called()
        recapture.assert_called_once()

    def test_the_error_path_runs_to_completion_too(self):
        """_apply_error reads host.has_panel on the same terms; nothing
        follows it there, so this pins that it simply does not raise."""
        app = self.build()
        c = app.controller
        with mock.patch.object(app, "_rebuild_menu") as rebuild:
            c._apply_error("cswap exploded", c.generation)
        rebuild.assert_called_once_with()
        self.assertEqual(c.last_error, "cswap exploded")


# --- menu construction --------------------------------------------------------

class TestMenuConstruction(MenubarTestCase):
    def test_loading_state_with_no_snapshot_yet(self):
        app = self.build()
        app.controller.snapshot = None
        app._rebuild_menu()
        self.assertEqual(app.menu[0].title, "Loading…")

    def test_a_failing_fetch_with_no_snapshot_shows_the_error(self):
        # Below the failures>=3 title flip, the menu is macOS's only surface
        # for a failing fetch — it must not sit on "Loading…" like a healthy
        # first launch (mirrors Linux/Windows).
        app = self.build()
        app.controller.snapshot = None
        app.controller.failures = 1
        app._rebuild_menu()
        self.assertEqual(app.menu[0].title, "cswap error — see tray.log")

    def test_the_active_row_is_marked_stale_after_a_failed_refresh(self):
        from smartbar.core import model
        app = self.build()
        app.controller.snapshot = snapshot(
            account(1, "active@x.com", active=True),
            account(2, "other@x.com"))
        app.controller.failures = 2      # stale numbers on a kept snapshot
        app._rebuild_menu()
        titles = [item.title for item in app.menu
                  if isinstance(item, self.menubar.rumps.MenuItem)]
        active_row = model.menu_row(app.controller.snapshot.accounts[0])
        other_row = model.menu_row(app.controller.snapshot.accounts[1])
        self.assertIn(active_row + "  (stale)", titles)   # active row marked
        self.assertIn(other_row, titles)                  # others untouched

    def test_the_active_row_and_a_dead_credential_are_not_clickable(self):
        from smartbar.core import model
        app = self.build()
        app.controller.snapshot = snapshot(
            account(1, "active@x.com", active=True),
            account(2, "dead@x.com", status="relogin_required"),
            account(3, "healthy@x.com"))
        app._rebuild_menu()
        rows = {item.title: item.callback for item in app.menu
               if isinstance(item, self.menubar.rumps.MenuItem)}
        active_row = model.menu_row(app.controller.snapshot.accounts[0])
        dead_row = model.menu_row(app.controller.snapshot.accounts[1])
        healthy_row = model.menu_row(app.controller.snapshot.accounts[2])
        self.assertIsNone(rows[active_row])
        self.assertIsNone(rows[dead_row])
        self.assertIsNotNone(rows[healthy_row])

    def test_openai_accounts_render_as_a_read_only_section(self):
        from smartbar.core import model
        app = self.build()
        snap = snapshot(account(1, "a@x.com", active=True))
        snap.openai = [model.Account(number=0, email="b@x.com", provider="openai")]
        app.controller.snapshot = snap
        app._rebuild_menu()
        titles = [item.title for item in app.menu if item is not None]
        self.assertIn("OpenAI", titles)
        openai_row = next(item for item in app.menu
                          if item is not None and "b@x.com" in item.title)
        self.assertIsNone(openai_row.callback)

    def test_update_pending_row_is_clickable_and_triggers_apply(self):
        app = self.build()
        app.controller.snapshot = snapshot()
        app.controller.update_pending = "9.9.9"
        app._rebuild_menu()
        row = next(i for i in app.menu if i is not None and "9.9.9" in i.title)
        self.assertEqual(row.callback, app._on_update)

    def test_update_blocked_row_has_no_callback(self):
        app = self.build()
        app.controller.snapshot = snapshot()
        app.controller.update_pending = ""
        app.controller.update_blocked = "dirty checkout"
        app._rebuild_menu()
        row = next(i for i in app.menu
                  if i is not None and "dirty checkout" in i.title)
        self.assertIsNone(row.callback)

    def test_the_check_row_reflects_the_controllers_three_states(self):
        app = self.build()
        app.controller.snapshot = snapshot()

        app.controller.checking = False
        app.controller.check_result = ""
        app._rebuild_menu()
        row = next(i for i in app.menu if i is not None and "Check for updates" in i.title)
        self.assertEqual(row.callback, app._on_check_update)

        app.controller.checking = True
        app._rebuild_menu()
        row = next(i for i in app.menu if i is not None and "Checking" in i.title)
        self.assertIsNone(row.callback)

        app.controller.checking = False
        app.controller.check_result = "✓ Up to date"
        app._rebuild_menu()
        row = next(i for i in app.menu if i is not None and i.title == "✓ Up to date")
        self.assertIsNone(row.callback)

    def test_the_trailing_rows_are_always_present(self):
        app = self.build()
        app.controller.snapshot = snapshot()
        app._rebuild_menu()
        titles = [i.title for i in app.menu if i is not None]
        self.assertIn("⟳ Refresh now", titles)
        self.assertIn("⚙ Open cswap TUI", titles)
        self.assertIn("⏻ Quit", titles)


# --- the switch flow: deliberately host-owned, not on TrayController ---------

class TestSwitchFailureIsNoLongerSilent(MenubarTestCase):
    """_make_switch/_apply_switch_error stay host-owned rather than routing
    through TrayController.on_switch/_set_action_error -- see menubar.py's
    own module docstring for why (that shared path neither notifies nor
    rebuilds the menu, which this front-end's panel-less UI needs for a
    failure to be visible at all). Mirrors UsageStore.swift's sticky
    switchError -- cleared only when a switch is attempted again, not by
    the next periodic refresh -- because that refresh is the one thing
    that must NOT erase the very error it is about to redraw around.
    """

    def _run_switch(self, app, number, thread):
        """Drive _make_switch's callback and its worker synchronously."""
        app._make_switch(number)(None)
        thread.call_args.kwargs["target"]()

    def test_a_failed_switch_sets_a_sticky_row_and_notifies(self):
        app = self.build()
        with mock.patch.object(self.menubar.threading, "Thread") as thread, \
             mock.patch.object(self.menubar.cswap, "switch",
                               side_effect=self.menubar.cswap.CswapError("in use")), \
             mock.patch.object(app.controller, "_start_fetch"):
            self._run_switch(app, 2, thread)
            # Not applied yet: the failure crossed back through
            # call_on_ui_thread, same as every other worker -> UI handoff.
            self.assertEqual(app.switch_error, "")
            app._drain_ui(None)
        self.assertEqual(app.switch_error, "Switch failed: in use")
        self.rumps.notification.assert_called_once()
        title, subtitle, body = self.rumps.notification.call_args.args
        self.assertEqual(title, "AI smartbar")
        self.assertIn("in use", body)

    def test_the_sticky_row_sits_above_the_account_list(self):
        """Closest rumps analogue to switchError's slot in PopoverView.swift,
        which sits above the account list -- this text menu has no header
        row to place it under."""
        app = self.build()
        app.switch_error = "Switch failed: in use"
        app.controller.snapshot = snapshot()
        app._rebuild_menu()
        self.assertEqual(app.menu[0].title, "✕ Switch failed: in use")

    def test_a_new_switch_attempt_clears_the_previous_sticky_error(self):
        """The clear happens the instant the row is clicked (main thread),
        before the worker even starts -- the optimistic clear UsageStore
        does in switchTo's `switchError = nil`, ahead of its own Task."""
        app = self.build()
        app.switch_error = "Switch failed: in use"
        with mock.patch.object(self.menubar.threading, "Thread"), \
             mock.patch.object(self.menubar.cswap, "switch"):
            app._make_switch(2)(None)
        self.assertEqual(app.switch_error, "")

    def test_a_successful_switch_never_notifies(self):
        app = self.build()
        with mock.patch.object(self.menubar.threading, "Thread") as thread, \
             mock.patch.object(self.menubar.cswap, "switch"), \
             mock.patch.object(app.controller, "_start_fetch"):
            self._run_switch(app, 2, thread)
            app._drain_ui(None)
        self.assertEqual(app.switch_error, "")
        self.rumps.notification.assert_not_called()

    def test_every_attempt_refetches_win_or_lose(self):
        """TrayController._start_fetch is safe from any thread (see its
        own docstring) -- called directly from the worker, unlike the old
        code's marshal-then-_tick indirection, with the same end result:
        a new fetch generation either way."""
        app = self.build()
        with mock.patch.object(self.menubar.threading, "Thread") as thread, \
             mock.patch.object(self.menubar.cswap, "switch",
                               side_effect=self.menubar.cswap.CswapError("in use")), \
             mock.patch.object(app.controller, "_start_fetch") as start_fetch:
            self._run_switch(app, 2, thread)
        start_fetch.assert_called_once_with()

    def test_the_failure_wording_matches_the_swift_store_word_for_word(self):
        """A reworded message in one front-end and not the other is drift a
        reader would never notice."""
        with open(SWIFT_STORE_PATH, encoding="utf-8") as handle:
            swift = handle.read()
        self.assertIn('"Switch failed: \\(failure)"', swift)
        with open(self.menubar.__file__, encoding="utf-8") as handle:
            mac = handle.read()
        self.assertIn('f"Switch failed: {message}"', mac)


if __name__ == "__main__":
    unittest.main()
