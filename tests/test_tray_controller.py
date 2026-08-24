"""Tests for smartbar/core/tray_controller.py.

This is the "pin it once" file the design that produced tray_controller.py
was written for: linux/tray.py, windows/tray.py and macos/menubar.py each
used to carry their own copy of this state machine, and
tests/test_linux_tray.py / tests/test_windows_tray.py / tests/test_macos_
menubar.py each pinned their own copy's behaviour separately. What is
pinned here instead:

  * The generation guard: a fetch (or an error) whose generation no longer
    matches self.generation must be dropped, not applied — the mechanism
    that keeps a slow pre-switch fetch from clobbering an optimistic
    switch guess or a newer fetch's own result.
  * The manual check-update flow's stickiness: three states (idle/
    checking/result), a stale token's answer is ignored, and the result
    clears itself after CHECK_RESULT_SECONDS via whatever the host's own
    delayed-dispatch primitive is.
  * RecapturePolicy's call sites: only a non-"refresh" action triggers a
    refetch, a failed `cswap add` is swallowed (logged, not raised).
  * Every UI touch that originates on a worker thread (a fetch, a check,
    a switch, a remove, a recapture) reaches `self.host` only through
    `host.call_on_ui_thread`, never directly — TestEveryUiTouchIsMarshalled
    below is the test the task's own instructions asked for by name.

FakeHost's `call_on_ui_thread`/`schedule` are plain recorders, NOT auto-
runners — the same deliberate choice tests/support/stubs.py's fake
GLib.idle_add already makes (see its own comment): a test has to be able
to tell "queued for the UI thread" apart from "ran inline on the worker
thread". `drain()`/`run_scheduled()` below play those queues back
explicitly, mirroring macOS's own `_drain_ui` poller.

threading.Thread is patched everywhere a controller method spawns one, and
the captured `target` is invoked synchronously in-place — never a real
thread — the same pattern tests/test_linux_tray.py's
`_run_worker_target` already established, so a background body still runs
(and can be asserted on) without ever racing the test itself.
"""
from __future__ import annotations

import ast
import contextlib
import unittest
from pathlib import Path
from unittest import mock

from smartbar.core import model
from smartbar.core import tray_controller as tc
from tests.support import stubs


# --- fixtures ----------------------------------------------------------

def _account(number, email, active=False, status="ok", metrics=None):
    return model.Account(number=number, email=email, active=active,
                         status=status, ok=(status == "ok"),
                         metrics=metrics if metrics is not None else [])


def _snapshot(*accounts):
    return model.Snapshot(accounts=list(accounts))


class FakeHost(tc.TrayHost):
    """Records every call a TrayController makes. `call_on_ui_thread` and
    `schedule` are recorders, not auto-runners — see the module docstring.
    """

    def __init__(self, *, has_panel=False):
        self.has_panel = has_panel
        self.order = []
        self.icon_calls = []
        self.title_calls = []
        self.rebuild_calls = 0
        self.notify_calls = []
        self.ui_queue = []
        self.scheduled = []
        self._panel_visible = False
        self.panel_refreshes = 0
        self.show_calls = 0
        self.hide_calls = 0
        self._argv = ["ai-smartbar", "--check-update", "--json"]

    # -- required contract ------------------------------------------

    def set_icon(self, states, update_pending):
        self.order.append("set_icon")
        self.icon_calls.append((states, update_pending))

    def set_title(self, text):
        self.order.append("set_title")
        self.title_calls.append(text)

    def rebuild_menu(self):
        self.order.append("rebuild_menu")
        self.rebuild_calls += 1

    def call_on_ui_thread(self, callback, *args):
        self.ui_queue.append((callback, args))

    def schedule(self, seconds, callback, *args):
        self.scheduled.append((seconds, callback, args))

    def notify(self, alert, urgency="critical"):
        self.order.append("notify")
        self.notify_calls.append((alert, urgency))

    def check_update_argv(self):
        return list(self._argv)

    # -- optional panel triad -----------------------------------------

    def show_panel(self):
        self.show_calls += 1
        self._panel_visible = True

    def hide_panel(self):
        self.hide_calls += 1
        self._panel_visible = False

    def panel_visible(self):
        return self._panel_visible

    def refresh_panel(self):
        self.order.append("refresh_panel")
        self.panel_refreshes += 1

    # -- test-only helpers ----------------------------------------------

    def drain(self):
        """Run every queued call_on_ui_thread callback, oldest first.
        Draining one can enqueue more (a worker's own call_on_ui_thread
        call fired synchronously by the patched Thread), so this keeps
        going until the queue is actually empty."""
        while self.ui_queue:
            callback, args = self.ui_queue.pop(0)
            callback(*args)

    def run_scheduled(self):
        due, self.scheduled = self.scheduled, []
        for _seconds, callback, args in due:
            callback(*args)


