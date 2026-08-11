"""Shared fake-toolkit scaffolding for the GUI-stubbed test files.

tests/test_windows_tray.py, tests/test_linux_tray.py,
tests/test_windows_popover_window.py and tests/test_macos_menubar.py each
used to hand-roll their OWN copy of a fake tkinter/PIL/cairo/pystray/gi/
rumps and push it into sys.modules directly (tests/test_runner_portability.py
did the same thing for a plain, non-GUI absent import -- fcntl -- via the
same "sys.modules[name] = None" idiom; see missing_module() below). Copies
of the same fake drift from each other one attribute at a time -- e.g. a
fake Gdk.EventMask growing SMOOTH_SCROLL_MASK in one copy but not the other
-- and a drifted fake makes a test assert something the REAL toolkit never
does, which is worse than no test at all: it looks green while pinning a
lie. Centralizing construction here means a new attribute a toolkit needs
gets added ONCE, and every caller sees it the same test run it lands.

Each `install_*` function below builds a fresh fake module (or namespace of
fake modules), pushes it into `sys.modules`, and returns what it built so a
caller can read or extend it. Where two files need genuinely different
fakes for the same toolkit -- test_windows_popover_window.py's Canvas
needs real canvasx/canvasy scroll-offset bookkeeping that
test_windows_tray.py's Canvas never touches, because nothing under test in
test_windows_tray.py reads it back -- the installer takes the widget
classes as parameters instead of hard-coding its own, so the caller
supplies its own fake and this module only does the sys.modules wiring
around it. That keeps the fakes behaviour-preserving per file while still
sharing the part that was ACTUALLY duplicated byte-for-byte.

None of these functions snapshot or restore sys.modules themselves -- that
is GuiStubbedTestCase's job, below. Install happens in a subclass's setUp,
after GuiStubbedTestCase.setUp has taken its snapshot.
"""
from __future__ import annotations

import contextlib
import importlib
import sys
import types
import unittest
from unittest import mock


# --- sys.modules snapshot/restore ------------------------------------------

class GuiStubbedTestCase(unittest.TestCase):
    """Snapshots the WHOLE of sys.modules in setUp, restores it verbatim in
    tearDown.

    Restoring only the handful of stub keys (tkinter/PIL/.../cairo/pystray/
    gi.*/rumps) is not enough: the module under test triggers a real,
    ordinary import of some OTHER module (smartbar.paint.tray_icon,
    smartbar.paint.popover_draw, ...) the first time it runs, and that
    module's own `import cairo` binds whatever object sys.modules["cairo"]
    holds AT THAT MOMENT into its own globals, permanently -- Python does
    not re-execute an `import` statement for a module already cached in
    sys.modules. If that first-ever import happens while this test's fake
    cairo is installed, popping just "cairo" back afterwards leaves the
    paint module itself still bound to the fake, corrupting every OTHER
    test file that imports it later in the same
    `python -m unittest discover` process. (Confirmed against an earlier
    draft of tests/test_windows_tray.py that only restored the stub keys:
    tests.test_popover_draw went from all-green to 12 errors + 1 failure
    when run after tests.test_windows_tray in the same process.)
    Snapshotting and restoring the whole of sys.modules removes every
    module a test's imports newly added, stub-bound or not, regardless of
    how many hops away from the stub it is.

    Subclasses call whichever `install_*` function(s) they need from their
    own setUp, after calling `super().setUp()`: which toolkit to fake
    differs per file, so this base class installs nothing itself.
    """

    def setUp(self):
        self._sys_modules_snapshot = dict(sys.modules)

    def tearDown(self):
        for name in list(sys.modules):
            if name not in self._sys_modules_snapshot:
                del sys.modules[name]
        sys.modules.update(self._sys_modules_snapshot)


@contextlib.contextmanager
def missing_module(name):
    """Temporarily mark `name` as an import already tried and found
    absent, so any `import name` inside the block raises ImportError
    immediately instead of re-running (or falling through to) whatever
    real module is actually installed -- this is the documented CPython
    mechanism for "this module is known missing": sys.modules[name] =
    None. It is the only way to prove a module-scope `import name` was
    genuinely removed from the code under test rather than merely
    unexercised by this test (see tests/test_runner_portability.py's
    TestImportSurvivesWithoutFcntl for what this pins: smartbar.update_
    runner/presence_runner used to `import fcntl` at module scope, which
    made the whole module unimportable on win32 even for a caller that
    only wanted CONFIG_DIR).

    Restores whatever sys.modules held for `name` beforehand -- present,
    absent, or already None -- when the block exits.
    """
    saved = sys.modules.get(name, "not-set")
    sys.modules[name] = None
    try:
        yield
    finally:
        if saved == "not-set":
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved


def reimport(dotted_name):
    """A fresh, real execution of `dotted_name` under whichever stubs are
    currently installed, even if some earlier test already cached it in
    sys.modules under a different stub state. Safe precisely because
    GuiStubbedTestCase.tearDown restores the FULL sys.modules snapshot
    afterwards -- whatever this (transitively) adds is gone by the next
    test regardless of whether this pop ever fires."""
    sys.modules.pop(dotted_name, None)
    return importlib.import_module(dotted_name)


