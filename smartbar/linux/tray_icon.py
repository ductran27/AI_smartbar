"""Cairo renderer for the tray badge — cairo only, never gi.

Split out of tray.py so the badge can be rendered (and eyeballed) without a
tray, a panel or a Linux session at all, the same way popover_draw can. The
design is the macOS MenuBarIcon at 6x scale, rendered from the same
model.RGB palette so the two platforms cannot drift apart.
"""
from __future__ import annotations

import cairo

from smartbar.core.model import RGB
from smartbar.linux.popover_draw import rounded_rect

# "Update waiting" badge — brighter than either usage red so it cannot be
# mistaken for a usage alarm (mirror of MenuBarIcon.badgeColor).
BADGE_RGB = (1.0, 0.23, 0.19)


def render_pills(states, path: str, update_pending: bool = False) -> str:
    """Twin-pill badge (same design as the macOS icon, 6x scale).

    One vertical pill per (fraction_used, color) state: general limit
    first, then per-model buckets. Fill anchors to the bottom and RISES
    as tokens are spent (nearly full = nearly at the limit). Empty
    states -> hollow pills + "?". The panel scales the PNG to its height.

    `update_pending` adds a dot in a WIDENED frame rather than over a pill:
    a pill's top is its most meaningful region, so covering it would trade
    usage information for a notification.
    """
    pill_w, pill_h, gap, margin, radius = 30, 96, 12, 12, 15
    count = len(states) if states else 2
    dot_d = 30 if update_pending else 0
    width = margin * 2 + pill_w * count + gap * (count - 1) + dot_d
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, pill_h)
    ctx = cairo.Context(surface)
    for index in range(count):
        x = margin + index * (pill_w + gap)
        if not states:
            rounded_rect(ctx, x + 3, 3, pill_w - 6, pill_h - 6, radius)
            ctx.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            ctx.set_line_width(6)
            ctx.stroke()
            continue
        rounded_rect(ctx, x, 0, pill_w, pill_h, radius)
        ctx.set_source_rgba(0.5, 0.5, 0.5, 0.45)
        ctx.fill()
        fraction, color_name = states[index]
        if fraction > 0:
            fill_h = max(12, round(pill_h * min(fraction, 1.0)))
            rounded_rect(ctx, x, pill_h - fill_h, pill_w, fill_h, radius)
            ctx.set_source_rgb(*RGB[color_name])
            ctx.fill()
    if update_pending:
        # cairo's origin is top-left, so this lands in the top-right corner.
        ctx.set_source_rgb(*BADGE_RGB)
        ctx.arc(width - dot_d / 2.0, dot_d / 2.0, dot_d / 2.0, 0, 6.283185)
        ctx.fill()
    if not states:
        ctx.set_source_rgba(1, 1, 1, 0.85)
        ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                             cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(48)
        extents = ctx.text_extents("?")
        ctx.move_to((width - dot_d - extents.width) / 2 - extents.x_bearing,
                    (pill_h - extents.height) / 2 - extents.y_bearing)
        ctx.show_text("?")
    surface.write_to_png(path)
    return path