class ControllerTestCase(unittest.TestCase):
    """A TrayController wired to a FakeHost, with every module-level
    side-effecting dependency patched so a test never touches the real
    filesystem, network, or spawns a real subprocess/thread."""

    def setUp(self):
        self.host = FakeHost()
        self.controller = tc.TrayController(self.host)
        self._patches = contextlib.ExitStack()
        self.addCleanup(self._patches.close)
        self.fake_presence_client = self._patches.enter_context(
            mock.patch.object(tc, "presence_client"))
        self.fake_presence_client.counts.return_value = {}
        self._patches.enter_context(
            mock.patch.object(tc.plan, "apply_plans"))
        self._patches.enter_context(
            mock.patch.object(tc.plan, "plans_by_email", return_value={}))
        self._patches.enter_context(
            mock.patch.object(tc.codex, "accounts", return_value=[]))
        self.thread_cls = self._patches.enter_context(
            mock.patch.object(tc.threading, "Thread"))

    def run_last_worker(self):
        """threading.Thread(target=run, ...) was called once; invoke that
        `run` synchronously, as if it were the worker thread."""
        _args, kwargs = self.thread_cls.call_args
        kwargs["target"]()


# --- import hygiene ------------------------------------------------------

class TestModuleImportsWithoutAnyToolkit(unittest.TestCase):
    """The whole point of the core/ seam: this module must be importable
    with none of gi, pystray, tkinter, rumps or cairo present — mirroring
    smartbar/paint/'s own toolkit-free discipline."""

    def test_import_survives_every_toolkit_being_absent(self):
        with contextlib.ExitStack() as stack:
            for name in ("gi", "pystray", "tkinter", "rumps", "cairo"):
                stack.enter_context(stubs.missing_module(name))
            fresh = stubs.reimport("smartbar.core.tray_controller")
        self.assertTrue(hasattr(fresh, "TrayController"))
        self.assertTrue(hasattr(fresh, "TrayHost"))


# --- generation guard ------------------------------------------------------

class TestGenerationGuard(ControllerTestCase):
    """A fetch (or an error) whose generation no longer matches
    self.generation must be dropped entirely — no host touch at all."""

    def test_a_superseded_snapshot_is_dropped(self):
        self.controller.generation = 5
        snap = _snapshot(_account(1, "a@x.com", active=True))
        self.controller._apply_snapshot(snap, 4)
        self.assertIsNone(self.controller.snapshot)
        self.assertEqual(self.host.rebuild_calls, 0)
        self.assertEqual(self.host.icon_calls, [])

    def test_the_current_snapshot_is_applied(self):
        self.controller.generation = 5
        self.controller.presence_started = True
        snap = _snapshot(_account(1, "a@x.com", active=True))
        with mock.patch.object(self.controller, "_pending_update",
                               return_value=""):
            self.controller._apply_snapshot(snap, 5)
        self.assertIs(self.controller.snapshot, snap)
        self.assertEqual(self.host.rebuild_calls, 1)
        self.assertEqual(len(self.host.icon_calls), 1)
        self.assertEqual(len(self.host.title_calls), 1)

    def test_a_superseded_error_does_not_bump_failures(self):
        self.controller.generation = 5
        self.controller._apply_error("boom", 4)
        self.assertEqual(self.controller.failures, 0)
        self.assertEqual(self.host.rebuild_calls, 0)

    def test_the_current_error_bumps_failures_and_repaints_after_three(self):
        self.controller.generation = 5
        self.controller._apply_error("first", 5)
        self.controller._apply_error("second", 5)
        self.assertEqual(self.host.icon_calls, [], "not yet at the 3-failure "
                        "threshold")
        self.controller._apply_error("third", 5)
        self.assertEqual(self.controller.failures, 3)
        self.assertEqual(len(self.host.icon_calls), 1)
        self.assertIn("third", self.host.title_calls[-1])
        # rebuild_menu runs on every failed apply, not only past threshold.
        self.assertEqual(self.host.rebuild_calls, 3)

    def test_apply_snapshot_clears_a_prior_failure_streak(self):
        self.controller.failures = 2
        self.controller.last_error = "stale"
        self.controller.generation = 1
        self.controller.presence_started = True
        snap = _snapshot()
        with mock.patch.object(self.controller, "_pending_update",
                               return_value=""):
            self.controller._apply_snapshot(snap, 1)
        self.assertEqual(self.controller.failures, 0)
        self.assertEqual(self.controller.last_error, "")


