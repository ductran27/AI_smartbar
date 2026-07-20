"""macOS menu-bar UI for AI_smartbar (rumps / native NSStatusBar).

NOT yet live-verified on a Mac — written to spec; run install/macos.sh on
the Mac, then check the menu bar. Logic lives in smartbar.core (unit-tested).
"""
import os
import subprocess
import threading
import time

import rumps

from smartbar.core import cswap, model
from smartbar.core.alerts import AlertManager

AUTO_ADD_COOLDOWN = 600  # retry ceiling when `cswap add` cannot succeed


class SmartBarApp(rumps.App):
    def __init__(self):
        super().__init__("⚪ …", quit_button=None)
        self.alerts = AlertManager()
        self.snapshot = None
        self.failures = 0
        self.last_auto_add = None  # monotonic; auto-registration cooldown
        # 60s harvests cswap's poll plans as they come due; no extra API
        # traffic (the store paces the network).
        interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self._rebuild_menu()
        self.timer = rumps.Timer(self._tick, interval)
        self.timer.start()
        self._tick(None)

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
        self.title = model.macos_title(snap.active_account)
        self._rebuild_menu()
        for alert in self.alerts.check(snap):
            rumps.notification("AI smartbar", alert.title, alert.body)
        self._maybe_auto_register(snap)

    def _maybe_auto_register(self, snap):
        # Mirror of tray.py: register an unregistered /login via `cswap add`.
        if os.environ.get("SMARTBAR_AUTO_ADD") == "off":
            return
        if not model.needs_registration(snap):
            return
        now = time.monotonic()
        if self.last_auto_add is not None \
                and now - self.last_auto_add < AUTO_ADD_COOLDOWN:
            return
        self.last_auto_add = now

        def run():
            try:
                cswap.add()
            except cswap.CswapError:
                return
            self._tick(None)
        threading.Thread(target=run, daemon=True).start()

    def _rebuild_menu(self):
        self.menu.clear()
        items = []
        if self.snapshot is None:
            items.append(rumps.MenuItem("Loading…"))
        else:
            for acct in self.snapshot.accounts:
                callback = None if acct.active else self._make_switch(acct.number)
                items.append(rumps.MenuItem(model.menu_row(acct), callback=callback))
        items.append(None)  # separator
        items.append(rumps.MenuItem("⟳ Refresh now", callback=self._tick))
        items.append(rumps.MenuItem("⚙ Open cswap TUI", callback=self._open_tui))
        items.append(rumps.MenuItem("⏻ Quit", callback=lambda _s: rumps.quit_application()))
        self.menu = items

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

    def _open_tui(self, _sender):
        subprocess.Popen(["osascript", "-e",
                          'tell application "Terminal" to do script "cswap tui"'])


def main():
    SmartBarApp().run()