# --- the shared catch-all fake widget ---------------------------------------

class FakeWidget:
    """A real class, not a MagicMock: both Popover(tk.Toplevel) and
    Popover(Gtk.Window) subclass this at class-definition time (i.e. at
    import time), and a bare MagicMock() instance cannot be used as a base
    class -- "metaclass conflict" -- so the fake has to be an actual type.

    Byte-for-byte identical between the old test_windows_tray.py and
    test_linux_tray.py copies before this extraction, which is exactly the
    kind of duplication this module exists to remove. It stays separate
    from test_windows_popover_window.py's much richer fake tk widget
    (bind/geometry/after bookkeeping) -- that one is not a duplicate of
    this, it needs real state this catch-all never tracks, so forcing them
    together would either strip the popover file's state tracking or
    saddle every OTHER caller with unused bookkeeping.

    Any attribute access that misses both the instance dict and the class
    body falls through to __getattr__ and gets back a no-op callable --
    this is what lets `_FakeWidget()` stand in for arbitrary tkinter/GTK
    widget methods (bind, pack, connect, ...) without enumerating them.

    HAZARD, and why it is permissive rather than strict for non-underscore
    names: test_linux_tray.py's TestPanelTooltips/TestPanelViewport build a
    Popover via `Popover.__new__(Popover)` (bypassing __init__ on purpose,
    to test one method in isolation) and then hand-set only the handful of
    underscore-prefixed instance attributes that method actually reads.
    Every OTHER public method this fake stands in for (bind, connect,
    show_all, ...) is expected to be reachable as a harmless no-op even
    though __init__ never ran -- tightening those too would break that
    "test one method in isolation" pattern. See __getattr__'s own
    docstring for the underscore-prefixed half of this story, which is
    hardened instead.
    """

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        # Hardened for underscore-prefixed names only: a Popover built via
        # __new__() (see this class's own docstring) never runs __init__,
        # so instance attributes it would normally set (self._scroll,
        # self._closed, ...) are simply absent. Before this guard, reading
        # one of those by accident returned a callable instead of raising
        # -- a bound-method-shaped value standing in for what should have
        # been, say, a float or a bool -- and the failure surfaced far
        # from its cause as a confusing TypeError deep inside real
        # application code, not here. Raising AttributeError instead makes
        # that fail at the point of the actual mistake: a test that forgot
        # to seed an instance attribute the method it calls needs.
        # Public (non-underscore) names stay permissive -- see the class
        # docstring's HAZARD paragraph for why.
        if name.startswith("_"):
            raise AttributeError(name)

        def _method(*a, **k):
            return None
        return _method


class FakeImage:
    """Stand-in for PIL.Image.open()'s return value. Identical across the
    old test_windows_tray.py and test_windows_popover_window.py copies."""

    @staticmethod
    def open(buf):
        return FakeImage()

    def load(self):
        pass


# --- tkinter / PIL / cairo (both Windows front-end test files) -------------

def install_tk(*, tk_cls, toplevel_cls=None, canvas_cls=None, label_cls=None):
    """Install a fake `tkinter` module into sys.modules and return it.

    Takes the actual widget classes as parameters rather than building its
    own -- see FakeWidget's docstring for why test_windows_tray.py's and
    test_windows_popover_window.py's fake widgets cannot be merged into
    one. `toplevel_cls`/`canvas_cls` default to `tk_cls` for callers (like
    test_windows_tray.py) that use one bare fake for all three.
    """
    toplevel_cls = tk_cls if toplevel_cls is None else toplevel_cls
    canvas_cls = tk_cls if canvas_cls is None else canvas_cls
    tkinter = types.ModuleType("tkinter")
    tkinter.Tk = tk_cls
    tkinter.Toplevel = toplevel_cls
    tkinter.Canvas = canvas_cls
    if label_cls is not None:
        tkinter.Label = label_cls
    sys.modules["tkinter"] = tkinter
    return tkinter


def install_pil(*, photoimage_cls):
    """Install a fake PIL/PIL.Image/PIL.ImageTk into sys.modules.

    `photoimage_cls` differs per caller: test_windows_tray.py reuses its
    bare fake widget, test_windows_popover_window.py needs a small
    recorder that keeps hold of the source image so a test can assert what
    was drawn -- passed in for the same reason install_tk() takes its
    widget classes as parameters.
    """
    pil = types.ModuleType("PIL")
    pil_image = types.ModuleType("PIL.Image")
    pil_image.open = FakeImage.open
    pil_image.Image = FakeImage
    pil_imagetk = types.ModuleType("PIL.ImageTk")
    pil_imagetk.PhotoImage = photoimage_cls
    pil.Image = pil_image
    pil.ImageTk = pil_imagetk
    sys.modules["PIL"] = pil
    sys.modules["PIL.Image"] = pil_image
    sys.modules["PIL.ImageTk"] = pil_imagetk
    return pil


