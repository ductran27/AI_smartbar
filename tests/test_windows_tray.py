"""Tests for smartbar/windows/tray.py and smartbar/windows/popover_window.py.

None of this touches a real Win32 tray, pycairo, or a display -- it can't,
run from macOS. What it pins instead, and why:

  1. Both modules import cleanly under fake tkinter/PIL/PIL.ImageTk/cairo/
     pystray, and popover_window's module-level enable_dpi_awareness() call
     does not blow up on a real (non-win32) host, since that function
     gates its whole body on sys.platform == "win32" before ever touching
     ctypes.windll.
  2. The tray menu's row order, and every glyph-led row LABEL, match
     smartbar/linux/tray.py's -- read from that file's own source text at
     test time, never retyped from memory, so a row added to one file and
     forgotten on the other fails a test instead of drifting silently.
  3. THE ONE THAT MATTERS MOST: every hit-region name popover_layout.build()
     can actually produce is a name _on_popover_action's own source (read
     via ast, not hand-copied) actually dispatches on, AND dispatches to
     the RIGHT handler -- membership alone would pass even with quit and
     refresh's bodies swapped.
  4. The pystray-worker-thread -> tk-main-thread marshal contract
     (self._to_main): the two call sites new to this port (_on_check_
     update's menu rebuild, _quit's root.quit()) must go through it rather
     than touching tk state directly from the wrong thread.
  5. _build_menu's account-row branch (popover is None, a snapshot with
     multiple switchable accounts) actually reaches, per-row, the account
     number that row's own label names -- not whichever number a late-
     bound closure would leak.
  6. The _check_row three-state label and the CHECK_RESULT_SECONDS
     stickiness of a just-finished manual check.
  7. _set_icon asks render_pills for the tray's real 32px pixel size via
     `scale=`, not the historical 6x-bitmap default.

Deliberately NOT covered, and why: window placement/anchoring math
(_position/_anchor_corner/_work_area_at), DPI-awareness's actual Win32
ctypes.windll branch, and anything about focus or a real Canvas paint --
all of that needs either a live display or a faked ctypes.windll, and a
test built on either would be exercising the fake, not the code. (An
earlier draft of this file asserted `not hasattr(ctypes, "windll")` as a
stand-in for "this host can't reach that branch" -- that is a property of
the interpreter running the suite, not of the code under test, and is
guaranteed to flip to a false positive the one time this ever runs on the
Windows box this port targets. Deleted rather than kept green by luck.)

Every GUI-stubbed test snapshots the WHOLE of sys.modules in setUp and
restores it verbatim in tearDown -- see GuiStubbedTestCase's own docstring
for why restoring only the six stub keys (tkinter/PIL/.../cairo/pystray)
is not enough.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import re
import sys
import textwrap
import threading
import types
import unittest
from unittest import mock

import smartbar

LINUX_TRAY_PATH = os.path.join(os.path.dirname(smartbar.__file__), "linux", "tray.py")

_STUBBED_MODULES = ("tkinter", "PIL", "PIL.Image", "PIL.ImageTk", "cairo", "pystray")

# A row label that _build_menu writes to the menu literally, in either
# file: something starting with an emoji/dingbat/arrow glyph, running up to
# (but not including) the closing quote or an f-string "{" interpolation.
_GLYPH_LABEL_RE = re.compile(r'["\']([\U0001F300-\U0001FAFF←-⯿][^"\'{]*)')


class _FakeWidget:
    """A real class, not a MagicMock: Popover(tk.Toplevel) subclasses this
    at class-definition time (i.e. at import time), and a bare MagicMock()
    instance cannot be used as a base class -- "metaclass conflict" -- so
    the fake has to be an actual type."""
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        def _method(*a, **k):
            return None
        return _method


class _FakeImage:
    @staticmethod
    def open(buf):
        return _FakeImage()

    def load(self):
        pass


def _install_gui_stubs():
    """Fake tkinter/PIL/PIL.ImageTk/cairo/pystray into sys.modules.

    Left to GuiStubbedTestCase's full-sys.modules snapshot/restore to undo
    -- this function only installs, it never has to remember what it
    replaced."""
    tkinter = types.ModuleType("tkinter")
    tkinter.Tk = _FakeWidget
    tkinter.Toplevel = _FakeWidget
    tkinter.Canvas = _FakeWidget
    sys.modules["tkinter"] = tkinter

    pil = types.ModuleType("PIL")
    pil_image = types.ModuleType("PIL.Image")
    pil_image.open = _FakeImage.open
    pil_image.Image = _FakeImage
    pil_imagetk = types.ModuleType("PIL.ImageTk")
    pil_imagetk.PhotoImage = _FakeWidget
    pil.Image = pil_image
    pil.ImageTk = pil_imagetk
    sys.modules["PIL"] = pil
    sys.modules["PIL.Image"] = pil_image
    sys.modules["PIL.ImageTk"] = pil_imagetk

    cairo = types.ModuleType("cairo")
    cairo.ImageSurface = lambda *a, **k: mock.MagicMock()
    cairo.Context = lambda *a, **k: mock.MagicMock()
    cairo.FORMAT_ARGB32 = 0
    cairo.FONT_SLANT_NORMAL = 0
    cairo.FONT_WEIGHT_BOLD = 0
    sys.modules["cairo"] = cairo

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
        # Not a bare sentinel: pystray's real separator is an
        # ordinary MenuItem (_base.py:607, `SEPARATOR = MenuItem(
        # '- - - -', None)`), so it carries .text/.enabled like any
        # other row and code that iterates a Menu may read them off
        # it. A plain object() here made _refresh_menu's signature
        # comprehension raise AttributeError against the fake while
        # being perfectly correct against the real library --
        # a fake that invents a failure the shipped code cannot
        # have is as bad as one that hides a real one.
        SEPARATOR = _MenuItem("- - - -", None)

        def __init__(self, *items):
            self.items = items

        def __iter__(self):
            # The real pystray.Menu is iterable (_base.py's
            # Menu.__iter__ -> _visible_items), and _refresh_menu
            # relies on that to build its change signature. A fake
            # without __iter__ makes every _refresh_menu call raise
            # TypeError -- which nothing caught, because until
            # TestRefreshMenuSkipsNoOpReassignments below no test in
            # this file ever called it.
            #
            # Fidelity note: the real __iter__ goes through
            # _visible_items(), which drops separators at the head
            # and tail and collapses runs of them. This yields the
            # items verbatim. That difference does not affect what
            # these tests assert (whether a signature CHANGED
            # between two builds), so it is left simple rather than
            # reimplemented and drifting.
            return iter(self.items)

    pystray.Icon = _Icon
    pystray.MenuItem = _MenuItem
    pystray.Menu = _Menu
    sys.modules["pystray"] = pystray


class GuiStubbedTestCase(unittest.TestCase):
    """Installs the fake GUI/tray modules for one test, then restores ALL
    of sys.modules to its pre-test snapshot -- not just the six stub keys.

    Restoring only tkinter/PIL/PIL.Image/PIL.ImageTk/cairo/pystray is not
    enough: smartbar.windows.tray and smartbar.windows.popover_window each
    trigger a real, ordinary import of smartbar.paint.tray_icon and
    smartbar.paint.popover_draw the first time they run, and either paint
    module's own `import cairo` binds whatever object sys.modules["cairo"]
    holds AT THAT MOMENT into ITS OWN module globals, permanently -- Python
    does not re-execute an `import` statement for a module already cached
    in sys.modules. If a paint module gets its first-ever import while this
    file's fake cairo is installed, popping just "cairo" back afterwards
    leaves smartbar.paint.tray_icon/popover_draw themselves still bound to
    the fake, corrupting every OTHER test file that imports them later in
    the same `python -m unittest discover` process. (Confirmed against an
    earlier draft of this file that only restored the six stub keys:
    tests.test_popover_draw went from all-green to 12 errors + 1 failure
    when run after tests.test_windows_tray in the same process.)
    Snapshotting and restoring the whole of sys.modules removes every
    module this test's imports newly added, stub-bound or not, regardless
    of how many hops away from tkinter/PIL/cairo/pystray they are.
    """

    def setUp(self):
        self._sys_modules_snapshot = dict(sys.modules)
        _install_gui_stubs()

    def tearDown(self):
        for name in list(sys.modules):
            if name not in self._sys_modules_snapshot:
                del sys.modules[name]
        sys.modules.update(self._sys_modules_snapshot)


def _reimport(dotted_name):
    """A fresh, real execution of `dotted_name` under whichever GUI stubs
    are currently installed, even if some earlier test already cached it in
    sys.modules under a different stub state. Safe precisely because
    GuiStubbedTestCase.tearDown restores the FULL sys.modules snapshot
    afterwards -- whatever this (transitively) adds is gone by the next
    test regardless of whether this pop ever fires."""
    sys.modules.pop(dotted_name, None)
    return importlib.import_module(dotted_name)


class TestImportsCleanly(GuiStubbedTestCase):
    """Both modules load under the fake tkinter/PIL/PIL.ImageTk/cairo/
    pystray, on the real (non-win32) sys.platform this machine actually
    has. popover_window's module-level enable_dpi_awareness() call is safe
    here because that function's own first line is `if sys.platform !=
    "win32": return` -- it never reaches ctypes.windll on this host, so
    nothing about ctypes needs faking to prove the import is clean."""

    def test_tray_module_imports_and_defines_tray(self):
        mod = _reimport("smartbar.windows.tray")
        self.assertTrue(hasattr(mod, "Tray"))
        self.assertTrue(hasattr(mod, "main"))

    def test_popover_window_module_imports_and_defines_popover(self):
        mod = _reimport("smartbar.windows.popover_window")
        self.assertTrue(hasattr(mod, "Popover"))
        # Must actually subclass the stubbed tk.Toplevel -- not just share
        # its name -- confirming the class body executed against a real
        # base type rather than silently swallowing a metaclass error.
        self.assertIn(sys.modules["tkinter"].Toplevel, mod.Popover.__mro__)

    def test_popover_window_survives_missing_pil_imagetk(self):
        # popover_window's own try/except around `from PIL import ImageTk`
        # is meant to degrade to ImageTk=None rather than crash the whole
        # module -- the one part of "modules import cleanly" that needs
        # its OWN sub-case, since the common path stubs PIL.ImageTk in
        # rather than testing its absence.
        del sys.modules["PIL.ImageTk"]
        del sys.modules["PIL"].ImageTk
        mod = _reimport("smartbar.windows.popover_window")
        self.assertIsNone(mod.ImageTk)
        self.assertIsNotNone(mod._IMAGETK_IMPORT_ERROR)


def _method_body_text(path, method_name):
    """Just `method_name`'s own source text, not the whole file.

    Needed because "⇅ Check for updates" is not written inline in
    _build_menu -- it lives inside _check_row, a separate method defined
    later in the file -- so a whole-file substring search would order it
    by where _check_row happens to sit textually, not by where
    `self._check_row()` is actually called from within _build_menu. This
    finds the def line and cuts off at the next same-indent "    def ",
    isolating exactly the text _build_menu's own body appends things in.
    """
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    start = text.index(f"def {method_name}(")
    rest = text[start:]
    next_def = rest.find("\n    def ", 1)
    return rest if next_def == -1 else rest[:next_def]


def _marker_order(body_text, markers):
    """`markers` sorted by each one's first byte-offset in `body_text`."""
    offsets = {}
    for marker in markers:
        offset = body_text.find(marker)
        if offset == -1:
            raise ValueError(f"{marker!r} not found in body text")
        offsets[marker] = offset
    return sorted(markers, key=lambda m: offsets[m])


def _idle_check_label(tray_mod):
    """The real, current text of the idle "Check for updates" row -- read
    by actually calling Tray._check_row in its idle state, not retyped
    from memory, so a wording change there is caught here too rather than
    assumed identical between this file and tray.py."""
    tray = tray_mod.Tray.__new__(tray_mod.Tray)
    tray.checking = False
    tray.check_result = ""
    label, _callback, _clickable = tray_mod.Tray._check_row(tray)
    return label


# The rows every _build_menu (Linux and this port) always appends
# regardless of account/update state. "self._check_row()" stands in for
# the check row's own label, resolved via _idle_check_label instead of a
# memorized string -- see that helper's docstring.
_ALWAYS_PRESENT_MARKERS = (
    "🔎 Open AI smartbar",
    "⟳ Refresh now",
    "self._check_row()",
    "⚙ Open cswap TUI",
    "⏻ Quit",
)


class TestMenuRowsMatchLinuxReference(GuiStubbedTestCase):
    """The Windows menu's row order and row labels must match Linux's,
    since a user who knows one tray's layout should not be surprised by
    the other's."""

    def test_expected_row_order_is_read_from_the_linux_source_not_memorized(self):
        # Sanity on the derivation itself, independent of the Windows port:
        # scrambling the input order must not survive the sort.
        body = _method_body_text(LINUX_TRAY_PATH, "_build_menu")
        order = _marker_order(body, tuple(reversed(_ALWAYS_PRESENT_MARKERS)))
        self.assertEqual(order, list(_ALWAYS_PRESENT_MARKERS))

    def test_windows_menu_matches_that_order_when_popover_and_check_are_available(self):
        body = _method_body_text(LINUX_TRAY_PATH, "_build_menu")
        order = _marker_order(body, _ALWAYS_PRESENT_MARKERS)
        mod = _reimport("smartbar.windows.tray")
        expected = [_idle_check_label(mod) if marker == "self._check_row()"
                   else marker for marker in order]
        with mock.patch.object(mod.update_core, "enabled", return_value=True):
            tray = mod.Tray.__new__(mod.Tray)
            tray.popover = object()          # any non-None sentinel
            tray.snapshot = None
            tray.failures = 0
            tray.update_pending = ""
            tray.checking = False
            tray.check_result = ""
            menu = tray._build_menu()
        rows = [item for item in menu.items
               if item is not mod.pystray.Menu.SEPARATOR]
        labels = [item.text for item in rows]
        self.assertEqual(labels, expected)
        open_item = rows[0]
        self.assertEqual(open_item.text, "🔎 Open AI smartbar")
        # Primary-clicking the tray icon must open the panel: pystray's
        # `default` flag on the open row is what wires that up. Dropping
        # it would leave the port's headline interaction silently dead.
        self.assertTrue(open_item.default,
                        "the open row must carry pystray's default=True "
                        "(primary-click) flag")
        # And the separator sits exactly where both files put it: right
        # after the open/account block, before the (possibly absent)
        # update row and "Refresh now".
        sep_index = menu.items.index(mod.pystray.Menu.SEPARATOR)
        open_index = menu.items.index(open_item)
        self.assertEqual(sep_index, open_index + 1)

    def test_no_glyph_led_menu_label_in_linux_is_missing_from_windows(self):
        """Every literal, glyph-led row label _build_menu can put in front
        of a user on Linux must appear literally somewhere in the Windows
        _build_menu body too -- derived from BOTH files' own source text
        at test time, not a fixed set copied into this test, so a label
        ADDED to linux/tray.py and simply forgotten on the Windows port
        fails this test instead of leaving it green."""
        mod = _reimport("smartbar.windows.tray")
        linux_body = _method_body_text(LINUX_TRAY_PATH, "_build_menu")
        windows_body = _method_body_text(mod.__file__, "_build_menu")
        linux_labels = set(_GLYPH_LABEL_RE.findall(linux_body))
        self.assertTrue(linux_labels, "the glyph regex found nothing -- it broke")
        missing = sorted(label for label in linux_labels
                         if label not in windows_body)
        self.assertEqual(missing, [],
                        f"label(s) present in linux/tray.py's _build_menu with "
                        f"no literal match anywhere in windows/tray.py's: "
                        f"{missing!r}")

    def test_loading_row_text_matches_linux_when_there_is_no_popover_or_snapshot(self):
        # Linux and Windows share the exact literal "Loading…" /
        # "cswap error — see tray.log" text -- read here, not retyped, so
        # a wording change in one file is caught.
        with open(LINUX_TRAY_PATH, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('"Loading…" if self.failures == 0 else', text)
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = mod.Tray.__new__(mod.Tray)
            tray.popover = None
            tray.snapshot = None
            tray.failures = 0
            tray.update_pending = ""
            tray.checking = False
            tray.check_result = ""
            menu = tray._build_menu()
        first = menu.items[0]
        self.assertEqual(first.text, "Loading…")
        self.assertFalse(first.enabled)


def _dispatch_names(method):
    """(equality names, startswith prefixes) `method` actually compares its
    `name` parameter against, read from the method's own AST.

    Hand-copying this from the source instead would make
    TestActionDispatchMatchesLayoutHits tautological -- exactly the
    mistake this file is guarding against.
    """
    src = textwrap.dedent(inspect.getsource(method))
    tree = ast.parse(src)
    equals, prefixes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            left = node.left
            if isinstance(left, ast.Name) and left.id == "name":
                for op, comparator in zip(node.ops, node.comparators):
                    if (isinstance(op, ast.Eq)
                            and isinstance(comparator, ast.Constant)
                            and isinstance(comparator.value, str)):
                        equals.add(comparator.value)
        is_startswith_call = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "name")
        if is_startswith_call:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                prefixes.add(arg.value)
    return equals, prefixes


class TestActionDispatchMatchesLayoutHits(GuiStubbedTestCase):
    """popover_layout.build()'s real hit names vs. what _on_popover_action
    actually dispatches on, both sides derived from real, current code,
    neither one hand-copied.

    A hit name build() can produce that the dispatcher's `equals`/
    `prefixes` sets do not cover is a click region with no handler at all:
    the canvas reports a hit, on_action(hit.name) fires, and every branch
    in _on_popover_action falls through doing nothing. No exception, no
    log line -- just a button that silently does not work. (This class
    only proves every real hit name is RECOGNISED somewhere in the
    dispatcher -- see TestActionDispatchInvokesTheRightHandler below for
    proof it is recognised by the RIGHT branch.)
    """

    def test_every_real_hit_name_is_one_the_dispatcher_recognises(self):
        # Pure Python, no cairo/GTK/tkinter involved, so this needs none
        # of the GUI stubs -- imported here rather than at module scope so
        # nothing about this test depends on import order within the file.
        from smartbar.core import model as core_model
        from smartbar.core import popover_layout

        active = core_model.Account(
            number=1, email="active@example.com", active=True,
            metrics=[core_model.Metric(key="5h", label="5h", short="5h", pct=42.0)])
        switchable = core_model.Account(
            number=2, email="other@example.com", active=False,
            metrics=[core_model.Metric(key="5h", label="5h", short="5h", pct=7.0)])
        snapshot = core_model.Snapshot(accounts=[active, switchable])

        layout = popover_layout.build(snapshot, pending_version="9.9.9")
        real_hit_names = {hit.name for hit in layout.hits}
        # Confirms the fixture actually exercises all three hit shapes
        # this test cares about, rather than accidentally testing nothing.
        self.assertIn("update", real_hit_names)
        self.assertIn("switch:2", real_hit_names)
        self.assertTrue({"quit", "refresh"} <= real_hit_names)

        tray_mod = _reimport("smartbar.windows.tray")
        equals, prefixes = _dispatch_names(tray_mod.Tray._on_popover_action)
        self.assertTrue(equals, "no equality checks found -- ast walk broke")
        self.assertTrue(prefixes, "no startswith prefix found -- ast walk broke")

        for hit_name in real_hit_names:
            recognised = (hit_name in equals
                         or any(hit_name.startswith(p) for p in prefixes))
            self.assertTrue(
                recognised,
                f"popover_layout can emit hit {hit_name!r} but "
                f"_on_popover_action's equals={equals!r} / "
                f"prefixes={prefixes!r} would silently ignore a click on it")

    def test_hit_names_survive_even_with_no_accounts_and_no_pending_update(self):
        # The "quit"/"refresh" header glyphs are unconditional in build();
        # confirm they still resolve with an empty/None snapshot, the
        # state the panel is in before the first successful fetch.
        from smartbar.core import popover_layout

        layout = popover_layout.build(None)
        real_hit_names = {hit.name for hit in layout.hits}
        self.assertEqual(real_hit_names, {"quit", "refresh"})

        tray_mod = _reimport("smartbar.windows.tray")
        equals, _prefixes = _dispatch_names(tray_mod.Tray._on_popover_action)
        self.assertTrue(real_hit_names <= equals)


class TestActionDispatchInvokesTheRightHandler(GuiStubbedTestCase):
    """_on_popover_action must not just RECOGNISE each hit name -- it must
    route it to the CORRECT handler.

    Proven necessary by mutation: swapping the "quit" and "refresh"
    branches' bodies (name == "quit" calling _start_fetch(), name ==
    "refresh" calling _quit()) leaves every membership-only test above
    green, because both names are still compared somewhere -- clicking
    Refresh would kill the tray and nothing here would notice unless a
    test calls the dispatcher and checks WHICH mocked handler actually
    ran.
    """

    def _tray_with_mocked_actions(self, tray_mod):
        tray = tray_mod.Tray.__new__(tray_mod.Tray)
        tray.popover = None
        tray._quit = mock.Mock()
        tray._start_fetch = mock.Mock()
        tray._on_update = mock.Mock()
        tray._on_switch = mock.Mock()
        return tray

    def test_quit_hit_calls_quit_and_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray._on_popover_action("quit")
        tray._quit.assert_called_once_with()
        tray._start_fetch.assert_not_called()
        tray._on_update.assert_not_called()
        tray._on_switch.assert_not_called()

    def test_refresh_hit_calls_start_fetch_and_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray._on_popover_action("refresh")
        tray._start_fetch.assert_called_once_with()
        tray._quit.assert_not_called()
        tray._on_update.assert_not_called()
        tray._on_switch.assert_not_called()

    def test_update_hit_calls_on_update_and_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray._on_popover_action("update")
        tray._on_update.assert_called_once_with()
        tray._quit.assert_not_called()
        tray._start_fetch.assert_not_called()
        tray._on_switch.assert_not_called()

    def test_switch_hit_calls_on_switch_with_the_right_number(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray._on_popover_action("switch:7")
        tray._on_switch.assert_called_once_with(7)
        tray._quit.assert_not_called()
        tray._start_fetch.assert_not_called()
        tray._on_update.assert_not_called()

    def test_remove_hit_arms_the_confirm_state_and_removes_nothing(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = ""
        tray._on_remove = mock.Mock()
        tray._on_popover_action("remove:claude:2")
        self.assertEqual(tray.confirm, "claude:2")
        tray._on_remove.assert_not_called()
        tray._start_fetch.assert_not_called()

    def test_cancel_remove_clears_the_confirm_state(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = "claude:2"
        tray._on_remove = mock.Mock()
        tray._on_popover_action("cancel-remove")
        self.assertEqual(tray.confirm, "")
        tray._on_remove.assert_not_called()

    def test_confirm_remove_routes_the_token_to_on_remove(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = "openai:a@x.com"
        tray._on_remove = mock.Mock()
        tray._on_popover_action("confirm-remove:openai:a@x.com")
        tray._on_remove.assert_called_once_with("openai:a@x.com")

    def test_card_hover_region_click_does_nothing(self):
        # card:* is the hover container the ✕ affordance rides on; a click
        # on the card body must not action anything.
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = ""
        tray._on_remove = mock.Mock()
        tray._on_popover_action("card:claude:2")
        self.assertEqual(tray.confirm, "")
        for handler in (tray._quit, tray._start_fetch, tray._on_update,
                        tray._on_switch, tray._on_remove):
            handler.assert_not_called()

    def test_dismiss_error_hit_clears_action_error_and_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = ""
        tray.action_error = "Switch failed: in use"
        tray._on_remove = mock.Mock()
        tray._on_popover_action("dismiss-error")
        self.assertEqual(tray.action_error, "")
        for handler in (tray._quit, tray._start_fetch, tray._on_update,
                        tray._on_switch, tray._on_remove):
            handler.assert_not_called()


class _InlineThread:
    """threading.Thread stand-in that runs the target synchronously on
    start() — lets the remove flow be asserted without real threads."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class TestRemoveFlow(GuiStubbedTestCase):
    """_on_remove drops the card optimistically, calls the ONE core
    removal function for the right provider, and refetches — the truth
    that resurrects the card if the removal failed."""

    def _tray(self, mod):
        from smartbar.core import model as core_model
        tray = mod.Tray.__new__(mod.Tray)
        tray.popover = None
        tray.confirm = "stale"
        tray.action_error = "stale error"
        tray._start_fetch = mock.Mock()
        tray._to_main = mock.Mock()  # a failure marshals _set_action_error
        tray.snapshot = core_model.Snapshot(accounts=[
            core_model.Account(number=1, email="a@x.com", active=True),
            core_model.Account(number=2, email="b@x.com")])
        tray.snapshot.openai = [
            core_model.Account(number=1, email="live@x.com", active=True),
            core_model.Account(number=2, email="old@x.com")]
        return tray

    def test_claude_removal_goes_through_cswap_by_slot_number(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        with mock.patch.object(mod.threading, "Thread", _InlineThread), \
             mock.patch.object(mod.cswap, "remove_account") as removed:
            tray._on_remove("claude:2")
        removed.assert_called_once_with(2)
        self.assertEqual([a.number for a in tray.snapshot.accounts], [1])
        self.assertEqual(tray.confirm, "")
        tray._start_fetch.assert_called_once_with()

    def test_openai_removal_goes_through_codex_by_email(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        with mock.patch.object(mod.threading, "Thread", _InlineThread), \
             mock.patch.object(mod.codex, "remove_account") as removed:
            tray._on_remove("openai:old@x.com")
        removed.assert_called_once_with("old@x.com")
        self.assertEqual([a.email for a in tray.snapshot.openai],
                         ["live@x.com"])
        tray._start_fetch.assert_called_once_with()

    def test_a_core_failure_is_logged_not_raised_and_still_refetches(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        with mock.patch.object(mod.threading, "Thread", _InlineThread), \
             mock.patch.object(mod.cswap, "remove_account",
                               side_effect=mod.cswap.CswapError("in use")):
            tray._on_remove("claude:2")   # must not raise
        tray._start_fetch.assert_called_once_with()
        # A new attempt clears the sticky error from a previous one, then a
        # failure inside the daemon thread reports the new one -- marshaled
        # through _to_main, never touching self.action_error directly from
        # the worker thread.
        self.assertEqual(tray.action_error, "")
        tray._to_main.assert_called_once_with(
            tray._set_action_error, "Remove failed: in use")

    def test_a_successful_removal_leaves_no_stale_error_behind(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        with mock.patch.object(mod.threading, "Thread", _InlineThread), \
             mock.patch.object(mod.cswap, "remove_account"):
            tray._on_remove("claude:2")
        self.assertEqual(tray.action_error, "")
        tray._to_main.assert_not_called()


class TestThreadToUiMarshalling(GuiStubbedTestCase):
    """The pystray worker thread never touches tk widgets directly -- every
    call that must land on the main thread goes through self._to_main
    (== root.after_idle), the literal analogue of GLib.idle_add.

    Proven necessary by mutation: replacing
    `self._to_main(self._refresh_menu)` in _on_check_update with a direct
    `self._refresh_menu()` call is a real cross-thread tkinter mutation --
    the exact defect class this port exists to avoid -- and no other test
    in this file exercises the marshal itself, so it stays green without
    this class.
    """

    def test_on_check_update_marshals_the_menu_rebuild_through_to_main(self):
        mod = _reimport("smartbar.windows.tray")
        tray = mod.Tray.__new__(mod.Tray)
        tray.checking = False
        tray.check_result = ""
        tray.check_token = 0
        tray._to_main = mock.Mock()
        tray._refresh_menu = mock.Mock()
        tray._check_update = mock.Mock()  # avoid a real subprocess/thread

        tray._on_check_update()

        tray._to_main.assert_called_once_with(tray._refresh_menu)
        tray._refresh_menu.assert_not_called()

    def test_quit_marshals_root_quit_through_to_main_not_a_direct_call(self):
        mod = _reimport("smartbar.windows.tray")
        tray = mod.Tray.__new__(mod.Tray)
        tray.icon = mock.Mock()
        tray.root = mock.Mock()
        tray._to_main = mock.Mock()
        with mock.patch.object(mod, "presence_client") as fake_presence:
            tray._quit()
            fake_presence.leave.assert_called_once_with()
        tray.icon.stop.assert_called_once_with()
        tray._to_main.assert_called_once_with(tray.root.quit)
        tray.root.quit.assert_not_called()


class TestBuildMenuAccountRows(GuiStubbedTestCase):
    """_build_menu's account-row branch: popover is None (so the menu
    falls back to plain-text account rows) AND a snapshot IS present.

    Neither existing menu test above exercises this combination -- both
    set snapshot=None. Proven necessary by mutation: reintroducing the
    classic in-loop-lambda late-binding bug (closing over the shared loop
    variable instead of a fresh one per row, as `_account_switch_action`
    exists specifically to avoid) leaves every other test in this file
    green while every switchable account row silently switches to
    whichever account happened to be LAST in the loop.
    """

    def test_each_switchable_account_row_switches_to_its_own_number_not_the_last(self):
        from smartbar.core import model as core_model

        mod = _reimport("smartbar.windows.tray")
        active = core_model.Account(number=1, email="active@example.com",
                                    active=True)
        switchable_a = core_model.Account(number=2, email="a@example.com")
        switchable_b = core_model.Account(number=3, email="b@example.com")
        snapshot = core_model.Snapshot(
            accounts=[active, switchable_a, switchable_b])

        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = mod.Tray.__new__(mod.Tray)
            tray.popover = None
            tray.snapshot = snapshot
            tray.failures = 0
            tray.update_pending = ""
            tray.checking = False
            tray.check_result = ""
            menu = tray._build_menu()

        rows_by_number = {2: None, 3: None}
        for item in menu.items:
            if item is mod.pystray.Menu.SEPARATOR:
                continue
            for number in rows_by_number:
                if f" {number} " in item.text:
                    rows_by_number[number] = item
        self.assertTrue(all(rows_by_number.values()),
                        "expected one enabled menu row per switchable "
                        f"account number, got {rows_by_number!r}")

        tray._on_switch = mock.Mock()
        rows_by_number[2].action(mod.pystray.Icon, rows_by_number[2])
        tray._on_switch.assert_called_once_with(2)

        tray._on_switch.reset_mock()
        rows_by_number[3].action(mod.pystray.Icon, rows_by_number[3])
        tray._on_switch.assert_called_once_with(3)


def _bare_tray(mod):
    """A Tray with every attribute _check_row/_checked/_clear_check_result
    touch, but never running __init__ (which would need a real popover/
    icon/root)."""
    tray = mod.Tray.__new__(mod.Tray)
    tray.root = types.SimpleNamespace(after=lambda *a, **k: None,
                                      after_idle=lambda *a, **k: None,
                                      quit=lambda: None)
    tray.popover = None
    tray.snapshot = None
    tray.icon = mod.pystray.Icon("ai-smartbar", title="AI smartbar", menu=None)
    tray.checking = False
    tray.check_result = ""
    tray.check_token = 0
    tray.update_pending = ""
    tray.update_blocked = ""
    tray._pending_update = lambda: ""          # avoid touching a real state file
    tray._refresh_menu = mock.Mock()
    tray._send_alert = mock.Mock()
    tray._set_icon = mock.Mock()
    return tray


class TestCheckRowThreeStateAndStickiness(GuiStubbedTestCase):
    """The manual "⇅ Check for updates" row's three mutually-exclusive
    states, and how long a just-finished result stays visible before the
    row reverts (CHECK_RESULT_SECONDS)."""

    def test_checking_state_shows_a_disabled_progress_label(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.checking = True
        label, callback, clickable = tray._check_row()
        self.assertEqual(label, "⇅ Checking for updates…")
        self.assertIsNone(callback)
        self.assertFalse(clickable)

    def test_result_state_shows_the_outcome_disabled_until_cleared(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.checking = False
        tray.check_result = "Already up to date"
        label, callback, clickable = tray._check_row()
        self.assertEqual(label, "Already up to date")
        self.assertIsNone(callback)
        self.assertFalse(clickable)

    def test_idle_state_is_clickable_and_bound_to_on_check_update(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.checking = False
        tray.check_result = ""
        label, callback, clickable = tray._check_row()
        self.assertEqual(label, "⇅ Check for updates")
        self.assertTrue(clickable)
        self.assertEqual(callback.__func__, mod.Tray._on_check_update)

    def test_checked_schedules_the_clear_at_check_result_seconds(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.checking = True
        tray.check_token = 5
        scheduled = []
        tray.root.after = lambda *a: scheduled.append(a)
        answer = {"label": "Up to date", "title": "AI smartbar", "body": "..."}

        tray._checked(5, answer)

        self.assertFalse(tray.checking)
        self.assertEqual(tray.check_result, "Up to date")
        self.assertEqual(len(scheduled), 1)
        delay, callback, token = scheduled[0]
        self.assertEqual(delay, mod.CHECK_RESULT_SECONDS * 1000)
        self.assertEqual(callback, tray._clear_check_result)
        self.assertEqual(token, 5)

    def test_checked_ignores_a_result_for_a_stale_token(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.checking = True
        tray.check_token = 6           # a newer check has since started
        scheduled = []
        tray.root.after = lambda *a: scheduled.append(a)

        tray._checked(5, {"label": "Up to date", "title": "x", "body": "y"})

        self.assertTrue(tray.checking)   # untouched -- the stale result is dropped
        self.assertEqual(tray.check_result, "")
        self.assertEqual(scheduled, [])

    def test_clear_check_result_reverts_the_row_only_for_the_current_token(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.check_token = 5
        tray.check_result = "Up to date"

        tray._clear_check_result(4)      # stale token: must not clear
        self.assertEqual(tray.check_result, "Up to date")

        tray._clear_check_result(5)      # current token: clears
        self.assertEqual(tray.check_result, "")


class TestIconRequestsTargetPixelSize(GuiStubbedTestCase):
    """_set_icon must ask render_pills to draw at the tray's own target
    pixel size via `scale=`, not accept the historical 6x-bitmap default
    and downscale after the fact."""

    def test_set_icon_passes_the_configured_scale_not_the_default(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        del tray._set_icon  # this test exercises the real method, not the mock
        calls = []

        def fake_render_pills(states, target, update_pending=False, scale=1.0):
            calls.append((states, update_pending, scale))

        mod.render_pills = fake_render_pills

        tray._set_icon(["state"])

        self.assertEqual(len(calls), 1)
        states, update_pending, scale = calls[0]
        self.assertEqual(states, ["state"])
        self.assertFalse(update_pending)
        # The real, concrete expectation: a 32px tray icon. Comparing
        # `scale` only against mod.TRAY_ICON_SCALE (as an earlier draft of
        # this test did) is algebraically self-satisfying -- that constant
        # is DEFINED as TRAY_ICON_PX / 96, so a check phrased purely in
        # terms of it would pass for any value of TRAY_ICON_PX at all.
        self.assertEqual(mod.TRAY_ICON_PX, 32)
        self.assertAlmostEqual(scale, 32 / 96)
        self.assertNotEqual(scale, 1.0)


class TestRefreshMenuSkipsNoOpReassignments(GuiStubbedTestCase):
    """_refresh_menu must not hand pystray a new Menu when nothing the menu
    displays has changed.

    Assigning `icon.menu` runs pystray's _update_menu(), which calls
    DestroyMenu on the previous HMENU synchronously -- and if the user has
    just right-clicked, that is the handle pystray's own _on_notify is
    displaying through a blocking TrackPopupMenuEx on its worker thread.
    Neither path takes a lock, so a refresh landing in that window destroys
    the menu out from under the pointer. The mitigation is a
    (text, enabled) signature diff suppressing the reassignment when
    nothing visible moved, which matters because most refreshes come from
    routine polling where nothing has.

    Two things needed pinning here. The obvious one is that the diff works.
    The other is that _refresh_menu runs at all under the fakes: it
    ITERATES the menu to build its signature, and this file's fake Menu had
    no __iter__, so every call raised TypeError. Nothing went red, because
    no other test here ever called it -- the whole mitigation was
    uncovered.
    """

    def _tray_ready_to_refresh(self, mod):
        tray = mod.Tray.__new__(mod.Tray)
        tray.popover = None
        tray.snapshot = None
        tray.failures = 0
        tray.update_pending = ""
        tray.checking = False
        tray.check_result = ""
        tray.icon = mod.pystray.Icon("ai-smartbar")
        tray._last_menu_signature = None
        return tray

    def test_the_first_refresh_actually_sends_a_menu(self):
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = self._tray_ready_to_refresh(mod)
            tray._refresh_menu()
        self.assertIsNotNone(tray.icon.menu)

    def test_an_unchanged_menu_is_not_sent_again(self):
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = self._tray_ready_to_refresh(mod)
            tray._refresh_menu()
            first = tray.icon.menu
            tray._refresh_menu()
        self.assertIs(tray.icon.menu, first,
                      "an unchanged menu was reassigned anyway, which runs "
                      "DestroyMenu on a handle a popup may be displaying")

    def test_a_menu_whose_text_changed_is_sent(self):
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = self._tray_ready_to_refresh(mod)
            tray._refresh_menu()
            first = tray.icon.menu
            # The fallback row reads "Loading…" only while failures == 0.
            tray.failures = 3
            tray._refresh_menu()
        self.assertIsNot(tray.icon.menu, first,
                         "a menu whose visible text changed was never sent, "
                         "so the tray would keep showing stale rows")


class TestDefaultActionCapabilityUsesPystraysRealFieldName(GuiStubbedTestCase):
    """_SUPPORTS_DEFAULT must read HAS_DEFAULT_ACTION -- pystray's actual
    capability field (0.19.5 _base.py, beside HAS_MENU, HAS_MENU_RADIO and
    HAS_NOTIFICATION).

    An earlier draft read "HAS_DEFAULT", which exists on no version of
    pystray. Because the read goes through getattr with a True default, the
    wrong name evaluated to True on every install -- indistinguishable from
    a correct check that happens to pass, so nothing failed anywhere and
    the capability guard had quietly become the constant True. A test
    asserting only that _SUPPORTS_DEFAULT is True cannot tell those two
    apart. These give the fake Icon a FALSE HAS_DEFAULT_ACTION, which the
    module can only observe by reading that exact attribute, so a typo or a
    reversion turns them red.
    """

    def test_a_false_capability_flag_is_actually_observed(self):
        sys.modules["pystray"].Icon.HAS_DEFAULT_ACTION = False
        mod = _reimport("smartbar.windows.tray")
        self.assertFalse(mod._SUPPORTS_DEFAULT)

    def test_the_open_row_drops_the_default_kwarg_when_unsupported(self):
        sys.modules["pystray"].Icon.HAS_DEFAULT_ACTION = False
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = mod.Tray.__new__(mod.Tray)
            tray.popover = object()
            tray.snapshot = None
            tray.failures = 0
            tray.update_pending = ""
            tray.checking = False
            tray.check_result = ""
            menu = tray._build_menu()
        flags = [item.default for item in menu.items
                 if item is not mod.pystray.Menu.SEPARATOR]
        self.assertNotIn(True, flags,
                         "default=True was passed to a backend that pystray "
                         "says does not support a default action")


class TestTooltipTruncationCountsUtf16CodeUnits(GuiStubbedTestCase):
    """The MAX_* caps bound ctypes WCHAR arrays, so they must be measured in
    UTF-16 code units, not Python code points.

    smartbar/core/model.py's status glyphs are astral (U+1F7E2 and
    friends), and each costs two WCHARs. A title of 127 such code points is
    254 code units, overflowing szTip[128] and raising "string too long"
    from inside pystray -- swallowed by the caller's except, so the tooltip
    just silently stops updating. Slicing with [:127] cannot catch that;
    _fit_wchars is what makes the cap mean what the comment says it means.
    """

    def test_astral_glyphs_are_counted_as_two_units_each(self):
        mod = _reimport("smartbar.windows.tray")
        fitted = mod._fit_wchars("\U0001F7E2" * 100, 10)
        self.assertEqual(len(fitted.encode("utf-16-le")) // 2, 10)
        self.assertEqual(len(fitted), 5)

    def test_a_short_ascii_string_is_returned_unchanged(self):
        mod = _reimport("smartbar.windows.tray")
        self.assertEqual(mod._fit_wchars("AI smartbar", 127), "AI smartbar")

    def test_truncation_never_splits_a_surrogate_pair(self):
        mod = _reimport("smartbar.windows.tray")
        # An odd limit cannot fit a whole pair, so the last one is dropped
        # rather than half-emitted -- the result must still encode cleanly.
        fitted = mod._fit_wchars("\U0001F7E2" * 4, 5)
        self.assertEqual(len(fitted.encode("utf-16-le")) // 2, 4)
        fitted.encode("utf-16-le").decode("utf-16-le")


class TestTabActionUpdatesProviderAndLayout(GuiStubbedTestCase):
    """The v0.8.0 OpenAI-tab feature (linux/tray.py:122-3): a "tab:..." hit
    must update self.provider, and the very next popover layout must be
    built with that provider -- mirroring linux/tray.py:111's
    `provider=self.provider` passthrough. Proven by mutation: a dispatcher
    that recognises "tab:" but forgets to write self.provider would pass
    every hit-name-recognition test above while leaving the OpenAI tab
    permanently stuck on whichever provider auto-resolves first.
    """

    def test_tab_hit_sets_provider_and_next_layout_uses_it(self):
        mod = _reimport("smartbar.windows.tray")
        tray = mod.Tray.__new__(mod.Tray)
        tray.popover = None
        tray.provider = ""
        tray._quit = mock.Mock()
        tray._start_fetch = mock.Mock()
        tray._on_update = mock.Mock()
        tray._on_switch = mock.Mock()

        tray._on_popover_action("tab:openai")
        self.assertEqual(tray.provider, "openai")
        tray._quit.assert_not_called()
        tray._start_fetch.assert_not_called()
        tray._on_update.assert_not_called()
        tray._on_switch.assert_not_called()

        # The next layout build must carry the provider just set above --
        # this is what actually makes the click switch panel tabs.
        tray.snapshot = None
        tray.update_pending = ""
        tray.update_blocked = ""
        tray.failures = 0
        tray.last_error = ""
        tray.action_error = ""
        tray.refreshing = False
        with mock.patch.object(mod.popover_layout, "build") as fake_build:
            tray._popover_layout(hover="quit")
        _args, kwargs = fake_build.call_args
        self.assertEqual(kwargs.get("provider"), "openai")


class TestApplySnapshotStampsPlanBadgesAndOpenaiAccounts(GuiStubbedTestCase):
    """The v0.7.0 plan-badge and v0.8.0 OpenAI-tab features
    (linux/tray.py:420-3): every applied snapshot must be stamped with plan
    badges and the ChatGPT account list before anything renders -- the
    painter and popover_layout already know how to draw both, so a missing
    call here is a silent parity gap, not a crash.
    """

    def _tray_ready_for_apply_snapshot(self, mod):
        tray = mod.Tray.__new__(mod.Tray)
        tray.generation = 0
        tray.failures = 1
        tray.last_error = "boom"
        tray.snapshot = None
        tray.presence_started = True  # skip the first-snapshot beat() call
        tray.popover = None
        tray.icon = mock.Mock()
        tray.alerts = mock.Mock()
        tray.alerts.check.return_value = []
        tray.recapture = mock.Mock()
        tray.recapture.action.return_value = None
        tray._pending_update = mock.Mock(return_value="")
        tray._set_icon = mock.Mock()
        tray._refresh_menu = mock.Mock()
        return tray

    def test_apply_snapshot_stamps_plan_badges_and_openai_accounts(self):
        mod = _reimport("smartbar.windows.tray")
        from smartbar.core import model as core_model

        tray = self._tray_ready_for_apply_snapshot(mod)
        snap = core_model.Snapshot(accounts=[])
        fake_openai_accounts = [mock.Mock(name="chatgpt-account")]

        with mock.patch.object(mod, "presence_client") as fake_presence_client, \
             mock.patch.object(mod.plan, "apply_plans") as fake_apply_plans, \
             mock.patch.object(
                 mod.plan, "plans_by_email",
                 return_value={"a@example.com": "Max"}) as fake_plans_by_email, \
             mock.patch.object(
                 mod.codex, "accounts",
                 return_value=fake_openai_accounts) as fake_accounts:
            fake_presence_client.counts.return_value = {}
            tray._apply_snapshot(snap, 0)

        fake_plans_by_email.assert_called_once_with()
        fake_apply_plans.assert_called_once_with(snap, fake_plans_by_email.return_value)
        fake_accounts.assert_called_once_with()
        self.assertEqual(snap.openai, fake_openai_accounts)


class TestOptimisticSwitchFlip(GuiStubbedTestCase):
    """_on_switch/_begin_switch mirror UsageStore.swift:152-187: a belt
    check against a dead stored credential, an immediate ACTIVE-chip flip,
    a generation bump so a stale in-flight fetch cannot land after it, and
    action_error's sticky-until-next-attempt semantics on failure.
    """

    def _tray(self, mod):
        from smartbar.core import model as core_model
        tray = mod.Tray.__new__(mod.Tray)
        tray.popover = mock.Mock()
        tray.popover.get_visible.return_value = True
        tray.snapshot = core_model.Snapshot(accounts=[
            core_model.Account(number=1, email="a@x.com", active=True),
            core_model.Account(number=2, email="b@x.com", active=False),
            core_model.Account(number=3, email="dead@x.com", active=False,
                               ok=False, status="relogin_required")])
        tray.action_error = "stale error"
        tray.generation = 0
        tray._generation_lock = threading.Lock()
        tray._start_fetch = mock.Mock()
        tray._to_main = mock.Mock()
        return tray

    def test_on_switch_always_marshals_through_to_main(self):
        # Reached from BOTH the tk thread (a popover card click) and the
        # pystray worker thread (a fallback-menu row) -- must never touch
        # self.snapshot/self.popover directly, so this only asserts the
        # marshal happened, not what _begin_switch itself does.
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        tray._begin_switch = mock.Mock()
        tray._on_switch(2)
        tray._to_main.assert_called_once_with(tray._begin_switch, 2)
        tray._begin_switch.assert_not_called()

    def test_blocked_account_sets_action_error_and_touches_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        with mock.patch.object(mod.threading, "Thread") as thread_ctor:
            tray._begin_switch(3)   # slot 3 is relogin_required
        thread_ctor.assert_not_called()
        self.assertEqual(
            tray.action_error,
            "Cannot switch: Re-login required — sign in as this "
            "account in Claude Code once")
        self.assertEqual(tray.generation, 0)   # no in-flight fetch invalidated
        self.assertTrue(tray.snapshot.accounts[0].active)    # unchanged
        self.assertFalse(tray.snapshot.accounts[2].active)
        tray.popover.refresh_layout.assert_called_once_with()

    def test_healthy_switch_flips_active_immediately_and_bumps_generation(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        with mock.patch.object(mod.threading, "Thread", _InlineThread), \
             mock.patch.object(mod.cswap, "switch") as switch:
            tray._begin_switch(2)
        # The flip is synchronous, before cswap.switch() is even called --
        # that is what "optimistic" means.
        self.assertFalse(tray.snapshot.accounts[0].active)
        self.assertTrue(tray.snapshot.accounts[1].active)
        self.assertEqual(tray.action_error, "")   # a new attempt clears it
        self.assertEqual(tray.generation, 1)
        switch.assert_called_once_with(2)
        tray._start_fetch.assert_called_once_with()
        tray._to_main.assert_not_called()   # cswap.switch succeeded

    def test_switch_failure_marshals_action_error_and_still_refetches(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        with mock.patch.object(mod.threading, "Thread", _InlineThread), \
             mock.patch.object(mod.cswap, "switch",
                               side_effect=mod.cswap.CswapError("in use")):
            tray._begin_switch(2)
        tray._to_main.assert_called_once_with(
            tray._set_action_error, "Switch failed: in use")
        tray._start_fetch.assert_called_once_with()

    def test_set_action_error_repaints_a_visible_popover(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        tray._set_action_error("Switch failed: boom")
        self.assertEqual(tray.action_error, "Switch failed: boom")
        tray.popover.refresh_layout.assert_called_once_with()

    def test_set_action_error_skips_repaint_when_popover_is_hidden(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        tray.popover.get_visible.return_value = False
        tray._set_action_error("Switch failed: boom")
        tray.popover.refresh_layout.assert_not_called()


class TestRefreshingFlagTracksFetchLifecycle(GuiStubbedTestCase):
    """`refreshing` dims/disables the panel's ⟳ glyph while a fetch is in
    flight (build()'s `refreshing` parameter) and must clear on BOTH a
    successful and a failed fetch -- left stuck True would permanently
    disable the button."""

    def _tray(self, mod):
        tray = mod.Tray.__new__(mod.Tray)
        tray.generation = 0
        tray._generation_lock = threading.Lock()
        tray.last_fetch_at = 0.0
        tray.refreshing = False
        return tray

    def test_start_fetch_raises_the_flag_before_the_daemon_starts(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        with mock.patch.object(mod.threading, "Thread") as thread_ctor:
            tray._start_fetch()
        self.assertTrue(tray.refreshing)
        thread_ctor.assert_called_once()

    def test_apply_snapshot_clears_the_flag_on_success(self):
        mod = _reimport("smartbar.windows.tray")
        from smartbar.core import model as core_model
        tray = self._tray(mod)
        tray.refreshing = True
        tray.failures = 1
        tray.last_error = "boom"
        tray.presence_started = True
        tray.popover = None
        tray.icon = mock.Mock()
        tray.alerts = mock.Mock()
        tray.alerts.check.return_value = []
        tray.recapture = mock.Mock()
        tray.recapture.action.return_value = None
        tray._pending_update = mock.Mock(return_value="")
        tray._set_icon = mock.Mock()
        tray._refresh_menu = mock.Mock()
        snap = core_model.Snapshot(accounts=[])
        with mock.patch.object(mod, "presence_client") as fake_presence, \
             mock.patch.object(mod.plan, "apply_plans"), \
             mock.patch.object(mod.plan, "plans_by_email", return_value={}), \
             mock.patch.object(mod.codex, "accounts", return_value=[]):
            fake_presence.counts.return_value = {}
            tray._apply_snapshot(snap, 0)
        self.assertFalse(tray.refreshing)

    def test_apply_error_clears_the_flag_on_failure(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        tray.refreshing = True
        tray.failures = 0
        tray.popover = None
        tray.icon = mock.Mock()
        tray._set_icon = mock.Mock()
        tray._refresh_menu = mock.Mock()
        tray._apply_error("boom", 0)
        self.assertFalse(tray.refreshing)

    def test_a_superseded_completion_leaves_the_flag_untouched(self):
        # A generation mismatch means early return; the newer fetch already
        # set refreshing True and owns clearing it, so a superseded, late-
        # arriving failure must not clear it out from under that newer one.
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray(mod)
        tray.generation = 2
        tray.refreshing = True
        tray._apply_error("boom", 1)
        self.assertTrue(tray.refreshing)


if __name__ == "__main__":
    unittest.main()
