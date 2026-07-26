"""GTK window that hosts the cairo-painted popover.

GTK contributes only a window and input events — everything visible is
painted by popover_draw from a popover_layout, so the panel is the same UI
as the macOS popover on XFCE, GNOME and KDE alike instead of inheriting each
distro's widget theme.

Why a window rather than a richer menu: AppIndicator menus travel to the
panel over DBus as dbusmenu, which carries labels, icons, checkmarks and
separators only. `Gtk.MenuItem.add(widget)` works in-process and is silently
dropped on the way out, so cards, filled bars and the ACTIVE chip cannot
exist inside a tray menu at any level of effort.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk   # noqa: E402

from smartbar.core import popover_layout   # noqa: E402
from smartbar.paint import popover_draw    # noqa: E402

TICK_SECONDS = 30    # countdowns are minute-resolution; this keeps them live
PIN_MARGIN = 12      # gap from the work-area corner a pinned panel sits in


class Popover(Gtk.Window):
    """Undecorated panel; `rebuild(hover)` supplies a fresh Layout.

    `pinned` (SMARTBAR_PANEL=always) makes this a permanent desktop readout
    instead of a popover: shown at startup, never auto-hidden, and anchored
    to a screen corner rather than following the pointer.
    """

    def __init__(self, rebuild, on_action, pinned=False):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.rebuild = rebuild
        self.on_action = on_action
        self.pinned = pinned
        self.layout = None
        self.hover = ""
        self._tick_id = 0
        self._size = (0, 0)   # last painted size; a change re-anchors a pin

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)          # no-op under Wayland; harmless
        # DOCK for a pin, UTILITY for a popover. Not cosmetic: xfwm4 constrains
        # an ordinary window to the monitor it thinks the window belongs to, and
        # on a desktop whose primary is a small headless dummy stacked over the
        # real display that clamp drags the panel hundreds of px inboard of the
        # corner (measured: a move to x=2218 landed at 1891). DOCK is exempt —
        # it is what a permanent readout is anyway.
        self.set_type_hint(Gdk.WindowTypeHint.DOCK if pinned
                           else Gdk.WindowTypeHint.UTILITY)
        self.set_app_paintable(True)
        if pinned:
            # Never take keyboard focus: a window that is always on screen
            # would otherwise steal it from whatever the user is typing into
            # — and with no focus there is no focus-out, so the popover's
            # self-hiding cannot fire either.
            self.set_accept_focus(False)
            self.set_focus_on_map(False)
            self.stick()                   # present on every workspace
        # Rounded corners need an RGBA visual and a compositor; without one
        # the corners just fall back to the window's own background.
        screen = self.get_screen()
        if screen is not None and screen.is_composited():
            visual = screen.get_rgba_visual()
            if visual is not None:
                self.set_visual(visual)

        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK
                             | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.area.connect("draw", self._on_draw)
        self.area.connect("button-press-event", self._on_click)
        self.area.connect("motion-notify-event", self._on_motion)
        self.area.connect("leave-notify-event", self._on_leave)
        self.add(self.area)

        self.connect("focus-out-event", self._on_focus_out)
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)

    # --- painting ---------------------------------------------------------
    def refresh_layout(self) -> None:
        layout = self.rebuild(self.hover)
        self.layout = layout
        width, height = int(round(layout.width)), int(round(layout.height))
        self.set_size_request(width, height)
        self.resize(width, height)
        self.area.queue_draw()
        if (width, height) != self._size:
            self._size = (width, height)
            # A pin is anchored to a corner, so a size change (an account
            # appearing, the update row arriving) has to re-anchor it or the
            # panel drifts away from that corner.
            if self.pinned and self.get_visible():
                self._position()

    def _on_draw(self, _area, ctx) -> bool:
        if self.layout is not None:
            popover_draw.draw(self.layout, ctx)
        return False

    # --- input ------------------------------------------------------------
    def _on_click(self, _area, event) -> bool:
        if self.layout is None:
            return False
        hit = self.layout.hit(event.x, event.y)
        if hit is not None:
            self.on_action(hit.name)
        return True

    def _on_motion(self, _area, event) -> bool:
        if self.layout is None:
            return False
        hit = self.layout.hit(event.x, event.y)
        name = hit.name if hit is not None else ""
        if name != self.hover:
            self.hover = name
            self.refresh_layout()
        return False

    def _on_leave(self, *_args) -> bool:
        if self.hover:
            self.hover = ""
            self.refresh_layout()
        return False

    def _on_key(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape and not self.pinned:
            self.hide_panel()
            return True
        return False

    def _on_focus_out(self, *_args) -> bool:
        if not self.pinned:
            self.hide_panel()  # behave like a popover, not a window
        return False

    def _on_delete(self, *_args) -> bool:
        self.hide_panel()
        return True            # never destroy: the tray reuses this window

    # --- visibility -------------------------------------------------------
    def show_panel(self) -> None:
        self.hover = ""
        self.refresh_layout()
        self.show_all()
        self.present()
        self._position()
        if self._tick_id == 0:
            self._tick_id = GLib.timeout_add_seconds(TICK_SECONDS, self._tick)

    def hide_panel(self) -> None:
        self.hover = ""
        self.hide()

    def toggle(self) -> None:
        self.hide_panel() if self.get_visible() else self.show_panel()

    def _tick(self) -> bool:
        if not self.get_visible():
            self._tick_id = 0
            return False
        self.refresh_layout()   # countdowns recompute from the reset times
        return True

    def _position(self) -> None:
        """Place the panel: a pin goes to a fixed corner, a popover to the
        pointer.

        Under Wayland a client cannot position itself — there are no global
        coordinates — so the compositor's own placement is left alone rather
        than faked.
        """
        display = Gdk.Display.get_default()
        if display is None or "x11" not in type(display).__name__.lower():
            return
        try:
            if self.pinned:
                self._anchor_corner(display)
                return
            _screen, px, py = display.get_default_seat().get_pointer().get_position()
            monitor = display.get_monitor_at_point(px, py)
            area = monitor.get_workarea()
            width, height = self.get_size()
            x = min(max(px - width // 2, area.x + 8),
                    area.x + area.width - width - 8)
            # Tray at the top of the screen? drop down; otherwise pop up.
            y = py + 24 if (py - area.y) < area.height // 2 else py - height - 24
            y = min(max(y, area.y + 8), area.y + area.height - height - 8)
            self.move(x, y)
        except Exception:       # placement is cosmetic; never break the panel
            pass

    def _anchor_corner(self, display) -> None:
        """Park the pin in the corner popover_layout.pin_origin picks."""
        areas = []
        for i in range(display.get_n_monitors()):
            monitor = display.get_monitor(i)
            if monitor is None:
                continue
            a = monitor.get_workarea()
            areas.append((a.x, a.y, a.width, a.height))
        # The size just laid out, which is exactly what we are anchoring;
        # get_size() only agrees once GTK has processed the resize.
        size = self._size if self._size != (0, 0) else self.get_size()
        origin = popover_layout.pin_origin(areas, size, PIN_MARGIN)
        if origin is not None:
            self.move(*origin)