class TestApplySnapshotOrdering(ControllerTestCase):
    """Pins the exact sequence the design's shared_methods entry names:
    set_icon -> set_title -> rebuild_menu -> panel refresh (if visible) ->
    notify (per alert) -> recapture. Reordering this would silently change
    what every front-end eventually shows."""

    def test_icon_title_menu_panel_and_alerts_fire_in_the_documented_order(self):
        host = FakeHost(has_panel=True)
        host._panel_visible = True
        controller = tc.TrayController(host)
        controller.generation = 1
        controller.presence_started = True
        account = _account(1, "a@x.com", active=True,
                           metrics=[model.Metric(key="5h", label="5h",
                                                 short="5h", pct=99.0)])
        snap = _snapshot(account)
        with mock.patch.object(controller, "_pending_update",
                               return_value=""):
            controller._apply_snapshot(snap, 1)
        self.assertEqual(
            host.order,
            ["set_icon", "set_title", "rebuild_menu", "refresh_panel",
             "notify"])

    def test_a_hidden_panel_is_never_refreshed(self):
        host = FakeHost(has_panel=True)
        host._panel_visible = False
        controller = tc.TrayController(host)
        controller.generation = 1
        controller.presence_started = True
        with mock.patch.object(controller, "_pending_update",
                               return_value=""):
            controller._apply_snapshot(_snapshot(), 1)
        self.assertNotIn("refresh_panel", host.order)

    def test_a_panel_less_host_is_never_asked_whether_it_is_visible(self):
        host = mock.Mock(wraps=FakeHost(has_panel=False))
        host.has_panel = False
        controller = tc.TrayController(host)
        controller.generation = 1
        controller.presence_started = True
        with mock.patch.object(controller, "_pending_update",
                               return_value=""):
            controller._apply_snapshot(_snapshot(), 1)
        host.panel_visible.assert_not_called()
        host.refresh_panel.assert_not_called()


# --- the manual "check for updates" flow ------------------------------------

class TestCheckRowThreeStates(ControllerTestCase):
    def test_checking_state_is_not_clickable(self):
        self.controller.checking = True
        label, clickable = self.controller._check_row()
        self.assertFalse(clickable)
        self.assertIn("Checking", label)

    def test_result_state_is_not_clickable(self):
        self.controller.check_result = "✓ Up to date"
        label, clickable = self.controller._check_row()
        self.assertEqual(label, "✓ Up to date")
        self.assertFalse(clickable)

    def test_idle_state_is_clickable(self):
        label, clickable = self.controller._check_row()
        self.assertTrue(clickable)
        self.assertIn("Check for updates", label)


class TestOnCheckUpdateMarshalsTheRebuild(ControllerTestCase):
    def test_the_rebuild_goes_through_call_on_ui_thread_not_directly(self):
        self.controller._on_check_update()
        self.assertEqual(self.host.rebuild_calls, 0, "must not repaint "
                        "before the queued callback is drained")
        self.host.drain()
        self.assertEqual(self.host.rebuild_calls, 1)

    def test_a_second_call_while_checking_is_a_no_op(self):
        self.controller._on_check_update()
        first_token = self.controller.check_token
        self.controller._on_check_update()
        self.assertEqual(self.controller.check_token, first_token)


