"""Tests for smartbar/macos/menubar.py, with rumps stubbed out.

This module had no tests at all, which is how it ended up as the outlier
among the three Python front-ends: no logging, no worker -> UI marshal, no
generation guard on fetches, and an unguarded rumps.notification() sitting
directly above the recapture call it could skip. It cannot be imported on the
machine this suite normally runs on (rumps is macOS-only and not installed),
so a fake rumps goes into sys.modules first — the same approach
tests/test_windows_tray.py uses for pystray/tkinter.

What is pinned here, and why:

  1. The module imports at all under a fake rumps.
  2. The generation guard: a snapshot from a fetch that started before an
     account switch must be DROPPED, not applied. Without it a slow
     pre-switch fetch silently puts the old account back on screen.
  3. _fetch (worker thread) hands its result to _to_main rather than
     touching self.title/self.menu itself. Asserted by calling _fetch with
     the UI methods replaced by tripwires.
  4. _drain_ui keeps draining after one queued callback raises — otherwise a
     single bad update would strand every later fetch's result behind it.
  5. A failing notification does not stop the work after it.
  6. The _check_row three-state label, and that its wording matches
     linux/tray.py's — read from that file's source at test time, never
     retyped, so a row reworded in one file and forgotten in the other fails
     a test instead of drifting.

Deliberately NOT covered: anything about a real NSStatusBar, real menu
rendering, or NSTimer scheduling. A test built on the fake rumps would be
exercising the fake.
"""
from __future__ import annotations

import importlib
import os
import queue
import sys
import types
import unittest
from unittest import mock

import smartbar

LINUX_TRAY_PATH = os.path.join(os.path.dirname(smartbar.__file__), "linux",
                               "tray.py")


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
    fake = types.ModuleType("rumps")
    fake.App = _FakeApp
    fake.Timer = _FakeTimer
    fake.MenuItem = _FakeMenuItem
    fake.notification = mock.Mock()
    fake.quit_application = mock.Mock()
    sys.modules["rumps"] = fake
    return fake


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
             mock.patch.object(self.menubar, "_noop_marker", create=True), \
             mock.patch("smartbar.update_runner.pending_for_ui",
                        return_value=("", "")):
            app = self.menubar.SmartBarApp()
        # Nothing may have actually run: the constructor's own _tick must be
        # a spawned thread, never inline work.
        thread.assert_called()
        return app


def snapshot(active_email="a@x.com"):
    from smartbar.core import model
    acct = model.Account(number=1, email=active_email, ok=True, status="ok",
                         active=True)
    snap = model.Snapshot()
    snap.accounts.append(acct)
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


class TestGenerationGuard(MenubarTestCase):
    """A slow pre-switch fetch must not land after the switch."""

    def test_a_superseded_snapshot_is_dropped(self):
        app = self.build()
        app.generation = 7
        app.title = "kept"
        app._apply_snapshot(snapshot("stale@x.com"), 6)
        self.assertIsNone(app.snapshot)
        self.assertEqual(app.title, "kept")

    def test_the_current_snapshot_is_applied(self):
        app = self.build()
        app.generation = 7
        with mock.patch("smartbar.update_runner.pending_for_ui",
                        return_value=("", "")):
            app._apply_snapshot(snapshot("fresh@x.com"), 7)
        self.assertIsNotNone(app.snapshot)
        self.assertEqual(app.snapshot.accounts[0].email, "fresh@x.com")

    def test_a_superseded_error_does_not_bump_the_failure_count(self):
        app = self.build()
        app.generation = 4
        app._apply_error(3)
        self.assertEqual(app.failures, 0)

    def test_each_tick_takes_a_new_generation(self):
        app = self.build()
        before = app.generation
        with mock.patch.object(self.menubar.threading, "Thread"):
            app._tick(None)
        self.assertEqual(app.generation, before + 1)


class TestWorkerNeverTouchesAppKit(MenubarTestCase):
    """_fetch runs on a daemon thread. AppKit is not thread-safe for UI
    mutation, so the result has to go through _to_main."""

    def test_fetch_hands_the_snapshot_to_the_main_thread(self):
        app = self.build()
        snap = snapshot()
        tripwire = mock.Mock(side_effect=AssertionError("touched UI directly"))
        with mock.patch.object(self.menubar.cswap, "fetch", return_value=snap), \
             mock.patch.object(self.menubar.presence, "apply_counts"), \
             mock.patch.object(self.menubar.plan, "apply_plans"), \
             mock.patch.object(self.menubar.codex, "accounts", return_value=[]), \
             mock.patch.object(self.menubar.presence_client, "beat"), \
             mock.patch.object(type(app), "_rebuild_menu", tripwire):
            app._fetch(app.generation)
        queued = app._ui_queue.get_nowait()
        # __func__, not the bound method: attribute access builds a NEW bound
        # method object every time, so `is` can never hold on one.
        self.assertIs(queued[0].__func__, type(app)._apply_snapshot)
        self.assertIs(queued[1][0], snap)

    def test_a_failed_fetch_also_goes_through_the_queue(self):
        app = self.build()
        with mock.patch.object(self.menubar.cswap, "fetch",
                               side_effect=self.menubar.cswap.CswapError("nope")):
            app._fetch(app.generation)
        queued = app._ui_queue.get_nowait()
        self.assertIs(queued[0].__func__, type(app)._apply_error)


