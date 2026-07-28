"""tkinter window that hosts the cairo-painted popover.

Ported from smartbar/linux/popover_window.py (Gtk.Window + Gtk.DrawingArea);
this is the same port on tk.Toplevel + tk.Canvas, so the panel stays the
same UI on Windows as on Linux/macOS instead of inheriting a native look —
same reason the Linux file exists at all. tkinter has no cairo-backed
widget, so where GTK hands popover_draw a Context bound straight to the
window's own backing surface (popover_window.py:107-110), this module
renders to an off-screen cairo.ImageSurface, PNG-encodes it to an in-memory
buffer, decodes that with Pillow and places the result on a Canvas as an
ImageTk.PhotoImage — see refresh_layout()/_paint() below.

Tested by tests/test_windows_popover_window.py, which stubs sys.modules
for "tkinter", "PIL", "PIL.Image", "PIL.ImageTk" and "cairo" before
importing this module: Popover subclasses tk.Toplevel directly, so the
fake "tkinter" module's Toplevel/Canvas need to be real classes usable as
a base type, not bare MagicMock() instances (those fail at class-
definition time -- "metaclass conflict" / "instances not usable as a base
type" -- not at call time). Platform is faked the same way the rest of
this codebase does it (see tests/test_presence.py): monkeypatching this
module's own `sys.platform` attribute, e.g. `popover_window.sys.platform
= "win32"`. That patch -- or an explicit call to enable_dpi_awareness()
-- has to happen *before* this module is imported: enable_dpi_awareness()
reads sys.platform once, at import time (see below), so patching it
afterward in a test's setUp() has no effect.

Two behaviours added on top of the original port, both covered by that
test file:

Per-hit tooltips (FINDING 8's `Hit.tooltip`, tray study item 5). tkinter
has no built-in tooltip widget; this is the standard workaround -- a
borderless Toplevel shown after a short hover dwell (_reset_tooltip /
_show_tooltip / _hide_tooltip) so the tooltip does not flicker in and out
on every pixel of pointer movement across a hit, and hidden again on
<Leave>, a click, or the panel closing. This is about the PANEL's
per-hit tooltips; the tray icon's own tooltip (Shell_NotifyIcon's
szTip[128], see MAX_TITLE_LEN in tray.py) is a separate, already-solved
problem.

A height cap with a scrollable viewport (FINDING 9, the tray study's own
measurement: 8 accounts -> 769pt, 12 -> 1117pt tall, comfortably past a
1080p work area's usable height with no cap and no way to reach what
scrolled off). refresh_layout() now paints the FULL content into the
cairo surface as before, but caps the window/canvas to whichever
monitor's work area is actually relevant (_max_panel_height_px) and lets
the canvas scroll to the rest via its own scrollregion + a bound
<MouseWheel>. Hit-testing (_on_click/_on_motion) reads back through
canvas.canvasx()/canvasy() so a click lands on the right layout
coordinate regardless of scroll position. Deliberately NOT done, because
it needs either a live display or a lot more Win32-specific plumbing than
this pass's remit covers: a scrollbar affordance (mouse-wheel and drag
are the only ways to reach the rest for now), keyboard/touch scrolling,
and smooth (as opposed to per-notch) scrolling. _position()'s existing
placement clamp already keeps the header on-screen once height is capped
to fit the work area; a defensive floor was added there anyway (see its
own docstring) so a cap that somehow still overflows can never push the
header above the monitor's top edge, which is this finding's one hard
requirement.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import logging
import sys

import cairo
import tkinter as tk
from PIL import Image
try:
    from PIL import ImageTk
except ImportError as exc:
    # Confirmed real risk (API research, item 6): ImageTk is compiled into
    # a Pillow wheel only if Tcl/Tk headers were present at Pillow's own
    # build time. Official PyPI Windows wheels normally have it, but "not a
    # hard guarantee for every environment" per that research — fail with a
    # clear message the first time a paint is attempted rather than an
    # opaque AttributeError deep inside _paint().
    ImageTk = None
    _IMAGETK_IMPORT_ERROR = exc
else:
    _IMAGETK_IMPORT_ERROR = None

from smartbar.core import popover_layout
from smartbar.core import popover_theme as t
from smartbar.paint import popover_draw

log = logging.getLogger("ai-smartbar")

TICK_SECONDS = 30    # countdowns are minute-resolution; this keeps them live
PIN_MARGIN = 12      # gap from the work-area corner a pinned panel sits in
TOOLTIP_DELAY_MS = 500     # hover dwell before a Hit.tooltip appears
MAX_HEIGHT_MARGIN = 48     # breathing room off the top+bottom of the work
                           # area a capped panel is still allowed to use

# Font override (study item 6 / contract D5): cairo's Win32/GDI backend has
# no fontconfig-style generic-name aliasing, so the "sans-serif"/"monospace"
# literals popover_draw._select_font() passes to select_font_face() resolve
# to whatever GDI substitutes by default instead of Segoe UI/Consolas. The
# actual fix — popover_draw reading t.FONT_SANS/t.FONT_MONO instead of the
# inline literals — is popover_draw.py's own follow-up (out of scope for
# this file). Setting the constants here is forward-compatible and inert
# until that follow-up lands: no code reads them yet, so this can't break
# anything today, and once it does land this module (imported before any
# draw() call on Windows) is where the values get set.
# Unconditional, deliberately: this is a platform override, not a default,
# and a hasattr guard would silently become a no-op -- reintroducing the
# exact bug it exists to prevent -- the moment popover_theme.py grows its
# own FONT_SANS/FONT_MONO for popover_draw._select_font() to read.
t.FONT_SANS = "Segoe UI"
t.FONT_MONO = "Consolas"


class _MonitorInfo(ctypes.Structure):
    """Mirrors Win32's MONITORINFO for GetMonitorInfoW's rcWork field."""
    _fields_ = [("cbSize", wintypes.DWORD),
               ("rcMonitor", wintypes.RECT),
               ("rcWork", wintypes.RECT),
               ("dwFlags", wintypes.DWORD)]


def _declare_signatures() -> None:
    """Pin argtypes/restype for every ctypes.windll entry point this module
    calls, instead of letting ctypes default them all to a 32-bit c_int.

    Left undeclared, a 64-bit handle like MonitorFromPoint's HMONITOR or
    winfo_id()'s HWND gets truncated (and sign-extended back into a
    different value) the moment it's read back, and the failure that
    causes is silent: GetMonitorInfoW/GetDpiForWindow just return FALSE/0
    for what looks like a valid handle (study_7_ab9ec2.txt). Declared once
    here, at import time on win32 only, rather than scattered per call.
    """
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = \
        [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.SystemParametersInfoW.argtypes = \
        [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    ctypes.windll.shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]


def enable_dpi_awareness() -> None:
    """Best-effort per-monitor-v2 DPI awareness for the whole process.

    Microsoft's own docs (API research, item 4) are explicit that this must
    run before any DPI-dependent API, "including before creating any UI in
    your process" — so this only does anything useful if the Windows entry
    point imports this module (which calls it at import time, below) before
    it instantiates tk.Tk(). Falls back to the coarser, older
    SetProcessDpiAwareness when SetProcessDpiAwarenessContext returns FALSE
    -- documented for ERROR_INVALID_PARAMETER (context value not recognised
    by this Windows build) and ERROR_ACCESS_DENIED (DPI awareness already
    set by an application manifest, or by an earlier call) -- or when the
    export itself is missing (AttributeError, older Windows still). Does
    nothing at all off Windows or under the stubbed-module test
    environment — both paths are ctypes.windll lookups, which don't exist
    outside win32, so both are wrapped rather than gated on a platform
    check alone.
    """
    if sys.platform != "win32":
        return
    _declare_signatures()
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4 (API research).
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            return
    except Exception:
        log.exception("SetProcessDpiAwarenessContext failed")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        log.exception("SetProcessDpiAwareness fallback failed")


enable_dpi_awareness()   # must run before the caller's tk.Tk(); see above


class Popover(tk.Toplevel):
    """Undecorated panel; `rebuild(hover)` supplies a fresh Layout.

    `pinned` (SMARTBAR_PANEL=always) makes this a permanent desktop readout
    instead of a popover: shown at startup, never auto-hidden, and anchored
    to a screen corner rather than following the pointer — same contract as
    the Linux Popover.

    Square corners (radius=0.0 is passed to popover_draw.draw() below by
    default) rather than the Linux/macOS rounded panel: GTK's rounded
    corners depend on an RGBA visual plus a compositing window manager
    (popover_window.py:69-75) giving true per-pixel alpha, so the corner
    pixels outside the rounded rect end up transparent. A plain tk.Toplevel
    has no such thing — wm_attributes("-alpha", ...) is whole-window
    opacity only, and true per-pixel alpha would need WS_EX_LAYERED +
    UpdateLayeredWindow via ctypes, which tkinter doesn't expose. Square
    sidesteps the whole problem: with radius=0 there is nothing to
    alpha-blend at a corner. If a square panel looks wrong against real
    Windows chrome, the documented fallback (not implemented here — this is
    a comment, not code, per this port's brief) is colour-key transparency:
    `self.wm_attributes("-transparentcolor", some_color)` plus painting
    that same color into WINDOW_BG's would-be corner pixels, which is a
    real tk/Windows feature but wasn't run through this port's API
    research pass, so it isn't wired up sight-unseen.
    """

    def __init__(self, rebuild, on_action, pinned=False, radius: float = 0.0):
        super().__init__()
        self.rebuild = rebuild
        self.on_action = on_action
        self.pinned = pinned
        self.radius = radius
        self.layout = None
        self.hover = ""
        self._tick_id = None
        self._size = (0, 0)    # last painted (scaled) size; pins re-anchor
        self._scale = 1.0      # monitor DPI / 96; refreshed in refresh_layout
        self._photo = None     # kept alive on the instance -- see below
        self._image_id = None
        self._tooltip_win = None       # the dwell-shown Toplevel, or None
        self._tooltip_after_id = None  # pending dwell timer, or None
        self._tooltip_hit = ""         # tooltip text owning the timer
        self._scroll_bound = False     # is <MouseWheel> currently bound?

        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.resizable(False, False)
        # No direct tkinter equivalent of Gtk's skip_taskbar_hint /
        # skip_pager_hint / set_type_hint(UTILITY|DOCK): an overrideredirect
        # window is already un-decorated, and is expected to be excluded
        # from the taskbar and Alt-Tab on Windows too -- but unlike this
        # file's other Windows claims, that one was NOT verified by this
        # port's API research pass. If it turns out not to hold, the
        # documented fallback is the WS_EX_TOOLWINDOW extended style,
        # settable via GetWindowLongPtr/SetWindowLongPtr on winfo_id() --
        # not wired up here, same as the -transparentcolor fallback below.
        if pinned:
            # GTK additionally calls set_accept_focus(False)/
            # set_focus_on_map(False) so a permanent readout can never steal
            # keyboard focus from whatever the user is typing into — tk
            # exposes no direct "reject focus" toggle for a Toplevel. The
            # closest available behaviour is show_panel() below simply
            # never calling focus_force() for a pin, so at least this port
            # never *asks* for focus, even though (unlike GTK) nothing stops
            # Windows from handing it focus anyway on some interaction path.
            pass
        self.withdraw()   # start hidden; show_panel() reveals it

        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)

        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Escape>", self._on_key)
        self.protocol("WM_DELETE_WINDOW", self._on_delete)

    # --- painting -----------------------------------------------------------
    def refresh_layout(self) -> None:
        layout = self.rebuild(self.hover)
        self.layout = layout
        self._update_scale()
        width = max(1, int(round(layout.width * self._scale)))
        full_height = max(1, int(round(layout.height * self._scale)))
        cap = self._max_panel_height_px()
        # cap <= 0 means the monitor lookup failed (see its own docstring)
        # -- degrade to the old, uncapped behaviour rather than guess.
        height = full_height if cap <= 0 else min(full_height, cap)
        self._paint(layout, width, full_height)
        self.canvas.configure(width=width, height=height,
                              scrollregion=(0, 0, width, full_height),
                              yscrollincrement=30)
        self._bind_scroll(height < full_height)
        self.geometry(f"{width}x{height}")
        if (width, height) != self._size:
            self._size = (width, height)
            # A pin is anchored to a corner, so a size change (an account
            # appearing, the update row arriving) has to re-anchor it or the
            # panel drifts away from that corner -- mirrors the Linux file.
            if self.pinned and self.get_visible():
                self._position()

    def _max_panel_height_px(self) -> int:
        """Max scaled-px panel height before content must scroll rather
        than grow past the bottom (or, if placed high enough, even the
        top) of the screen -- FINDING 9: measured at 8 accounts -> 769pt,
        12 -> 1117pt, comfortably past a 1080p work area's usable height
        with no cap at all. 0 means "could not determine one" (headless,
        a lookup failure, or the stubbed-tkinter test environment) --
        refresh_layout() treats that as "do not clamp" rather than
        guessing, same as this file's other best-effort monitor lookups.

        Uses whichever monitor's work area is actually relevant: the one
        under the pointer for a popover (that is where _position() will
        place it), or the widest -- pin_origin's own tie-break -- for a
        pin, whose corner is chosen before any window exists to ask
        "which monitor am I on".
        """
        try:
            if self.pinned:
                areas = [a for a in self._monitor_workareas()
                        if a[2] > 0 and a[3] > 0]
                if not areas:
                    return 0
                _, _, _, area_h = max(areas, key=lambda a: a[2] * a[3])
            else:
                px, py = self.winfo_pointerx(), self.winfo_pointery()
                _, _, _, area_h = self._work_area_at(px, py)
            return max(0, area_h - 2 * MAX_HEIGHT_MARGIN)
        except Exception:
            log.exception("screen height lookup failed; panel will not be capped")
            return 0

    def _bind_scroll(self, scrollable: bool) -> None:
        """(Un)bind the mouse wheel to match whether there is anything to
        scroll -- avoids a bound handler quietly consuming wheel events
        over a panel that has nothing to scroll."""
        if scrollable and not self._scroll_bound:
            self.canvas.bind("<MouseWheel>", self._on_scroll)
            self._scroll_bound = True
        elif not scrollable and self._scroll_bound:
            self.canvas.unbind("<MouseWheel>")
            self._scroll_bound = False
            self.canvas.yview_moveto(0.0)

    def _on_scroll(self, event) -> None:
        # Windows wheel deltas are multiples of 120 per notch; positive
        # means "scroll up" (matches yview_scroll's own sign convention).
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _paint(self, layout, width: int, height: int) -> None:
        if ImageTk is None:
            raise RuntimeError(
                "Pillow was built without Tk support (PIL.ImageTk unavailable): "
                f"{_IMAGETK_IMPORT_ERROR}")
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        ctx.scale(self._scale, self._scale)
        popover_draw.draw(layout, ctx, radius=self.radius)
        buf = io.BytesIO()
        surface.write_to_png(buf)
        buf.seek(0)
        image = Image.open(buf)
        # Kept as an instance attribute deliberately -- a PhotoImage is only
        # referenced by Tk at the Tcl/C level via its image name, not by the
        # canvas item from Python's own perspective, so a local-variable-only
        # reference is garbage-collected the moment this method returns and
        # the canvas goes blank even though itemconfig() reported success.
        self._photo = ImageTk.PhotoImage(image)
        if self._image_id is None:
            self._image_id = self.canvas.create_image(
                0, 0, anchor="nw", image=self._photo)
        else:
            self.canvas.itemconfig(self._image_id, image=self._photo)

    def _update_scale(self) -> None:
        """Refresh self._scale from the monitor DPI under this window.

        Re-read on every refresh rather than cached once, since a pin or a
        dragged popover can move to a different monitor with a different
        DPI (Per-Monitor-v2). GetDpiForWindow needs Windows 10 1607+ and a
        realised HWND; anything else (older Windows, or winfo_id() on a
        stubbed Toplevel in tests) leaves self._scale at whatever it already
        was rather than raising.
        """
        try:
            hwnd = self.winfo_id()
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi:
                self._scale = dpi / 96.0
        except Exception:
            log.exception("GetDpiForWindow failed")

    # --- input ---------------------------------------------------------------
    def _on_click(self, event) -> None:
        self._reset_tooltip("")
        if self.layout is None:
            return
        x = self.canvas.canvasx(event.x) / self._scale
        y = self.canvas.canvasy(event.y) / self._scale
        hit = self.layout.hit(x, y)
        if hit is not None:
            self.on_action(hit.name)

    def _on_motion(self, event) -> None:
        if self.layout is None:
            return
        x = self.canvas.canvasx(event.x) / self._scale
        y = self.canvas.canvasy(event.y) / self._scale
        hit = self.layout.hit(x, y)
        name = hit.name if hit is not None else ""
        if name != self.hover:
            self.hover = name
            self.refresh_layout()
            # tooltip_at(), not `hit`: hit() skips DISABLED hits, and the
            # blocked "Make Active" button is disabled — its tooltip is the
            # only thing that says why it will not respond.
            self._reset_tooltip(self.layout.tooltip_at(x, y))

    def _on_leave(self, _event) -> None:
        if self.hover:
            self.hover = ""
            self.refresh_layout()
        self._reset_tooltip("")

    # --- tooltips --------------------------------------------------------
    def _reset_tooltip(self, tip: str) -> None:
        """(Re)start the dwell timer for the hovered tooltip TEXT, or hide
        it outright when nothing (or nothing with a tooltip) is hovered.

        Takes the text rather than the Hit because the text now comes from
        Layout.tooltip_at(), which — unlike hit() — also answers for a
        DISABLED hit, and a disabled control is the one most in need of
        explaining itself.

        tkinter ships no tooltip widget -- _show_tooltip below is the
        standard workaround, a borderless Toplevel. Gated behind a short
        dwell (TOOLTIP_DELAY_MS) so it doesn't flicker in and out on every
        pixel the pointer crosses within one Hit; _on_motion only calls
        this when the HOVERED HIT actually changed, which already gates
        out most of that, but the timer still protects against a rapid
        hop across several small hits (e.g. the tab row).
        """
        if self._tooltip_after_id is not None:
            self.after_cancel(self._tooltip_after_id)
            self._tooltip_after_id = None
        self._tooltip_hit = tip
        self._hide_tooltip()
        if tip:
            self._tooltip_after_id = self.after(
                TOOLTIP_DELAY_MS, self._show_tooltip, tip)

    def _show_tooltip(self, tip: str) -> None:
        self._tooltip_after_id = None
        if tip != self._tooltip_hit:
            return  # hover moved on before the dwell timer fired
        self._hide_tooltip()
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        label = tk.Label(win, text=tip, background="#2b2b2b",
                         foreground="#f2f2f2", borderwidth=1, relief="solid",
                         padx=6, pady=3, font=(t.FONT_SANS, 9))
        label.pack()
        x = self.winfo_pointerx() + 12
        y = self.winfo_pointery() + 18
        win.geometry(f"+{x}+{y}")
        self._tooltip_win = win

    def _hide_tooltip(self) -> None:
        if self._tooltip_win is not None:
            self._tooltip_win.destroy()
            self._tooltip_win = None

    def _on_key(self, _event) -> None:
        if not self.pinned:
            self.hide_panel()

    def _on_focus_out(self, _event) -> None:
        if not self.pinned:
            self.hide_panel()   # behave like a popover, not a window

    def _on_delete(self) -> None:
        self.hide_panel()       # never destroy: the tray reuses this window

    # --- visibility ------------------------------------------------------------
    def show_panel(self) -> None:
        self.hover = ""
        self.refresh_layout()
        self.deiconify()
        self.lift()
        if not self.pinned:
            # Confirmed by Tk's own docs (API research, item 3): neither the
            # X11 nor the Windows Tk implementation gives an
            # overrideredirect window focus when it is raised, so without
            # this, Escape and FocusOut dismissal silently never fire. A pin
            # deliberately skips it -- see the constructor comment on why a
            # permanent readout must never steal focus.
            self.focus_force()
        self._position()
        if self._tick_id is None:
            # GLib.timeout_add_seconds repeats on its own; tk's after() is
            # one-shot, so _tick() below has to reschedule itself each time.
            self._tick_id = self.after(TICK_SECONDS * 1000, self._tick)

    def hide_panel(self) -> None:
        self.hover = ""
        self._reset_tooltip("")
        self.withdraw()

    def toggle(self) -> None:
        self.hide_panel() if self.get_visible() else self.show_panel()

    def get_visible(self) -> bool:
        return self.state() not in ("withdrawn", "iconic")

    def _tick(self) -> None:
        if not self.get_visible():
            self._tick_id = None
            return
        self.refresh_layout()   # countdowns recompute from the reset times
        self._tick_id = self.after(TICK_SECONDS * 1000, self._tick)

    # --- placement ---------------------------------------------------------
    def _position(self) -> None:
        """Place the panel: a pin goes to a fixed corner, a popover under or
        above the pointer -- the Win32 analogue of the Linux file's Gdk
        pointer + monitor lookup (popover_window.py:176-202).
        """
        try:
            if self.pinned:
                self._anchor_corner()
                return
            px, py = self.winfo_pointerx(), self.winfo_pointery()
            area = self._work_area_at(px, py)
            # Same hazard _anchor_corner() documents below: winfo_width()/
            # height() only agree with the geometry() just applied in
            # refresh_layout() once Tk has processed the resize, which the
            # very first show_panel() hasn't given it a chance to do yet.
            width, height = self._size if self._size != (0, 0) \
                else (self.winfo_width(), self.winfo_height())
            x = min(max(px - width // 2, area[0] + 8),
                    area[0] + area[2] - width - 8)
            # Tray at the top of the screen? drop down; otherwise pop up.
            y = py + 24 if (py - area[1]) < area[3] // 2 else py - height - 24
            y = min(max(y, area[1] + 8), area[1] + area[3] - height - 8)
            # A height cap failure (or a monitor lookup returning something
            # unexpected) must never leave the header placed above the
            # work area's top edge -- FINDING 9's one hard requirement,
            # independent of whatever _max_panel_height_px did or didn't
            # manage to clamp.
            y = max(y, area[1])
            self.geometry(f"+{int(x)}+{int(y)}")
        except Exception:   # placement is cosmetic; never break the panel
            log.exception("popover placement failed")

    def _anchor_corner(self) -> None:
        """Park the pin in the corner popover_layout.pin_origin picks."""
        areas = self._monitor_workareas()
        # The size just laid out, which is exactly what we are anchoring;
        # winfo_width()/height() only agree once Tk has processed the resize.
        size = self._size if self._size != (0, 0) \
            else (self.winfo_width(), self.winfo_height())
        origin = popover_layout.pin_origin(areas, size, PIN_MARGIN)
        if origin is not None:
            x, y = origin
            self.geometry(f"+{int(x)}+{int(y)}")

    def _work_area_at(self, px: int, py: int):
        """(x, y, w, h) of the monitor work area under a point.

        MonitorFromPoint + GetMonitorInfoW's rcWork is the standard
        multi-monitor mechanism, but unlike SPI_GETWORKAREA (confirmed
        directly against Microsoft Learn by the API research pass) this
        specific pairing wasn't independently re-verified there, so a wrong
        or changed signature falls through to the confirmed SPI_GETWORKAREA
        call -- correct on a single monitor, and better than nothing when
        the pointer is on a secondary one it can't distinguish.
        """
        try:
            MONITOR_DEFAULTTONEAREST = 2
            point = wintypes.POINT(int(px), int(py))
            hmon = ctypes.windll.user32.MonitorFromPoint(
                point, MONITOR_DEFAULTTONEAREST)
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                w, h = r.right - r.left, r.bottom - r.top
                if w > 0 and h > 0:
                    return (r.left, r.top, w, h)
        except Exception:
            log.exception(
                "MonitorFromPoint/GetMonitorInfoW work-area lookup failed")
        try:
            # SystemParametersInfoW returns a BOOL, and it has to be
            # checked: wintypes.RECT() zero-initialises, so a FALSE return
            # would otherwise hand back (0, 0, 0, 0) -- indistinguishable
            # from a genuine (if degenerate) work area to every caller.
            SPI_GETWORKAREA = 0x0030
            rect = wintypes.RECT()
            ok = ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
            if ok:
                w, h = rect.right - rect.left, rect.bottom - rect.top
                if w > 0 and h > 0:
                    return (rect.left, rect.top, w, h)
        except Exception:
            log.exception("SystemParametersInfoW(SPI_GETWORKAREA) failed")
        return (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())

    def _monitor_workareas(self):
        """Every monitor's work area as (x, y, w, h), for pin_origin().

        Same confirmed-vs-unverified split as _work_area_at(): a failure
        anywhere in the EnumDisplayMonitors callback degrades to an empty
        list, so pin_origin() returns None and the pin is simply left
        wherever it already was rather than raising.
        """
        areas = []
        try:
            # Win32's MONITORENUMPROC is BOOL CALLBACK(HMONITOR, HDC,
            # LPRECT, LPARAM); LPARAM is a pointer-sized integer, not a
            # float -- a c_double fourth argument here would corrupt the
            # stdcall stack on 32-bit Windows (study_7_ab9ec2.txt).
            proto = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

            def _callback(hmonitor, _hdc, _rect, _data):
                info = _MonitorInfo()
                info.cbSize = ctypes.sizeof(_MonitorInfo)
                if ctypes.windll.user32.GetMonitorInfoW(
                        hmonitor, ctypes.byref(info)):
                    r = info.rcWork
                    areas.append((r.left, r.top,
                                 r.right - r.left, r.bottom - r.top))
                return 1

            ctypes.windll.user32.EnumDisplayMonitors(
                None, None, proto(_callback), 0)
        except Exception:
            log.exception("EnumDisplayMonitors failed")
        return areas
