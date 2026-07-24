"""Cairo painter for a popover Layout — imports cairo, never gi.

Staying GTK-free is deliberate: `ai-smartbar --preview-popover` can then
render the exact Linux panel to a PNG on any machine with pycairo, which is
how this UI gets reviewed without a Linux desktop in the loop.

Everything is painted rather than themed, so the panel looks the same on
XFCE, GNOME and KDE and matches the macOS popover instead of inheriting a
distro's widget style.
"""
from __future__ import annotations

import cairo

from smartbar.core import popover_theme as t

TAU = 6.283185307179586


def rounded_rect(ctx, x, y, w, h, r) -> None:
    r = max(0.0, min(r, h / 2, w / 2))
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -TAU / 4, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, TAU / 4)
    ctx.arc(x + r, y + h - r, r, TAU / 4, TAU / 2)
    ctx.arc(x + r, y + r, r, TAU / 2, TAU * 3 / 4)
    ctx.close_path()


def _set(ctx, rgba) -> None:
    ctx.set_source_rgba(*rgba)


def _select_font(ctx, label) -> None:
    ctx.select_font_face(
        "monospace" if label.mono else "sans-serif",
        cairo.FONT_SLANT_NORMAL,
        cairo.FONT_WEIGHT_BOLD if label.bold else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(label.size)


def _fit(ctx, text: str, max_width: float) -> str:
    """Middle-truncate to fit — mirrors SwiftUI .truncationMode(.middle)."""
    if max_width <= 0 or ctx.text_extents(text).x_advance <= max_width:
        return text
    for keep in range(len(text) - 1, 0, -1):
        head = (keep + 1) // 2
        tail = keep - head
        candidate = text[:head] + "…" + (text[len(text) - tail:] if tail else "")
        if ctx.text_extents(candidate).x_advance <= max_width:
            return candidate
    return "…"


def _draw_box(ctx, box) -> None:
    rounded_rect(ctx, box.x, box.y, box.w, box.h, box.radius)
    if box.fill is not None:
        _set(ctx, box.fill)
        if box.stroke is not None:
            ctx.fill_preserve()
        else:
            ctx.fill()
    if box.stroke is not None:
        _set(ctx, box.stroke)
        ctx.set_line_width(box.line_width)
        ctx.stroke()
    ctx.new_path()


def _draw_dot(ctx, dot) -> None:
    _set(ctx, dot.color)
    if dot.hollow:
        ctx.set_line_width(dot.line_width)
        ctx.arc(dot.cx, dot.cy, max(0.5, dot.r - dot.line_width / 2), 0, TAU)
        ctx.stroke()
    else:
        ctx.arc(dot.cx, dot.cy, dot.r, 0, TAU)
        ctx.fill()
    ctx.new_path()


def _wrap(ctx, text: str, max_width: float, max_lines: int) -> list:
    """Word-wrap to at most max_lines, truncating the last — .lineLimit(n)."""
    if max_lines <= 1 or max_width <= 0:
        return [_fit(ctx, text, max_width)]
    words, lines, current = text.split(), [], ""
    for index, word in enumerate(words):
        candidate = (current + " " + word).strip()
        if current and ctx.text_extents(candidate).x_advance > max_width:
            lines.append(current)
            if len(lines) == max_lines - 1:
                current = " ".join(words[index:])   # _fit truncates the rest
                break
            current = word
        else:
            current = candidate
    lines.append(_fit(ctx, current, max_width))
    return lines[:max_lines]


def _draw_label(ctx, label) -> None:
    _select_font(ctx, label)
    lines = _wrap(ctx, label.text, label.max_width, label.max_lines)
    ascent, descent, line_h = ctx.font_extents()[:3]
    _set(ctx, label.color)
    top = label.y - (len(lines) - 1) * line_h / 2
    for index, line in enumerate(lines):
        advance = ctx.text_extents(line).x_advance
        if label.anchor == "right":
            x = label.x - advance
        elif label.anchor == "center":
            x = label.x - advance / 2
        else:
            x = label.x
        ctx.move_to(x, top + index * line_h + (ascent - descent) / 2)
        ctx.show_text(line)
    ctx.new_path()


def _draw_glyph(ctx, glyph) -> None:
    """SF Symbols have no portable twin and no font can be assumed to carry
    "⟳"/"⏻", so the two icon buttons are stroked from paths."""
    radius = glyph.size / 2 * 0.72
    _set(ctx, glyph.color)
    ctx.set_line_width(1.5)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    if glyph.kind == "refresh":
        ctx.arc(glyph.cx, glyph.cy, radius, 0.45, TAU - 0.85)
        ctx.stroke()
        tipx = glyph.cx + radius
        head = glyph.size * 0.22
        ctx.move_to(tipx - head * 0.5, glyph.cy - head * 0.25)
        ctx.line_to(tipx + head * 0.45, glyph.cy - head * 0.15)
        ctx.line_to(tipx - head * 0.1, glyph.cy + head * 0.75)
        ctx.close_path()
        ctx.fill()
    else:  # "power": ring open at the top, plus the stem
        ctx.arc(glyph.cx, glyph.cy, radius, -TAU / 4 + 0.62,
                -TAU / 4 - 0.62 + TAU)
        ctx.stroke()
        ctx.move_to(glyph.cx, glyph.cy - radius * 1.25)
        ctx.line_to(glyph.cx, glyph.cy - radius * 0.15)
        ctx.stroke()
    ctx.new_path()


def draw(layout, ctx, background=t.WINDOW_BG, radius: float = 14.0) -> None:
    """Paint a whole panel; `radius` rounds the window itself."""
    ctx.save()
    ctx.set_operator(cairo.OPERATOR_SOURCE)
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.paint()
    ctx.restore()
    rounded_rect(ctx, 0, 0, layout.width, layout.height, radius)
    _set(ctx, background)
    ctx.fill()
    ctx.new_path()
    for shape in layout.shapes:
        if isinstance(shape, t.Box):
            _draw_box(ctx, shape)
        elif isinstance(shape, t.Dot):
            _draw_dot(ctx, shape)
        elif isinstance(shape, t.Label):
            _draw_label(ctx, shape)
        elif isinstance(shape, t.Glyph):
            _draw_glyph(ctx, shape)


def render_png(layout, path: str, scale: float = 2.0) -> str:
    """Rasterise to a PNG — the review path for a UI with no desktop here."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                 int(layout.width * scale),
                                 int(layout.height * scale))
    ctx = cairo.Context(surface)
    ctx.scale(scale, scale)
    draw(layout, ctx)
    surface.write_to_png(path)
    return path
