"""The suite must SKIP without pycairo, never error.

This is pinned because the machine that would notice is not the machine
that runs CI. CI installs pinned pycairo and verifies `import cairo` as its
own step, so every one of these guards is dormant there -- the only place a
removed guard shows up is a contributor's pycairo-less interpreter, as ~60
red tests that look exactly like a regression they just caused. That is
precisely the shape of breakage nobody notices for months, so it gets a
test that runs everywhere instead of a comment.

What is NOT pinned here: that the guarded tests still pass WITH pycairo.
That needs no help -- it is what the ordinary suite does on every run.
"""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from tests.support import stubs
from tests import test_linux_tray, test_tray_host_conformance


class TestTheGuardSkipsRatherThanErrors(unittest.TestCase):
    def test_it_raises_SkipTest_when_pycairo_is_absent(self):
        with mock.patch.object(stubs, "HAS_PYCAIRO", False):
            with self.assertRaises(unittest.SkipTest) as caught:
                stubs.skip_without_pycairo()
        # The message has to name the fix: whoever hits this is by
        # definition someone whose environment differs from CI's.
        self.assertIn("pip install pycairo", str(caught.exception))

    def test_it_does_nothing_when_pycairo_is_present(self):
        with mock.patch.object(stubs, "HAS_PYCAIRO", True):
            self.assertIsNone(stubs.skip_without_pycairo())

    def test_the_answer_is_resolved_once_at_import_not_per_call(self):
        """Every install_* pushes a fake `cairo` into sys.modules, so a
        guard that asked the import system at CALL time would answer about
        whichever fake happened to be installed -- and silently stop
        skipping. HAS_PYCAIRO is a plain module-level bool for that reason.
        """
        self.assertIsInstance(stubs.HAS_PYCAIRO, bool)


class TestBothCallSitesStillGuard(unittest.TestCase):
    """The two places that reach cairo INDIRECTLY -- through
    smartbar/linux/tray.py's module-scope `import smartbar.paint.tray_icon`
    -- and therefore cannot rely on test_popover_draw.py's own skip.
    """

    def test_the_linux_tray_base_case_guards_in_setUp(self):
        source = inspect.getsource(test_linux_tray.GuiStubbedTestCase.setUp)
        self.assertIn("skip_without_pycairo", source,
                      "test_linux_tray's base setUp stopped guarding; all 50+ "
                      "of its tests will ERROR rather than skip wherever "
                      "pycairo is absent")

    def test_the_conformance_linux_host_guards_before_loading(self):
        source = inspect.getsource(test_tray_host_conformance._linux_host)
        self.assertIn("skip_without_pycairo", source,
                      "the conformance suite's linux host stopped guarding; "
                      "its subtest will ERROR rather than skip wherever "
                      "pycairo is absent")


if __name__ == "__main__":
    unittest.main()
