"""macOS menu-bar UI for AI_smartbar (rumps / native NSStatusBar).

NOT yet live-verified on a Mac — written to spec; run install/macos.sh on
the Mac, then check the menu bar. Logic lives in smartbar.core (unit-tested).

The fetch/apply/alert/recapture/check-update state machine itself lives in
smartbar.core.tray_controller (TrayController) rather than here — see that
module's own docstring for why. This class is TrayController's host: it
owns the rumps.App object (the menu bar text, the menu) and implements the
TrayHost contract (set_icon/set_title/rebuild_menu/call_on_ui_thread/
schedule/notify/check_update_argv) as thin bindings onto rumps, plus
whatever stays genuinely rumps-shaped: building the actual rumps.MenuItem
rows and the worker -> UI queue+timer poll rumps' lack of an idle_add
equivalent forces.

Two divergences from Linux/Windows worth flagging up front, both preserved
rather than flattened by this migration:

  * set_icon(states, update_pending) is a no-op here. This file has never
    rendered pixel icon state — the menu-bar TEXT is the only visual
    surface — and the controller calls set_icon uniformly across all three
    hosts regardless, per TrayHost's own contract. A documented,
    pre-existing gap this refactor exposes rather than papers over (see
    the migration design's per_platform note on set_icon), not a behavior
    this file is dropping.
  * set_title(text) ignores the generic `text` (model.title_line, a
    tooltip-style line shared by the other two hosts) and instead renders
    model.macos_title from the controller's own state — the short
    glyph+text form the literal, only-visible menu-bar bar needs. See
    set_title's own docstring for how it recovers which of the two old
    titles ("⚪ ?" on repeated failure, macos_title otherwise) to show
    without a third parameter the shared contract does not offer.

One piece is DELIBERATELY NOT migrated onto the controller, reported as a
blocker rather than silently flattened: the switch flow. TrayController.
on_switch's failure path (_set_action_error) only sets a field and,
optionally, refreshes an open panel — it does not notify or rebuild the
menu, because Linux/Windows surface a switch failure through the popover
alone. This front-end has no popover; the menu IS the whole UI, and its
"✕ Switch failed: …" row (see _rebuild_menu) plus a notification are the
ONLY way a failure is ever visible here. Routing through
TrayController.on_switch/_set_action_error as-is would make a failed
switch here silently invisible — no notification, no menu update — a real
behavior regression, not a refactor. _make_switch/_apply_switch_error
therefore stay host-owned, using TrayController._start_fetch() (documented
safe to call from any thread) for the one piece that genuinely is shared:
the post-attempt refetch.

Three rules this file did not used to follow, each already settled in the
Linux and Windows front-ends and each learned the same way:

  * Nothing off the main thread touches AppKit. `_fetch` (now inside
    TrayController) runs in a worker and used to set self.title, rebuild
    self.menu and raise notifications straight from there. Every UI
    mutation now goes through `call_on_ui_thread`, the analogue of
    GLib.idle_add (Linux) and root.after_idle (Windows).
  * Fetches carry a generation, so one that started before an account
    switch cannot land after it and quietly put the old account back on
    screen.
  * Failures are logged. Every `except` here used to swallow in silence —
    on the one front-end whose own header admits it has never run on real
    hardware, which is precisely where an invisible failure costs most.
"""
# See smartbar/linux/tray.py's own note: the repo floor is 3.9, where a
# PEP 604 annotation is a runtime TypeError without this. Every other module
# in the package already carries it; these two front-ends were the gap.
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading

import rumps

from smartbar import __version__, presence_client
from smartbar.core import cswap, model, paths, portable, presence
from smartbar.core.alerts import Alert
from smartbar.core.tray_controller import TrayController

CACHE_DIR = paths.cache_dir()
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
LAUNCHER = os.path.join(REPO_ROOT, "bin", "ai-smartbar")
# rumps has no idle_add. An NSTimer callback is the one place this app is
# guaranteed to be on the main thread, so the worker -> UI queue is drained
# by a short repeating timer instead.
UI_DRAIN_SECONDS = 0.2

log = logging.getLogger("ai-smartbar")


