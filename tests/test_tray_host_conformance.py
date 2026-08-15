"""Pins that every real front-end actually satisfies the TrayHost contract.

A host satisfies TrayHost by DUCK TYPING, not by subclassing: linux/tray.py
and windows/tray.py declare a plain `class Tray:`, and macos/menubar.py's
base is already rumps.App. That is deliberate (see tray_controller.py's own
docstring), but it has a sharp edge — TrayHost's class-level defaults reach
NOBODY. `has_panel = False` and the panel triad's no-op bodies read like
inherited fallbacks and are not: a host that omits one gets an
AttributeError from the controller, not the default.

That edge already drew blood. SmartBarApp never declared `has_panel`, so
TrayController._apply_snapshot's `if self.host.has_panel and ...` raised
AttributeError on EVERY macOS fetch. It was invisible rather than fatal:
_drain_ui swallows a failing queued callback by design (one bad update must
not strand every later fetch behind it), so the icon/title/menu — written
just above that line — kept updating normally while the two steps below it,
the limit-alert loop and _maybe_recapture, silently never ran.

Neither existing test file could catch it. test_tray_controller.py's
FakeHost DOES subclass TrayHost, so it inherits everything a real host must
declare for itself; test_macos_menubar.py mocks controller._tick/
_start_fetch throughout, by design, so the real controller never runs
against the real host. The seam between them was untested. This file is
that seam, and it is deliberately structural: it compares member SETS, so a
member added to TrayHost tomorrow becomes required of all three hosts on
the test run it lands, without anyone remembering to come back here.

The behavioural counterpart — the real controller's apply path driven
against the real macOS host — lives in test_macos_menubar.py, next to the
fake rumps it needs.
"""
from __future__ import annotations

import re
import os
import unittest

import smartbar
from smartbar.core import tray_controller as tc
from tests.support import stubs


# --- the contract, derived rather than restated -----------------------------

# Only ever reached when has_panel is True, so a panel-less host legitimately
# omits all four. Every OTHER public member of TrayHost is mandatory.
PANEL_TRIAD = ("show_panel", "hide_panel", "panel_visible", "refresh_panel")


def _contract_members():
    """Every public member TrayHost declares. Read off the class instead of
    being listed here so this file cannot drift from the contract it is
    supposed to be enforcing."""
    return {name for name in vars(tc.TrayHost) if not name.startswith("_")}


def _required_members():
    return _contract_members() - set(PANEL_TRIAD)


# --- the three real hosts ---------------------------------------------------

class _FakeRumpsApp:
    """A real class, not a MagicMock: SmartBarApp subclasses rumps.App at
    class-definition time, and a MagicMock instance cannot be a base class."""

    def __init__(self, title="", quit_button=None):
        self.title = title
        self.menu = []


def _linux_host():
    # The guard lives here rather than in each caller: linux is the one host
    # that needs a REAL pycairo (install_gi leaves cairo unfaked, and
    # smartbar/linux/tray.py imports smartbar.paint.tray_icon at module
    # scope), and both tests below load their hosts through this same
    # function. Raised inside `with self.subTest(host=...)`, the skip is
    # recorded against the linux subtest alone and windows/macos still run.
    stubs.skip_without_pycairo()
    stubs.install_gi()
    return stubs.reimport("smartbar.linux.tray").Tray


def _windows_host():
    stubs.install_tk(tk_cls=stubs.FakeWidget)
    stubs.install_pil(photoimage_cls=stubs.FakeWidget)
    stubs.install_cairo()
    stubs.install_pystray()
    return stubs.reimport("smartbar.windows.tray").Tray


def _macos_host():
    stubs.install_rumps(app_cls=_FakeRumpsApp, timer_cls=object,
                        menuitem_cls=object)
    return stubs.reimport("smartbar.macos.menubar").SmartBarApp


HOSTS = (("linux", _linux_host), ("windows", _windows_host),
         ("macos", _macos_host))


class TestEveryHostDeclaresTheContract(stubs.GuiStubbedTestCase):
    """Each host is loaded under its OWN toolkit stubs, one per test, and
    GuiStubbedTestCase restores the whole of sys.modules in between — the
    three fake toolkits must never be installed at the same time."""

    def test_every_host_declares_every_mandatory_member(self):
        required = _required_members()
        self.assertIn("has_panel", required)  # guards the derivation itself
        for name, load in HOSTS:
            with self.subTest(host=name):
                host_cls = load()
                missing = sorted(m for m in required
                                 if not hasattr(host_cls, m))
                self.assertEqual(missing, [], "%s host is missing %s; a "
                                 "TrayHost default cannot reach it, the "
                                 "controller will raise AttributeError"
                                 % (name, missing))

    def test_a_host_that_can_have_a_panel_declares_the_whole_triad(self):
        """has_panel is a literal False only on a host with no panel at all.
        Anything else — Linux/Windows' `self.popover is not None` property —
        means the controller CAN reach the triad, so all four must exist."""
        for name, load in HOSTS:
            with self.subTest(host=name):
                host_cls = load()
                # Defaulted so a host missing has_panel entirely is reported
                # by the test above alone, rather than failing both with the
                # same root cause.
                if getattr(host_cls, "has_panel", False) is False:
                    continue
                missing = sorted(m for m in PANEL_TRIAD
                                 if not hasattr(host_cls, m))
                self.assertEqual(missing, [], "%s host can report has_panel "
                                 "True but is missing %s" % (name, missing))


# --- the other direction ----------------------------------------------------

class TestTheControllerStaysInsideTheContract(unittest.TestCase):
    def test_every_host_call_the_controller_makes_is_declared_on_TrayHost(self):
        """The mirror of the tests above: those pin that hosts implement
        everything TrayHost declares, this pins that the controller asks for
        nothing MORE. A new self.host.foo() added to the controller without
        a matching TrayHost member would otherwise reach three hosts that
        have no reason to know about it, and fail on all of them at runtime
        rather than here."""
        path = os.path.join(os.path.dirname(os.path.dirname(smartbar.__file__)),
                            "smartbar", "core", "tray_controller.py")
        with open(path) as handle:
            source = handle.read()
        touched = set(re.findall(r"self\.host\.([A-Za-z_]\w*)", source))
        self.assertTrue(touched, "found no self.host.* calls at all — the "
                                 "regex has stopped matching the source")
        undeclared = sorted(touched - _contract_members())
        self.assertEqual(undeclared, [], "TrayController calls %s but "
                         "TrayHost does not declare it" % undeclared)


if __name__ == "__main__":
    unittest.main()
