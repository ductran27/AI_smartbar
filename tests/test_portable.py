"""Tests for smartbar/core/portable.py — the lock/spawn/no-window shims."""

import os
import subprocess
import tempfile
import unittest

from smartbar.core import portable


class TestLockIsExclusive(unittest.TestCase):
    """The whole point of `lock()` is to keep two beats of the same cron
    job from stepping on each other. If a second call could succeed while
    the first handle is still open, two `update_runner` runs could race
    the same git checkout at once — exactly the bug this replaces flock
    to prevent.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Deliberately addCleanup rather than `def tearDown: self.tmp.cleanup()`.
        # unittest runs tearDown BEFORE the callbacks a test method registers,
        # so a tearDown delete would run while this test's lock handle is still
        # open. POSIX unlinks an open file happily; Windows raises WinError 32.
        # Same queue means LIFO order: handles close, then the directory goes.
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "run.lock")

    def test_a_second_lock_while_the_first_is_held_returns_none(self):
        first = portable.lock(self.path)
        self.addCleanup(lambda: first and first.close())
        self.assertIsNotNone(first)
        second = portable.lock(self.path)
        self.assertIsNone(second)

    def test_the_lock_can_be_taken_again_once_the_first_handle_closes(self):
        first = portable.lock(self.path)
        self.assertIsNotNone(first)
        first.close()
        second = portable.lock(self.path)
        self.addCleanup(lambda: second and second.close())
        self.assertIsNotNone(second)

    def test_the_lock_file_need_not_pre_exist(self):
        # Every call site creates the *directory* first
        # (os.makedirs(CACHE_DIR, exist_ok=True)) but never the file itself;
        # lock() has to create the file on first use or every first run
        # would spuriously fail.
        self.assertFalse(os.path.exists(self.path))
        handle = portable.lock(self.path)
        self.addCleanup(lambda: handle and handle.close())
        self.assertIsNotNone(handle)
        self.assertTrue(os.path.exists(self.path))

    def test_a_missing_parent_directory_is_not_this_functions_job(self):
        # Matches the documented precondition: callers os.makedirs() the
        # cache dir before ever calling lock(). Handing lock() a path whose
        # directory does not exist should fail closed (None), not raise.
        missing = os.path.join(self.tmp.name, "no-such-dir", "run.lock")
        self.assertIsNone(portable.lock(missing))

    def test_locking_does_not_truncate_a_file_that_already_has_content(self):
        # This is the whole reason the shim opens with "a+" instead of the
        # original call sites' "w": msvcrt.locking() locks a byte range, and
        # locking byte 0 of a file another process just truncated to zero
        # behaves differently than locking byte 0 of a file left alone.
        with open(self.path, "w") as f:
            f.write("state-from-a-previous-run")
        handle = portable.lock(self.path)
        self.addCleanup(lambda: handle and handle.close())
        self.assertIsNotNone(handle)
        # Read back through the lock handle, not a second open(). msvcrt
        # takes a genuine byte-range lock, so on Windows an independent
        # reader gets PermissionError while the lock is held; POSIX flock is
        # advisory and allows it, which is the only reason a second open()
        # ever looked correct here. lock() opens "a+" and its docstring
        # already tells callers to seek(0) for a fresh view.
        handle.seek(0)
        self.assertEqual(handle.read(), "state-from-a-previous-run")


class TestNoWindow(unittest.TestCase):
    """`no_window()` only has anything to do on win32; everywhere else it
    must be a true no-op dict so callers can unconditionally splat it into
    subprocess.run without ever adding a flag POSIX does not understand.
    """

    def setUp(self):
        self.saved_platform = portable.sys.platform

    def tearDown(self):
        portable.sys.platform = self.saved_platform

    def test_empty_on_posix(self):
        for plat in ("darwin", "linux"):
            portable.sys.platform = plat
            self.assertEqual(portable.no_window(), {})

    def test_windows_flag_value_is_create_no_window(self):
        # CREATE_NO_WINDOW is only defined by the subprocess module itself
        # on win32; everywhere else we assert against its known constant
        # value (0x08000000) so this test still runs on macOS/Linux CI.
        portable.sys.platform = "win32"
        expected = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.assertEqual(portable.no_window(), {"creationflags": expected})


class TestSpawnDetached(unittest.TestCase):
    """`spawn_detached` swaps its strategy per platform; assert on the kwargs
    it builds (via a fake Popen) rather than actually spawning a process, so
    these run identically on every platform including whichever one CI is.
    """

    def setUp(self):
        self.saved_platform = portable.sys.platform
        self.saved_popen = portable.subprocess.Popen
        self.calls = []

        def fake_popen(argv, **kwargs):
            self.calls.append((argv, kwargs))
            return object()

        portable.subprocess.Popen = fake_popen

    def tearDown(self):
        portable.sys.platform = self.saved_platform
        portable.subprocess.Popen = self.saved_popen

    def test_posix_gets_start_new_session(self):
        for plat in ("darwin", "linux"):
            self.calls.clear()
            portable.sys.platform = plat
            portable.spawn_detached(["echo", "hi"], stdout=subprocess.DEVNULL)
            argv, kwargs = self.calls[0]
            self.assertEqual(argv, ["echo", "hi"])
            self.assertTrue(kwargs.get("start_new_session"))
            self.assertNotIn("creationflags", kwargs)

    def test_windows_gets_detached_process_and_new_process_group(self):
        portable.sys.platform = "win32"
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        portable.spawn_detached(["echo", "hi"], stdout=subprocess.DEVNULL)
        argv, kwargs = self.calls[0]
        self.assertEqual(argv, ["echo", "hi"])
        self.assertNotIn("start_new_session", kwargs)
        self.assertEqual(kwargs["creationflags"], detached | new_group)

    def test_callers_stdout_and_stderr_pass_through_untouched(self):
        portable.sys.platform = "darwin"
        portable.spawn_detached(["echo", "hi"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        _argv, kwargs = self.calls[0]
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