class TestCheckUpdateWorker(ControllerTestCase):
    def test_runs_the_hosts_own_argv_and_marshals_the_answer(self):
        self.controller.check_token = 3
        done = mock.Mock(stdout='{"label": "✓ Up to date", '
                                '"title": "AI smartbar", "body": ""}')
        with mock.patch.object(tc.subprocess, "run",
                               return_value=done) as run:
            self.controller._check_update(3)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], self.host.check_update_argv())
        self.assertEqual(self.controller.check_result, "",
                         "queued, not yet applied, until drain() runs it")
        self.host.drain()
        self.assertEqual(self.controller.check_result, "✓ Up to date")

    def test_a_broken_reply_becomes_the_check_failed_row(self):
        self.controller.check_token = 1
        with mock.patch.object(tc.subprocess, "run",
                               side_effect=OSError("no launcher")):
            self.controller._check_update(1)
        self.host.drain()
        self.assertIn("failed", self.controller.check_result.lower())


class TestCheckedStickiness(ControllerTestCase):
    def test_a_stale_token_is_ignored(self):
        self.controller.check_token = 2
        self.controller.checking = True
        with mock.patch.object(self.controller, "_pending_update"):
            self.controller._checked(1, {"label": "✓ Up to date"})
        self.assertTrue(self.controller.checking, "the newer check's own "
                        "in-flight flag must survive")
        self.assertEqual(self.host.notify_calls, [])

    def test_the_current_token_applies_notifies_and_schedules_the_clear(self):
        self.controller.check_token = 1
        self.controller.checking = True
        with mock.patch.object(self.controller, "_pending_update"):
            self.controller._checked(
                1, {"label": "⬆ 1.2.3 available", "title": "AI smartbar update",
                    "body": "1.2.3 is ready."})
        self.assertFalse(self.controller.checking)
        self.assertEqual(self.controller.check_result, "⬆ 1.2.3 available")
        self.assertEqual(len(self.host.notify_calls), 1)
        alert, urgency = self.host.notify_calls[0]
        self.assertEqual(urgency, "normal")
        self.assertEqual(alert.title, "AI smartbar update")
        self.assertEqual(len(self.host.scheduled), 1)
        seconds, callback, args = self.host.scheduled[0]
        self.assertEqual(seconds, tc.CHECK_RESULT_SECONDS)
        self.assertEqual(callback, self.controller._clear_check_result)
        self.assertEqual(args, (1,))

    def test_a_missing_label_falls_back_to_the_shared_check_failed_wording(self):
        self.controller.check_token = 1
        with mock.patch.object(self.controller, "_pending_update"):
            self.controller._checked(1, None)
        self.assertIn("failed", self.controller.check_result.lower())


class TestClearCheckResult(ControllerTestCase):
    def test_clears_only_for_the_still_current_token(self):
        self.controller.check_token = 9
        self.controller.check_result = "✓ Up to date"
        self.controller._clear_check_result(8)
        self.assertEqual(self.controller.check_result, "✓ Up to date")
        self.controller._clear_check_result(9)
        self.assertEqual(self.controller.check_result, "")
        self.assertEqual(self.host.rebuild_calls, 1)

    def test_a_result_already_cleared_does_not_rebuild_again(self):
        self.controller.check_token = 9
        self.controller.check_result = ""
        self.controller._clear_check_result(9)
        self.assertEqual(self.host.rebuild_calls, 0)


# --- RecapturePolicy pacing --------------------------------------------

class TestRecaptureIntegration(ControllerTestCase):
    def test_no_action_means_no_cswap_add_and_no_thread(self):
        with mock.patch.object(self.controller.recapture, "action",
                               return_value=None):
            self.controller._maybe_recapture(_snapshot())
        self.thread_cls.assert_not_called()

    def test_a_register_action_runs_add_and_refetches(self):
        with mock.patch.object(self.controller.recapture, "action",
                               return_value="register"), \
             mock.patch.object(tc.cswap, "add") as add:
            self.controller._maybe_recapture(_snapshot())
            self.run_last_worker()
        add.assert_called_once()
        self.assertEqual(self.controller.generation, 1, "a register/heal "
                        "action must trigger a real refetch")

    def test_a_refresh_action_runs_add_but_does_not_refetch(self):
        with mock.patch.object(self.controller.recapture, "action",
                               return_value="refresh"), \
             mock.patch.object(tc.cswap, "add") as add:
            self.controller._maybe_recapture(_snapshot())
            self.run_last_worker()
        add.assert_called_once()
        self.assertEqual(self.controller.generation, 0, "routine re-capture "
                        "must not itself trigger a refetch")

    def test_a_failing_add_is_logged_and_swallowed_not_raised(self):
        with mock.patch.object(self.controller.recapture, "action",
                               return_value="heal"), \
             mock.patch.object(tc.cswap, "add",
                               side_effect=tc.cswap.CswapError("busy")):
            self.controller._maybe_recapture(_snapshot())
            self.run_last_worker()   # must not raise
        self.assertEqual(self.controller.generation, 0)


