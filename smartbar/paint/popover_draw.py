"""Cairo painter for a popover Layout — imports cairo, never gi.

Staying toolkit-free is deliberate, and is what lets every platform share
one painter: `ai-smartbar --preview-popover` renders the exact panel each of
them shows to a PNG on any machine with pycairo, which is how this UI gets
reviewed without a desktop session in the loop.

Everything is painted rather than themed, so the panel looks the same on
XFCE, GNOME, KDE and Windows, and matches the macOS popover instead of
inheriting whatever widget style the host happens to supply.
"""
from __future__ import annotations

from math import cos, sin

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


def _draw_refresh(ctx, glyph) -> None:
    radius = glyph.size / 2 * 0.72
    ctx.arc(glyph.cx, glyph.cy, radius, 0.45, TAU - 0.85)
    ctx.stroke()
    tipx = glyph.cx + radius
    head = glyph.size * 0.22
    ctx.move_to(tipx - head * 0.5, glyph.cy - head * 0.25)
    ctx.line_to(tipx + head * 0.45, glyph.cy - head * 0.15)
    ctx.line_to(tipx - head * 0.1, glyph.cy + head * 0.75)
    ctx.close_path()
    ctx.fill()


def _draw_close(ctx, glyph) -> None:  # the per-card remove ✕
    radius = glyph.size / 2 * 0.72
    arm = radius * 0.9
    ctx.move_to(glyph.cx - arm, glyph.cy - arm)
    ctx.line_to(glyph.cx + arm, glyph.cy + arm)
    ctx.move_to(glyph.cx + arm, glyph.cy - arm)
    ctx.line_to(glyph.cx - arm, glyph.cy + arm)
    ctx.stroke()


def _draw_power(ctx, glyph) -> None:  # ring open at the top, plus the stem
    radius = glyph.size / 2 * 0.72
    ctx.arc(glyph.cx, glyph.cy, radius, -TAU / 4 + 0.62, -TAU / 4 - 0.62 + TAU)
    ctx.stroke()
    ctx.move_to(glyph.cx, glyph.cy - radius * 1.25)
    ctx.line_to(glyph.cx, glyph.cy - radius * 0.15)
    ctx.stroke()


def _draw_overview(ctx, glyph) -> None:
    """Four rounded squares in a 2x2 grid, filled — an "everything at once"
    mark, solid rather than stroked so it still reads at TAB_MARK's size."""
    cell = glyph.size * 0.38
    gap = glyph.size * 0.12
    corner = cell * 0.3
    for dx in (-1, 1):
        for dy in (-1, 1):
            x = glyph.cx + dx * (cell + gap) / 2 - cell / 2
            y = glyph.cy + dy * (cell + gap) / 2 - cell / 2
            rounded_rect(ctx, x, y, cell, cell, corner)
            ctx.fill()


def _draw_claude(ctx, glyph) -> None:
    """A simplified wordless "A": two strokes from an apex plus a
    crossbar. Deliberately not a reproduction of Anthropic's mark — just a
    short shape recognisable enough to tell the Claude tab apart at a
    glance, the way the label beside it already does in words."""
    half = glyph.size * 0.34
    top = glyph.cy - glyph.size * 0.38
    bottom = glyph.cy + glyph.size * 0.38
    ctx.move_to(glyph.cx, top)
    ctx.line_to(glyph.cx - half, bottom)
    ctx.move_to(glyph.cx, top)
    ctx.line_to(glyph.cx + half, bottom)
    bar_y = glyph.cy + glyph.size * 0.12
    bar_half = half * 0.55
    ctx.move_to(glyph.cx - bar_half, bar_y)
    ctx.line_to(glyph.cx + bar_half, bar_y)
    ctx.stroke()


