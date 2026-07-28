"""macOS menu-bar UI for AI_smartbar (rumps / native NSStatusBar).

NOT yet live-verified on a Mac — written to spec; run install/macos.sh on
the Mac, then check the menu bar. Logic lives in smartbar.core (unit-tested).

Three rules this file did not used to follow, each already settled in the
Linux and Windows front-ends and each learned the same way:

  * Nothing off the main thread touches AppKit. `_fetch` runs in a worker
    and used to set self.title, rebuild self.menu and raise notifications
    straight from there. Every UI mutation now goes through `_to_main`, the
    analogue of GLib.idle_add (Linux) and root.after_idle (Windows).
  * Fetches carry a generation, so one that started before an account switch
    cannot land after it and quietly put the old account back on screen.
  * Failures are logged. Every `except` here used to swallow in silence — on
    the one front-end whose own header admits it has never run on real
    hardware, which is precisely where an invisible failure costs most.
"""
import json
import logging
import os
import queue
import subprocess
import threading
import time

import rumps

from smartbar import __version__, presence_client
from smartbar.core import codex, cswap, model, paths, plan, portable, presence
from smartbar.core import update as update_core
from smartbar.core.alerts import AlertManager
from smartbar.core.recapture import RecapturePolicy

CACHE_DIR = paths.cache_dir()
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
LAUNCHER = os.path.join(REPO_ROOT, "bin", "ai-smartbar")
# A manual check runs `git fetch`. Bounded so a stalled network leaves the row
# saying "Check failed" rather than "Checking…" for ever.
CHECK_TIMEOUT = 120
# How long the outcome sits in the menu before the row goes back to normal.
CHECK_RESULT_SECONDS = 20
# rumps has no idle_add. An NSTimer callback is the one place this app is
# guaranteed to be on the main thread, so the worker -> UI queue is drained
# by a short repeating timer instead.
UI_DRAIN_SECONDS = 0.2

log = logging.getLogger("ai-smartbar")