_CAIRO_BASE_CONSTANTS = {
    "FORMAT_ARGB32": 0,
    "FONT_SLANT_NORMAL": 0,
    "FONT_WEIGHT_BOLD": 0,
}


def install_cairo(*, extra=None):
    """Install a fake `cairo` into sys.modules and return it.

    `extra` adds constants beyond the base three every caller needs --
    test_windows_popover_window.py's popover_draw also touches
    FONT_WEIGHT_NORMAL/LINE_CAP_ROUND/OPERATOR_SOURCE, which
    test_windows_tray.py's tray_icon never does; silently growing the base
    set for everyone would hide that difference instead of documenting it.
    """
    cairo = types.ModuleType("cairo")
    cairo.ImageSurface = lambda *a, **k: mock.MagicMock()
    cairo.Context = lambda *a, **k: mock.MagicMock()
    for const_name, value in _CAIRO_BASE_CONSTANTS.items():
        setattr(cairo, const_name, value)
    for const_name, value in (extra or {}).items():
        setattr(cairo, const_name, value)
    sys.modules["cairo"] = cairo
    return cairo


# --- pystray (Windows tray only) --------------------------------------------

def install_pystray():
    """Install a fake `pystray` into sys.modules and return it."""
    pystray = types.ModuleType("pystray")

    class _Icon:
        HAS_DEFAULT_ACTION = True

        def __init__(self, name, icon=None, title=None, menu=None):
            self.name, self.icon, self.title, self.menu = name, icon, title, menu

        def notify(self, msg, title=None):
            pass

        def run(self):
            pass

        def stop(self):
            pass

    class _MenuItem:
        def __init__(self, text, action, checked=None, radio=False,
                     default=False, visible=True, enabled=True):
            self.text = text
            self.action = action
            self.enabled = enabled
            self.default = default

    class _Menu:
        # Not a bare sentinel: pystray's real separator is an ordinary
        # MenuItem (pystray's _base.py's Menu.SEPARATOR,
        # `SEPARATOR = MenuItem('- - - -', None)`),
        # so it carries .text/.enabled like any other row and code that
        # iterates a Menu may read them off it. A plain object() here made
        # _refresh_menu's signature comprehension raise AttributeError
        # against the fake while being perfectly correct against the real
        # library -- a fake that invents a failure the shipped code cannot
        # have is as bad as one that hides a real one.
        SEPARATOR = _MenuItem("- - - -", None)

        def __init__(self, *items):
            self.items = items

        def __iter__(self):
            # The real pystray.Menu is iterable (_base.py's Menu.__iter__
            # -> _visible_items), and _refresh_menu relies on that to
            # build its change signature. A fake without __iter__ makes
            # every _refresh_menu call raise TypeError.
            #
            # Fidelity note: the real __iter__ goes through
            # _visible_items(), which drops separators at the head and
            # tail and collapses runs of them. This yields the items
            # verbatim. That difference does not affect what these tests
            # assert (whether a signature CHANGED between two builds), so
            # it is left simple rather than reimplemented and drifting.
            return iter(self.items)

    pystray.Icon = _Icon
    pystray.MenuItem = _MenuItem
    pystray.Menu = _Menu
    sys.modules["pystray"] = pystray
    return pystray


# --- gi / GTK (Linux tray only) ---------------------------------------------

def install_gi():
    """Install a fake gi/gi.repository.{Gtk,Gdk,GLib,AyatanaAppIndicator3}
    into sys.modules and return a namespace of the fakes.

    cairo is deliberately left alone by this function -- pycairo IS
    installed in the environment this normally runs in, so
    smartbar.paint.tray_icon/popover_draw import and run for real
    underneath the fake toolkit.
    """
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_a, **_k: None
    sys.modules["gi"] = gi

    repository = types.ModuleType("gi.repository")
    sys.modules["gi.repository"] = repository
    gi.repository = repository

    class _Window(FakeWidget):
        pass

    class _DrawingArea(FakeWidget):
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
    # A plain recorder, NOT an auto-runner: a test has to be able to tell
    # "queued for the main loop" apart from "ran inline on the worker
    # thread".
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


# --- rumps (macOS menubar only) ---------------------------------------------

def install_rumps(*, app_cls, timer_cls, menuitem_cls):
    """Install a fake `rumps` into sys.modules and return it.

    The three classes are supplied by the caller (test_macos_menubar.py)
    rather than built here, for the same reason install_tk() takes its
    widget classes as parameters: test_macos_menubar.py reaches back into
    `timer_cls.started` directly to reset it between tests, so it needs to
    keep holding the class object it passed in.
    """
    fake = types.ModuleType("rumps")
    fake.App = app_cls
    fake.Timer = timer_cls
    fake.MenuItem = menuitem_cls
    fake.notification = mock.Mock()
    fake.quit_application = mock.Mock()
    sys.modules["rumps"] = fake
    return fake
