"""Windows system tray: pystray icon, launcher menu, and the card panel.

Ported from smartbar/linux/tray.py (AppIndicator + Gtk.Menu). The menu here
is still just the popover's launcher plus the actions -- Win32's classic
Shell_NotifyIcon menu can carry labels/icons/checkmarks and nothing richer,
same limitation dbusmenu has on Linux -- so the cards live in the popover
window (smartbar/windows/popover_window.py) and the menu falls back to text
rows if that window could not be built, exactly as on Linux.

The fetch/apply/alert/recapture/check-update state machine itself lives in
smartbar.core.tray_controller (TrayController) rather than here -- see that
module's own docstring for why. This class is TrayController's host: it
owns pystray/tk objects (the icon, the menu, the popover) and implements the
TrayHost contract (set_icon/set_title/rebuild_menu/call_on_ui_thread/
schedule/notify/check_update_argv/the panel triad) as thin bindings onto
pystray/tkinter, plus whatever stays genuinely Win32-shaped and therefore
platform-specific: building the actual pystray.MenuItem objects, the
signature-diffing reassignment dance, the popover window itself, and the
optimistic-switch flip's own (deliberately partial -- see
_flip_active_optimistically's own docstring) repaint.

Two event loops, not one. GTK gave the Linux tray a single main loop that
owned the AppIndicator menu, the popover window and every timer. pystray's
Icon.run() is only main-thread-mandatory on macOS (confirmed against the
pystray FAQ), so on Windows the pattern is Icon.run() on a background
"pystray worker" thread while a tkinter root.mainloop() owns the real main
thread and every tk widget (Decision D1 in CONTRACT.md). That split adds a
cross-thread boundary GTK never had: pystray dispatches its menu-item
callbacks (_on_open, _on_switch, _on_refresh, _on_check_update, _on_update,
_on_tui, _on_quit) on ITS OWN worker thread, not the tk thread, so any of
them that touches self.popover or self.icon.menu/.icon/.title must marshal
through `self.call_on_ui_thread(...)` (== root.after_idle), the exact
analogue of GLib.idle_add. Every marshal site is commented "thread -> UI
handoff" at its call.

Dropped, not ported (each is a Linux/AppIndicator-only workaround with no
Windows analogue -- see CONTRACT.md decisions D4/D6/D7 and the tray study's
findings 7-8): the alternating-icon-filename cache-bust dance (pystray's
Windows backend repaints immediately on `icon.icon = image`, no name cache
to defeat); middle-click-to-open (Win32 has a real primary-click default
action, so `_on_open` is wired as pystray's `default=True` menu item
instead, guarded by `Icon.HAS_DEFAULT_ACTION` -- confirmed against pystray
0.19.5's own _base.py, where it is one of the four HAS_* class fields
alongside HAS_MENU, HAS_MENU_RADIO and HAS_NOTIFICATION); libnotify/
notify-send (replaced by `Icon.notify()`, whose Windows balloon-vs-toast
rendering the API research could not confirm -- unverified-on-Windows);
the pending-menu-swap-until-hidden dance (GTK-shell-specific; pystray's
Menu is an immutable snapshot reassigned wholesale to `icon.menu`, so
`rebuild_menu` just does that every time); and Linux's refresh-the-
moment-the-menu-opens handler (linux/tray.py's _install_menu's
`menu.connect('show',
...)`, which starts a fetch if the data is >10s stale). pystray exposes no
"menu is about to open" hook the way GTK's Gtk.Menu 'show' signal did --
`_on_notify`'s right-click handling goes straight into `TrackPopupMenuEx`
with no callout a caller can observe -- so there is nothing to attach the
equivalent of that handler to for the native right-click menu. The one
path this module CAN see land on its own thread, the popover's left-click
open, keeps the same >10s-stale gate (`_on_open`, below); a right-click
open of the plain menu (fallback path, or whenever the popover is
unavailable) gets no such refresh and relies on the next regular `_tick`
instead.

Testing this module does not require a real Win32 tray, pycairo, or a
display. Before importing it, a test registers fakes in sys.modules for
"pystray", "tkinter", "PIL", "PIL.Image", "PIL.ImageTk" and "cairo" -- the
last two because this module imports smartbar.paint.tray_icon (which
imports cairo directly) and, once smartbar.windows.popover_window becomes
importable, that module needs the same tkinter/PIL/cairo fakes described in
its own docstring. A bare "can this import" smoke test only needs the first
three; exercising _make_popover's success path needs all five plus a real
(or faked) smartbar.windows.popover_window.Popover.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

import pystray
import tkinter as tk
from PIL import Image

from smartbar import __version__, presence_client
from smartbar.core import model, paths, popover_layout, portable, presence
from smartbar.core import update as update_core
from smartbar.core.tray_controller import TrayController
from smartbar.paint.tray_icon import render_pills

CACHE_DIR = paths.cache_dir()
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
LAUNCHER = os.path.join(REPO_ROOT, "bin", "ai-smartbar")
# Shell_NotifyIcon's tooltip field is a WCHAR szTip[128] INCLUDING the
# terminating NUL (confirmed against pystray 0.19.5's own ctypes struct,
# _util/win32.py: `('szTip', wintypes.WCHAR * 128)`), so 128 chars of real
# text leaves no room for that NUL: ctypes itself accepts a 128-char
# assignment without complaint, but the resulting buffer only fits real
# text through index 126, i.e. 127 WCHARs is the actual safe cap --
# code units, not code points; see _fit_wchars for why those differ
# here and why slicing with [:127] is not the same measurement.
# GTK/SNI's own limit (if any) is not the same, so title text must be
# re-truncated here rather than trusting the Linux-side message[:80] slice
# as sufficient.
MAX_TITLE_LEN = 127
# Notification balloon fields have their own, smaller ctypes buffers: szInfo
# WCHAR[256] (body) and szInfoTitle WCHAR[64] (title), both NUL-inclusive
# same as szTip above, so the safe caps are one less than the buffer size.
# Unlike the tray tooltip, nothing was truncating these before this fix — an
# over-long alert.body/alert.title would make ctypes raise "string too long"
# out of pystray's own _message(), which notify already logs and swallows,
# but the user never sees the notification at all.
MAX_NOTIFY_BODY_LEN = 255
MAX_NOTIFY_TITLE_LEN = 63
# render_pills' reference geometry is a 96px-tall bitmap at scale=1.0; a
# tray that does not scale for us (Windows, unlike AppIndicator) asks for
# the pixel size it wants directly instead: scale = height / 96.
TRAY_ICON_PX = 32
TRAY_ICON_SCALE = TRAY_ICON_PX / 96
# HAS_DEFAULT_ACTION is one of pystray's four HAS_* class fields (confirmed
# against 0.19.5's _base.py, alongside HAS_MENU/HAS_MENU_RADIO/
# HAS_NOTIFICATION). getattr still guards this rather than reading the
# attribute directly, since pystray does not promise every build/backend
# defines it -- but there is no "HAS_DEFAULT" field on any version; that
# name was simply wrong and made this getattr's default the only thing
# deciding the value on every install, verified or not.
_SUPPORTS_DEFAULT = getattr(pystray.Icon, "HAS_DEFAULT_ACTION", True)

log = logging.getLogger("ai-smartbar")


def _fit_wchars(text: str, limit: int) -> str:
    """Truncate `text` to at most `limit` UTF-16 code units.

    Every buffer these strings end up in is a ctypes `wintypes.WCHAR`
    array, and one WCHAR is one UTF-16 code unit -- so a character outside
    the Basic Multilingual Plane costs two slots, not one. Slicing with
    `text[:limit]` counts code points instead, which is a different number,
    and this codebase really does carry astral characters: the status
    glyphs in smartbar/core/model.py are U+1F7E2 and friends and the search
    row is U+1F50E, every one of them a surrogate pair. A 127-code-point
    title built from those is 254 WCHARs, overflowing szTip[128] and making
    ctypes raise "string too long" inside pystray's own icon update -- the
    exact failure the MAX_* caps exist to prevent, reintroduced by
    measuring in the wrong unit. Truncation happens on a code-point
    boundary so the result can never end mid-surrogate-pair.
    """
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    kept, used = [], 0
    for char in text:
        cost = 2 if ord(char) > 0xFFFF else 1
        if used + cost > limit:
            break
        kept.append(char)
        used += cost
    return "".join(kept)


def _prime_dpi_awareness():
    """Import the popover module now, before any Tk widget can exist.

    smartbar.windows.popover_window sets per-monitor DPI awareness as an
    import-time side effect, because Microsoft's own docs say it must run
    before the first UI object is created and cannot be changed afterwards.
    Tray._make_popover() imports that same module lazily (mirroring Linux's
    try/except fallback so a missing pycairo/PIL degrades to a text menu
    instead of crashing), but by the time __init__ reaches that point
    `root = tk.Tk()` already exists in main() -- too late. Importing here,
    before Tk() is constructed, is what makes the timing right regardless
    of whether the popover ends up available at all; the module import
    itself is cached, so _make_popover's later import is a cheap no-op.
    """
    try:
        from smartbar.windows import popover_window  # noqa: F401
    except Exception:
        log.exception("could not prime DPI awareness before Tk() existed")


class Tray:
    def __init__(self, root):
        # 60s harvests cswap's poll plans the moment they come due (incl.
        # the 60s urgent cadence near the limit); the store still paces the
        # real network, so faster polling adds no API traffic.
        self.root = root
        self.interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self.controller = TrayController(self)
        self.provider = ""   # panel tab; "" auto-resolves in the layout
        self.confirm = ""    # card awaiting remove confirmation, or ""
        self._shutdown = False  # set once _quit has scheduled root.quit
        self._last_menu_signature = None  # last (text, enabled) rows sent to pystray
        self.controller._pending_update()  # seeds update_pending/blocked
        self.pinned = model.panel_pinned()
        self.popover = self._make_popover()
        self.icon = pystray.Icon("ai-smartbar", title="AI smartbar",
                                 menu=self._build_menu())
        # Hollow "?" pills until the first fetch lands.
        self.set_icon([], bool(self.controller.update_pending))
        if self.pinned and self.popover is not None:
            # Up before the first fetch lands: the panel renders its own
            # loading/error state and refreshes in place once data arrives.
            # __init__ runs on the tk main thread (main() calls Tray(root)
            # before the pystray worker thread starts), so no marshal here.
            self.popover.show_panel()

    def _make_popover(self):
        """The card panel, or None if this session cannot host a window.

        Never fatal: the menu keeps working as a plain-text fallback (see
        _build_menu), which matters when pycairo, PIL's Tk support, or a
        display of some kind is missing.
        """
        try:
            from smartbar.windows.popover_window import Popover
            return Popover(self._popover_layout, self._on_popover_action,
                           pinned=self.pinned)
        except Exception:
            log.exception("popover unavailable; falling back to a text menu")
            return None

    def _popover_layout(self, hover=""):
        """Fresh layout for the panel — same builder the macOS/Linux popovers
        use, so the three platforms cannot drift apart in what a card says."""
        c = self.controller
        return popover_layout.build(
            c.snapshot, version=__version__,
            pending_version=c.update_pending,
            blocked_reason=c.update_blocked,
            fetched_at=c.snapshot.fetched_at if c.snapshot else "",
            stale=bool(c.failures and c.snapshot),
            error=c.last_error if c.snapshot is None else "",
            hover=hover, provider=self.provider, confirm=self.confirm,
            action_error=c.action_error, refreshing=c.refreshing,
            stale_reason=c.last_error)

    # --- TrayHost: thread -> UI handoff --------------------------------

    def call_on_ui_thread(self, callback, *args):
        """Marshal `callback(*args)` onto the tk main thread. thread -> UI handoff.

        The literal analogue of GLib.idle_add: whatever calls this is NOT
        allowed to touch self.popover or self.icon.icon/.menu/.title itself.
        On Linux only the fetch/check daemon threads needed this (GTK's menu
        callbacks already ran on the one true main loop). Here pystray's
        worker thread ALSO dispatches every menu-item callback (_on_open,
        _on_switch, _on_refresh, _on_check_update, _on_update, _on_tui,
        _on_quit) off the tk thread (Decision D1), so this module routes any
        of those that touch UI state through here too.

        Guarded by self._shutdown: TrayController's fetch/check/switch/
        remove worker threads are plain `daemon=True` threads with nothing
        joining them, so one can still be mid-flight when the user quits
        (e.g. the 120s --check-update subprocess). Without this guard its
        late `call_on_ui_thread(...)` call reaches `root.after_idle` on a
        root that `_quit` has already told to stop -- after_idle then
        blocks in WaitForMainloop for about a second and raises
        RuntimeError, on whatever daemon thread happened to call it.
        `_quit` sets `_shutdown` only once its own root.quit() marshal is
        already queued, so that one call always still goes through.

        Residual risk, deliberately left open: this is check-then-
        act. A daemon can read the flag as False, be descheduled,
        and resume after _quit has set it and mainloop has already
        returned -- landing exactly the RuntimeError the guard
        exists to prevent. Closing it properly means joining the
        daemons with a timeout on the quit path instead of leaving
        them fire-and-forget, which is a larger change than this
        fix. The guard removes the common case (a callback
        scheduled well after quit); it does not remove the race.
        """
        if self._shutdown:
            log.info("dropped a callback scheduled after shutdown: %r", callback)
            return
        self.root.after_idle(callback, *args)

    def schedule(self, seconds, callback, *args):
        """Run callback(*args) on the tk thread after `seconds`. tkinter's
        `root.after(ms, func, *args)` natively forwards args -- unlike
        linux/tray.py's schedule, which has to wrap the call because
        GLib.timeout_add_seconds only invokes its callback with no
        arguments of its own."""
        self.root.after(int(seconds * 1000), callback, *args)

    def _on_popover_action(self, name):
        """Route a hit-tested click from the panel.

        Fires from Popover's own Canvas <Button-1> binding, which only ever
        runs on the tk main thread (tk delivers widget events on whichever
        thread is running mainloop) — no marshal needed here, unlike the
        pystray-dispatched handlers below.
        """
        if name == "quit":
            self._quit()
            return
        if name == "refresh":
            self.controller._start_fetch()
        elif name == "update":
            self._on_update()
        elif name.startswith("tab:"):
            self.provider = name.split(":", 1)[1]
            self.confirm = ""
        elif name.startswith("confirm-remove:"):
            self.confirm = ""
            self.controller.on_remove(name.split(":", 1)[1])
        elif name == "cancel-remove":
            self.confirm = ""
        elif name.startswith("remove:"):
            # First click of the two-step removal: arm the in-card confirm.
            self.confirm = name.split(":", 1)[1]
        elif name.startswith("card:"):
            pass   # hover container — a click on the card body does nothing
        elif name.startswith("switch:"):
            self._on_switch(int(name.split(":", 1)[1]))
        elif name == "dismiss-error":
            self.controller.action_error = ""
        if self.popover is not None:
            self.popover.refresh_layout()

    def _on_open(self):
        """pystray's default-action callback (primary click / the launcher
        menu row) — fires on the pystray worker thread, not the tk thread,
        so touching the popover has to go through call_on_ui_thread. thread
        -> UI handoff. New relative to Linux: _on_open there ran on GTK's
        single main loop and could call popover.show_panel() directly.
        """
        if self.popover is None:
            return
        self.confirm = ""   # a fresh open never starts mid-question
        self.call_on_ui_thread(self.popover.show_panel)  # thread -> UI handoff
        # Opening the panel is the user looking: refresh so what they read is
        # current (cswap's store paces the real network traffic). Safe to
        # call from any thread — see TrayController._start_fetch's own
        # docstring.
        if time.monotonic() - self.controller.last_fetch_at > 10:
            self.controller._start_fetch()

    def _build_menu(self):
        """The tray menu is the panel's launcher plus the actions.

        Shell_NotifyIcon's native popup menu can only carry labels/icons/
        checkmarks, so the cards live in the popover window. When that
        window could not be created the old text rows come back, so the
        tray is still usable — the same fallback Linux's dbusmenu limit
        forced (linux/tray.py's _build_menu docstring).
        """
        c = self.controller
        rows = []
        if self.popover is not None:
            open_kwargs = {"default": True} if _SUPPORTS_DEFAULT else {}
            rows.append(pystray.MenuItem("🔎 Open AI smartbar",
                                         self._menu_open, **open_kwargs))
        elif c.snapshot is None:
            label = "Loading…" if c.failures == 0 else "cswap error — see tray.log"
            rows.append(pystray.MenuItem(label, _noop, enabled=False))
        else:
            stale = "  (stale)" if c.failures else ""
            for acct in c.snapshot.accounts:
                label = model.menu_row(acct) + (stale if acct.active else "")
                # A dead stored credential must not be switched to: it would
                # restore a login Anthropic already rejected.
                if acct.active or model.switch_blocked(acct):
                    rows.append(pystray.MenuItem(label, _noop, enabled=False))
                else:
                    action = _account_switch_action(self, acct.number)
                    rows.append(pystray.MenuItem(label, action))
        rows.append(pystray.Menu.SEPARATOR)
        if c.update_pending:
            rows.append(pystray.MenuItem(f"⬆ Update to {c.update_pending}",
                                         self._menu_update))
        # "Refresh now" re-reads usage; this asks the REMOTE whether a new
        # release exists, which otherwise only happened on the updater's own
        # 6-hourly timer — so a device could sit up to 6h behind with no way
        # to ask. Hidden when this device has opted out of updating, where a
        # check button would promise something that cannot happen.
        rows.append(pystray.MenuItem("⟳ Refresh now", self._menu_refresh))
        if update_core.enabled():
            check_label, clickable = c._check_row()
            action = (_check_row_action(self._on_check_update) if clickable
                      else _noop)
            rows.append(pystray.MenuItem(check_label, action, enabled=clickable))
        rows.append(pystray.MenuItem("⚙ Open cswap TUI", self._menu_tui))
        rows.append(pystray.MenuItem("⏻ Quit", self._menu_quit))
        return pystray.Menu(*rows)

    # --- TrayHost: rebuild_menu -----------------------------------------

    def rebuild_menu(self):
        """Reassign the whole menu — pystray has no "currently open, defer
        the swap" signal the way GTK's Gtk.Menu show/hide did (Decision D7),
        so unlike Linux's rebuild_menu/_install_menu split there is nothing
        to hold back: every rebuild goes straight to `icon.menu`. Must only
        run on the tk main thread (every caller already arranges that,
        directly or via call_on_ui_thread).

        CORRECTION to the D7 note above: "nothing to hold back" describes
        the API, not the risk. Reassigning `icon.menu` runs pystray's
        `_update_menu()`, which calls `win32.DestroyMenu` on the previous
        HMENU synchronously on THIS (tk) thread with no lock. If the user
        has just right-clicked, pystray's own `_on_notify` is showing that
        exact HMENU via a blocking `TrackPopupMenuEx` call on the pystray
        worker thread at the same time (confirmed in pystray 0.19.5's
        _win32.py: neither path takes a lock). A refresh landing in that
        window destroys the handle the popup is still displaying. pystray
        exposes no "menu is open" signal to defer on and no lock to share,
        so closing this race fully would mean monkeypatching pystray's
        private `_message_handlers`/`_on_notify`/`_update_menu` — worse, in
        the judgement of this port, than the bug: it would trade a rare,
        cosmetic popup glitch for a hard dependency on pystray internals
        that can silently change across versions.

        Mitigation actually applied: skip the reassignment (and therefore
        the DestroyMenu call) whenever the built menu's visible text/enabled
        state is identical to what was last sent, via `_last_menu_signature`.
        Most `rebuild_menu` calls come from routine polling (`_tick` ->
        `TrayController._apply_snapshot`) where nothing the menu shows has
        actually changed, so this removes most of the reassignments a user
        could collide with — it narrows the window, it does not close it. A
        refresh that DOES carry new content (an account switch, a manual
        check finishing, an update becoming available) still reassigns and
        still carries the residual race described above.
        """
        menu = self._build_menu()
        signature = tuple((item.text, item.enabled) for item in menu)
        if signature == self._last_menu_signature:
            return
        self._last_menu_signature = signature
        self.icon.menu = menu

    # -- pystray menu-item actions ------------------------------------
    # Thin (icon, item) -> None wrappers so the actual handlers below can
    # stay argument-free like their Linux (_item)-only counterparts. Every
    # one of these fires on the pystray worker thread (Decision D1), same
    # as Linux's GTK "activate" signal fired on the one true main loop —
    # the difference is that here it is NOT the tk thread, so each handler
    # marshals internally wherever it touches self.popover/self.icon.

    def _menu_open(self, icon, item):
        self._on_open()

    def _menu_update(self, icon, item):
        self._on_update()

    def _menu_refresh(self, icon, item):
        self._on_refresh()

    def _menu_tui(self, icon, item):
        self._on_tui()

    def _menu_quit(self, icon, item):
        self._on_quit()

    # --- switch ------------------------------------------------------

    def _on_switch(self, number):
        """Reached from BOTH the tk thread (_on_popover_action, a card's
        "Make Active" hit) and the pystray worker thread (a fallback-menu
        account row, via _account_switch_action) -- unlike _on_remove,
        which only ever fires from the tk thread. TrayController.on_switch
        itself is safe to call from either: it only ever touches host state
        through call_on_ui_thread (see that method's own docstring)."""
        self.controller.on_switch(number, self._flip_active_optimistically)

    def _flip_active_optimistically(self, number):
        """ACTIVE flip supplied to TrayController.on_switch as the
        flip_active callable -- always reached through call_on_ui_thread
        (see that method's docstring), so this only ever runs on the tk
        thread.

        Unlike Linux's _flip_active_optimistically, the icon/title/menu are
        deliberately NOT repainted here -- only the account list itself
        moves, plus the panel if one is open. The icon lags one
        set_icon/set_title/rebuild_menu cycle behind (i.e. until the forced
        refetch TrayController.on_switch starts next actually lands),
        exactly as before this extraction and the same as macOS today; see
        the design's per_platform note on set_icon for why the two front-
        ends were never required to repaint at the same point.

        This callable is repaint-only. It used to bump `generation` under
        the controller's private _generation_lock, which was correct here
        but wrong on Linux, where the same step was a bare `+=` -- three
        hosts, three different treatments of one controller invariant.
        TrayController.on_switch now owns that bump for every host, so no
        front-end reaches into the controller's lock at all.
        """
        c = self.controller
        if c.snapshot is None:
            return
        for acct in c.snapshot.accounts:
            acct.active = acct.number == number
        if self.popover is not None:
            self.popover.refresh_layout()

    # --- update apply / check row --------------------------------------

    def _on_update(self):
        """Apply the waiting release. Detached on purpose: the updater
        restarts this very tray, so it must not be our child."""
        try:
            portable.spawn_detached([sys.executable, LAUNCHER, "--update"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            log.exception("could not start the updater")

    def _on_check_update(self):
        """Fires on the pystray worker thread (via _check_row_action's
        wrapper in _build_menu, or _menu_open-style dispatch); the rebuild
        this triggers is marshaled by TrayController itself through
        call_on_ui_thread, so nothing further is needed here."""
        self.controller._on_check_update()

    # --- TrayHost: the manual check's subprocess ------------------------

    def check_update_argv(self):
        """`sys.executable` prefixes LAUNCHER because Windows has no
        shebang-based execution the way POSIX does (presence_client.py
        already does the same for its own beat()/leave() subprocess
        calls)."""
        return [sys.executable, LAUNCHER, "--check-update", "--json"]

    def _on_refresh(self):
        self.controller._start_fetch()

    def _on_tui(self):
        """Open a console running the cswap TUI.

        Linux shells out to whatever `x-terminal-emulator` is registered;
        Windows has no such alias, so `cmd /c start "" cswap tui` asks the
        shell to open a brand-new console window itself — the empty ""
        is `start`'s own window-title argument, required whenever the
        command that follows takes arguments of its own (otherwise `start`
        treats the first quoted string as the command, per cmd's own
        `start /?` help text).

        `start` hands the launch off and cmd.exe returns 0 regardless of
        whether it could actually find `cswap`, so the `except OSError`
        below only ever catches cmd.exe itself failing to start — it cannot
        catch a missing cswap, the actual failure this row exists to
        report. Checked with `shutil.which` up front instead, which is what
        actually reports that failure. `**portable.no_window()` is restored
        here too: without it the `cmd /c` launcher process itself flashes a
        console for an instant before `start` opens the real, visible
        window — the exact flash `no_window()` exists to suppress for a
        short-lived wrapper process (see its own docstring); the terminal
        `start` opens for `cswap tui` is unaffected, since that is a
        separate process `start` creates without CREATE_NO_WINDOW.
        """
        if shutil.which("cswap") is None:
            log.error("could not open the cswap TUI: cswap not found on PATH")
            return
        try:
            subprocess.Popen(["cmd", "/c", "start", "", "cswap", "tui"],
                             **portable.no_window())
        except OSError:
            log.exception("could not open terminal")

    # --- TrayHost: notification ------------------------------------------

    def notify(self, alert, urgency="critical"):
        """Uses pystray's Icon.notify() (Decision D6) rather than a hand-
        rolled Win32 toast/balloon: it is the one notification path needing
        zero new Windows-specific code. Unverified-on-Windows: whether this
        renders as a legacy balloon or a modern action-center toast — the
        API research could not confirm either way. The length limits ARE
        known (confirmed against pystray 0.19.5's ctypes struct): body and
        title are truncated to MAX_NOTIFY_BODY_LEN/MAX_NOTIFY_TITLE_LEN so
        an over-long alert degrades to a clipped notification instead of
        pystray's `_message()` raising "string too long" and this except
        swallowing the notification entirely. `urgency` has no pystray
        equivalent -- see TrayHost.notify's own docstring for why hosts
        without one are free to ignore the parameter.
        """
        try:
            self.icon.notify(
                _fit_wchars(alert.body, MAX_NOTIFY_BODY_LEN),
                _fit_wchars(alert.title, MAX_NOTIFY_TITLE_LEN))
        except Exception:
            log.exception("failed to send notification")

    # --- TrayHost: icon / title -------------------------------------------

    def set_icon(self, states, update_pending):
        """Render straight into an in-memory PIL.Image — no alternating-
        filename cache-bust dance (linux/tray.py's set_icon): pystray's
        Windows backend repaints immediately on `icon.icon = image`, with
        no icon-name cache to defeat (see the tray study's finding 7). The
        BytesIO buffer never touches disk.
        """
        buf = io.BytesIO()
        render_pills(states, buf, update_pending=update_pending,
                     scale=TRAY_ICON_SCALE)
        buf.seek(0)
        image = Image.open(buf)
        image.load()  # decode now, while buf is still alive
        self.icon.icon = image

    def set_title(self, text):
        self.icon.title = _fit_wchars(text, MAX_TITLE_LEN)

    def _quit(self):
        """Deliberate quit: stop being counted before going away.

        Two event loops now, not GTK's one, so both must be told to stop.
        `icon.stop()` is the documented, normal way a pystray menu action
        ends its own Icon.run() loop (safe to call from the thread already
        running it — this is pystray's own idiom, not a marshal case).
        `root.quit()` unblocks root.mainloop() on the tk thread, which is
        NOT the thread _quit() runs on when reached via the "⏻ Quit" menu
        row (that fires on the pystray worker thread). Reached from
        _on_popover_action("quit") too, which already runs on the tk
        thread — after_idle from the tk thread just runs on the very next
        idle tick, so this is safe either way, not just from the worker
        thread.

        `_shutdown` is set only AFTER the root.quit() marshal above is
        already queued, and never before: setting it earlier would make
        call_on_ui_thread silently drop that very call. Once set, any
        callback still arriving afterwards (e.g. a --check-update
        subprocess finishing after Quit was already clicked) is a no-op
        instead of reaching after_idle on a root that is being/has been
        destroyed — see call_on_ui_thread's own docstring for the failure
        that guards against.
        """
        presence_client.leave()
        self.icon.stop()
        self.call_on_ui_thread(self.root.quit)  # thread -> UI handoff
        self._shutdown = True

    def _on_quit(self):
        self._quit()

    def _tick(self):
        """Recurring usage poll. tkinter's `after` has no GLib-style "return
        True to repeat", so each firing re-arms its own next one — always
        from the tk thread, since `after` callbacks run there."""
        self.controller._tick()
        self.root.after(self.interval * 1000, self._tick)

    def _presence_tick(self):
        """Re-announce this device; the counts land on the next poll. Same
        self-rescheduling shape as _tick, for the same reason."""
        presence_client.beat(self.controller.snapshot)
        self.root.after(int(presence.interval()) * 1000, self._presence_tick)

    # --- TrayHost: the optional panel triad -------------------------------

    @property
    def has_panel(self):
        return self.popover is not None

    def show_panel(self):
        self.popover.show_panel()

    def hide_panel(self):
        self.popover.hide_panel()

    def panel_visible(self):
        return self.popover.get_visible()

    def refresh_panel(self):
        self.popover.refresh_layout()


def _noop(icon, item):
    """Placeholder action for a disabled/informational menu row.

    pystray's MenuItem signature documents `action` as a plain positional
    parameter, not one confirmed to tolerate None — coding around that
    uncertainty with a real, harmless callable is cheaper than finding out
    the hard way that some backend calls a None action anyway.
    """
    return None


def _account_switch_action(tray, number):
    """A fresh (icon, item) -> None closure bound to one account number.

    A `lambda icon, item: tray._on_switch(number)` built inside _build_menu's
    per-account loop would all close over the SAME `number` cell and fire
    with whatever `number` happened to equal after the loop finished — the
    classic late-binding bug. Returning a fresh closure from a function call
    gives each row its own `number` parameter instead.
    """
    def action(icon, item):
        tray._on_switch(number)
    return action


def _check_row_action(callback):
    """Adapts a Tray method (Linux-style, no args) to pystray's (icon, item)
    action signature — `callback` is `self._on_check_update` from
    `_build_menu`, and this is the only place that needs the adaptation."""
    def action(icon, item):
        callback()
    return action


def _run_icon(tray):
    """Target for the pystray worker thread — never pass tray.icon.run
    directly to Thread(target=...).

    threading.Thread hands an uncaught exception to threading.excepthook,
    whose default prints to sys.stderr — None under pythonw.exe, so it
    would vanish with nothing recorded in tray.log: the worker thread dies,
    root.mainloop() keeps running with no tray icon left, and there is no
    menu left to quit from either. Catching it here logs the failure to
    tray.log and then quits cleanly instead of leaving the app invisible
    with no way out.
    """
    try:
        tray.icon.run()
    except Exception:
        log.exception("pystray Icon.run() failed; shutting down")
        tray._quit()


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200_000:
            os.remove(LOG_FILE)
    except OSError:
        # Ported verbatim from the POSIX side, where an open-file remove
        # just unlinks the name and keeps working. Windows is not
        # unlink-on-open: if anything else still has LOG_FILE open (e.g. a
        # second tray instance) os.remove raises PermissionError here, and
        # silently passing meant the 200 KB cap would stop applying with no
        # trace anywhere. logging isn't configured yet at this point in
        # main(), but the module logger still has Python's own lastResort
        # stderr fallback, so this is strictly better than the bare `pass`
        # it replaces even before basicConfig runs below.
        log.exception("could not rotate %s", LOG_FILE)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log.info("ai-smartbar %s starting (interval %ss)", __version__,
             os.environ.get("SMARTBAR_INTERVAL", "60"))
    # Must run before the very first Tk widget — see _prime_dpi_awareness's
    # own docstring for why this cannot simply happen inside Tray.__init__.
    _prime_dpi_awareness()
    root = tk.Tk()
    root.withdraw()  # no real main window: the popover is its own Toplevel
    tray = Tray(root)
    tray.controller._start_fetch()
    root.after(tray.interval * 1000, tray._tick)
    if presence.enabled():
        root.after(int(presence.interval()) * 1000, tray._presence_tick)
    # Decision D1: pystray's Icon.run() is only main-thread-mandatory on
    # macOS (confirmed against the pystray FAQ), so on Windows it runs on
    # its own worker thread while root.mainloop() owns the real main
    # thread and every tk widget.
    threading.Thread(target=_run_icon, args=(tray,), daemon=True).start()
    root.mainloop()
