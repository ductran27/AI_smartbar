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


def _fit(ctx, text: str, max_width: float, mode: str = "tail") -> str:
    """Truncate to fit, mirroring SwiftUI's own default (.tail) or the one
    Text that opts into .truncationMode(.middle) — see Label.mode's comment
    in popover_theme.py for which is which."""
    if max_width <= 0 or ctx.text_extents(text).x_advance <= max_width:
        return text
    if mode == "middle":
        for keep in range(len(text) - 1, 0, -1):
            head = (keep + 1) // 2
            tail = keep - head
            candidate = (text[:head] + "…"
                        + (text[len(text) - tail:] if tail else ""))
            if ctx.text_extents(candidate).x_advance <= max_width:
                return candidate
        return "…"
    for keep in range(len(text) - 1, 0, -1):
        candidate = text[:keep] + "…"
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


def _area_gradient(area, alpha: float):
    """A vertical cairo gradient from the shape's shared ramp stops (offset 0
    at the bottom), scaled by `alpha` — full strength for the edge, a wash for
    the fill."""
    grad = cairo.LinearGradient(0, area.y + area.h, 0, area.y)  # bottom → top
    for offset, (red, green, blue, a) in area.stops:
        grad.add_color_stop_rgba(offset, red, green, blue, a * alpha)
    return grad


def _draw_area(ctx, area) -> None:
    """A value-over-time trend: a rounded track, the area under the sampled
    curve washed in the used-ramp gradient, and the curve's top edge stroked
    in that ramp at full strength. Samples map evenly left→right (newest at the
    right edge); a None BREAKS the run so a gap shows only the track, never a
    line smeared across minutes that were never sampled."""
    rounded_rect(ctx, area.x, area.y, area.w, area.h, area.radius)
    _set(ctx, area.track)
    ctx.fill()
    ctx.new_path()

    values = area.values
    count = len(values)
    if count == 0:
        return
    baseline = area.y + area.h

    def px(i):
        return area.x + (area.w * i / (count - 1) if count > 1 else area.w / 2)

    def py(value):
        return baseline - area.h * max(0.0, min(100.0, value)) / 100.0

    # Consecutive present samples; the curve is one run per unbroken stretch.
    runs, run = [], []
    for i, value in enumerate(values):
        if value is None:
            if run:
                runs.append(run)
                run = []
        else:
            run.append((i, value))
    if run:
        runs.append(run)

    fill = _area_gradient(area, t.SYS_AREA_FILL_ALPHA)
    edge = _area_gradient(area, 1.0)
    ctx.save()
    rounded_rect(ctx, area.x, area.y, area.w, area.h, area.radius)
    ctx.clip()                                   # keep fill/edge in the panel
    ctx.set_line_width(area.line_width)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    for run in runs:
        if len(run) == 1:                        # an isolated minute: a dot
            i, value = run[0]
            ctx.set_source(edge)
            ctx.arc(px(i), py(value), area.line_width, 0, TAU)
            ctx.fill()
            ctx.new_path()
            continue
        ctx.move_to(px(run[0][0]), baseline)
        for i, value in run:
            ctx.line_to(px(i), py(value))
        ctx.line_to(px(run[-1][0]), baseline)
        ctx.close_path()
        ctx.set_source(fill)
        ctx.fill()
        for order, (i, value) in enumerate(run):
            (ctx.line_to if order else ctx.move_to)(px(i), py(value))
        ctx.set_source(edge)
        ctx.stroke()
        ctx.new_path()
    ctx.restore()


def _split_long_word(ctx, word: str, max_width: float) -> list:
    """A single token wider than the line, character-split into fitting
    chunks — SwiftUI's Text does this, and without it an unbroken path in
    an error message drew straight past the panel edge."""
    chunks, current = [], ""
    for char in word:
        candidate = current + char
        if current and ctx.text_extents(candidate).x_advance > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [word]


def _wrap(ctx, text: str, max_width: float, max_lines: int,
          mode: str = "tail") -> list:
    """Word-wrap to at most max_lines, truncating the last — .lineLimit(n)."""
    if max_lines <= 1 or max_width <= 0:
        return [_fit(ctx, text, max_width, mode)]
    words = []
    for word in text.split():
        if ctx.text_extents(word).x_advance > max_width:
            words.extend(_split_long_word(ctx, word, max_width))
        else:
            words.append(word)
    lines, current = [], ""
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
    lines.append(_fit(ctx, current, max_width, mode))
    # Every emitted line fits — not only the last: a char-split chunk is
    # exact, but a rejoined tail can still be wide.
    return [_fit(ctx, line, max_width, mode) for line in lines[:max_lines]]


def _draw_label(ctx, label) -> None:
    _select_font(ctx, label)
    lines = _wrap(ctx, label.text, label.max_width, label.max_lines,
                  label.mode)
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


