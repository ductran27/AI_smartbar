"""XFCE/Linux system-tray UI: AppIndicator + cairo-drawn badge icon."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

import logging
import os
import subprocess
import threading
import time

import cairo
from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk

from smartbar import __version__
from smartbar.core import cswap, model
from smartbar.core.alerts import AlertManager

CACHE_DIR = os.path.expanduser("~/.cache/ai-smartbar")
ICON_DIR = os.path.join(CACHE_DIR, "icons")
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")
AUTO_ADD_COOLDOWN = 600  # retry ceiling when `cswap add` cannot succeed

COLORS = {"green": (0.18, 0.65, 0.32), "yellow": (0.85, 0.65, 0.13),
          "low": (0.894, 0.376, 0.294), "critical": (0.80, 0.184, 0.184),
          "gray": (0.45, 0.45, 0.45)}

log = logging.getLogger("ai-smartbar")


def _rounded_rect(ctx, x, y, w, h, r):
    r = min(r, h / 2, w / 2)
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -1.5708, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, 1.5708)
    ctx.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    ctx.arc(x + r, y + r, r, 3.1416, 4.7124)
    ctx.close_path()


def render_pills(states, path: str) -> None:
    """Twin-pill badge (same design as the macOS icon, 6x scale).

    One vertical pill per (fraction_left, color) state: general limit
    first, then per-model buckets. Fill anchors to the bottom and drains
    downward as tokens are spent. Empty states -> hollow pills + "?".
    The panel scales the PNG to its height.
    """
    pill_w, pill_h, gap, margin, radius = 30, 96, 12, 12, 15
    n = len(states) if states else 2
    w = margin * 2 + pill_w * n + gap * (n - 1)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, pill_h)
    ctx = cairo.Context(surface)
    for i in range(n):
        x = margin + i * (pill_w + gap)
        if not states:
            _rounded_rect(ctx, x + 3, 3, pill_w - 6, pill_h - 6, radius)
            ctx.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            ctx.set_line_width(6)
            ctx.stroke()
            continue
        _rounded_rect(ctx, x, 0, pill_w, pill_h, radius)
        ctx.set_source_rgba(0.5, 0.5, 0.5, 0.45)
        ctx.fill()
        frac, color_name = states[i]
        if frac > 0:
            fill_h = max(12, round(pill_h * min(frac, 1.0)))
            _rounded_rect(ctx, x, pill_h - fill_h, pill_w, fill_h, radius)
            ctx.set_source_rgb(*COLORS[color_name])
            ctx.fill()
    if not states:
        ctx.set_source_rgba(1, 1, 1, 0.85)
        ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                             cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(48)
        ext = ctx.text_extents("?")
        ctx.move_to((w - ext.width) / 2 - ext.x_bearing,
                    (pill_h - ext.height) / 2 - ext.y_bearing)
        ctx.show_text("?")
    surface.write_to_png(path)


class Tray:
    def __init__(self):
        os.makedirs(ICON_DIR, exist_ok=True)
        # 60s harvests cswap's poll plans the moment they come due (incl.
        # the 60s urgent cadence near the limit); the store still paces the
        # real network, so faster polling adds no API traffic.
        self.interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self.alerts = AlertManager()
        self.snapshot = None
        self.failures = 0
        self.flip = False
        self.generation = 0  # stamps fetches so superseded results are dropped
        self.menu = None
        self.pending_menu = None  # rebuilt while open; swapped in on hide
        self.last_fetch_at = 0.0  # monotonic; guards menu-open refreshes
        self.last_auto_add = None  # monotonic; auto-registration cooldown
        self.indicator = AppIndicator.Indicator.new(
            "ai-smartbar", "dialog-information",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_icon_theme_path(ICON_DIR)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._set_icon([])  # hollow "?" pills until the first fetch lands
        self._install_menu(self._build_menu())
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

    def _set_icon(self, states):
        # Alternate two icon names: AppIndicator ignores a set_icon_full call
        # with the current name, so a single name would never repaint.
        self.flip = not self.flip
        name = f"state-{'a' if self.flip else 'b'}"
        render_pills(states, os.path.join(ICON_DIR, name + ".png"))
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
        menu.connect("show", self._on_menu_show)
        menu.connect("hide", self._on_menu_hide)
        menu.show_all()
        return menu

    def _install_menu(self, menu):
        self.menu = menu
        self.pending_menu = None
        self.indicator.set_menu(menu)

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
        self.generation += 1
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
        self.failures = 0
        self.snapshot = snap
        if snap.schema_warning:
            log.warning("%s", snap.schema_warning)
        account = snap.active_account
        self._set_icon(model.pill_states(account))
        self.indicator.set_title(model.title_line(account))
        self._refresh_menu()
        for alert in self.alerts.check(snap):
            self._send_alert(alert)
        self._maybe_auto_register(snap)
        return False

    def _maybe_auto_register(self, snap):
        # /login with an unregistered account leaves no slot active:
        # register it via cswap's own non-interactive `add`. The cooldown
        # stops retry spam while add cannot succeed (logged out, locked
        # keychain). SMARTBAR_AUTO_ADD=off disables.
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
            except cswap.CswapError as exc:
                log.info("auto-add skipped: %s", exc)
                return
            log.info("auto-registered current login via cswap add")
            self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    def _apply_error(self, message, generation):
        if generation != self.generation:
            return False  # superseded
        self.failures += 1
        if self.failures >= 3:
            self._set_icon([])
            self.indicator.set_title(f"AI smartbar — cswap error: {message[:80]}")
        self._refresh_menu()
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
