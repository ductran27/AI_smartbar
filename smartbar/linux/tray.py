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
os.environ.setdefault("GDK_BACKEND", "x11,wayland")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

import logging
import subprocess
import time

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk

from smartbar import __version__, presence_client
from smartbar.core import (model, paths, popover_layout, portable, presence,
                           usage_history)
from smartbar.core import update as update_core
from smartbar.core.tray_controller import TrayController
from smartbar.paint.tray_icon import render_pills

CACHE_DIR = paths.cache_dir()
ICON_DIR = os.path.join(CACHE_DIR, "icons")
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
LAUNCHER = os.path.join(REPO_ROOT, "bin", "ai-smartbar")

log = logging.getLogger("ai-smartbar")


class Tray:
    def __init__(self):
        os.makedirs(ICON_DIR, exist_ok=True)
        # 60s harvests cswap's poll plans the moment they come due (incl.
        # the 60s urgent cadence near the limit); the store still paces the
        # real network, so faster polling adds no API traffic.
        self.interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
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
            stale_reason=c.last_error,
            history=usage_history.active_series(c.snapshot))

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

    def _init_notify(self):
        self._libnotify = None
        try:
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify
            Notify.init("AI smartbar")
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
                subprocess.run(["notify-send", "-u", urgency,
                                alert.title, alert.body], timeout=10, check=False)
        except Exception:
            log.exception("failed to send notification")

    # --- TrayHost: icon / title --------------------------------------------

    def set_icon(self, states, update_pending):
        # Alternate two icon names: AppIndicator ignores a set_icon_full call
        # with the current name, so a single name would never repaint.
        self.flip = not self.flip
        name = f"state-{'a' if self.flip else 'b'}"
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
        self.menu = menu
        self.pending_menu = None
        self.indicator.set_menu(menu)
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
        try:
            subprocess.Popen(["x-terminal-emulator", "-e", "cswap", "tui"])
        except OSError:
            log.exception("could not open terminal")

    def _quit(self):
        """Deliberate quit: stop being counted before going away."""
        presence_client.leave()
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
    tray = Tray()
    tray.controller._start_fetch()
    GLib.timeout_add_seconds(tray.interval, tray.controller._tick)
    if presence.enabled():
        GLib.timeout_add_seconds(int(presence.interval()), tray._presence_tick)
    Gtk.main()
