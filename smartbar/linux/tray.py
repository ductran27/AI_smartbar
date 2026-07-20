"""XFCE/Linux system-tray UI: AppIndicator + cairo-drawn badge icon."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

import logging
import os
import subprocess
import threading

import cairo
from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk

from smartbar import __version__
from smartbar.core import cswap, model
from smartbar.core.alerts import AlertManager

CACHE_DIR = os.path.expanduser("~/.cache/ai-smartbar")
ICON_DIR = os.path.join(CACHE_DIR, "icons")
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")

COLORS = {"green": (0.18, 0.65, 0.32), "yellow": (0.85, 0.65, 0.13),
          "red": (0.80, 0.16, 0.16), "gray": (0.45, 0.45, 0.45)}

log = logging.getLogger("ai-smartbar")


def render_icon(rows, path: str) -> None:
    """Stacked badge: one rounded-rect line per (text, color) row.

    One row (e.g. loading/error) renders as the original single badge; two
    rows stack general-limit above the per-model bucket, each independently
    colored. The panel scales the PNG to its height.
    """
    w, row_h, gap, r = 96, 40, 6, 10
    h = row_h * len(rows) + gap * (len(rows) - 1)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surface)
    ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    for i, (text, color_name) in enumerate(rows):
        top = i * (row_h + gap)
        ctx.new_sub_path()
        ctx.arc(w - r, top + r, r, -1.5708, 0)
        ctx.arc(w - r, top + row_h - r, r, 0, 1.5708)
        ctx.arc(r, top + row_h - r, r, 1.5708, 3.1416)
        ctx.arc(r, top + r, r, 3.1416, 4.7124)
        ctx.close_path()
        ctx.set_source_rgb(*COLORS[color_name])
        ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        ctx.set_font_size(30)
        ext = ctx.text_extents(text)
        ctx.move_to((w - ext.width) / 2 - ext.x_bearing,
                    top + (row_h - ext.height) / 2 - ext.y_bearing)
        ctx.show_text(text)
    surface.write_to_png(path)


class Tray:
    def __init__(self):
        os.makedirs(ICON_DIR, exist_ok=True)
        self.interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self.alerts = AlertManager()
        self.snapshot = None
        self.failures = 0
        self.flip = False
        self.indicator = AppIndicator.Indicator.new(
            "ai-smartbar", "dialog-information",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_icon_theme_path(ICON_DIR)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._set_icon([("...", "gray")])
        self.indicator.set_menu(self._build_menu())
        self._init_notify()

    def _init_notify(self):
        self.notify = None
        try:
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify
            Notify.init("AI smartbar")
            self.notify = Notify
        except Exception:
            log.warning("libnotify unavailable; will fall back to notify-send")

    def _send_alert(self, alert):
        try:
            if self.notify is not None:
                self.notify.Notification.new(alert.title, alert.body,
                                             "dialog-warning").show()
            else:
                subprocess.run(["notify-send", "-u", "critical",
                                alert.title, alert.body], timeout=10, check=False)
        except Exception:
            log.exception("failed to send notification")

    def _set_icon(self, rows):
        # Alternate two icon names: AppIndicator ignores a set_icon_full call
        # with the current name, so a single name would never repaint.
        self.flip = not self.flip
        name = f"state-{'a' if self.flip else 'b'}"
        render_icon(rows, os.path.join(ICON_DIR, name + ".png"))
        self.indicator.set_icon_full(name, "AI smartbar usage")

    def _build_menu(self):
        menu = Gtk.Menu()
        if self.snapshot is None:
            label = "Loading…" if self.failures == 0 else "cswap error — see tray.log"
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(False)
            menu.append(item)
        else:
            stale = "  (stale)" if self.failures else ""
            for acct in self.snapshot.accounts:
                item = Gtk.MenuItem(label=model.menu_row(acct)
                                    + (stale if acct.active else ""))
                if acct.active:
                    item.set_sensitive(False)
                else:
                    item.connect("activate", self._on_switch, acct.number)
                menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        for label, callback in (("⟳ Refresh now", self._on_refresh),
                                ("⚙ Open cswap TUI", self._on_tui),
                                ("⏻ Quit", self._on_quit)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)
        menu.show_all()
        return menu

    def _on_switch(self, _item, number):
        def run():
            try:
                cswap.switch(number)
            except cswap.CswapError:
                log.exception("switch failed")
            self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    def _on_refresh(self, _item):
        self._start_fetch()

    def _on_tui(self, _item):
        try:
            subprocess.Popen(["x-terminal-emulator", "-e", "cswap", "tui"])
        except OSError:
            log.exception("could not open terminal")

    def _on_quit(self, _item):
        Gtk.main_quit()

    def _start_fetch(self):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            snap = cswap.fetch()
        except cswap.CswapError as exc:
            log.warning("fetch failed: %s", exc)
            GLib.idle_add(self._apply_error, str(exc))
            return
        GLib.idle_add(self._apply_snapshot, snap)

    def _apply_snapshot(self, snap):
        self.failures = 0
        self.snapshot = snap
        if snap.schema_warning:
            log.warning("%s", snap.schema_warning)
        account = snap.active_account
        self._set_icon(model.icon_rows(account))
        self.indicator.set_title(model.title_line(account))
        self.indicator.set_menu(self._build_menu())
        for alert in self.alerts.check(snap):
            self._send_alert(alert)
        return False

    def _apply_error(self, message):
        self.failures += 1
        if self.failures >= 3:
            self._set_icon([("?", "gray")])
            self.indicator.set_title(f"AI smartbar — cswap error: {message[:80]}")
        self.indicator.set_menu(self._build_menu())
        return False

    def _tick(self):
        self._start_fetch()
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
    Gtk.main()
