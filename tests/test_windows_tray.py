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
     (call_on_ui_thread/schedule): the concrete Win32 binding
     (root.after_idle/root.after, plus the post-shutdown drop guard), not
     the state machine that depends on it -- that machine is pinned once
     in tests/test_tray_controller.py.
  5. _build_menu's account-row branch (popover is None, a snapshot with
     multiple switchable accounts) actually reaches, per-row, the account
     number that row's own label names -- not whichever number a late-
     bound closure would leak.
  6. _set_icon/set_title/notify's toolkit-specific truncation (UTF-16 code
     units, not Python code points) and pixel-scale request.

The fetch/apply/alert/recapture/check-update state machine itself now lives
in smartbar.core.tray_controller (TrayController) and is pinned exactly
once, there, in tests/test_tray_controller.py -- see that file's own
docstring. What THIS file pins instead is the pystray/tkinter-facing half:
the TrayHost contract's concrete binding, and whatever stays genuinely
Win32-shaped and therefore cannot move into the controller at all.

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
for why restoring only the six stub keys (tkinter/PIL/.../cairo/pystray) is
not enough.
"""
from __future__ import annotations

import ast
import inspect
import os
import re
import sys
import textwrap
import types
import unittest
from unittest import mock

import smartbar
from tests.support import stubs

LINUX_TRAY_PATH = os.path.join(os.path.dirname(smartbar.__file__), "linux", "tray.py")

_STUBBED_MODULES = ("tkinter", "PIL", "PIL.Image", "PIL.ImageTk", "cairo", "pystray")

# A row label that _build_menu writes to the menu literally, in either
# file: something starting with an emoji/dingbat/arrow glyph, running up to
# (but not including) the closing quote or an f-string "{" interpolation.
_GLYPH_LABEL_RE = re.compile(r'["\']([\U0001F300-\U0001FAFF←-⯿][^"\'{]*)')


def _install_gui_stubs():
    """Fake tkinter/PIL/PIL.ImageTk/cairo/pystray into sys.modules, via the
    shared installers in tests/support/stubs.py -- see that module's
    docstring for why the construction itself lives there rather than
    here.

    Left to GuiStubbedTestCase's full-sys.modules snapshot/restore to undo
    -- this function only installs, it never has to remember what it
    replaced."""
    stubs.install_tk(tk_cls=stubs.FakeWidget)
    stubs.install_pil(photoimage_cls=stubs.FakeWidget)
    stubs.install_cairo()
    stubs.install_pystray()


class GuiStubbedTestCase(stubs.GuiStubbedTestCase):
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
    the same `python -m unittest discover` process. Snapshotting and
    restoring the whole of sys.modules removes every module this test's
    imports newly added, stub-bound or not, regardless of how many hops
    away from tkinter/PIL/cairo/pystray they are. See tests/support/
    stubs.py's own GuiStubbedTestCase for the shared snapshot/restore
    mechanics; this subclass only adds the install step.
    """

    def setUp(self):
        super().setUp()
        _install_gui_stubs()


_reimport = stubs.reimport


