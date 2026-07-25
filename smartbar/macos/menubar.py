"""macOS menu-bar UI for AI_smartbar (rumps / native NSStatusBar).

NOT yet live-verified on a Mac — written to spec; run install/macos.sh on
the Mac, then check the menu bar. Logic lives in smartbar.core (unit-tested).
"""
import os
import subprocess
import threading
import time

import rumps

from smartbar import presence_client
from smartbar.core import cswap, model, plan, presence
from smartbar.core.alerts import AlertManager
from smartbar.core.recapture import RecapturePolicy


class SmartBarApp(rumps.App):
    def __init__(self):
        super().__init__("⚪ …", quit_button=None)
        self.alerts = AlertManager()
        self.snapshot = None
        self.failures = 0
        self.recapture = RecapturePolicy()  # paces register/heal/refresh adds
        self.presence_started = False  # first beat waits for the first fetch
        # 60s harvests cswap's poll plans as they come due; no extra API
        # traffic (the store paces the network).
        interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self._rebuild_menu()
        self.timer = rumps.Timer(self._tick, interval)
        self.timer.start()
        if presence.enabled():
            self.presence_timer = rumps.Timer(self._presence_tick,
                                              int(presence.interval()))
            self.presence_timer.start()
        self._tick(None)

    def _presence_tick(self, _sender):
        presence_client.beat(self.snapshot)

    def _tick(self, _sender):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            snap = cswap.fetch(fresh=True)
        except cswap.CswapError:
            self.failures += 1
            if self.failures >= 3:
                self.title = "⚪ ?"
            return
        self.failures = 0
        self.snapshot = snap
        # Device counts and plan badges ride along in model.account_label,
        # so the menu rows pick them up with no further work here.
        presence.apply_counts(snap, presence_client.counts())
        plan.apply_plans(snap, plan.plans_by_email())
        if not self.presence_started:
            self.presence_started = True
            presence_client.beat(snap)
        self.title = model.macos_title(snap.active_account)
        self._rebuild_menu()
        for alert in self.alerts.check(snap):
            rumps.notification("AI smartbar", alert.title, alert.body)
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
            except cswap.CswapError:
                return
            if action != "refresh":  # registration/heal changes the display
                self._tick(None)
        threading.Thread(target=run, daemon=True).start()

    def _rebuild_menu(self):
        self.menu.clear()
        items = []
        if self.snapshot is None:
            items.append(rumps.MenuItem("Loading…"))
        else:
            for acct in self.snapshot.accounts:
                # No callback for the active row or a dead stored credential
                # (switching to one restores a login Anthropic rejected).
                blocked = acct.active or model.switch_blocked(acct)
                callback = None if blocked else self._make_switch(acct.number)
                items.append(rumps.MenuItem(model.menu_row(acct), callback=callback))
        items.append(None)  # separator
        pending = self._pending_update()
        if pending:
            items.append(rumps.MenuItem(f"⬆ Update to {pending}",
                                        callback=self._on_update))
        items.append(rumps.MenuItem("⟳ Refresh now", callback=self._tick))
        items.append(rumps.MenuItem("⚙ Open cswap TUI", callback=self._open_tui))
        items.append(rumps.MenuItem("⏻ Quit", callback=self._on_quit))
        self.menu = items

    def _pending_update(self) -> str:
        """The release the updater found waiting, or "" — never raises."""
        try:
            from smartbar import update_runner
            from smartbar.core import update as update_core
            return update_core.pending_version(update_runner.load_state())
        except Exception:
            return ""

    def _on_update(self, _sender):
        """Apply it detached: the updater restarts this very app."""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        try:
            subprocess.Popen([os.path.join(repo, "bin", "ai-smartbar"), "--update"],
                             start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def _make_switch(self, number):
        def callback(_sender):
            def run():
                try:
                    cswap.switch(number)
                except cswap.CswapError:
                    pass
                self._tick(None)
            threading.Thread(target=run, daemon=True).start()
        return callback

    def _on_quit(self, _sender):
        """Deliberate quit: stop being counted before going away."""
        presence_client.leave()
        rumps.quit_application()

    def _open_tui(self, _sender):
        subprocess.Popen(["osascript", "-e",
                          'tell application "Terminal" to do script "cswap tui"'])


def main():
    SmartBarApp().run()
