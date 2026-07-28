"""Linux system tray: AppIndicator icon, launcher menu, and the card panel.

The visible UI is the popover window (smartbar/linux/popover_window.py),
which is the same design as the macOS popover. The menu here is its launcher
plus the actions, because an AppIndicator menu is serialised to the panel as
dbusmenu and can carry nothing but labels, icons and checkmarks — never the
cards and filled bars. If the window cannot be created the menu falls back to
the old text rows so the tray still works.
"""
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

import json
import logging
import subprocess
import threading
import time

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk

from smartbar import __version__, presence_client
from smartbar.core import codex, cswap, model, paths, plan
from smartbar.core import popover_layout, portable, presence
from smartbar.core import update as update_core
from smartbar.core.alerts import Alert, AlertManager
from smartbar.core.recapture import RecapturePolicy
from smartbar.paint.tray_icon import render_pills

CACHE_DIR = paths.cache_dir()
ICON_DIR = os.path.join(CACHE_DIR, "icons")
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
LAUNCHER = os.path.join(REPO_ROOT, "bin", "ai-smartbar")
# A manual check runs `git fetch`. Bounded so a stalled network leaves the row
# saying "Check failed" rather than "Checking…" for ever.
CHECK_TIMEOUT = 120
# How long the outcome sits in the menu before the row goes back to normal.
CHECK_RESULT_SECONDS = 20

log = logging.getLogger("ai-smartbar")


