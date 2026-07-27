"""Cairo renderer for the tray badge — cairo only, never gi.

Split out of tray.py so the badge can be rendered (and eyeballed) without a
tray, a panel or a desktop session at all, the same way popover_draw can.
The design is the macOS MenuBarIcon, rendered from the same model.RGB
palette so the platforms cannot drift apart.
"""
from __future__ import annotations

import cairo

from smartbar.core.model import RGB
from smartbar.paint.popover_draw import rounded_rect

# "Update waiting" badge — brighter than either usage red so it cannot be
# mistaken for a usage alarm (mirror of MenuBarIcon.badgeColor).
BADGE_RGB = (1.0, 0.23, 0.19)

# The reference geometry in pixels, at scale=1.0: the 6x-scale bitmap this
# has always produced, which AppIndicator then scales down to the panel's
# height. Named rather than inline so `scale` has one place to multiply.
_PILL_W, _PILL_H, _GAP, _MARGIN, _RADIUS = 30, 96, 12, 12, 15
_DOT_D = 30       # update-waiting dot diameter
_INSET = 3        # hollow-pill stroke inset
_LINE_W = 6       # hollow-pill stroke width
_MIN_FILL = 12    # a non-zero fraction must never render invisible
_FONT = 48        # the "?" on a hollow badge


def render_pills(states, target, update_pending: bool = False,
                 scale: float = 1.0):
    """Twin-pill badge (same design as the macOS icon). Returns `target`.

    One vertical pill per (fraction_used, color) state: general limit
    first, then per-model buckets. Fill anchors to the bottom and RISES
    as tokens are spent (nearly full = nearly at the limit). Empty
    states -> hollow pills + "?".

    `update_pending` adds a dot in a WIDENED frame rather than over a pill:
    a pill's top is its most meaningful region, so covering it would trade
    usage information for a notification.

    `scale` multiplies every dimension. 1.0 is the historical 96px-tall
    bitmap, which AppIndicator scales down to the panel height for us — a
    tray that does NOT scale for you (Windows) asks for the size it wants
    instead, scale = height / 96, so 1/3 for a 32px icon. At 1.0 the output
    is byte-identical to before this parameter existed.

    `target` is anything cairo's ImageSurface.write_to_png takes: a path, or
    a writable binary file object. The file-object form is what lets a
    caller go surface -> BytesIO -> PIL.Image with no temp file, which is
    how a pystray tray wants its icon — the write-a-PNG-then-alternate-the-
    filename dance below exists only to work around an AppIndicator quirk.
    """
    def px(value: float) -> int:
        return max(1, round(value * scale))

    pill_w, pill_h = px(_PILL_W), px(_PILL_H)
    gap, margin, radius = px(_GAP), px(_MARGIN), px(_RADIUS)
    count = len(states) if states else 2
    dot_d = px(_DOT_D) if update_pending else 0
    width = margin * 2 + pill_w * count + gap * (count - 1) + dot_d
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, pill_h)
    ctx = cairo.Context(surface)
    for index in range(count):
        x = margin + index * (pill_w + gap)
        if not states:
            inset = px(_INSET)
            rounded_rect(ctx, x + inset, inset, pill_w - inset * 2,
                         pill_h - inset * 2, radius)
            ctx.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            ctx.set_line_width(px(_LINE_W))
            ctx.stroke()
            continue
        rounded_rect(ctx, x, 0, pill_w, pill_h, radius)
        ctx.set_source_rgba(0.5, 0.5, 0.5, 0.45)
        ctx.fill()
        fraction, color_name = states[index]
        if fraction > 0:
            fill_h = max(px(_MIN_FILL), round(pill_h * min(fraction, 1.0)))
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
        ctx.set_font_size(px(_FONT))
        extents = ctx.text_extents("?")
        ctx.move_to((width - dot_d - extents.width) / 2 - extents.x_bearing,
                    (pill_h - extents.height) / 2 - extents.y_bearing)
        ctx.show_text("?")
    surface.write_to_png(target)
    return target