# --- on_switch ---------------------------------------------------------

class TestOnSwitch(ControllerTestCase):
    def test_a_blocked_account_sets_action_error_and_never_flips(self):
        snap = _snapshot(_account(1, "dead@x.com", status="relogin_required"))
        self.controller.snapshot = snap
        flip = mock.Mock()
        self.controller.on_switch(1, flip)
        self.thread_cls.assert_not_called()
        self.assertEqual(self.host.ui_queue[0][0], self.controller._set_action_error)
        self.host.drain()
        self.assertIn("Cannot switch", self.controller.action_error)
        flip.assert_not_called()

    def test_a_healthy_switch_marshals_the_flip_and_bumps_generation(self):
        snap = _snapshot(_account(1, "a@x.com", active=True),
                         _account(2, "b@x.com"))
        self.controller.snapshot = snap
        self.controller.generation = 7
        flip = mock.Mock()
        with mock.patch.object(tc.cswap, "switch"):
            self.controller.on_switch(2, flip)
            flip.assert_not_called()  # not yet -- still queued
            self.host.drain()
            self.run_last_worker()
        flip.assert_called_once_with(2)
        # TWO bumps, not one: on_switch's own (invalidating any fetch already
        # in flight when the user clicked) and _start_fetch's (stamping the
        # forced refetch). An earlier form of this test expected a single
        # bump. It only looked right because `flip` is a Mock here, so the
        # bump the real host used to perform never ran -- and that elision is
        # exactly how the unguarded-Linux/locked-Windows drift stayed
        # invisible to a green suite.
        self.assertEqual(self.controller.generation, 9)

    def test_the_generation_bump_lands_before_the_worker_can_race_it(self):
        """Regression. The bump used to live in each host's flip_active, and
        the copies disagreed: linux/tray.py did a bare `+=`, windows/tray.py
        took the controller's private _generation_lock, macos/menubar.py did
        nothing. flip_active is marshalled, and GLib.idle_add ALWAYS defers
        rather than running inline, so Linux's unguarded bump could interleave
        with the switch worker's own locked increment inside _start_fetch and
        lose an update -- and a lost update lets a stale pre-switch fetch match
        self.generation again and overwrite the fresh post-switch snapshot.
        The bump is the controller's job now, done synchronously before any
        worker exists. This pins that ordering: the counter has already moved
        while the repaint is still only queued."""
        self.controller.snapshot = _snapshot(_account(1, "a@x.com", active=True),
                                             _account(2, "b@x.com"))
        self.controller.generation = 7
        flip = mock.Mock()
        with mock.patch.object(tc.cswap, "switch"):
            self.controller.on_switch(2, flip)
            flip.assert_not_called()          # repaint still queued...
            self.assertEqual(self.controller.generation, 8)   # ...counter moved

    def test_no_front_end_bumps_the_generation_counter_itself(self):
        """The counter and the lock guarding it are controller-private. Three
        hosts once treated that one invariant three different ways, which is
        the drift smartbar/core/tray_controller.py exists to make impossible;
        a source scrape is the cheapest thing that actually stops it coming
        back, since none of these front-ends can be executed on the machine
        that runs this suite.

        Parsed rather than grepped on purpose: these front-ends discuss the
        counter at length in their docstrings (explaining why they no longer
        touch it), and a plain substring scan cannot tell prose from code."""
        repo = Path(__file__).resolve().parent.parent
        for path in ("smartbar/macos/menubar.py", "smartbar/linux/tray.py",
                     "smartbar/windows/tray.py"):
            tree = ast.parse((repo / path).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.AugAssign)
                        and isinstance(node.target, ast.Attribute)
                        and node.target.attr == "generation"):
                    self.fail(f"{path} bumps the controller's generation "
                              f"itself (line {node.lineno})")
                if (isinstance(node, ast.Attribute)
                        and node.attr == "_generation_lock"):
                    self.fail(f"{path} reaches into the controller's private "
                              f"_generation_lock (line {node.lineno})")

    def test_a_switch_failure_marshals_action_error_and_still_refetches(self):
        snap = _snapshot(_account(1, "a@x.com", active=True))
        self.controller.snapshot = snap
        with mock.patch.object(
                tc.cswap, "switch",
                side_effect=tc.cswap.CswapError("in use")):
            self.controller.on_switch(1, mock.Mock())
            self.run_last_worker()
            self.host.drain()
        self.assertEqual(self.controller.action_error, "Switch failed: in use")
        # 2, not 1: on_switch's own synchronous bump plus _start_fetch's. The
        # point being pinned is unchanged -- _start_fetch must still run after
        # a failed switch -- but the counter now moves twice on every switch.
        self.assertEqual(self.controller.generation, 2, "_start_fetch must "
                        "still run even after a switch failure")

    def test_a_new_attempt_clears_any_previous_sticky_error(self):
        self.controller.action_error = "Switch failed: stale old error"
        snap = _snapshot(_account(1, "a@x.com", active=True))
        self.controller.snapshot = snap
        with mock.patch.object(tc.cswap, "switch"):
            self.controller.on_switch(1, mock.Mock())
        self.assertEqual(self.controller.action_error, "")

    def test_no_snapshot_yet_is_a_no_op_not_a_crash(self):
        flip = mock.Mock()
        with mock.patch.object(tc.cswap, "switch"):
            self.controller.on_switch(2, flip)   # must not raise
            self.host.drain()
            self.run_last_worker()
        flip.assert_called_once_with(2)


