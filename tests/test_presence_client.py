"""Tests for smartbar/presence_client.py — the spawn side, not the git side.

Nothing here launches a real process: portable.spawn_detached is stubbed, so
what is pinned is the bookkeeping around it.
"""
from __future__ import annotations

import unittest
from unittest import mock

from smartbar import presence_client


class FakeProc:
    """Just enough Popen for _reap: a poll() whose answer the test controls."""

    def __init__(self, alive=True):
        self.returncode = None if alive else 0
        self.stdin = mock.Mock()

    def poll(self):
        return self.returncode

    def finish(self):
        self.returncode = 0


class Env(unittest.TestCase):
    def setUp(self):
        presence_client._outstanding.clear()
        self.addCleanup(presence_client._outstanding.clear)


class TestBeatsAreReaped(Env):
    """The bug: one zombie per beat, for the whole life of the tray.

    start_new_session=True detaches the child's session but leaves it a child
    of this process, and _spawn never waited on it. Over a multi-day session
    with a beat every few minutes that is hundreds of dead entries in the
    process table.
    """

    def test_a_finished_beat_is_dropped_when_the_next_one_starts(self):
        first = FakeProc()
        with mock.patch.object(presence_client.portable, "spawn_detached",
                               return_value=first):
            presence_client._spawn(["--presence-beat"], "{}")
        self.assertEqual(presence_client._outstanding, [first])

        first.finish()  # the beat exits while the tray keeps running
        second = FakeProc()
        with mock.patch.object(presence_client.portable, "spawn_detached",
                               return_value=second):
            presence_client._spawn(["--presence-beat"], "{}")
        # The dead one is gone; only the live one is still tracked.
        self.assertEqual(presence_client._outstanding, [second])

    def test_a_beat_still_running_is_kept(self):
        """Reaping must never wait on or discard a beat that is still going."""
        first = FakeProc()
        second = FakeProc()
        for proc in (first, second):
            with mock.patch.object(presence_client.portable, "spawn_detached",
                                   return_value=proc):
                presence_client._spawn(["--presence-beat"], "{}")
        self.assertEqual(presence_client._outstanding, [first, second])

    def test_a_beat_that_never_started_is_not_tracked(self):
        """A failed spawn has no process to reap, and must not be recorded."""
        with mock.patch.object(presence_client.portable, "spawn_detached",
                               side_effect=OSError("no such file")):
            presence_client._spawn(["--presence-beat"], "{}")
        self.assertEqual(presence_client._outstanding, [])


if __name__ == "__main__":
    unittest.main()
