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

This module has no test coverage yet -- no test_windows_tray.pysrc (or
equivalent) exists in this tree. If/when one is written, it would need to
stub sys.modules for "tkinter", "PIL", "PIL.Image", "PIL.ImageTk" and
"cairo" before importing this module: Popover subclasses tk.Toplevel
directly, so the fake "tkinter" module's Toplevel/Canvas need to be real
classes usable as a base type, not bare MagicMock() instances (those fail
at class-definition time -- "metaclass conflict" / "instances not usable
as a base type" -- not at call time). Platform would be faked the same
way the rest of this codebase does it (see tests/test_presence.py):
monkeypatch this module's own `sys.platform` attribute, e.g.
`popover_window.sys.platform = "win32"`. That patch -- or an explicit call
to enable_dpi_awareness() -- has to happen *before* this module is
imported: enable_dpi_awareness() reads sys.platform once, at import time
(see below), so patching it afterward in a test's setUp() has no effect.
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
        height = max(1, int(round(layout.height * self._scale)))
        self._paint(layout, width, height)
        self.canvas.configure(width=width, height=height)
        self.geometry(f"{width}x{height}")
        if (width, height) != self._size:
            self._size = (width, height)
            # A pin is anchored to a corner, so a size change (an account
            # appearing, the update row arriving) has to re-anchor it or the
            # panel drifts away from that corner -- mirrors the Linux file.
            if self.pinned and self.get_visible():
                self._position()

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
        if self.layout is None:
            return
        hit = self.layout.hit(event.x / self._scale, event.y / self._scale)
        if hit is not None:
            self.on_action(hit.name)

    def _on_motion(self, event) -> None:
        if self.layout is None:
            return
        hit = self.layout.hit(event.x / self._scale, event.y / self._scale)
        name = hit.name if hit is not None else ""
        if name != self.hover:
            self.hover = name
            self.refresh_layout()

    def _on_leave(self, _event) -> None:
        if self.hover:
            self.hover = ""
            self.refresh_layout()

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
