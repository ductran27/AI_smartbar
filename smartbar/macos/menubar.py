"""macOS menu-bar UI for AI_smartbar (rumps / native NSStatusBar).

NOT yet live-verified on a Mac — written to spec; run install/macos.sh on
the Mac, then check the menu bar. Logic lives in smartbar.core (unit-tested).
"""
import os
import subprocess
import threading

import rumps

from smartbar.core import cswap, model
from smartbar.core.alerts import AlertManager


class SmartBarApp(rumps.App):
    def __init__(self):
        super().__init__("⚪ …", quit_button=None)
        self.alerts = AlertManager()
        self.snapshot = None
        self.failures = 0
        interval = int(os.environ.get("SMARTBAR_INTERVAL", "300"))
        self._rebuild_menu()
        self.timer = rumps.Timer(self._tick, interval)
        self.timer.start()
        self._tick(None)

    def _tick(self, _sender):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            snap = cswap.fetch()
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