def _bare_tray(mod, snapshot=None):
    """A Tray with no constructor side effects: no pystray icon build, no
    popover window, no thread -- just the plain-attribute state the
    methods under test actually read, plus a REAL TrayController wired to
    this tray as its host (the controller itself is toolkit-free, so
    building one costs nothing here), mirroring how Tray.__init__ wires the
    two together."""
    tray = mod.Tray.__new__(mod.Tray)
    tray.controller = mod.TrayController(tray)
    tray.controller.snapshot = snapshot
    tray.provider = ""
    tray.confirm = ""
    tray._shutdown = False
    tray._last_menu_signature = None
    tray.root = types.SimpleNamespace(after=lambda *a, **k: None,
                                      after_idle=lambda *a, **k: None,
                                      quit=lambda: None)
    tray.popover = mock.MagicMock(name="popover")
    tray.popover.get_visible.return_value = True
    tray.icon = mod.pystray.Icon("ai-smartbar", title="AI smartbar", menu=None)
    return tray


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

    Needed because a check row's label is not written inline in
    _build_menu -- it lives inside TrayController._check_row -- so a
    whole-file substring search would order it by where that method
    happens to sit textually, not by where it is actually called from
    within _build_menu. This finds the def line and cuts off at the next
    same-indent "    def ", isolating exactly the text _build_menu's own
    body appends things in.
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
    by actually calling TrayController._check_row in its idle state, not
    retyped from memory, so a wording change there is caught here too
    rather than assumed identical between this file and tray.py. The row
    label now lives on the shared controller, not on either front-end, so
    a bare controller (no real host needed -- _check_row never touches
    one) is enough."""
    controller = tray_mod.TrayController(mock.Mock())
    controller.checking = False
    controller.check_result = ""
    label, _clickable = controller._check_row()
    return label


# The rows every _build_menu (Linux and this port) always appends
# regardless of account/update state. "c._check_row()" stands in for the
# check row's own label, resolved via _idle_check_label instead of a
# memorized string -- see that helper's docstring. Reads "c._check_row()"
# (not "self._check_row()") because both front-ends now read the state
# machine off a local `c = self.controller` alias, per the tray_controller
# extraction.
_ALWAYS_PRESENT_MARKERS = (
    "🔎 Open AI smartbar",
    "⟳ Refresh now",
    "c._check_row()",
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
        expected = [_idle_check_label(mod) if marker == "c._check_row()"
                   else marker for marker in order]
        with mock.patch.object(mod.update_core, "enabled", return_value=True):
            tray = _bare_tray(mod)
            tray.popover = object()          # any non-None sentinel
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
        self.assertIn('"Loading…" if c.failures == 0 else', text)
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = _bare_tray(mod)
            tray.popover = None
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
    branches' bodies (name == "quit" calling controller._start_fetch(),
    name == "refresh" calling _quit()) leaves every membership-only test
    above green, because both names are still compared somewhere --
    clicking Refresh would kill the tray and nothing here would notice
    unless a test calls the dispatcher and checks WHICH mocked handler
    actually ran.
    """

    def _tray_with_mocked_actions(self, tray_mod):
        tray = _bare_tray(tray_mod)
        tray.popover = None
        tray._quit = mock.Mock()
        tray._on_update = mock.Mock()
        tray._on_switch = mock.Mock()
        tray.controller = mock.MagicMock(name="controller")
        return tray

    def test_quit_hit_calls_quit_and_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray._on_popover_action("quit")
        tray._quit.assert_called_once_with()
        tray.controller._start_fetch.assert_not_called()
        tray._on_update.assert_not_called()
        tray._on_switch.assert_not_called()

    def test_refresh_hit_calls_start_fetch_and_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray._on_popover_action("refresh")
        tray.controller._start_fetch.assert_called_once_with()
        tray._quit.assert_not_called()
        tray._on_update.assert_not_called()
        tray._on_switch.assert_not_called()

    def test_update_hit_calls_on_update_and_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray._on_popover_action("update")
        tray._on_update.assert_called_once_with()
        tray._quit.assert_not_called()
        tray.controller._start_fetch.assert_not_called()
        tray._on_switch.assert_not_called()

    def test_switch_hit_calls_on_switch_with_the_right_number(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray._on_popover_action("switch:7")
        tray._on_switch.assert_called_once_with(7)
        tray._quit.assert_not_called()
        tray.controller._start_fetch.assert_not_called()
        tray._on_update.assert_not_called()

    def test_remove_hit_arms_the_confirm_state_and_removes_nothing(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = ""
        tray._on_popover_action("remove:claude:2")
        self.assertEqual(tray.confirm, "claude:2")
        tray.controller.on_remove.assert_not_called()
        tray.controller._start_fetch.assert_not_called()

    def test_cancel_remove_clears_the_confirm_state(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = "claude:2"
        tray._on_popover_action("cancel-remove")
        self.assertEqual(tray.confirm, "")
        tray.controller.on_remove.assert_not_called()

    def test_confirm_remove_routes_the_token_to_on_remove(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = "openai:a@x.com"
        tray._on_popover_action("confirm-remove:openai:a@x.com")
        tray.controller.on_remove.assert_called_once_with("openai:a@x.com")
        self.assertEqual(tray.confirm, "")

    def test_card_hover_region_click_does_nothing(self):
        # card:* is the hover container the ✕ affordance rides on; a click
        # on the card body must not action anything.
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = ""
        tray._on_popover_action("card:claude:2")
        self.assertEqual(tray.confirm, "")
        tray._quit.assert_not_called()
        tray.controller._start_fetch.assert_not_called()
        tray._on_update.assert_not_called()
        tray._on_switch.assert_not_called()
        tray.controller.on_remove.assert_not_called()

    def test_dismiss_error_hit_clears_action_error_and_nothing_else(self):
        mod = _reimport("smartbar.windows.tray")
        tray = self._tray_with_mocked_actions(mod)
        tray.confirm = ""
        tray.controller.action_error = "Switch failed: in use"
        tray._on_popover_action("dismiss-error")
        self.assertEqual(tray.controller.action_error, "")
        tray._quit.assert_not_called()
        tray.controller._start_fetch.assert_not_called()
        tray._on_update.assert_not_called()
        tray._on_switch.assert_not_called()
        tray.controller.on_remove.assert_not_called()


class TestThreadHandoff(GuiStubbedTestCase):
    """TrayController depends on host.call_on_ui_thread/host.schedule for
    every worker-thread -> UI-thread touch a fetch, switch, remove,
    recapture or check-update makes -- the module docstring calls these
    the highest-risk edits in the whole refactor, since they used to be
    3 separate GLib-idle_add-style call sites scattered across _fetch/
    _on_switch/_on_remove and are now one shared seam. Proven necessary by
    mutation: replacing call_on_ui_thread's body with a direct call is a
    real cross-thread tkinter mutation, and nothing else in this file
    would notice."""

    def test_call_on_ui_thread_is_root_after_idle(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        calls = []
        tray.root = types.SimpleNamespace(
            after_idle=lambda cb, *a: calls.append((cb, a)))
        callback = mock.Mock()
        tray.call_on_ui_thread(callback, 1, 2)
        self.assertEqual(calls, [(callback, (1, 2))])

    def test_a_callback_scheduled_after_shutdown_is_dropped(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray._shutdown = True
        tray.root = mock.Mock()
        tray.call_on_ui_thread(mock.Mock())
        tray.root.after_idle.assert_not_called()

    def test_schedule_forwards_seconds_in_milliseconds_and_the_args(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        calls = []
        tray.root = types.SimpleNamespace(
            after=lambda ms, cb, *a: calls.append((ms, cb, a)))
        callback = mock.Mock()
        tray.schedule(20, callback, "token")
        self.assertEqual(calls, [(20000, callback, ("token",))])

    def test_quit_marshals_root_quit_through_call_on_ui_thread(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.icon = mock.Mock()
        tray.root = mock.Mock()
        tray.call_on_ui_thread = mock.Mock()
        with mock.patch.object(mod, "presence_client") as fake_presence:
            tray._quit()
            fake_presence.leave.assert_called_once_with()
        tray.icon.stop.assert_called_once_with()
        tray.call_on_ui_thread.assert_called_once_with(tray.root.quit)
        tray.root.quit.assert_not_called()

    def test_on_check_update_delegates_to_the_controller(self):
        # The rebuild-marshal itself is TrayController._on_check_update's
        # job (pinned in test_tray_controller.py's
        # TestOnCheckUpdateMarshalsTheRebuild/TestEveryUiTouchIsMarshalled)
        # -- this only pins that the host's thin wrapper reaches it.
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.controller = mock.MagicMock(name="controller")
        tray._on_check_update()
        tray.controller._on_check_update.assert_called_once_with()


class TestSwitchDelegatesToController(GuiStubbedTestCase):
    """_on_switch is pure delegation onto TrayController.on_switch -- the
    decision logic (the blocked-account re-check, the marshal, the
    worker-thread tail) is pinned once in test_tray_controller.py's
    TestOnSwitch. What matters here is only that the toolkit-facing method
    reaches the right controller call with the right flip callable."""

    def test_on_switch_delegates_with_the_flip_callable(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.controller = mock.MagicMock(name="controller")
        tray._on_switch(3)
        tray.controller.on_switch.assert_called_once_with(
            3, tray._flip_active_optimistically)


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
            tray = _bare_tray(mod, snapshot=snapshot)
            tray.popover = None
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


class TestBuildMenuOpenAIRows(GuiStubbedTestCase):
    """_build_menu's fallback (popover is None) must also list OpenAI/ChatGPT
    accounts as read-only rows -- the same block linux/tray.py's fallback has.
    Windows dropped them entirely, so a Windows device that fell back to the
    text menu never saw its ChatGPT usage at all."""

    def test_openai_accounts_appear_as_readonly_rows_in_the_fallback_menu(self):
        from smartbar.core import model as core_model

        mod = _reimport("smartbar.windows.tray")
        active = core_model.Account(number=1, email="active@example.com",
                                    active=True)
        openai = core_model.Account(number=0, email="chatgpt@example.com",
                                    provider="openai")
        snapshot = core_model.Snapshot(accounts=[active], openai=[openai])

        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = _bare_tray(mod, snapshot=snapshot)
            tray.popover = None
            menu = tray._build_menu()

        by_text = {item.text: item for item in menu.items
                   if item is not mod.pystray.Menu.SEPARATOR}
        openai_row = mod.model.menu_row(openai)
        self.assertIn("OpenAI", by_text)
        self.assertIn(openai_row, by_text)
        # Read-only: no switcher exists for ChatGPT logins.
        self.assertFalse(by_text["OpenAI"].enabled)
        self.assertFalse(by_text[openai_row].enabled)


class TestIconRequestsTargetPixelSize(GuiStubbedTestCase):
    """set_icon must ask render_pills to draw at the tray's own target
    pixel size via `scale=`, not accept the historical 6x-bitmap default
    and downscale after the fact."""

    def test_set_icon_passes_the_configured_scale_not_the_default(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        calls = []

        def fake_render_pills(states, target, update_pending=False, scale=1.0):
            calls.append((states, update_pending, scale))

        mod.render_pills = fake_render_pills

        tray.set_icon(["state"], False)

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

    def test_forwards_the_update_pending_flag_it_was_given(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        calls = []
        mod.render_pills = lambda states, target, update_pending=False, \
            scale=1.0: calls.append(update_pending)
        tray.set_icon([], True)
        self.assertEqual(calls, [True])


class TestSetTitle(GuiStubbedTestCase):
    """set_title truncates in UTF-16 code units (see MAX_TITLE_LEN's own
    comment on szTip's ctypes buffer size) and forwards to icon.title."""

    def test_a_short_title_is_forwarded_unchanged(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.set_title("AI smartbar — 42%")
        self.assertEqual(tray.icon.title, "AI smartbar — 42%")

    def test_an_over_long_title_is_truncated_to_the_wchar_cap(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.set_title("\U0001F7E2" * 200)
        self.assertLessEqual(
            len(tray.icon.title.encode("utf-16-le")) // 2, mod.MAX_TITLE_LEN)


class TestNotify(GuiStubbedTestCase):
    """notify() forwards to pystray's Icon.notify(body, title) -- note the
    body-then-title argument order, the opposite of Icon's own title-then-
    menu constructor order -- with both fields truncated to their own,
    smaller ctypes buffer caps."""

    def test_forwards_truncated_body_and_title_to_icon_notify(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.icon = mock.Mock()
        alert = types.SimpleNamespace(title="Low", body="80% used")
        tray.notify(alert, "critical")
        tray.icon.notify.assert_called_once_with("80% used", "Low")

    def test_over_long_fields_are_truncated_before_reaching_pystray(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.icon = mock.Mock()
        alert = types.SimpleNamespace(title="T" * 200,
                                      body="\U0001F7E2" * 300)
        tray.notify(alert)
        body, title = tray.icon.notify.call_args.args
        self.assertLessEqual(len(body.encode("utf-16-le")) // 2,
                             mod.MAX_NOTIFY_BODY_LEN)
        self.assertLessEqual(len(title.encode("utf-16-le")) // 2,
                             mod.MAX_NOTIFY_TITLE_LEN)

    def test_a_notification_failure_is_swallowed_not_raised(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.icon = mock.Mock()
        tray.icon.notify.side_effect = Exception("boom")
        alert = types.SimpleNamespace(title="Low", body="80% used")
        tray.notify(alert, "critical")   # must not raise


class TestOpenPanelHotkey(GuiStubbedTestCase):
    """The open-panel hotkey's Windows half (Ctrl+Alt+A). The actual
    RegisterHotKey/GetMessageW pump in _run_hotkey_loop needs real
    ctypes.windll, which does not exist off win32 at all -- unlike this
    file's tkinter/PIL/pystray fakes, ctypes itself is not faked anywhere
    in this suite, so that loop genuinely cannot run here (see the module
    docstring's own note on this split). What IS pinned: the seam a
    WM_HOTKEY message is handed to, and that the Win32 constants this
    port sends RegisterHotKey are the real documented values rather than
    a typo that would silently register the wrong combination (or none
    at all).
    """

    def test_a_hotkey_message_reaches_the_exact_same_open_action(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        with mock.patch.object(tray, "_on_open") as on_open:
            mod._on_hotkey_message(tray)
        on_open.assert_called_once_with()

    def test_the_registered_combination_is_ctrl_alt_a_with_norepeat(self):
        # Real Win32 values (winuser.h): MOD_ALT=0x0001, MOD_CONTROL=0x0002,
        # MOD_NOREPEAT=0x4000, VK_A=0x41. A typo here would either fail to
        # register at all or silently claim a different key combination
        # than the one documented to the user.
        mod = _reimport("smartbar.windows.tray")
        self.assertEqual(mod.MOD_ALT, 0x0001)
        self.assertEqual(mod.MOD_CONTROL, 0x0002)
        self.assertEqual(mod.MOD_NOREPEAT, 0x4000)
        self.assertEqual(mod.VK_A, 0x41)
        self.assertEqual(mod.WM_HOTKEY, 0x0312)


class TestCheckUpdateArgv(GuiStubbedTestCase):
    def test_returns_sys_executable_prefixed_launcher_check_update_json(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        self.assertEqual(tray.check_update_argv(),
                         [mod.sys.executable, mod.LAUNCHER,
                          "--check-update", "--json"])


class TestPanelTriadDelegatesToPopover(GuiStubbedTestCase):
    def test_has_panel_true_when_a_popover_exists(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        self.assertTrue(tray.has_panel)

    def test_has_panel_false_when_no_popover_could_be_built(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.popover = None
        self.assertFalse(tray.has_panel)

    def test_show_hide_visible_and_refresh_all_forward_to_the_popover(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.show_panel()
        tray.popover.show_panel.assert_called_once()
        tray.hide_panel()
        tray.popover.hide_panel.assert_called_once()
        tray.popover.get_visible.return_value = True
        self.assertTrue(tray.panel_visible())
        tray.refresh_panel()
        tray.popover.refresh_layout.assert_called_once()


class TestRefreshMenuSkipsNoOpReassignments(GuiStubbedTestCase):
    """rebuild_menu must not hand pystray a new Menu when nothing the menu
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
    The other is that rebuild_menu runs at all under the fakes: it
    ITERATES the menu to build its signature, and this file's fake Menu had
    no __iter__, so every call raised TypeError. Nothing went red, because
    no other test here ever called it -- the whole mitigation was
    uncovered.
    """

    def _tray_ready_to_refresh(self, mod):
        tray = _bare_tray(mod)
        tray.popover = None
        tray.icon = mod.pystray.Icon("ai-smartbar")
        tray._last_menu_signature = None
        return tray

    def test_the_first_refresh_actually_sends_a_menu(self):
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = self._tray_ready_to_refresh(mod)
            tray.rebuild_menu()
        self.assertIsNotNone(tray.icon.menu)

    def test_an_unchanged_menu_is_not_sent_again(self):
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = self._tray_ready_to_refresh(mod)
            tray.rebuild_menu()
            first = tray.icon.menu
            tray.rebuild_menu()
        self.assertIs(tray.icon.menu, first,
                      "an unchanged menu was reassigned anyway, which runs "
                      "DestroyMenu on a handle a popup may be displaying")

    def test_a_menu_whose_text_changed_is_sent(self):
        mod = _reimport("smartbar.windows.tray")
        with mock.patch.object(mod.update_core, "enabled", return_value=False):
            tray = self._tray_ready_to_refresh(mod)
            tray.rebuild_menu()
            first = tray.icon.menu
            # The fallback row reads "Loading…" only while failures == 0.
            tray.controller.failures = 3
            tray.rebuild_menu()
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
            tray = _bare_tray(mod)
            tray.popover = object()
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
    """The v0.8.0 OpenAI-tab feature (linux/tray.py's _on_popover_action's
    "tab:" dispatch): a "tab:..." hit must update self.provider, and the
    very next popover layout must be built with that provider -- mirroring
    linux/tray.py's _popover_layout's `provider=self.provider` passthrough.
    Proven by mutation: a dispatcher
    that recognises "tab:" but forgets to write self.provider would pass
    every hit-name-recognition test above while leaving the OpenAI tab
    permanently stuck on whichever provider auto-resolves first.
    """

    def test_tab_hit_sets_provider_and_next_layout_uses_it(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.popover = None
        tray._quit = mock.Mock()
        tray._on_update = mock.Mock()
        tray._on_switch = mock.Mock()
        tray.controller._start_fetch = mock.Mock()

        tray._on_popover_action("tab:openai")
        self.assertEqual(tray.provider, "openai")
        tray._quit.assert_not_called()
        tray.controller._start_fetch.assert_not_called()
        tray._on_update.assert_not_called()
        tray._on_switch.assert_not_called()

        # The next layout build must carry the provider just set above --
        # this is what actually makes the click switch panel tabs.
        with mock.patch.object(mod.popover_layout, "build") as fake_build:
            tray._popover_layout(hover="quit")
        _args, kwargs = fake_build.call_args
        self.assertEqual(kwargs.get("provider"), "openai")

    def test_kill_hit_arms_confirm_and_confirm_kill_delegates(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.popover = None
        tray._on_popover_action("kill:100:1000")
        self.assertEqual(tray.confirm, "100:1000")
        tray.controller = mock.MagicMock(name="controller")
        tray._on_popover_action("confirm-kill:100:1000")
        tray.controller.on_kill.assert_called_once_with("100:1000")
        self.assertEqual(tray.confirm, "")

    def test_popover_layout_forwards_the_system_payload(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod)
        tray.controller.system = {"leftovers": {"rows": []},
                                  "busy": {"rows": []}}
        with mock.patch.object(mod.popover_layout, "build") as fake_build:
            tray._popover_layout()
        self.assertIs(fake_build.call_args.kwargs["system"],
                      tray.controller.system)

class TestOptimisticFlip(GuiStubbedTestCase):
    """TrayController.on_switch owns WHEN the optimistic flip runs (pinned
    in test_tray_controller.py's TestOnSwitch); _flip_active_optimistically
    is WHAT Windows does with it -- genuinely host-bound per the design's
    own divergence note, since the repaint is made through this host's own
    state.

    Unlike Linux, the icon/title/menu are deliberately NOT repainted here
    (see that method's own docstring): only the account list and the panel
    (if one is open) move immediately -- the icon lags one apply_snapshot
    cycle behind, same as macOS, exactly as this port did before the
    extraction.
    """

    def test_flip_moves_active_without_touching_generation(self):
        from smartbar.core import model as core_model
        mod = _reimport("smartbar.windows.tray")
        snap = core_model.Snapshot(accounts=[
            core_model.Account(number=1, email="a@x.com", active=True),
            core_model.Account(number=2, email="b@x.com")])
        tray = _bare_tray(mod, snapshot=snap)
        tray.controller.generation = 7

        tray._flip_active_optimistically(2)

        self.assertFalse(snap.accounts[0].active)
        self.assertTrue(snap.accounts[1].active)
        self.assertEqual(snap.active_account.number, 2)
        # Deliberately UNCHANGED. This flip is repaint-only; the bump that
        # matches UsageStore.swift's `fetchGeneration += 1` moved into
        # TrayController.on_switch so that all three hosts get one treatment
        # of it instead of three. This copy used to take the controller's
        # private _generation_lock, which was right here and wrong on Linux.
        self.assertEqual(tray.controller.generation, 7)
        tray.popover.refresh_layout.assert_called_once_with()

    def test_no_snapshot_yet_is_a_no_op_not_a_crash(self):
        mod = _reimport("smartbar.windows.tray")
        tray = _bare_tray(mod, snapshot=None)
        tray._flip_active_optimistically(2)   # must not raise
        self.assertEqual(tray.controller.generation, 0)
        tray.popover.refresh_layout.assert_not_called()

    def test_a_missing_popover_is_tolerated(self):
        from smartbar.core import model as core_model
        mod = _reimport("smartbar.windows.tray")
        snap = core_model.Snapshot(accounts=[
            core_model.Account(number=1, email="a@x.com", active=True)])
        tray = _bare_tray(mod, snapshot=snap)
        tray.popover = None
        tray._flip_active_optimistically(1)   # must not raise
        self.assertEqual(tray.controller.generation, 0)   # repaint-only now


if __name__ == "__main__":
    unittest.main()
