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

import json
import logging
import os
import time

import gi

gi.require_version("Gtk", "3.0")
# Explicit even though Gtk 3.0 implies it: the import below names Gdk FIRST,
# and in a process that has not loaded Gtk yet an unpinned Gdk resolves to
# 4.0 on any box with GTK4 typelibs — making Gtk 3.0 unloadable after it.
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk   # noqa: E402

from smartbar.core import paths, popover_layout   # noqa: E402
from smartbar.paint import popover_draw    # noqa: E402

TICK_SECONDS = 30    # countdowns are minute-resolution; this keeps them live
CORNER_MARGIN = 12   # gap from the work-area corner the panel parks in
DRAG_THRESHOLD = 4   # px of pointer travel that turns a press into a drag
MAX_HEIGHT_MARGIN = 48   # breathing room off the top+bottom of the work area
SCROLL_STEP = 30         # content px per wheel notch (the Windows increment)
# Where a drag's final origin is remembered across shows and restarts. Cache,
# not config: losing it only means the panel re-opens in its default corner.
POSITION_FILE = os.path.join(paths.cache_dir(), "panel-position.json")

log = logging.getLogger("ai-smartbar")


class Popover(Gtk.Window):
    """Undecorated panel; `rebuild(hover)` supplies a fresh Layout.

    Opens where the user last dragged it, else in the top-right corner of
    the work area. The window is undecorated, so dragging is ours to
    provide: press anywhere — cards and buttons included — and move past a
    small threshold to relocate the panel; a press that stays put is the
    click it always was. The dragged spot is kept (POSITION_FILE) across
    shows and restarts.

    `pinned` (SMARTBAR_PANEL=always) makes this a permanent desktop readout
    instead of a popover: shown at startup and never auto-hidden.

    Taller than the screen, the panel becomes a scrolling viewport rather
    than running off the bottom of the work area: the window is capped at
    _max_panel_height() and the wheel moves the paint underneath it. The
    wheel is the only way to reach the rest — no scrollbar affordance, no
    keyboard scrolling — and a still pointer keeps whatever hover it had
    while the content slides under it. All three match the Windows
    viewport, which disclosed the same gaps: one UI, one set of holes.
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
        self._press = None    # armed click: (x_root, y_root, x, y, hit, time)
        self._dragging = False
        self._drag_offset = (0, 0)   # pointer-to-origin offset while dragging
        self._placed = None   # last origin WE chose, to spot WM-driven moves
        self._saved = self._load_position()  # origin a drag chose, or None
        self._move_grace = 0.0  # ignore focus-out just after a Wayland drag
        self._scroll = 0.0   # content px hidden above the viewport's top edge
        self._overflow = 0   # content px that do not fit; 0 = nothing to scroll
        display = Gdk.Display.get_default()
        self._x11 = (display is not None
                     and "x11" in type(display).__name__.lower())

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
                             | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK
                             | Gdk.EventMask.LEAVE_NOTIFY_MASK
                             | Gdk.EventMask.SCROLL_MASK
                             | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        # The panel is painted, not a widget tree, so GTK has nothing to
        # attach a tooltip to on its own: set_has_tooltip puts the pointer
        # position through our own hit-test instead (FINDING 8).
        self.area.set_has_tooltip(True)
        self.area.connect("query-tooltip", self._on_query_tooltip)
        self.area.connect("draw", self._on_draw)
        self.area.connect("button-press-event", self._on_press)
        self.area.connect("button-release-event", self._on_release)
        self.area.connect("motion-notify-event", self._on_motion)
        self.area.connect("leave-notify-event", self._on_leave)
        self.area.connect("scroll-event", self._on_scroll)
        self.add(self.area)

        self.connect("focus-out-event", self._on_focus_out)
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)

    # --- painting ---------------------------------------------------------
    def refresh_layout(self) -> None:
        layout = self.rebuild(self.hover)
        self.layout = layout
        width, full_height = int(round(layout.width)), int(round(layout.height))
        cap = self._max_panel_height()
        # cap <= 0 means the lookup failed (see its own docstring): degrade
        # to the old, uncapped behaviour rather than guess a screen size.
        height = full_height if cap <= 0 else min(full_height, cap)
        self._overflow = max(0, full_height - height)
        self._scroll = min(self._scroll, float(self._overflow))
        self.set_size_request(width, height)
        self.resize(width, height)
        self.area.queue_draw()
        if (width, height) != self._size:
            self._size = (width, height)
            # A pin stays on screen, so a size change (an account appearing,
            # the update row arriving) has to re-place it — back into its
            # corner, or onto the remembered spot — or it drifts.
            if self.pinned and self.get_visible():
                self._position()

    def _on_draw(self, _area, ctx) -> bool:
        if self.layout is not None:
            # The FULL layout is painted every time and the window clips it;
            # scrolling moves the paint, so nothing downstream of here (the
            # painter, the layout, the hit rects) knows a viewport exists.
            ctx.translate(0, -self._scroll)
            popover_draw.draw(self.layout, ctx)
        return False

    # --- input ------------------------------------------------------------
    def _on_press(self, _area, event) -> bool:
        if event.button != 1 or self.layout is None:
            return False
        hit = self.layout.hit(event.x, self._content_y(event.y))
        # Nothing fires yet: a press only arms. The click happens on release,
        # so that dragging from anywhere — a card, a button, the header —
        # moves the panel instead of activating whatever sat under the
        # pointer when the grab began.
        self._press = (event.x_root, event.y_root, event.x, event.y,
                       hit.name if hit is not None else "", event.time)
        self._dragging = False
        return True

    def _on_release(self, _area, event) -> bool:
        if event.button != 1:
            return False
        if self._dragging:
            self._dragging = False
            self._press = None
            self._remember(tuple(self.get_position()))
            return True
        press, self._press = self._press, None
        if press is None or self.layout is None:
            return False
        hit = self.layout.hit(event.x, self._content_y(event.y))
        # Fire only when the release lands on what was pressed — ordinary
        # button semantics, and a sub-threshold wobble stays a click.
        if hit is not None and hit.name == press[4]:
            self.on_action(hit.name)
        return True

    def _on_motion(self, _area, event) -> bool:
        if self._dragging:
            self.move(int(event.x_root - self._drag_offset[0]),
                      int(event.y_root - self._drag_offset[1]))
            return True
        if self._press is not None:
            x_root, y_root, x, y, _hit, stamp = self._press
            if (abs(event.x_root - x_root) > DRAG_THRESHOLD
                    or abs(event.y_root - y_root) > DRAG_THRESHOLD):
                self._start_drag(x, y, x_root, y_root, stamp)
                return True
        if self.layout is None:
            return False
        hit = self.layout.hit(event.x, self._content_y(event.y))
        name = hit.name if hit is not None else ""
        if name != self.hover:
            self.hover = name
            self.refresh_layout()
        return False

    def _start_drag(self, x, y, x_root, y_root, stamp) -> None:
        """Turn the armed press into a window move.

        X11: move the window ourselves from each motion event. This works
        for the UTILITY popover and the DOCK pin alike (window managers
        refuse interactive moves for docks), and it leaves us knowing the
        final origin, which is what gets remembered. Wayland: hand the
        gesture to the compositor — a client cannot move itself there, and
        the compositor never says where the drag ended, so there is nothing
        to remember either.
        """
        if self._x11:
            self._dragging = True
            self._drag_offset = (x, y)
            return
        self._press = None
        # The compositor may briefly pull focus during its move; that must
        # not read as "the user moved on" and hide the panel mid-drag.
        self._move_grace = time.monotonic() + 2.0
        try:
            self.begin_move_drag(1, int(x_root), int(y_root), stamp)
        except Exception:
            log.debug("begin_move_drag failed", exc_info=True)

    def _on_query_tooltip(self, _area, x, y, keyboard_mode, tooltip) -> bool:
        """Explain whatever the pointer is over — the GTK half of FINDING 8.

        Returning False is what HIDES a tooltip GTK is already showing, so
        the "nothing to say here" path must fall through rather than leave
        the previous hit's text up as the pointer moves on.

        Keyboard mode is declined outright: it asks for the tooltip of the
        focused widget, and this window deliberately owns exactly one
        (the DrawingArea) with no pointer position to hit-test against.

        tooltip_at(), not hit(): the blocked "Make Active" button is a
        disabled hit, and its tooltip is the only thing that says why it
        is refusing. See popover_theme.Layout.tooltip_at.
        """
        if self.layout is None or keyboard_mode:
            return False
        text = self.layout.tooltip_at(x, self._content_y(y))
        if not text:
            return False
        tooltip.set_text(text)
        return True

    def _on_leave(self, *_args) -> bool:
        if self._dragging:
            # The window is chasing the pointer; a momentary exit while it
            # catches up is not the pointer leaving.
            return False
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
        if time.monotonic() < self._move_grace:
            return False       # a compositor-driven drag, not the user leaving
        if not self.pinned:
            self.hide_panel()  # behave like a popover, not a window
        return False

    def _on_delete(self, *_args) -> bool:
        self.hide_panel()
        return True            # never destroy: the tray reuses this window

    # --- scrolling viewport -----------------------------------------------
    def _content_y(self, y: float) -> float:
        """Widget y -> layout y.

        EVERY hit test has to come through here. The viewport scrolls the
        PAINT (one ctx.translate in _on_draw), so once the panel is scrolled
        a pointer 10px below the window's top edge is over content that may
        be hundreds of px down the layout -- and a click would otherwise
        fire whichever button happens to sit at the untranslated coordinate.
        """
        return y + self._scroll

    def _on_scroll(self, _area, event) -> bool:
        """Wheel an overtall panel -- FINDING 9's other half.

        Returns False when there is nothing to scroll rather than
        swallowing the event, so a wheel over a panel that fits still
        reaches whatever would otherwise have had it.
        """
        if self._overflow <= 0:
            return False
        delta = self._scroll_delta(event)
        if not delta:
            return False
        self._scroll = max(0.0, min(float(self._overflow),
                                    self._scroll + delta * SCROLL_STEP))
        self.area.queue_draw()
        return True

    @staticmethod
    def _scroll_delta(event) -> float:
        """Notches from either kind of GTK scroll event; + is towards the end.

        Both have to be read. A touchpad (and an X11 wheel under a
        compositor that synthesises smooth scrolling) sends direction=SMOOTH
        with the real numbers in get_scroll_deltas(); a plain wheel sends
        UP/DOWN and get_scroll_deltas() returns nothing useful. Handling
        only one leaves the other device scrolling nothing at all.
        """
        direction = getattr(event, "direction", None)
        if direction == Gdk.ScrollDirection.SMOOTH:
            ok, _dx, dy = event.get_scroll_deltas()
            return dy if ok else 0.0
        if direction == Gdk.ScrollDirection.DOWN:
            return 1.0
        if direction == Gdk.ScrollDirection.UP:
            return -1.0
        return 0.0

    def _max_panel_height(self) -> int:
        """Panel px to cap at before content must scroll instead of running
        past the bottom of the work area.

        FINDING 9, measured: 8 accounts is 769pt of content and 12 is
        1117pt, both past what a 1080p work area can show. 0 means "could
        not work one out" -- no display, a lookup that raised, or the
        stubbed-gi test environment -- and refresh_layout treats that as
        "do not clamp" rather than guessing a screen size, the same way
        _position() treats a failed placement as cosmetic.
        """
        try:
            display = Gdk.Display.get_default()
            if display is None:
                return 0
            areas = [a for a in self._workareas(display)
                     if a[2] > 0 and a[3] > 0]
            if not areas:
                return 0
            # The monitor the panel is on, if it is anywhere yet; otherwise
            # the roomiest, which is both pin_origin's own tie-break and
            # where an unplaced panel is about to be parked.
            area_h = max(areas, key=lambda a: a[2] * a[3])[3]
            origin = self._placed or self._saved
            if origin is not None:
                for ax, ay, aw, ah in areas:
                    if (ax <= origin[0] < ax + aw
                            and ay <= origin[1] < ay + ah):
                        area_h = ah
                        break
            return max(0, area_h - 2 * MAX_HEIGHT_MARGIN)
        except Exception:
            log.exception("screen height lookup failed; panel stays uncapped")
            return 0

    # --- visibility -------------------------------------------------------
    def show_panel(self) -> None:
        self.hover = ""
        self._scroll = 0.0   # a fresh open starts at the top of the content
        self.refresh_layout()
        self._position()   # before the map, so the panel APPEARS in place …
        self.show_all()
        self.present()
        self._position()   # … and again after it, for WMs that adjust on map
        if self._tick_id == 0:
            self._tick_id = GLib.timeout_add_seconds(TICK_SECONDS, self._tick)

    def hide_panel(self) -> None:
        self._note_wm_move()
        self.hover = ""
        self._press = None
        self._dragging = False
        self.hide()

    def toggle(self) -> None:
        self.hide_panel() if self.get_visible() else self.show_panel()

    def _tick(self) -> bool:
        if not self.get_visible():
            self._tick_id = 0
            return False
        try:
            self.refresh_layout()   # countdowns recompute from the reset times
        except Exception:
            # PyGObject treats a raising callback as "remove me", and
            # _tick_id then held a stale id so show_panel never re-armed:
            # one bad layout froze every countdown for the process lifetime.
            log.exception("panel tick failed")
        return True

    def _position(self) -> None:
        """Place the panel: where the user last dragged it, else the default
        top-right corner — the same corner (popover_layout.pin_origin) a
        pinned panel has always parked in.

        Under Wayland a client cannot position itself — there are no global
        coordinates — so the compositor's own placement is left alone and
        only the drag (compositor-driven there) can relocate the panel.
        """
        if not self._x11:
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        try:
            areas = self._workareas(display)
            # The size just laid out, which is exactly what we are placing;
            # get_size() only agrees once GTK has processed the resize.
            size = self._size if self._size != (0, 0) else self.get_size()
            origin = (popover_layout.restore_origin(self._saved, areas, size)
                      or popover_layout.pin_origin(areas, size, CORNER_MARGIN))
            if origin is not None:
                self.move(*origin)
                self._placed = tuple(origin)
        except Exception:       # placement is cosmetic; never break the panel
            pass

    @staticmethod
    def _workareas(display):
        areas = []
        for i in range(display.get_n_monitors()):
            monitor = display.get_monitor(i)
            if monitor is None:
                continue
            a = monitor.get_workarea()
            areas.append((a.x, a.y, a.width, a.height))
        return areas

    # --- remembered position ----------------------------------------------
    def _note_wm_move(self) -> None:
        """Keep a move made around us too (Super+drag and friends): a window
        that is not where this code put it was moved by the user."""
        if not (self._x11 and self.get_visible() and self._placed):
            return
        try:
            origin = tuple(self.get_position())
        except Exception:
            return
        if origin != self._placed:
            self._remember(origin)

    def _remember(self, origin) -> None:
        """A drag is the user choosing a spot: keep it across shows and runs."""
        self._saved = origin
        self._placed = origin
        try:
            tmp = POSITION_FILE + ".tmp"
            with open(tmp, "w") as handle:
                json.dump({"x": origin[0], "y": origin[1]}, handle)
            os.replace(tmp, POSITION_FILE)
        except OSError:
            log.debug("could not save the panel position", exc_info=True)

    @staticmethod
    def _load_position():
        try:
            with open(POSITION_FILE) as handle:
                data = json.load(handle)
            return (int(data["x"]), int(data["y"]))
        except (OSError, ValueError, TypeError, KeyError):
            return None