def _draw_openai(ctx, glyph) -> None:
    """A hollow hexagon with two internal spokes, stroked — a short,
    generic "knot" for the OpenAI tab, not a reproduction of their logo."""
    r = glyph.size * 0.42
    points = []
    for i in range(6):
        angle = TAU / 6 * i - TAU / 4
        points.append((glyph.cx + r * cos(angle), glyph.cy + r * sin(angle)))
    ctx.move_to(*points[0])
    for x, y in points[1:]:
        ctx.line_to(x, y)
    ctx.close_path()
    ctx.stroke()
    ctx.move_to(glyph.cx, glyph.cy)
    ctx.line_to(*points[0])
    ctx.move_to(glyph.cx, glyph.cy)
    ctx.line_to(*points[3])
    ctx.stroke()


def _draw_clock(ctx, glyph) -> None:
    """A stroked circle with hour and minute hands — sits immediately left
    of a countdown, marking "this number is a duration"."""
    r = glyph.size * 0.42
    ctx.arc(glyph.cx, glyph.cy, r, 0, TAU)
    ctx.stroke()
    ctx.move_to(glyph.cx, glyph.cy)
    ctx.line_to(glyph.cx, glyph.cy - r * 0.55)          # hour hand
    ctx.move_to(glyph.cx, glyph.cy)
    ctx.line_to(glyph.cx + r * 0.6, glyph.cy - r * 0.1)  # minute hand
    ctx.stroke()


def _draw_pause(ctx, glyph) -> None:
    """Two rounded vertical bars, filled — prefixes the footer's "update
    held" label."""
    bar_w = glyph.size * 0.22
    bar_h = glyph.size * 0.78
    gap = glyph.size * 0.18
    top = glyph.cy - bar_h / 2
    for dx in (-1, 1):
        x = glyph.cx + dx * (gap / 2 + bar_w / 2) - bar_w / 2
        rounded_rect(ctx, x, top, bar_w, bar_h, bar_w / 2)
        ctx.fill()


def _draw_warn(ctx, glyph) -> None:
    """A stroked triangle with a vertical stem and a filled dot — prefixes
    a blocked account's explanatory line."""
    r = glyph.size * 0.46
    top = (glyph.cx, glyph.cy - r)
    left = (glyph.cx - r * 0.92, glyph.cy + r * 0.75)
    right = (glyph.cx + r * 0.92, glyph.cy + r * 0.75)
    ctx.move_to(*top)
    ctx.line_to(*right)
    ctx.line_to(*left)
    ctx.close_path()
    ctx.stroke()
    ctx.move_to(glyph.cx, glyph.cy - r * 0.35)
    ctx.line_to(glyph.cx, glyph.cy + r * 0.2)
    ctx.stroke()
    ctx.arc(glyph.cx, glyph.cy + r * 0.5, glyph.size * 0.05, 0, TAU)
    ctx.fill()


# kind -> drawing function. A dict instead of a growing if/elif chain, so a
# tenth kind is one more entry rather than one more branch to read past.
_GLYPH_DRAWERS = {
    "refresh": _draw_refresh,
    "close": _draw_close,
    "power": _draw_power,
    "quit": _draw_power,   # the header's Quit button; same shape as
                           # "power" under its own name (see t.Glyph).
    "overview": _draw_overview,
    "claude": _draw_claude,
    "openai": _draw_openai,
    "clock": _draw_clock,
    "pause": _draw_pause,
    "warn": _draw_warn,
}


def _draw_glyph(ctx, glyph) -> None:
    """SF Symbols have no portable twin and no font can be assumed to carry
    "⟳"/"⏻", so every symbol on the panel is drawn from a path rather
    than typeset. Handles every kind t.Glyph's docstring lists; a kind not
    in _GLYPH_DRAWERS falls through to "power" rather than raising, so a
    typo never crashes the panel — it just paints the wrong icon, which is
    exactly the failure tests/test_popover_draw.py's dispatch-coverage
    test exists to catch before it ships.
    """
    _set(ctx, glyph.color)
    ctx.set_line_width(1.5)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    _GLYPH_DRAWERS.get(glyph.kind, _draw_power)(ctx, glyph)
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
