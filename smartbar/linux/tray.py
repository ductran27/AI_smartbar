"""Linux system tray: AppIndicator icon, launcher menu, and the card panel.

The visible UI is the popover window (smartbar/linux/popover_window.py),
which is the same design as the macOS popover. The menu here is its launcher
plus the actions, because an AppIndicator menu is serialised to the panel as
dbusmenu and can carry nothing but labels, icons and checkmarks — never the
cards and filled bars. If the window cannot be created the menu falls back to
the old text rows so the tray still works.

The fetch/apply/alert/recapture/check-update state machine itself lives in
smartbar.core.tray_controller (TrayController) rather than here — see that
module's own docstring for why. This class is TrayController's host: it owns
GTK objects (the indicator, the menu, the popover) and implements the
TrayHost contract (set_icon/set_title/rebuild_menu/call_on_ui_thread/
schedule/notify/check_update_argv/the panel triad) as thin bindings onto
AppIndicator/GLib/libnotify, plus whatever stays genuinely GTK-shaped and
therefore platform-specific: building the actual Gtk.MenuItem objects, the
pending-menu-until-hide swap dance, the popover window itself, and the
optimistic-switch flip's own repaint (supplied to TrayController.on_switch
as a callable, per the design's own divergence note on why that step cannot
be shared).

Also owns the open-panel hotkey's Linux half: no cross-desktop system-wide
hotkey API exists here without a new dependency this repo doesn't carry, so
`main()` writes this process's PID to PID_FILE and answers SIGUSR1 (via
GLib.unix_signal_add, not a raw signal.signal handler — see
_on_open_panel_signal's own docstring for why) by opening the panel exactly
as a click would. `bin/ai-smartbar --open-panel` is the sender; the actual
key binding lives in the user's own desktop environment's keyboard
settings. See docs/superpowers/specs/2026-08-16-open-panel-hotkey-design.md.
"""
# Must stay the first statement after the docstring — above even the
# gi.require_version dance below, which is what Python requires of any
# __future__ import. Present for the same reason every other module in this
# package has it: the repo floor is 3.9 (pyproject target-version), where
# `str | None` in an annotation is a runtime TypeError without it. ruff's
# FA102 enforces that, but a front-end this file's size should not be one
# `X | Y` away from failing to import on the oldest Mac we support.
from __future__ import annotations

import os

# Prefer X11 (XWayland, on a Wayland desktop) over the native Wayland
# backend. Wayland gives a client no say in where its window opens — GNOME
# just centers the panel — no way to move an undecorated window, and no way
# to read where a drag ended, so neither the default corner nor the
# remembered position (see popover_window) can exist there. Under XWayland
# all three work. The environment variable, not gdk_set_allowed_backends:
# from Python that call comes too late (the display manager is already up
# once gi.repository.Gdk is importable — verified: it is silently ignored),
# while GDK reads GDK_BACKEND with the same ordered-fallback semantics, so
# a session with no X server still falls through to Wayland and keeps
# compositor placement. setdefault: an explicit GDK_BACKEND always wins.
_GDK_BACKEND_ORIGINAL = os.environ.get("GDK_BACKEND")   # for child processes
os.environ.setdefault("GDK_BACKEND", "x11,wayland")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

import logging
import signal
import subprocess
import time

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk

from smartbar import __version__, presence_client
from smartbar.core import (cswap, model, paths, popover_layout, portable,
                           presence, sysmon)
from smartbar.core import update as update_core
from smartbar.core.tray_controller import TrayController
from smartbar.paint.tray_icon import render_pills

CACHE_DIR = paths.cache_dir()
ICON_DIR = os.path.join(CACHE_DIR, "icons")
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
LAUNCHER = os.path.join(REPO_ROOT, "bin", "ai-smartbar")
# See paths.tray_pid_file()'s own docstring for why this lives under
# CACHE_DIR and what reads it (bin/ai-smartbar's --open-panel, the CLI half
# of the open-panel hotkey feature — GNOME/KDE/etc. have no portable
# system-wide hotkey API this repo can hook without a new dependency, so the
# actual key binding is the user's own DE keyboard settings running this
# command; see the README's Linux panel section and the design doc).
PID_FILE = paths.tray_pid_file()

log = logging.getLogger("ai-smartbar")