class TestDrainSurvivesABadCallback(MenubarTestCase):
    def test_one_raising_callback_does_not_strand_the_rest(self):
        """Otherwise a single bad update freezes every later fetch's result."""
        app = self.build()
        seen = []
        app._to_main(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        app._to_main(seen.append, "second")
        app._drain_ui(None)
        self.assertEqual(seen, ["second"])
        self.assertTrue(app._ui_queue.empty())


class TestNotificationsAreGuarded(MenubarTestCase):
    def test_a_failing_notification_does_not_stop_the_work_after_it(self):
        """rumps.notification raises with no bundle id or no permission —
        both normal for a locally built app. Unguarded it skipped the
        _maybe_recapture call directly below it in _apply_snapshot."""
        app = self.build()
        app.generation = 1
        self.rumps.notification.side_effect = RuntimeError("no bundle id")
        alert = types.SimpleNamespace(title="t", body="b")
        with mock.patch.object(app.alerts, "check", return_value=[alert]), \
             mock.patch.object(type(app), "_maybe_recapture") as recapture, \
             mock.patch("smartbar.update_runner.pending_for_ui",
                        return_value=("", "")):
            app._apply_snapshot(snapshot(), 1)
        recapture.assert_called_once()


class TestCheckUpdateRow(MenubarTestCase):
    """macOS had no manual update-check row at all; Linux and Windows both
    do, so a rumps device sat up to 6h behind with no way to ask."""

    def test_the_row_has_three_states(self):
        app = self.build()
        label, callback = app._check_row()
        self.assertEqual(label, "⇅ Check for updates")
        self.assertIsNotNone(callback)

        app.checking = True
        label, callback = app._check_row()
        self.assertEqual(label, "⇅ Checking for updates…")
        self.assertIsNone(callback)   # un-clickable while in flight

        app.checking = False
        app.check_result = "✓ Up to date"
        label, callback = app._check_row()
        self.assertEqual(label, "✓ Up to date")
        self.assertIsNone(callback)

    def test_its_labels_match_the_linux_tray_word_for_word(self):
        """Source-scraped, not retyped: a reworded row on one front-end and
        not the other is drift a reader would never notice."""
        with open(LINUX_TRAY_PATH, encoding="utf-8") as handle:
            linux = handle.read()
        for label in ("⇅ Check for updates", "⇅ Checking for updates…"):
            self.assertIn(label, linux)
        with open(self.menubar.__file__, encoding="utf-8") as handle:
            mac = handle.read()
        for label in ("⇅ Check for updates", "⇅ Checking for updates…"):
            self.assertIn(label, mac)

    def test_a_stale_check_result_is_ignored(self):
        app = self.build()
        app.check_token = 3
        app.checking = True
        app._checked(2, {"label": "✓ Up to date"})
        self.assertTrue(app.checking)      # untouched by the superseded reply
        self.assertEqual(app.check_result, "")

    def test_a_broken_reply_becomes_the_check_failed_row(self):
        app = self.build()
        app.check_token = 1
        with mock.patch("smartbar.update_runner.pending_for_ui",
                        return_value=("", "")), \
             mock.patch.object(self.menubar.threading, "Timer"):
            app._checked(1, None)
        self.assertEqual(app.check_result, "✕ Check failed")
        self.assertFalse(app.checking)


class TestPendingUpdateIsShared(MenubarTestCase):
    def test_it_delegates_to_the_one_shared_reader(self):
        """All three front-ends had their own copy of this; the macOS one had
        already lost the blocked half."""
        app = self.build()
        with mock.patch("smartbar.update_runner.pending_for_ui",
                        return_value=("9.9.9", "dirty checkout")) as reader:
            self.assertEqual(app._pending_update(), "9.9.9")
        reader.assert_called_once_with()
        self.assertEqual(app.update_blocked, "dirty checkout")


if __name__ == "__main__":
    unittest.main()