class Tray:
    def __init__(self):
        os.makedirs(ICON_DIR, exist_ok=True)
        # 60s harvests cswap's poll plans the moment they come due (incl.
        # the 60s urgent cadence near the limit); the store still paces the
        # real network, so faster polling adds no API traffic.
        self.interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self.alerts = AlertManager()
        self.snapshot = None
        self.provider = ""   # panel tab; "" auto-resolves in the layout
        self.confirm = ""    # card awaiting remove confirmation, or ""
        self.failures = 0
        self.flip = False
        self.generation = 0  # stamps fetches so superseded results are dropped
        self.menu = None
        self.pending_menu = None  # rebuilt while open; swapped in on hide
        self.last_fetch_at = 0.0  # monotonic; guards menu-open refreshes
        self.recapture = RecapturePolicy()  # paces register/heal/refresh adds
        self.last_error = ""
        self.action_error = ""   # switch/remove failure; sticky until next
                                  # attempt (mirrors UsageStore.swift:15-16)
        self.refreshing = False  # true while a fetch is in flight
        self.update_blocked = ""
        self.presence_started = False  # first beat waits for the first fetch
        self.open_item = None
        self.checking = False     # a manual update check is in flight
        self.check_result = ""    # its outcome, shown in the row for a while
        self.check_token = 0      # so a stale timer cannot clear a newer result
        self.update_pending = self._pending_update()  # "" or "X.Y.Z"
        self.pinned = model.panel_pinned()
        self.popover = self._make_popover()
        self.indicator = AppIndicator.Indicator.new(
            "ai-smartbar", "dialog-information",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_icon_theme_path(ICON_DIR)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._set_icon([])  # hollow "?" pills until the first fetch lands
        self._install_menu(self._build_menu())
        self._init_notify()
        if self.pinned and self.popover is not None:
            # Up before the first fetch lands: the panel renders its own
            # loading/error state and refreshes in place once data arrives.
            self.popover.show_panel()

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
        return popover_layout.build(
            self.snapshot, version=__version__,
            pending_version=self.update_pending,
            blocked_reason=self.update_blocked,
            fetched_at=self.snapshot.fetched_at if self.snapshot else "",
            stale=bool(self.failures and self.snapshot),
            error=self.last_error if self.snapshot is None else "",
            hover=hover, provider=self.provider, confirm=self.confirm,
            action_error=self.action_error, refreshing=self.refreshing,
            stale_reason=self.last_error)

    def _on_popover_action(self, name):
        """Route a hit-tested click from the panel."""
        if name == "quit":
            self._quit()
            return
        if name == "refresh":
            self._start_fetch()
        elif name == "update":
            self._on_update(None)
        elif name == "dismiss-error":
            self.action_error = ""
        elif name.startswith("tab:"):
            self.provider = name.split(":", 1)[1]
            self.confirm = ""
        elif name.startswith("confirm-remove:"):
            self._on_remove(name.split(":", 1)[1])
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
        if time.monotonic() - self.last_fetch_at > 10:
            self._start_fetch()

    def _init_notify(self):
        self.notify = None
        try:
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify
            Notify.init("AI smartbar")
            self.notify = Notify
        except Exception:
            log.warning("libnotify unavailable; will fall back to notify-send")

    def _send_alert(self, alert, urgency="critical", icon="dialog-warning"):
        """Any object with .title/.body — a limit alert or an update check.

        Urgency is a parameter because a limit alert should interrupt, while
        "up to date" is a confirmation the user just asked for.
        """
        try:
            if self.notify is not None:
                self.notify.Notification.new(alert.title, alert.body,
                                             icon).show()
            else:
                subprocess.run(["notify-send", "-u", urgency,
                                alert.title, alert.body], timeout=10, check=False)
        except Exception:
            log.exception("failed to send notification")

    def _set_icon(self, states):
        # Alternate two icon names: AppIndicator ignores a set_icon_full call
        # with the current name, so a single name would never repaint.
        self.flip = not self.flip
        name = f"state-{'a' if self.flip else 'b'}"
        render_pills(states, os.path.join(ICON_DIR, name + ".png"),
                     update_pending=bool(self.update_pending))
        self.indicator.set_icon_full(name, "AI smartbar usage")

    def _build_menu(self):
        """The tray menu is the panel's launcher plus the actions.

        dbusmenu can only carry labels/icons/checkmarks, so the cards live in
        the popover window. When that window could not be created the old
        text rows come back, so the tray is still usable.
        """
        menu = Gtk.Menu()
        self.open_item = None
        if self.popover is not None:
            item = Gtk.MenuItem(label="🔎 Open AI smartbar")
            item.connect("activate", self._on_open)
            menu.append(item)
            self.open_item = item
        elif self.snapshot is None:
            label = "Loading…" if self.failures == 0 else "cswap error — see tray.log"
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(False)
            menu.append(item)
        else:
            stale = "  (stale)" if self.failures else ""
            for acct in self.snapshot.accounts:
                item = Gtk.MenuItem(label=model.menu_row(acct)
                                    + (stale if acct.active else ""))
                if acct.active or model.switch_blocked(acct):
                    # A dead stored credential must not be switched to: it
                    # would restore a login Anthropic already rejected.
                    item.set_sensitive(False)
                else:
                    item.connect("activate", self._on_switch, acct.number)
                menu.append(item)
            if self.snapshot.openai:
                # Read-only rows: no switcher exists for ChatGPT logins.
                menu.append(Gtk.SeparatorMenuItem())
                header = Gtk.MenuItem(label="OpenAI")
                header.set_sensitive(False)
                menu.append(header)
                for acct in self.snapshot.openai:
                    item = Gtk.MenuItem(label=model.menu_row(acct))
                    item.set_sensitive(False)
                    menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        if self.update_pending:
            item = Gtk.MenuItem(label=f"⬆ Update to {self.update_pending}")
            item.connect("activate", self._on_update)
            menu.append(item)
        # "Refresh now" re-reads usage; this asks the REMOTE whether a new
        # release exists, which otherwise only happened on the updater's own
        # 6-hourly timer — so a device could sit up to 6h behind with no way
        # to ask. Hidden when this device has opted out of updating, where a
        # check button would promise something that cannot happen.
        rows = [("⟳ Refresh now", self._on_refresh, True)]
        if update_core.enabled():
            rows.append(self._check_row())
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

    def _refresh_menu(self):
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
        if time.monotonic() - self.last_fetch_at > 10:
            self._start_fetch()

    def _on_menu_hide(self, _menu):
        if self.pending_menu is not None:
            self._install_menu(self.pending_menu)

    def _on_switch(self, _item, number):
        self.action_error = ""
        self._flip_active_optimistically(number)

        def run():
            try:
                cswap.switch(number)
            except cswap.CswapError as exc:
                log.exception("switch failed")
                GLib.idle_add(self._set_action_error, f"Switch failed: {exc}")
            self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    def _flip_active_optimistically(self, number):
        """ACTIVE chip/icon/title move now, matching UsageStore.swift:
        159-166: bumping self.generation (like Swift's fetchGeneration +=
        1) makes _apply_snapshot/_apply_error drop any pre-switch fetch
        that lands after this guess, and the forced refetch _on_switch
        starts next is the truth that confirms or corrects it."""
        if self.snapshot is None:
            return
        for acct in self.snapshot.accounts:
            acct.active = acct.number == number
        self.generation += 1
        self.refreshing = False
        account = self.snapshot.active_account
        self._set_icon(model.pill_states(account) if account else [])
        self.indicator.set_title(model.title_line(account))
        self._refresh_menu()
        if self.popover is not None and self.popover.get_visible():
            self.popover.refresh_layout()

    def _set_action_error(self, message):
        """GLib.idle_add target: a worker thread must never poke UI state
        directly (that is a crash waiting to happen, not a cosmetic bug),
        so a switch/remove failure lands here instead."""
        self.action_error = message
        if self.popover is not None and self.popover.get_visible():
            self.popover.refresh_layout()
        return False

    def _on_remove(self, token):
        """Confirmed removal ("<provider>:<id>"): drop the card now, remove
        in the background through the ONE core function per provider, then
        refetch — the truth that resurrects the card if the removal failed."""
        provider, _, ident = token.partition(":")
        self.confirm = ""
        self.action_error = ""
        if self.snapshot is not None:
            if provider == "claude":
                self.snapshot.accounts = [
                    a for a in self.snapshot.accounts
                    if str(a.number) != ident]
            else:
                self.snapshot.openai = [
                    a for a in self.snapshot.openai if a.email != ident]

        def run():
            try:
                if provider == "claude":
                    cswap.remove_account(int(ident))
                else:
                    codex.remove_account(ident)
            except (cswap.CswapError, ValueError, OSError) as exc:
                log.exception("remove failed")
                GLib.idle_add(self._set_action_error, f"Remove failed: {exc}")
            self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    def _pending_update(self) -> str:
        """The release the updater found waiting, or "" — never raises.

        Also records why an update is being held back (dirty checkout,
        unpushed commits) so the panel footer can say so.
        """
        # update_runner stays a LAZY import: it pulls in the lock shims and
        # the git layer, and a tray must not pay for those at import time.
        # The reading itself is shared — see update_runner.pending_for_ui.
        from smartbar import update_runner
        pending, self.update_blocked = update_runner.pending_for_ui()
        return pending

    def _on_update(self, _item):
        """Apply the waiting release. Detached on purpose: the updater
        restarts this very tray, so it must not be our child."""
        try:
            portable.spawn_detached([LAUNCHER, "--update"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            log.exception("could not start the updater")

    def _check_row(self):
        """(label, callback, clickable) for the manual update-check row."""
        if self.checking:
            return "⇅ Checking for updates…", None, False
        if self.check_result:
            return self.check_result, None, False
        return "⇅ Check for updates", self._on_check_update, True

    def _on_check_update(self, _item):
        if self.checking:
            return
        self.checking = True
        self.check_result = ""
        self.check_token += 1
        self._refresh_menu()
        threading.Thread(target=self._check_update, args=(self.check_token,),
                         daemon=True).start()

    def _check_update(self, token):
        """Ask the remote, off the main loop — this does a network fetch.

        `--check-update --json` does the whole thing: it only reports (applying
        stays the separate, deliberate "⬆ Update to …" row) and it decides what
        to SAY, so the macOS popover and this menu cannot drift apart in either
        the wording or the rules behind it.
        """
        try:
            done = subprocess.run([LAUNCHER, "--check-update", "--json"],
                                  capture_output=True, text=True,
                                  timeout=CHECK_TIMEOUT,
                                  **portable.no_window())
            answer = json.loads(done.stdout)
        except Exception:
            log.exception("update check could not run")
            answer = None
        GLib.idle_add(self._checked, token, answer)

    def _checked(self, token, answer):
        if token != self.check_token:
            return False  # superseded by a newer check
        self.checking = False
        self.update_pending = self._pending_update()   # also sets update_blocked
        if not isinstance(answer, dict) or not answer.get("label"):
            failure = update_core.check_outcome(failed=True)
            answer = {"label": failure.label, "title": failure.title,
                      "body": failure.body}
        self.check_result = answer["label"]
        # Clicking a row closes the menu, so the label alone would be invisible
        # until the user opened it again — the notification is the real feedback.
        self._send_alert(Alert(title=answer.get("title", "AI smartbar"),
                               body=answer.get("body", "")),
                         urgency="normal", icon="dialog-information")
        # Repaint: the red dot on the tray icon is driven by update_pending, so
        # a check that just found a release has to make the badge appear.
        account = self.snapshot.active_account if self.snapshot else None
        self._set_icon(model.pill_states(account) if account else [])
        self._refresh_menu()
        GLib.timeout_add_seconds(CHECK_RESULT_SECONDS,
                                 self._clear_check_result, token)
        return False

    def _clear_check_result(self, token):
        if token == self.check_token and self.check_result:
            self.check_result = ""
            self._refresh_menu()
        return False

    def _on_refresh(self, _item):
        self._start_fetch()

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

    def _start_fetch(self):
        self.generation += 1
        self.refreshing = True
        self.last_fetch_at = time.monotonic()
        threading.Thread(target=self._fetch, args=(self.generation,),
                         daemon=True).start()

    def _fetch(self, generation):
        try:
            snap = cswap.fetch(fresh=True)
        except cswap.CswapError as exc:
            log.warning("fetch failed: %s", exc)
            GLib.idle_add(self._apply_error, str(exc), generation)
            return
        GLib.idle_add(self._apply_snapshot, snap, generation)

    def _apply_snapshot(self, snap, generation):
        if generation != self.generation:
            return False  # superseded (e.g. a pre-switch fetch landing late)
        self.refreshing = False
        self.failures = 0
        self.last_error = ""
        self.snapshot = snap
        # Stamp the device counts and plan badges before anything renders:
        # the menu rows and the panel both name accounts through
        # model.account_label.
        presence.apply_counts(snap, presence_client.counts())
        plan.apply_plans(snap, plan.plans_by_email())
        # ChatGPT accounts ride the snapshot's separate list (cheap local
        # reads, mtime-cached); the panel's OpenAI tab renders them.
        snap.openai = codex.accounts()
        if not self.presence_started:
            # First real snapshot: announce this device now rather than
            # after a whole interval, and do it with accounts already in
            # hand so the beat costs no cswap call.
            self.presence_started = True
            presence_client.beat(snap)
        if snap.schema_warning:
            log.warning("%s", snap.schema_warning)
        account = snap.active_account
        # Cheap local file read; keeps the badge and the menu row in step
        # with whatever the update agent last decided.
        self.update_pending = self._pending_update()
        self._set_icon(model.pill_states(account))
        self.indicator.set_title(model.title_line(account))
        self._refresh_menu()
        if self.popover is not None and self.popover.get_visible():
            self.popover.refresh_layout()
        for alert in self.alerts.check(snap):
            self._send_alert(alert)
        self._maybe_recapture(snap)
        return False

    def _maybe_recapture(self, snap):
        # `cswap add` keeps stored credentials alive: it registers an
        # unregistered /login, heals an active slot whose backup died
        # (relogin_required), and periodically re-captures the live login
        # so Claude Code's token rotations never orphan the backup. The
        # policy paces all three; SMARTBAR_AUTO_ADD/SMARTBAR_RECAPTURE=off
        # disable them.
        action = self.recapture.action(snap, time.monotonic())
        if action is None:
            return

        def run():
            try:
                cswap.add()
            except cswap.CswapError as exc:
                log.info("cswap add (%s) skipped: %s", action, exc)
                return
            log.info("cswap add ran (%s): current login re-captured", action)
            if action != "refresh":  # registration/heal changes what we show
                self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    def _apply_error(self, message, generation):
        if generation != self.generation:
            return False  # superseded
        self.refreshing = False
        self.failures += 1
        self.last_error = message
        if self.failures >= 3:
            self._set_icon([])
            self.indicator.set_title(f"AI smartbar — cswap error: {message[:80]}")
        self._refresh_menu()
        if self.popover is not None and self.popover.get_visible():
            self.popover.refresh_layout()
        return False

    def _tick(self):
        self._start_fetch()
        return True

    def _presence_tick(self):
        """Re-announce this device; the counts land on the next poll."""
        presence_client.beat(self.snapshot)
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
    tray._start_fetch()
    GLib.timeout_add_seconds(tray.interval, tray._tick)
    if presence.enabled():
        GLib.timeout_add_seconds(int(presence.interval()), tray._presence_tick)
    Gtk.main()