# --- on_remove -----------------------------------------------------------

class TestOnRemove(ControllerTestCase):
    def test_claude_removal_filters_by_number_and_refetches(self):
        snap = _snapshot(_account(1, "a@x.com"), _account(2, "b@x.com"))
        self.controller.snapshot = snap
        with mock.patch.object(tc.cswap, "remove_account") as remove:
            self.controller.on_remove("claude:1")
            self.run_last_worker()
        remove.assert_called_once_with(1)
        self.assertEqual([a.number for a in snap.accounts], [2])
        self.assertEqual(self.controller.generation, 1)

    def test_openai_removal_filters_by_email(self):
        snap = model.Snapshot(openai=[
            model.Account(number=0, email="a@x.com", provider="openai"),
            model.Account(number=0, email="b@x.com", provider="openai")])
        self.controller.snapshot = snap
        with mock.patch.object(tc.codex, "remove_account") as remove:
            self.controller.on_remove("openai:a@x.com")
            self.run_last_worker()
        remove.assert_called_once_with("a@x.com")
        self.assertEqual([a.email for a in snap.openai], ["b@x.com"])

    def test_a_core_failure_marshals_action_error_and_still_refetches(self):
        snap = _snapshot(_account(5, "c@x.com"))
        self.controller.snapshot = snap
        with mock.patch.object(
                tc.cswap, "remove_account",
                side_effect=tc.cswap.CswapError("no such")):
            self.controller.on_remove("claude:5")
            self.run_last_worker()
            self.host.drain()
        self.assertEqual(self.controller.action_error, "Remove failed: no such")
        self.assertEqual(self.controller.generation, 1)

    def test_a_successful_removal_leaves_no_stale_error_behind(self):
        self.controller.action_error = "Remove failed: stale"
        snap = _snapshot(_account(5, "c@x.com"))
        self.controller.snapshot = snap
        with mock.patch.object(tc.cswap, "remove_account"):
            self.controller.on_remove("claude:5")
        self.assertEqual(self.controller.action_error, "")


# --- _start_fetch / _tick / _fetch --------------------------------------

class TestStartFetch(ControllerTestCase):
    def test_bumps_generation_and_raises_refreshing_before_the_worker_runs(self):
        self.controller._start_fetch()
        self.assertEqual(self.controller.generation, 1)
        self.assertTrue(self.controller.refreshing)
        self.thread_cls.assert_called_once()

    def test_tick_starts_a_fetch_and_signals_repeat(self):
        result = self.controller._tick()
        self.assertTrue(result)
        self.assertEqual(self.controller.generation, 1)


