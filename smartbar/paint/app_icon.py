"""Cairo renderer for the application logo — cairo only, never gi.

The logo is the menu-bar mark at icon scale: the same vertical pills, the
same bottom-anchored fill, the same RGB ramp. That is the whole point of it
— the thing in the Dock, in Login Items, on a notification and on a Windows
shortcut has to be recognisably the object already living in the menu bar,
so MenuBarIcon/tray_icon's geometry (pill 5 wide x 16 tall, gap = h/8) is
reproduced here rather than re-invented.

Two deliberate departures from the runtime badge, both because a logo is
read at 1024px and not 16:

  * The fill's top edge is FLAT (see _level). The badge rounds it, which at
    16px is sub-pixel and invisible; at icon scale a rounded top makes the
    fill read as a second capsule floating inside the tube instead of as a
    level in it.
  * The states are FIXED. The badge is a live readout; a logo cannot be,
    and it must not permanently say "you are out of budget" either, so the
    ramp stops at `low` rather than reaching `critical`.

Unlike tray_icon this is not called at runtime. It generates the committed
asset (assets/ai-smartbar.png), the same checked-in-generated-artifact
arrangement Version.swift has, because installers must not need pycairo:
macOS installs a Swift app and never pip-installs anything, and Windows
only gets pycairo after the venv step the icon is already needed by.

Regenerate with:  python3 -m smartbar.paint.app_icon assets/ai-smartbar.png
"""
from __future__ import annotations

import math

import cairo

from smartbar.core.model import RGB

# The reference canvas. Everything below is expressed against it and scaled,
# so render(size=...) stays exact at any size.
SIZE = 1024
INSET = 88.0                  # ground square margin (Apple's icon grid)
RIM_W = 3.0
PILL_H = 560.0
PILL_RATIO = 5 / 16           # MenuBarIcon.pillWidth / .pillHeight
GAP_RATIO = 1 / 8             # MenuBarIcon.gap / .pillHeight
SQUIRCLE_N = 5.0              # |u|^n + |v|^n = 1 — Apple's silhouette
SQUIRCLE_STEPS = 512

# Deep ink-teal rather than neutral graphite: the ground is tinted toward
# the brand green so the icon belongs to this product and not to every other
# dark-square app icon.
GROUND_TOP = (0.055, 0.114, 0.118)
GROUND_BOTTOM = (0.020, 0.047, 0.055)
TRACK = (1.0, 1.0, 1.0, 0.13)
# A hairline lip, so the silhouette still separates from a dark wallpaper.
RIM = (1.0, 1.0, 1.0, 0.09)

# (fraction used, RGB key) per pill, ascending — the product's ramp read as
# a chart. Stops at `low`; see the module docstring.
STATES = ((0.34, "green"), (0.58, "yellow"), (0.82, "low"))


def _squircle(ctx, x: float, y: float, side: float) -> None:
    """Apple's continuous-curvature icon silhouette.

    A plain rounded rect reads visibly boxier at 1024px, and cairo has no
    superellipse primitive, so this walks the curve as a polyline. At 512
    steps the segments are well under a pixel.
    """
    a = side / 2.0
    cx, cy = x + a, y + a
    for step in range(SQUIRCLE_STEPS + 1):
        angle = 2 * math.pi * step / SQUIRCLE_STEPS
        cos, sin = math.cos(angle), math.sin(angle)
        u = math.copysign(abs(cos) ** (2.0 / SQUIRCLE_N), cos)
        v = math.copysign(abs(sin) ** (2.0 / SQUIRCLE_N), sin)
        if step:
            ctx.line_to(cx + a * u, cy + a * v)
        else:
            ctx.move_to(cx + a * u, cy + a * v)
    ctx.close_path()


def _tube(ctx, x: float, y: float, w: float, h: float) -> None:
    """A capsule: semicircular caps, so the ends match the badge's pills."""
    r = w / 2.0
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def _level(ctx, x: float, y: float, w: float, h: float, fill: float) -> None:
    """Paint the bottom `fill` of a tube: rounded below, flat on top.

    Clipping the WHOLE tube to the fill band rather than building a
    part-round path is what keeps this correct for any fraction — a fill
    shorter than the cap radius comes out as the cap's own curve, with no
    special case to get wrong.
    """
    ctx.save()
    ctx.rectangle(x, y + h - fill, w, fill)
    ctx.clip()
    _tube(ctx, x, y, w, h)
    ctx.fill()
    ctx.restore()


def render(target, size: int = SIZE):
    """Draw the logo at `size` px square. Returns `target`.

    `target` is anything cairo's ImageSurface.write_to_png takes: a path, or
    a writable binary file object.
    """
    scale = size / SIZE
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    ctx.scale(scale, scale)

    _squircle(ctx, INSET, INSET, SIZE - INSET * 2)
    ground = cairo.LinearGradient(0, INSET, 0, SIZE - INSET)
    ground.add_color_stop_rgb(0, *GROUND_TOP)
    ground.add_color_stop_rgb(1, *GROUND_BOTTOM)
    ctx.set_source(ground)
    ctx.fill_preserve()
    ctx.set_source_rgba(*RIM)
    ctx.set_line_width(RIM_W)
    ctx.stroke()

    pill_w = PILL_H * PILL_RATIO
    gap = PILL_H * GAP_RATIO
    total = pill_w * len(STATES) + gap * (len(STATES) - 1)
    left = (SIZE - total) / 2.0
    top = (SIZE - PILL_H) / 2.0
    for index, (fraction, color) in enumerate(STATES):
        x = left + index * (pill_w + gap)
        _tube(ctx, x, top, pill_w, PILL_H)
        ctx.set_source_rgba(*TRACK)
        ctx.fill()
        ctx.set_source_rgb(*RGB[color])
        _level(ctx, x, top, pill_w, PILL_H, PILL_H * fraction)

    surface.write_to_png(target)
    return target


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "assets/ai-smartbar.png"
    # install/linux.sh passes a size, to fill an icon-theme directory with a
    # freshly drawn icon rather than a resample of the committed 1024 one.
    px = int(sys.argv[2]) if len(sys.argv) > 2 else SIZE
    render(out, px)
    print("wrote", out, f"({px}px)")
