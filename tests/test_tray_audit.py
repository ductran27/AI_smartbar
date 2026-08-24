"""Audit-driven TrayController pins (2026-08-24, batch B8): worker-thread
resilience, tick supersession, invisible-error paths."""
from __future__ import annotations

import unittest
from unittest import mock

from smartbar.core import tray_controller as tc
from tests.test_tray_controller import (ControllerTestCase, _account,
                                        _snapshot)


class TestStaleFetchCannotResurrectARemovedCard(ControllerTestCase):
    def test_pre_remove_snapshot_is_dropped(self):
        self.controller.snapshot = _snapshot(_account(1, "a@x.com"))
        stale_generation = self.controller.generation
        stale_snap = _snapshot(_account(1, "a@x.com"), _account(2, "b@x.com"))
        with mock.patch.object(tc.cswap, "remove_account"):
            self.controller.on_remove("claude:1")
        # The fetch that started BEFORE the removal now lands…
        self.controller._apply_snapshot(stale_snap, stale_generation)
        self.host.drain()
        # …and must be dropped, not resurrect the removed card.
        self.assertNotEqual(self.controller.snapshot, stale_snap)


class TestTickDoesNotSupersedeASlowFetch(ControllerTestCase):
    def test_tick_skips_while_refreshing(self):
        self.controller.refreshing = True
        before = self.controller.generation
        self.assertTrue(self.controller._tick())
        self.assertEqual(self.controller.generation, before)
        self.thread_cls.assert_not_called()


class TestUnexpectedFetchErrorsReachTheUser(ControllerTestCase):
    def test_typeerror_becomes_apply_error_not_a_dead_thread(self):
        with mock.patch.object(tc.cswap, "fetch",
                               side_effect=TypeError("int expected")):
            self.controller._start_fetch()
            _args, kwargs = self.thread_cls.call_args
            kwargs["target"](*kwargs.get("args", ()))
            self.host.drain()
        self.assertFalse(self.controller.refreshing,
                         "refreshing must not stick True forever")
        self.assertIn("TypeError", self.controller.last_error)


class TestKillWorkerSurvivesRunnerExceptions(ControllerTestCase):
    def test_exception_reports_and_reticks(self):
        self.controller.system = {"leftovers": {"rows": []},
                                  "busy": {"rows": []}}
        with mock.patch.object(tc.sysmon_runner, "kill",
                               side_effect=AttributeError("SIGKILL")):
            self.controller.on_kill("1:1")
            self.run_last_worker()
            self.host.drain()
        self.assertIn("Kill failed", self.controller.action_error)


class TestSysmonTickInFlightGuard(ControllerTestCase):
    def test_second_tick_waits_for_the_first(self):
        with mock.patch.object(tc.sysmon, "enabled", return_value=True):
            self.controller.sysmon_tick()
            self.controller.sysmon_tick()     # first still in flight
        self.assertEqual(self.thread_cls.call_count, 1)

    def test_guard_clears_after_the_sample_lands(self):
        payload = {"leftovers": {"rows": []}, "busy": {"rows": []},
                   "alerts": []}
        with mock.patch.object(tc.sysmon, "enabled", return_value=True), \
             mock.patch.object(tc.sysmon_runner, "background_tick",
                               return_value=payload):
            self.controller.sysmon_tick()
            self.run_last_worker()
            self.host.drain()
            self.controller.sysmon_tick()
        self.assertEqual(self.thread_cls.call_count, 2)


class TestSysmonGateLivesInTheHost(ControllerTestCase):
    """Which hosts poll the System tab is the HOST's decision. The pure
    controller carried a `sys.platform == "win32"` gate for a while (its
    own rules forbid one), which made every tick-driving test in this suite
    fail on the Windows CI leg; the Windows host simply never schedules a
    tick instead."""

    def test_the_controller_ticks_on_any_platform(self):
        import sys
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.object(tc.sysmon, "enabled", return_value=True):
            self.controller.sysmon_tick()
        self.assertEqual(self.thread_cls.call_count, 1)

    def test_the_windows_host_never_schedules_a_tick(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "smartbar", "windows", "tray.py"),
                  encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("sysmon_tick(", source)


class TestBrokenCswapDoesNotHideAWaitingUpdate(ControllerTestCase):
    def test_apply_error_rereads_the_pending_update(self):
        # Resolve the module at TEST time: test_runner_portability reloads
        # smartbar.update_runner, so a string-target patch bound earlier in
        # the run can end up on a stale module object.
        import importlib
        ur = importlib.import_module("smartbar.update_runner")
        with mock.patch.object(ur, "pending_for_ui",
                               return_value=("9.9.9", "")):
            self.controller._apply_error("cswap exploded",
                                         self.controller.generation)
        self.assertEqual(self.controller.update_pending, "9.9.9")


class TestActionErrorsAreVisibleWithoutAPanel(ControllerTestCase):
    def test_menu_switch_failure_notifies(self):
        self.host._panel_visible = False
        self.controller._set_action_error("Switch failed: boom")
        titles = [alert.body for alert, _urgency in self.host.notify_calls]
        self.assertTrue(any("Switch failed" in body for body in titles))


if __name__ == "__main__":
    unittest.main()