class TestFetchWorker(ControllerTestCase):
    def test_a_successful_fetch_marshals_apply_snapshot(self):
        snap = _snapshot()
        with mock.patch.object(tc.cswap, "fetch", return_value=snap):
            self.controller._fetch(3)
        self.assertEqual(self.host.ui_queue[0][0], self.controller._apply_snapshot)
        self.assertEqual(self.host.ui_queue[0][1], (snap, 3))

    def test_a_failed_fetch_marshals_apply_error(self):
        with mock.patch.object(
                tc.cswap, "fetch",
                side_effect=tc.cswap.CswapError("offline")):
            self.controller._fetch(3)
        callback, args = self.host.ui_queue[0]
        self.assertEqual(callback, self.controller._apply_error)
        self.assertEqual(args, ("offline", 3))


# --- the real prize: nothing touches the host off the UI thread ------------

class TestEveryUiTouchIsMarshalled(ControllerTestCase):
    """Every worker-thread completion in this file must reach `self.host`
    only via `host.call_on_ui_thread` — never a direct call from the
    thread that produced the result. Proven by never letting FakeHost's
    recorder auto-run: if any code path skipped the handoff, the state it
    was supposed to change would already be visible here, before drain()."""

    def test_a_fetch_failure_touches_nothing_until_drained(self):
        with mock.patch.object(
                tc.cswap, "fetch",
                side_effect=tc.cswap.CswapError("offline")):
            self.controller._fetch(0)
        self.assertEqual(self.controller.failures, 0)
        self.assertEqual(self.host.rebuild_calls, 0)
        self.host.drain()
        self.assertEqual(self.controller.failures, 1)

    def test_a_fetch_success_touches_nothing_until_drained(self):
        with mock.patch.object(tc.cswap, "fetch", return_value=_snapshot()):
            self.controller._fetch(0)
        self.assertIsNone(self.controller.snapshot)
        self.assertEqual(self.host.icon_calls, [])
        self.host.drain()
        self.assertIsNotNone(self.controller.snapshot)

    def test_a_switch_failure_touches_nothing_until_drained(self):
        snap = _snapshot(_account(1, "a@x.com", active=True))
        self.controller.snapshot = snap
        with mock.patch.object(
                tc.cswap, "switch",
                side_effect=tc.cswap.CswapError("in use")):
            self.controller.on_switch(1, mock.Mock())
            self.run_last_worker()
        self.assertEqual(self.controller.action_error, "",
                         "must still be empty until the queued callback runs")
        self.host.drain()
        self.assertIn("in use", self.controller.action_error)

    def test_a_remove_failure_touches_nothing_until_drained(self):
        snap = _snapshot(_account(5, "c@x.com"))
        self.controller.snapshot = snap
        with mock.patch.object(
                tc.cswap, "remove_account",
                side_effect=tc.cswap.CswapError("no such")):
            self.controller.on_remove("claude:5")
            self.run_last_worker()
        self.assertEqual(self.controller.action_error, "")
        self.host.drain()
        self.assertIn("no such", self.controller.action_error)

    def test_a_check_updates_menu_rebuild_touches_nothing_until_drained(self):
        self.controller._on_check_update()
        self.assertEqual(self.host.rebuild_calls, 0)
        self.host.drain()
        self.assertEqual(self.host.rebuild_calls, 1)


# --- _pending_update -------------------------------------------------------

class TestPendingUpdate(ControllerTestCase):
    """`from smartbar import update_runner` (inside _pending_update) resolves
    through whatever the `smartbar` package's own `update_runner` attribute
    currently is, which is not always sys.modules["smartbar.update_runner"]
    -- tests/test_runner_portability.py's own reload helper leaves that
    attribute pointed at a module it popped from sys.modules again once its
    tests finish, a pre-existing test-isolation gap in that file, unrelated
    to this one. Resolving the SAME `from smartbar import update_runner`
    statement here, right before patching it, targets whatever object
    _pending_update() is actually about to use instead of guessing via a
    string path that could name a different (already-orphaned) object."""

    def _current_update_runner_module(self):
        from smartbar import update_runner
        return update_runner

    def test_delegates_to_the_one_shared_reader(self):
        module = self._current_update_runner_module()
        with mock.patch.object(module, "pending_for_ui",
                               return_value=("1.2.3", "")) as reader:
            result = self.controller._pending_update()
        reader.assert_called_once_with()
        self.assertEqual(result, "1.2.3")
        self.assertEqual(self.controller.update_pending, "1.2.3")

    def test_records_why_an_update_is_held_back(self):
        module = self._current_update_runner_module()
        with mock.patch.object(module, "pending_for_ui",
                               return_value=("", "dirty checkout")):
            self.controller._pending_update()
        self.assertEqual(self.controller.update_blocked, "dirty checkout")