def _write_pid_file():
    """Best-effort: lets `ai-smartbar --open-panel` find this process.

    Never fatal — a tray that can't write its PID still works from the
    tray icon itself, it just can't be reached by the CLI hotkey helper.
    """
    try:
        with open(PID_FILE, "w") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        log.exception("could not write PID file at %s", PID_FILE)


def _remove_pid_file():
    """Undo _write_pid_file() on a clean quit, so --open-panel fails loudly
    ("no running tray") instead of signalling a PID a new, unrelated
    process might have been assigned in the meantime."""
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


class Tray:
    def __init__(self):
        os.makedirs(ICON_DIR, exist_ok=True)
        # 60s harvests cswap's poll plans the moment they come due (incl.
        # the 60s urgent cadence near the limit); the store still paces the
        # real network, so faster polling adds no API traffic.
        self.interval = TrayController.poll_interval_from_env()
        self.controller = TrayController(self)
        self.provider = ""   # panel tab; "" auto-resolves in the layout
        self.confirm = ""    # card awaiting remove confirmation, or ""
        self.flip = False
        self.menu = None
        self.pending_menu = None  # rebuilt while open; swapped in on hide
        self.open_item = None
        self.controller._pending_update()  # seeds update_pending/blocked
        self.pinned = model.panel_pinned()
        self.popover = self._make_popover()
        self.indicator = AppIndicator.Indicator.new(
            "ai-smartbar", "dialog-information",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_icon_theme_path(ICON_DIR)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        # Hollow "?" pills until the first fetch lands.
        self.set_icon([], bool(self.controller.update_pending))
        self._install_menu(self._build_menu())
        self._init_notify()
        if self.pinned and self.has_panel:
            # Up before the first fetch lands: the panel renders its own
            # loading/error state and refreshes in place once data arrives.
            self.show_panel()

    def _make_popover(self):
        """The card panel, or None if this session cannot host a window.

        Never fatal: the menu keeps working as a plain-text fallback (see
        _build_menu), which matters on a headless or misconfigured session.
        """
        try:
            from smartbar.linux.popover_window import Popover
            return Popover(self._popover_layout, self._on_popover_action,
                           pinned=self.pinned)
        except Exception:
            log.exception("popover unavailable; falling back to a text menu")
            return None

    def _popover_layout(self, hover=""):
        """Fresh layout for the panel — same builder the macOS popover mirrors."""
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
            stale_reason=c.last_error, system=c.system)

    def _on_popover_action(self, name):
        """Route a hit-tested click from the panel."""
        if name == "quit":
            self._quit()
            return
        if name == "refresh":
            self._on_refresh(None)
        elif name == "update":
            self._on_update(None)
        elif name == "dismiss-error":
            self.controller.action_error = ""
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
        elif name.startswith("confirm-kill:"):
            self.confirm = ""
            self.controller.on_kill(name.split(":", 1)[1])
        elif name == "cancel-kill":
            self.confirm = ""
        elif name.startswith("kill:"):
            # First click of the two-step kill: arm the in-row confirm.
            self.confirm = name.split(":", 1)[1]
        elif name.startswith("row:"):
            pass   # hover container — a click on a process row does nothing
        elif name.startswith("card:"):
            pass   # hover container — a click on the card body does nothing
        elif name.startswith("switch:"):
            self._on_switch(None, int(name.split(":", 1)[1]))
        if self.popover is not None:
            self.popover.refresh_layout()

    def _on_open(self, _item):
        if self.popover is None:
            return
        self.confirm = ""   # a fresh open never starts mid-question
        self.popover.show_panel()
        # Opening the panel is the user looking: refresh so what they read is
        # current (cswap's store paces the real network traffic).
        if time.monotonic() - self.controller.last_fetch_at > 10:
            self.controller._start_fetch()

    def _on_open_panel_signal(self):
        """GLib.unix_signal_add callback for SIGUSR1 — the open-panel
        hotkey's CLI half (see bin/ai-smartbar's open_panel() and PID_FILE
        above). Unlike a raw `signal.signal` handler, GLib dispatches this
        on the main loop itself rather than inside actual POSIX signal
        context, so calling straight into _on_open (same action a menu
        click or middle-click reaches) is exactly as safe as any other GTK
        callback in this file — nothing async-signal-unsafe here.

        Must return True: a GLib source callback that returns False is
        REMOVED, so returning anything falsy would make this fire once and
        then silently stop working for the rest of the process's life.
        """
        log.info("SIGUSR1 received: showing the panel")
        self._on_open(None)
        return True

    def _init_notify(self):
        self._libnotify = None
        try:
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify
            # init() returns False (no D-Bus) without raising; treating that
            # as success left every show() raising and the notify-send
            # fallback never used.
            if not Notify.init("AI smartbar"):
                raise RuntimeError("Notify.init returned False")
            self._libnotify = Notify
        except Exception:
            log.warning("libnotify unavailable; will fall back to notify-send")

    # --- TrayHost: notification -------------------------------------------

    def notify(self, alert, urgency="critical"):
        """Show a native notification. See TrayHost.notify's docstring for
        why `urgency` is the only signal this gets — the icon name is
        derived from it here rather than threaded through as its own
        parameter, exactly reproducing the two icons the old _send_alert
        callers picked by hand (dialog-warning for a limit alert, dialog-
        information for a check-result confirmation)."""
        icon = "dialog-information" if urgency == "normal" else "dialog-warning"
        try:
            if self._libnotify is not None:
                self._libnotify.Notification.new(alert.title, alert.body,
                                                 icon).show()
            else:
                # Fire-and-forget: this runs on the GTK loop, and a
                # notification daemon being activated (or hung) blocked
                # the whole UI for up to the timeout.
                subprocess.Popen(["notify-send", "-u", urgency,
                                  alert.title, alert.body],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except Exception:
            log.exception("failed to send notification")

    # --- TrayHost: icon / title --------------------------------------------

    def set_icon(self, states, update_pending):
        # Alternate two icon names: AppIndicator ignores a set_icon_full call
        # with the current name, so a single name would never repaint.
        self.flip = not self.flip
        name = f"state-{'a' if self.flip else 'b'}"
        # Per call: the cache dir is documented as safe to delete while the
        # tray runs, and a missing ICON_DIR made every poll's set_icon raise
        # (stranding the controller mid-apply).
        os.makedirs(ICON_DIR, exist_ok=True)
        render_pills(states, os.path.join(ICON_DIR, name + ".png"),
                     update_pending=update_pending)
        self.indicator.set_icon_full(name, "AI smartbar usage")

    def set_title(self, text):
        self.indicator.set_title(text)

    # --- menu construction (platform-specific: Gtk.Menu/Gtk.MenuItem) -----

    def _build_menu(self):
        """The tray menu is the panel's launcher plus the actions.

        dbusmenu can only carry labels/icons/checkmarks, so the cards live in
        the popover window. When that window could not be created the old
        text rows come back, so the tray is still usable.
        """
        c = self.controller
        menu = Gtk.Menu()
        self.open_item = None
        if self.popover is not None:
            item = Gtk.MenuItem(label="🔎 Open AI smartbar")
            item.connect("activate", self._on_open)
            menu.append(item)
            self.open_item = item
        elif c.snapshot is None:
            label = "Loading…" if c.failures == 0 else "cswap error — see tray.log"
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(False)
            menu.append(item)
        else:
            stale = "  (stale)" if c.failures else ""
            for acct in c.snapshot.accounts:
                item = Gtk.MenuItem(label=model.menu_row(acct)
                                    + (stale if acct.active else ""))
                if acct.active or model.switch_blocked(acct):
                    # A dead stored credential must not be switched to: it
                    # would restore a login Anthropic already rejected.
                    item.set_sensitive(False)
                else:
                    item.connect("activate", self._on_switch, acct.number)
                menu.append(item)
            if c.snapshot.openai:
                # Read-only rows: no switcher exists for ChatGPT logins.
                menu.append(Gtk.SeparatorMenuItem())
                header = Gtk.MenuItem(label="OpenAI")
                header.set_sensitive(False)
                menu.append(header)
                for acct in c.snapshot.openai:
                    item = Gtk.MenuItem(label=model.menu_row(acct))
                    item.set_sensitive(False)
                    menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        if c.update_pending:
            item = Gtk.MenuItem(label=f"⬆ Update to {c.update_pending}")
            item.connect("activate", self._on_update)
            menu.append(item)
        # "Refresh now" re-reads usage; this asks the REMOTE whether a new
        # release exists, which otherwise only happened on the updater's own
        # 6-hourly timer — so a device could sit up to 6h behind with no way
        # to ask. Hidden when this device has opted out of updating, where a
        # check button would promise something that cannot happen.
        rows = [("⟳ Refresh now", self._on_refresh, True)]
        if update_core.enabled():
            label, clickable = c._check_row()
            rows.append((label, self._on_check_update, clickable))
        rows += [("⚙ Open cswap TUI", self._on_tui, True),
                 ("⏻ Quit", self._on_quit, True)]
        for label, callback, clickable in rows:
            item = Gtk.MenuItem(label=label)
            if clickable:
                item.connect("activate", callback)
            else:
                item.set_sensitive(False)
            menu.append(item)
        menu.connect("show", self._on_menu_show)
        menu.connect("hide", self._on_menu_hide)
        menu.show_all()
        return menu

    def _install_menu(self, menu):
        old = self.menu
        self.menu = menu
        self.pending_menu = None
        self.indicator.set_menu(menu)
        # A GtkMenu owns its own popup toplevel, which GTK keeps until the
        # widget is destroyed; rebuilding one per poll (≥1440/day) without
        # destroying the previous one grew the tray's RSS for weeks.
        if old is not None and old is not menu:
            try:
                old.destroy()
            except Exception:
                log.debug("could not destroy the previous menu")
        # StatusNotifier gives no left-click callback — left-click always
        # shows the menu — so middle-click is the one-gesture way in.
        if self.open_item is not None:
            try:
                self.indicator.set_secondary_activate_target(self.open_item)
            except Exception:
                log.debug("middle-click activation unsupported here")

    # --- TrayHost: rebuild_menu ---------------------------------------------

    def rebuild_menu(self):
        new_menu = self._build_menu()
        if self.menu is not None and self.menu.get_mapped():
            # Swapping the menu out from under the pointer closes it on
            # some shells — hold the rebuild until this open menu hides.
            # A rebuild that supersedes a still-pending one must destroy
            # the superseded menu too, or it leaks like the old ones did.
            if self.pending_menu is not None and self.pending_menu is not new_menu:
                try:
                    self.pending_menu.destroy()
                except Exception:
                    log.debug("could not destroy the superseded pending menu")
            self.pending_menu = new_menu
        else:
            self._install_menu(new_menu)

    def _on_menu_show(self, _menu):
        # An opening menu is the user looking: refresh so what they read is
        # current (cswap's store paces the real network traffic).
        if time.monotonic() - self.controller.last_fetch_at > 10:
            self.controller._start_fetch()

    def _on_menu_hide(self, _menu):
        if self.pending_menu is not None:
            self._install_menu(self.pending_menu)

    # --- switch --------------------------------------------------------------

    def _on_switch(self, _item, number):
        self.controller.on_switch(number, self._flip_active_optimistically)

    def _flip_active_optimistically(self, number):
        """ACTIVE chip/icon/title move now, matching UsageStore.switchTo's
        optimistic-flip block. The generation bump that makes
        _apply_snapshot/_apply_error drop any pre-switch fetch landing after
        this guess is NOT done here: TrayController.on_switch does it under
        its own lock before this callable is ever marshalled, because a bare
        `+=` on this side raced the switch's worker thread (see that bump's
        comment). This callable is repaint-only. The forced refetch
        on_switch starts next is the truth that confirms or corrects the
        guess. Supplied to TrayController.on_switch as the flip_active
        callable — see that method's own docstring for why the repaint
        itself stays here rather than in the controller."""
        c = self.controller
        if c.snapshot is None:
            return
        for acct in c.snapshot.accounts:
            acct.active = acct.number == number
        c.refreshing = False
        account = c.snapshot.active_account
        self.set_icon(model.pill_states(account) if account else [],
                      bool(c.update_pending))
        self.set_title(model.title_line(account))
        self.rebuild_menu()
        if self.popover is not None and self.popover.get_visible():
            self.popover.refresh_layout()

    # --- update apply / check row --------------------------------------------

    def _on_update(self, _item):
        """Apply the waiting release. Detached on purpose: the updater
        restarts this very tray, so it must not be our child."""
        try:
            portable.spawn_detached([LAUNCHER, "--update"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            log.exception("could not start the updater")

    def _on_check_update(self, _item):
        self.controller._on_check_update()

    # --- TrayHost: the manual check's subprocess -----------------------------

    def check_update_argv(self):
        return [LAUNCHER, "--check-update", "--json"]

    def _on_refresh(self, _item):
        self.controller._start_fetch()

    def _on_tui(self, _item):
        """Open `cswap tui` in a terminal. x-terminal-emulator is Debian's
        alternatives name only; Fedora/Arch/openSUSE boxes silently did
        nothing. Try the user's own $TERMINAL first, then the common ones.
        The child gets the ORIGINAL GDK_BACKEND (the tray forces x11 first
        for AppIndicator's sake; a GTK terminal inheriting that runs under
        XWayland — blurry with fractional scaling, wrong input methods)."""
        try:
            binary = cswap._binary()
        except Exception:
            binary = "cswap"
        env = dict(os.environ)
        if _GDK_BACKEND_ORIGINAL is None:
            env.pop("GDK_BACKEND", None)
        else:
            env["GDK_BACKEND"] = _GDK_BACKEND_ORIGINAL
        candidates = []
        user_terminal = os.environ.get("TERMINAL", "").strip()
        if user_terminal:
            candidates.append([user_terminal, "-e", binary, "tui"])
        candidates += [
            ["x-terminal-emulator", "-e", binary, "tui"],
            ["gnome-terminal", "--", binary, "tui"],
            ["konsole", "-e", binary, "tui"],
            ["xfce4-terminal", "-e", f"{binary} tui"],
            ["xterm", "-e", binary, "tui"],
        ]
        for argv in candidates:
            try:
                # start_new_session: the terminal is its own process group
                # and is reaped by init, never a zombie of this tray.
                subprocess.Popen(argv, env=env, start_new_session=True,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
        log.error("no terminal emulator found for `cswap tui`")
        self.controller.action_error = ("No terminal emulator found — run "
                                        "`cswap tui` in a shell")
        # refresh_panel() dereferences self.popover, which is legitimately
        # None when the popover failed to build (_make_popover is "never
        # fatal"). Every other touch of it in this file is guarded; this one
        # was not. The error still reaches the menu fallback either way.
        if self.has_panel:
            self.refresh_panel()

    def _quit(self):
        """Deliberate quit: stop being counted before going away."""
        presence_client.leave()
        _remove_pid_file()
        Gtk.main_quit()

    def _on_quit(self, _item):
        self._quit()

    # --- TrayHost: thread -> UI handoff ---------------------------------------

    def call_on_ui_thread(self, callback, *args):
        GLib.idle_add(callback, *args)

    def schedule(self, seconds, callback, *args):
        def _fire():
            callback(*args)
            return False  # never repeat: this is a one-shot delayed call
        GLib.timeout_add_seconds(int(seconds), _fire)

    # --- TrayHost: the optional panel triad -----------------------------------

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

    def _presence_tick(self):
        """Re-announce this device; the counts land on the next poll."""
        presence_client.beat(self.controller.snapshot)
        return True


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200_000:
            os.remove(LOG_FILE)
    except OSError:
        pass
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log.info("ai-smartbar %s starting (interval %ss)", __version__,
             os.environ.get("SMARTBAR_INTERVAL", "60"))
    try:
        tray = Tray()
        # GLib.unix_signal_add, not signal.signal: the latter only runs its
        # handler between bytecode instructions on the MAIN thread and
        # would have to somehow poke the GTK loop awake itself;
        # unix_signal_add integrates the signal into the same main loop
        # everything else here already runs on (self-pipe under the hood),
        # which is also why the handler is safe to touch GTK/self.popover
        # directly. Installed BEFORE the PID file is written: SIGUSR1's
        # default disposition is TERMINATE, so a hotkey press in the gap
        # between the two used to kill the tray it was meant to open.
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1,
                             tray._on_open_panel_signal)
        _write_pid_file()
        tray.controller._start_fetch()
        GLib.timeout_add_seconds(tray.interval, tray.controller._tick)
        if presence.enabled():
            GLib.timeout_add_seconds(int(presence.interval()),
                                     tray._presence_tick)
        if sysmon.enabled():
            # Machine vitals + leftover processes on their own cadence; the
            # tick samples in a worker so it never blocks the GTK loop.
            tray.controller.sysmon_tick()

            def _sysmon_tick():
                try:
                    tray.controller.sysmon_tick()
                except Exception:
                    # A raising GLib callback cancels its timer for good.
                    log.exception("sysmon tick failed")
                return True
            GLib.timeout_add_seconds(sysmon.interval(), _sysmon_tick)
        Gtk.main()
    except Exception:
        # The installer starts this detached with stderr discarded; without
        # this line a missing AppIndicator typelib or an unwritable icon
        # dir died invisibly and the "check tray.log" hint pointed at an
        # empty file.
        log.exception("tray died")
        raise