# Claude's mark is a starburst: rays of uneven length radiating from a
# solid centre, deliberately drawn off-grid rather than plotted evenly.
# CLAUDE_REACH is each ray's length as a fraction of the glyph radius and
# CLAUDE_SKEW nudges it off its even share of the circle — fixed tables
# rather than random numbers so the mark is identical every render and can
# be transcribed into ProviderMark.swift unchanged.
CLAUDE_REACH = (1.00, .86, .94, .82, .90, 1.00, .84, .92, .88, .97, .83)
CLAUDE_SKEW = (.00, .04, -.03, .02, -.05, .01, .03, -.02, .05, -.01, .02)


def _draw_claude(ctx, glyph) -> None:
    """A filled starburst — eleven tapered rays whose wide ends overlap
    into a solid hub. At TAB_MARK's 11pt only the silhouette survives, so
    the rays carry the mark and the hand-drawn wobble of the real one is
    reduced to CLAUDE_SKEW."""
    base = glyph.size * 0.075        # half-width where a ray meets the hub
    for i, reach in enumerate(CLAUDE_REACH):
        angle = TAU * i / len(CLAUDE_REACH) + TAU * CLAUDE_SKEW[i] - TAU / 4
        # The ray's base is a chord across the hub, so it is drawn on the
        # normal — the angle a quarter turn from the ray's own direction.
        nx, ny = cos(angle + TAU / 4), sin(angle + TAU / 4)
        tip = glyph.size * 0.5 * reach
        ctx.move_to(glyph.cx + base * nx, glyph.cy + base * ny)
        ctx.line_to(glyph.cx + tip * cos(angle), glyph.cy + tip * sin(angle))
        ctx.line_to(glyph.cx - base * nx, glyph.cy - base * ny)
        ctx.close_path()
        ctx.fill()
    ctx.arc(glyph.cx, glyph.cy, glyph.size * 0.14, 0, TAU)   # solid hub
    ctx.fill()


def _draw_openai(ctx, glyph) -> None:
    """OpenAI's blossom reduced to what survives at 11pt: a six-lobed
    rosette around a hexagonal core. The real mark's woven over-and-under
    is invisible at this size, so it is dropped rather than approximated —
    the lobe count, the roundness and the hexagon are what make it read."""
    peak = glyph.size * 0.46         # lobe tip
    valley = peak * 0.78             # the dip between two lobes
    core = glyph.size * 0.23

    def polar(r, angle):
        return (glyph.cx + r * cos(angle), glyph.cy + r * sin(angle))

    ctx.move_to(*polar(valley, 0.0))
    for k in range(6):
        here, nxt = TAU * k / 6, TAU * (k + 1) / 6
        # Both control points sit out at `peak`, splayed a half-lobe apart,
        # which rounds the lobe off instead of pulling it to a point.
        ctx.curve_to(*polar(peak, here + TAU / 24),
                     *polar(peak, nxt - TAU / 24), *polar(valley, nxt))
    ctx.close_path()
    ctx.stroke()
    # The core is rotated a half-lobe against the rosette, so its corners
    # point at the lobes rather than at the dips between them — aligned the
    # other way it reads as a six-pointed star.
    for k in range(6):
        point = polar(core, TAU * k / 6 + TAU / 12)
        ctx.line_to(*point) if k else ctx.move_to(*point)
    ctx.close_path()
    ctx.stroke()


def _draw_system(ctx, glyph) -> None:
    """A pulse line — the System tab's mark. A flat baseline with one spike,
    reading as an activity/heartbeat trace, which is what the tab shows: how
    hard the machine is working and what is running on it."""
    size = glyph.size
    left = glyph.cx - size * 0.46
    right = glyph.cx + size * 0.46
    mid = glyph.cy
    ctx.move_to(left, mid)
    ctx.line_to(glyph.cx - size * 0.20, mid)
    ctx.line_to(glyph.cx - size * 0.06, mid - size * 0.34)   # spike up
    ctx.line_to(glyph.cx + size * 0.10, mid + size * 0.30)   # dip down
    ctx.line_to(glyph.cx + size * 0.22, mid)
    ctx.line_to(right, mid)
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
    "claude": _draw_claude,
    "openai": _draw_openai,
    "system": _draw_system,
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


def draw(layout, ctx, background=None, radius: float = 14.0) -> None:
    """Paint a whole panel; `radius` rounds the window itself.

    The ground defaults to the layout's OWN `background` — the window colour
    of the Scheme that built it — so switching appearance is one argument to
    popover_layout.build() and the painter follows automatically. Passing
    `background` overrides it, which only a caller compositing the panel onto
    something other than a window should need.
    """
    if background is None:
        background = getattr(layout, "background", t.DARK.window_bg)
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
        elif isinstance(shape, t.Area):
            _draw_area(ctx, shape)
        elif isinstance(shape, t.Dot):
            _draw_dot(ctx, shape)
        elif isinstance(shape, t.Label):
            _draw_label(ctx, shape)
        elif isinstance(shape, t.Glyph):
            _draw_glyph(ctx, shape)


def render_png(layout, path: str, scale: float = 2.0, background=None) -> str:
    """Rasterise to a PNG — the review path for a UI with no desktop here."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                 int(layout.width * scale),
                                 int(layout.height * scale))
    ctx = cairo.Context(surface)
    ctx.scale(scale, scale)
    draw(layout, ctx, background=background)
    surface.write_to_png(path)
    return path