class TestSysmon(ControllerTestCase):
    """The System-tab payload the controller holds, its notifications, and
    the guarded kill it routes to the runner."""

    PAYLOAD = {
        "cpu": {"pct": 13, "cores": [1], "caption": "x"},
        "mem": {"pct": 50.0, "caption": "x"},
        "history": {"pct": [], "peakText": "peak 0%", "lastPct": 0},
        "machine": {"caption": "x"},
        "leftovers": {"chip": "", "more": 0, "foot": "",
                      "rows": [{"token": "100:1", "kind": "junk",
                                "name": "X", "sub": "", "meta": "", "age": 1,
                                "cores": 5.0, "burning": True}]},
        "busy": {"caption": "", "rows": []},
        "alerts": [{"key": "burning:100:1",
                    "title": "2 leftovers burning 9 cores", "body": "open it"}],
        "autokilled": [], "live": False,
    }

    def test_tick_stores_payload_and_notifies_alerts(self):
        with mock.patch.object(tc.sysmon_runner, "background_tick",
                               return_value=self.PAYLOAD):
            self.controller.sysmon_tick()
            self.run_last_worker()
            self.host.drain()
        self.assertEqual(self.controller.system, self.PAYLOAD)
        self.assertEqual(len(self.host.notify_calls), 1)
        self.assertIn("burning", self.host.notify_calls[0][0].title)

    def test_the_same_alert_fires_once_across_ticks(self):
        # One burning orphan used to notify EVERY tick (every 60 s) until
        # killed. Dedupe on the alert's `key`, re-armed when it disappears.
        with mock.patch.object(tc.sysmon_runner, "background_tick",
                               return_value=self.PAYLOAD):
            for _ in range(3):
                self.controller.sysmon_tick()
                self.run_last_worker()
                self.host.drain()
        self.assertEqual(len(self.host.notify_calls), 1)

    def test_a_cleared_alert_rearms(self):
        quiet = dict(self.PAYLOAD, alerts=[])
        with mock.patch.object(tc.sysmon_runner, "background_tick",
                               side_effect=[self.PAYLOAD, quiet,
                                            self.PAYLOAD]):
            for _ in range(3):
                self.controller.sysmon_tick()
                self.run_last_worker()
                self.host.drain()
        self.assertEqual(len(self.host.notify_calls), 2)

    def test_tick_does_nothing_when_disabled(self):
        with mock.patch.object(tc.sysmon, "enabled", return_value=False):
            self.controller.sysmon_tick()
        self.assertIsNone(self.controller.system)
        self.thread_cls.assert_not_called()

    def test_on_kill_optimistically_drops_the_row_and_calls_the_runner(self):
        self.controller.system = {"leftovers": {"rows": [
            {"token": "100:1", "kind": "junk", "name": "X"}]},
            "busy": {"rows": []}}
        with mock.patch.object(tc.sysmon_runner, "kill",
                               return_value=(True, "")) as killer:
            self.controller.on_kill("100:1")
            # the row is gone immediately (optimistic)
            self.assertEqual(
                self.controller.system["leftovers"]["rows"], [])
            self.run_last_worker()
        killer.assert_called_once_with("100:1")

    def test_on_kill_failure_surfaces_an_action_error(self):
        self.controller.system = {"leftovers": {"rows": []},
                                  "busy": {"rows": []}}
        with mock.patch.object(tc.sysmon_runner, "kill",
                               return_value=(False, "EPERM")):
            self.controller.on_kill("100:1")
            self.run_last_worker()
            self.host.drain()
        self.assertIn("EPERM", self.controller.action_error)


if __name__ == "__main__":
    unittest.main()