class SmartBarApp(rumps.App):
    def __init__(self):
        super().__init__("⚪ …", quit_button=None)
        self.alerts = AlertManager()
        self.snapshot = None
        self.failures = 0
        self.generation = 0   # stamps fetches so superseded results are dropped
        self.checking = False
        self.check_result = ""
        self.check_token = 0
        self.update_pending = ""
        self.update_blocked = ""
        self.switch_error = ""  # sticky until the next switch attempt
        self.recapture = RecapturePolicy()  # paces register/heal/refresh adds
        self.presence_started = False  # first beat waits for the first fetch
        self._ui_queue = queue.Queue()
        # 60s harvests cswap's poll plans as they come due; no extra API
        # traffic (the store paces the network).
        interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self.update_pending = self._pending_update()
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

    # ------------------------------------------------ thread -> UI handoff

    def _to_main(self, callback, *args):
        """Queue callback(*args) for the main thread. Worker -> UI handoff.

        AppKit is not thread-safe for UI mutation, and rumps offers no
        idle_add. Whatever calls this is NOT allowed to touch self.title,
        self.menu or rumps.notification itself.
        """
        self._ui_queue.put((callback, args))

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

    def _notify(self, title, subtitle, body):
        """rumps.notification, guarded. Main thread only.

        Unguarded, a notification failure — no permission granted, or no
        bundle identifier, both entirely normal for a locally built app —
        raised through the alert loop in _apply_snapshot and skipped the
        _maybe_recapture below it, so something cosmetic silently stopped
        account re-capture.
        """
        try:
            rumps.notification(title, subtitle, body)
        except Exception:
            log.exception("could not post a notification")

    # ------------------------------------------------------------- fetching

    def _presence_tick(self, _sender):
        presence_client.beat(self.snapshot)

    def _tick(self, _sender):
        self.generation += 1
        threading.Thread(target=self._fetch, args=(self.generation,),
                         daemon=True).start()

    def _fetch(self, generation):
        """Worker thread: does the I/O, touches no AppKit state."""
        try:
            snap = cswap.fetch(fresh=True)
        except cswap.CswapError as exc:
            log.warning("fetch failed: %s", exc)
            self._to_main(self._apply_error, generation)
            return
        try:
            # Device counts and plan badges ride along in model.account_label,
            # so the menu rows pick them up with no further work here. All
            # three are file/subprocess reads and belong off the main thread.
            presence.apply_counts(snap, presence_client.counts())
            plan.apply_plans(snap, plan.plans_by_email())
            # ChatGPT accounts ride the snapshot's separate list; this text
            # menu shows them as read-only rows under an OpenAI header.
            snap.openai = codex.accounts()
        except Exception:
            log.exception("could not decorate the snapshot")
        if not self.presence_started:
            self.presence_started = True
            presence_client.beat(snap)   # a spawn, not a UI touch
        self._to_main(self._apply_snapshot, snap, generation)

    def _apply_error(self, generation):
        if generation != self.generation:
            return
        self.failures += 1
        if self.failures >= 3:
            self.title = "⚪ ?"

    def _apply_snapshot(self, snap, generation):
        if generation != self.generation:
            return  # superseded, e.g. a pre-switch fetch landing late
        self.failures = 0
        self.snapshot = snap
        self.title = model.macos_title(snap.active_account)
        self.update_pending = self._pending_update()
        self._rebuild_menu()
        for alert in self.alerts.check(snap):
            self._notify("AI smartbar", alert.title, alert.body)
        self._maybe_recapture(snap)

    def _maybe_recapture(self, snap):
        # Mirror of tray.py: `cswap add` registers an unregistered /login,
        # heals a dead active backup and periodically re-captures the live
        # login so token rotations never orphan the backup.
        action = self.recapture.action(snap, time.monotonic())
        if action is None:
            return

        def run():
            try:
                cswap.add()
            except cswap.CswapError as exc:
                log.warning("cswap add (%s) failed: %s", action, exc)
                return
            if action != "refresh":  # registration/heal changes the display
                self._to_main(self._tick, None)
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------- the menu

    def _rebuild_menu(self):
        self.menu.clear()
        items = []
        if self.switch_error:
            items.append(rumps.MenuItem(f"✕ {self.switch_error}"))
            items.append(None)  # separator
        if self.snapshot is None:
            items.append(rumps.MenuItem("Loading…"))
        else:
            for acct in self.snapshot.accounts:
                # No callback for the active row or a dead stored credential
                # (switching to one restores a login Anthropic rejected).
                blocked = acct.active or model.switch_blocked(acct)
                callback = None if blocked else self._make_switch(acct.number)
                items.append(rumps.MenuItem(model.menu_row(acct), callback=callback))
            if self.snapshot.openai:
                items.append(None)
                items.append(rumps.MenuItem("OpenAI"))
                for acct in self.snapshot.openai:
                    # Read-only: no switcher exists for ChatGPT logins.
                    items.append(rumps.MenuItem(model.menu_row(acct)))
        items.append(None)  # separator
        if self.update_pending:
            items.append(rumps.MenuItem(f"⬆ Update to {self.update_pending}",
                                        callback=self._on_update))
        elif self.update_blocked:
            # No callback: there is nothing to apply, only something to say.
            items.append(rumps.MenuItem(f"✕ Update held back: {self.update_blocked}"))
        label, callback = self._check_row()
        items.append(rumps.MenuItem(label, callback=callback))
        items.append(rumps.MenuItem("⟳ Refresh now", callback=self._tick))
        items.append(rumps.MenuItem("⚙ Open cswap TUI", callback=self._open_tui))
        items.append(rumps.MenuItem("⏻ Quit", callback=self._on_quit))
        self.menu = items

    def _pending_update(self) -> str:
        """The release the updater found waiting, or "" — never raises.

        Also records why an update is being held back (dirty checkout,
        unpushed commits) so the row above can say so.
        """
        # update_runner stays a LAZY import: it pulls in the lock shims and
        # the git layer, and a menu bar must not pay for those at import
        # time. The reading itself is shared — see update_runner.pending_for_ui.
        from smartbar import update_runner
        pending, self.update_blocked = update_runner.pending_for_ui()
        return pending

    # ---------------------------------------------------- the manual check

    def _check_row(self):
        """(label, callback) for the manual update-check row.

        Three states, matching linux/tray.py's row exactly: idle, in flight,
        and a result that sits there for CHECK_RESULT_SECONDS. A callback of
        None is how rumps renders a row as un-clickable.
        """
        if self.checking:
            return "⇅ Checking for updates…", None
        if self.check_result:
            return self.check_result, None
        return "⇅ Check for updates", self._on_check_update

    def _on_check_update(self, _sender):
        if self.checking:
            return
        self.checking = True
        self.check_result = ""
        self.check_token += 1
        self._rebuild_menu()
        threading.Thread(target=self._check_update, args=(self.check_token,),
                         daemon=True).start()

    def _check_update(self, token):
        """Worker thread — this does a network fetch.

        `--check-update --json` does the whole thing: it only reports (applying
        stays the separate, deliberate "⬆ Update to …" row) and it decides what
        to SAY, so this menu, the Linux tray and the Swift popover cannot drift
        apart in either the wording or the rules behind it.
        """
        try:
            done = subprocess.run([LAUNCHER, "--check-update", "--json"],
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=CHECK_TIMEOUT,
                                  **portable.no_window())
            answer = json.loads(done.stdout)
        except Exception:
            log.exception("update check could not run")
            answer = None
        self._to_main(self._checked, token, answer)

    def _checked(self, token, answer):
        if token != self.check_token:
            return  # superseded by a newer check
        self.checking = False
        self.update_pending = self._pending_update()  # also sets update_blocked
        if not isinstance(answer, dict) or not answer.get("label"):
            failure = update_core.check_outcome(failed=True)
            answer = {"label": failure.label, "title": failure.title,
                      "body": failure.body}
        self.check_result = answer["label"]
        # Clicking a row closes the menu, so the label alone would be invisible
        # until the user opened it again — the notification is the real feedback.
        self._notify(answer.get("title", "AI smartbar"), "",
                     answer.get("body", ""))
        self._rebuild_menu()
        expiry = threading.Timer(CHECK_RESULT_SECONDS, self._to_main,
                                 args=(self._clear_check_result, token))
        expiry.daemon = True  # must not hold a quit open for 20s
        expiry.start()

    def _clear_check_result(self, token):
        if token == self.check_token and self.check_result:
            self.check_result = ""
            self._rebuild_menu()

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
                    self._to_main(self._apply_switch_error, str(exc))
                self._to_main(self._tick, None)
            threading.Thread(target=run, daemon=True).start()
        return callback

    def _apply_switch_error(self, message):
        """Worker -> main: the switch failed. Sticky until _make_switch's
        callback clears it on the next attempt, exactly like the Swift
        store's switchError (UsageStore.swift:15, :156, :181) -- not on a
        timer, and not on the next periodic _tick, so it survives long
        enough to actually be read.
        """
        self.switch_error = f"Switch failed: {message}"
        self._notify("AI smartbar", "", self.switch_error)
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