class SmartBarApp(rumps.App):
    # TrayHost: this front-end has no popover, so the controller's panel
    # triad (panel_visible/refresh_panel) is never reached. Declared HERE
    # rather than inherited: a host satisfies TrayHost by duck typing, not
    # by subclassing — this one's base is already rumps.App — so TrayHost's
    # own `has_panel = False` default never reaches it. Omitting it is not
    # a silent no-op: _apply_snapshot/_apply_error read host.has_panel
    # unconditionally, and an AttributeError there is swallowed by
    # _drain_ui, costing the alert and recapture steps that follow it.
    has_panel = False

    def __init__(self):
        super().__init__("⚪ …", quit_button=None)
        self.controller = TrayController(self)
        # Sticky until the next switch attempt; menu-only surfacing (this
        # front-end has no popover) — see module docstring for why this
        # stays host-owned rather than moving to the controller's own
        # action_error/on_switch.
        self.switch_error = ""
        self._ui_queue = queue.Queue()
        # 60s harvests cswap's poll plans as they come due; no extra API
        # traffic (the store paces the network).
        interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self.controller._pending_update()
        self._rebuild_menu()
        # Started before the first fetch on purpose: the fetch hands its
        # result back through this queue, so nothing may be sitting in it
        # unread.
        self.ui_timer = rumps.Timer(self._drain_ui, UI_DRAIN_SECONDS)
        self.ui_timer.start()
        self.timer = rumps.Timer(self._tick, interval)
        self.timer.start()
        if presence.enabled():
            self.presence_timer = rumps.Timer(self._presence_tick,
                                              int(presence.interval()))
            self.presence_timer.start()
        self._tick(None)

    # ------------------------------------------------ TrayHost: thread -> UI

    def call_on_ui_thread(self, callback, *args):
        """Queue callback(*args) for the main thread. Worker -> UI handoff.

        AppKit is not thread-safe for UI mutation, and rumps offers no
        idle_add. Whatever calls this is NOT allowed to touch self.title,
        self.menu or rumps.notification itself.
        """
        self._ui_queue.put((callback, args))

    def schedule(self, seconds, callback, *args):
        """Run callback(*args) on the main thread after `seconds`.

        rumps/AppKit has no delayed-idle primitive analogous to GLib.
        timeout_add_seconds or tk's root.after. A plain daemon
        threading.Timer fires call_on_ui_thread once its delay elapses,
        reusing the same queue+_drain_ui poll every other UI touch goes
        through — the same shape the old, now-controller-owned _checked's
        expiry timer used, generalized to any delay/callback.
        """
        timer = threading.Timer(seconds, self.call_on_ui_thread,
                                args=(callback,) + args)
        timer.daemon = True  # must not hold a quit open
        timer.start()

    def _drain_ui(self, _sender):
        """Main thread: run whatever the workers queued, one drain per tick."""
        while True:
            try:
                callback, args = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                callback(*args)
            except Exception:
                # One bad update must not stop the drain: leaving items in the
                # queue would freeze every later fetch's result too.
                log.exception("a queued UI update failed")

    # ------------------------------------------------------ TrayHost: notify

    def notify(self, alert, urgency="critical"):
        """rumps.notification, guarded. Main thread only.

        Unguarded, a notification failure — no permission granted, or no
        bundle identifier, both entirely normal for a locally built app —
        raised through TrayController._apply_snapshot's alert loop and
        skipped the _maybe_recapture call right after it, so something
        cosmetic silently stopped account re-capture. Since this guard
        lives here rather than in the controller, that stays true
        regardless of which caller (an alert, a check result, a switch
        failure) reaches this.

        rumps.notification takes 3 fields (title, subtitle, body); the
        shared Alert the controller passes only carries title+body, so
        subtitle is always "" here — see TrayHost.notify's own docstring
        and the migration design's per-platform note on why a host with
        no `urgency` concept of its own (rumps has none) is free to ignore
        that parameter.
        """
        try:
            rumps.notification(alert.title, "", alert.body)
        except Exception:
            log.exception("could not post a notification")

    # -------------------------------------------------- TrayHost: icon/title

    def set_icon(self, states, update_pending):
        """No-op — see the module docstring's divergences note. This file
        has never rendered pixel icon state; the menu-bar TEXT (set_title,
        below) is the only visual surface here."""

    def set_title(self, text):
        """Ignore `text` (model.title_line, shared with Linux/Windows) and
        render model.macos_title from the controller's own state instead —
        see the module docstring's divergences note for why.

        The controller only ever calls this from two places: inside
        _apply_snapshot (where self.controller.failures is always 0 by
        the time this runs) and inside _apply_error's failures >= 3
        branch. Branching on failures here recovers which of the two old
        titles applies without needing a parameter the shared contract
        does not offer.
        """
        c = self.controller
        if c.failures >= 3:
            self.title = "⚪ ?"
            return
        account = c.snapshot.active_account if c.snapshot else None
        self.title = model.macos_title(account)

    # ---------------------------------------------------- TrayHost: the menu

    def rebuild_menu(self):
        self._rebuild_menu()

    def _rebuild_menu(self):
        c = self.controller
        self.menu.clear()
        items = []
        if self.switch_error:
            items.append(rumps.MenuItem(f"✕ {self.switch_error}"))
            items.append(None)  # separator
        if c.snapshot is None:
            items.append(rumps.MenuItem("Loading…"))
        else:
            for acct in c.snapshot.accounts:
                # No callback for the active row or a dead stored credential
                # (switching to one restores a login Anthropic rejected).
                blocked = acct.active or model.switch_blocked(acct)
                callback = None if blocked else self._make_switch(acct.number)
                items.append(rumps.MenuItem(model.menu_row(acct), callback=callback))
            if c.snapshot.openai:
                items.append(None)
                items.append(rumps.MenuItem("OpenAI"))
                for acct in c.snapshot.openai:
                    # Read-only: no switcher exists for ChatGPT logins.
                    items.append(rumps.MenuItem(model.menu_row(acct)))
        items.append(None)  # separator
        if c.update_pending:
            items.append(rumps.MenuItem(f"⬆ Update to {c.update_pending}",
                                        callback=self._on_update))
        elif c.update_blocked:
            # No callback: there is nothing to apply, only something to say.
            items.append(rumps.MenuItem(f"✕ Update held back: {c.update_blocked}"))
        label, clickable = c._check_row()
        items.append(rumps.MenuItem(
            label, callback=self._on_check_update if clickable else None))
        items.append(rumps.MenuItem("⟳ Refresh now", callback=self._tick))
        items.append(rumps.MenuItem("⚙ Open cswap TUI", callback=self._open_tui))
        items.append(rumps.MenuItem("⏻ Quit", callback=self._on_quit))
        self.menu = items

    # --------------------------------------- TrayHost: the manual check row

    def check_update_argv(self):
        return [LAUNCHER, "--check-update", "--json"]

    def _on_check_update(self, _sender):
        self.controller._on_check_update()

    # ------------------------------------------------------------- fetching

    def _presence_tick(self, _sender):
        presence_client.beat(self.controller.snapshot)

    def _tick(self, _sender):
        self.controller._tick()

    # ------------------------------------------------------------- actions

    def _on_update(self, _sender):
        """Apply it detached: the updater restarts this very app."""
        try:
            portable.spawn_detached([LAUNCHER, "--update"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            log.exception("could not start the updater")

    def _make_switch(self, number):
        def callback(_sender):
            self.switch_error = ""
            self._rebuild_menu()

            def run():
                try:
                    cswap.switch(number)
                except cswap.CswapError as exc:
                    log.warning("switch to #%s failed: %s", number, exc)
                    self.call_on_ui_thread(self._apply_switch_error, str(exc))
                # Safe from any thread — see TrayController._start_fetch's
                # own docstring. Runs whether the switch above succeeded or
                # not: the truth of a failed switch is the very refetch
                # that confirms nothing actually moved.
                self.controller._start_fetch()
            threading.Thread(target=run, daemon=True).start()
        return callback

    def _apply_switch_error(self, message):
        """Worker -> main: the switch failed. Sticky until _make_switch's
        callback clears it on the next attempt, exactly like the Swift
        store's switchError (its declaration, its switchTo switchBlocked-
        guard assignment, and its switchTo async-failure assignment) --
        not on a timer, and not on the next periodic _tick, so it survives long
        enough to actually be read.
        """
        self.switch_error = f"Switch failed: {message}"
        self.notify(Alert(title="AI smartbar", body=self.switch_error))
        self._rebuild_menu()

    def _on_quit(self, _sender):
        """Deliberate quit: stop being counted before going away."""
        try:
            presence_client.leave()
        except Exception:
            log.exception("could not withdraw this device")
        rumps.quit_application()

    def _open_tui(self, _sender):
        try:
            subprocess.Popen(["osascript", "-e",
                              'tell application "Terminal" to do script "cswap tui"'])
        except OSError:
            log.exception("could not open the cswap TUI")


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200_000:
            os.remove(LOG_FILE)
    except OSError:
        # logging isn't configured yet, but the module logger still has
        # Python's lastResort stderr fallback — better than a bare pass.
        log.exception("could not rotate %s", LOG_FILE)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log.info("ai-smartbar %s starting (interval %ss)", __version__,
             os.environ.get("SMARTBAR_INTERVAL", "60"))
    SmartBarApp().run()
