"""Pure layout for the popover — no cairo, no GTK, no clock of its own.

Turns a snapshot into positioned primitives plus named hit rectangles, so
the Linux panel is the same UI as the macOS one rather than a lookalike:
identical card structure, identical bar geometry, identical wording. Being
a pure function also makes the part most likely to rot — where a click
lands — unit-testable (tests/test_popover_layout.py).

Mirrors PopoverView.swift + AccountCardView.swift + MetricBarRow.swift.
"""
from __future__ import annotations

from datetime import datetime, timezone

from smartbar.core import model
from smartbar.core import popover_theme as t
from smartbar.core.reset_countdown_format import parse_iso, remaining_text

NO_ACCOUNTS = ("No accounts yet — sign in to Claude Code and it will be "
               "registered automatically")
UNREGISTERED = "Current login isn't registered — adding it automatically"


def pin_origin(workareas, size, margin):
    """Top-right corner for a pinned panel, given every monitor's work area.

    `workareas` is a list of (x, y, width, height); `size` is (width, height).

    Deliberately NOT the "primary" monitor. A headless dummy plug — routine on
    desktops that must keep a GPU output alive — often claims primary while
    being smaller than, and stacked on top of, the display the user actually
    looks at; anchoring to it parks a permanent readout in the middle of the
    visible screen. The roomiest work area is the real one. `y` then clears
    the tallest top panel across all monitors, because a monitor that hosts no
    panel reports no strut for one that overlaps it.

    Returns (x, y), or None when there are no work areas to place against.
    """
    areas = [a for a in workareas if a and a[2] > 0 and a[3] > 0]
    if not areas:
        return None
    x0, _y0, width, _height = max(areas, key=lambda a: a[2] * a[3])
    top = max(a[1] for a in areas)
    panel_w, _panel_h = size
    return (x0 + width - panel_w - margin, top + margin)


def updated_label(fetched_at: str, now=None) -> str:
    """"Updated 2:02 PM" from an ISO stamp, or "" — mirrors the Swift header."""
    stamp = parse_iso(fetched_at)
    if stamp is None:
        return ""
    local = stamp.astimezone()
    return "Updated " + local.strftime("%I:%M %p").lstrip("0")


def state_lines(account) -> int:
    """How many lines the explanatory text needs (SwiftUI wraps at 2).

    Estimated from the width factors rather than measured, so card height
    stays a pure function; the renderer wraps to the same limit.
    """
    text = model.state_text(account) or "No usage data"
    room = t.WIDTH - 2 * t.PAD - 2 * t.CARD_PAD_H
    wide = t.text_width(text, t.SIZE_CAPTION) > room
    return t.STATE_MAX_LINES if wide else 1


def card_height(account) -> float:
    """Height of one account card, metrics or explanatory line."""
    if account.metrics:
        body = (len(account.metrics) * t.ROW_H
                + (len(account.metrics) - 1) * t.ROW_GAP)
    else:
        body = t.STATE_ROW_H + (state_lines(account) - 1) * t.STATE_LINE_H
    return t.CARD_PAD_V * 2 + t.CARD_HEADER_H + t.CARD_INNER_GAP + body


def _button(shapes, hits, name, right, cy, text, *, enabled=True,
            accent=False, hover=""):
    """Right-aligned pill button; returns its left edge."""
    width = t.text_width(text, t.SIZE_CAPTION, bold=accent) + t.BUTTON_PAD_H * 2
    x = right - width
    top = cy - t.BUTTON_H / 2
    if accent:
        fill = t.ACCENT_HOVER if hover == name else t.ACCENT
        border = None
    elif not enabled:
        fill, border = t.BUTTON_DISABLED, None
    else:
        fill = t.BUTTON_BG_HOVER if hover == name else t.BUTTON_BG
        border = t.BUTTON_BORDER
    shapes.append(t.Box(x, top, width, t.BUTTON_H, radius=t.BUTTON_H / 2,
                        fill=fill, stroke=border))
    shapes.append(t.Label(x + width / 2, cy, text, size=t.SIZE_CAPTION,
                          bold=accent, anchor="center",
                          color=t.TEXT if enabled else t.TEXT_TERTIARY))
    hits.append(t.Hit(name, x, top, width, t.BUTTON_H, enabled=enabled))
    return x


def _card(shapes, hits, account, top, now, hover):
    """One account card: dot, email, ACTIVE chip or Make Active, metric rows."""
    height = card_height(account)
    left, right = t.PAD, t.WIDTH - t.PAD
    shapes.append(t.Box(
        left, top, right - left, height, radius=t.CARD_RADIUS, fill=t.CARD_BG,
        stroke=t.CARD_BORDER_ACTIVE if account.active else t.CARD_BORDER,
        line_width=1.5 if account.active else 1.0))

    inner_l, inner_r = left + t.CARD_PAD_H, right - t.CARD_PAD_H
    head_cy = top + t.CARD_PAD_V + t.CARD_HEADER_H / 2
    dot_color = t.status_rgba(model.dot_color(account))
    shapes.append(t.Dot(inner_l + t.DOT_R, head_cy, t.DOT_R, dot_color,
                        hollow=model.dot_style(account) == "hollow"))

    if account.active:
        chip_w = t.text_width("ACTIVE", t.SIZE_CHIP, bold=True) + 14
        chip_x = inner_r - chip_w
        shapes.append(t.Box(chip_x, head_cy - t.CHIP_H / 2, chip_w, t.CHIP_H,
                            radius=t.CHIP_H / 2,
                            fill=t.status_rgba("green")))
        shapes.append(t.Label(chip_x + chip_w / 2, head_cy, "ACTIVE",
                              size=t.SIZE_CHIP, bold=True, anchor="center",
                              color=(1, 1, 1, 1)))
        control_l = chip_x
    else:
        # A dead stored credential must not be switchable: activating it
        # would restore a login Anthropic already rejected.
        control_l = _button(shapes, hits, f"switch:{account.number}", inner_r,
                            head_cy, "Make Active", hover=hover,
                            enabled=not model.switch_blocked(account))

    shapes.append(t.Label(inner_l + t.DOT_R * 2 + 7, head_cy, account.email,
                          size=t.SIZE_EMAIL, bold=True, color=t.TEXT,
                          max_width=control_l - 8 - (inner_l + t.DOT_R * 2 + 7)))

    body_top = top + t.CARD_PAD_V + t.CARD_HEADER_H + t.CARD_INNER_GAP
    if not account.metrics:
        blocked = model.switch_blocked(account)
        lines = state_lines(account)
        block_h = t.STATE_ROW_H + (lines - 1) * t.STATE_LINE_H
        shapes.append(t.Label(inner_l, body_top + block_h / 2,
                              model.state_text(account) or "No usage data",
                              size=t.SIZE_CAPTION,
                              color=t.WARNING if blocked else t.TEXT_SECONDARY,
                              max_width=inner_r - inner_l, max_lines=lines))
        return height

    bar_l = inner_l + t.LABEL_W + t.BAR_GAP
    bar_w = inner_r - t.VALUE_W - t.BAR_GAP - bar_l
    for index, metric in enumerate(account.metrics):
        cy = body_top + index * (t.ROW_H + t.ROW_GAP) + t.ROW_H / 2
        shapes.append(t.Label(inner_l, cy, metric.label, size=t.SIZE_ROW_LABEL,
                              bold=True, color=t.TEXT, max_width=t.LABEL_W))
        shapes.append(t.Box(bar_l, cy - t.BAR_H / 2, bar_w, t.BAR_H,
                            radius=t.BAR_H / 2, fill=t.BAR_TRACK))
        fraction = min(max(metric.pct, 0.0), 100.0) / 100.0
        if fraction > 0:
            shapes.append(t.Box(bar_l, cy - t.BAR_H / 2,
                                max(6.0, bar_w * fraction), t.BAR_H,
                                radius=t.BAR_H / 2,
                                fill=t.status_rgba(model.color(metric.pct))))
        # Countdown recomputed from the absolute reset time so an old
        # snapshot still shows a live wait (mirror of Metric.liveCountdown).
        countdown = remaining_text(metric.resets_at, now) or metric.countdown
        value = f"{round(metric.pct)}%" + (f" · {countdown}" if countdown else "")
        shapes.append(t.Label(inner_r, cy, value, size=t.SIZE_ROW_VALUE,
                              mono=True, anchor="right",
                              color=t.TEXT_SPENT if metric.pct >= 100
                              else (1, 1, 1, 0.8)))
    return height


def build(snapshot, *, version="", pending_version="", blocked_reason="",
          fetched_at="", stale=False, error="", now=None, hover="") -> t.Layout:
    """Positioned primitives + hit rects for the whole popover."""
    now = now or datetime.now(timezone.utc)
    shapes, hits = [], []
    right = t.WIDTH - t.PAD

    head_cy = t.PAD + t.HEADER_H / 2
    shapes.append(t.Label(t.PAD, head_cy, "AI smartbar", size=t.SIZE_TITLE,
                          bold=True, color=t.TEXT))
    updated = updated_label(fetched_at, now)
    if updated:
        shapes.append(t.Label(t.PAD + 82, head_cy, updated,
                              size=t.SIZE_CAPTION, color=t.TEXT_TERTIARY))
    if stale:
        shapes.append(t.Label(t.PAD + 82 + t.text_width(updated, t.SIZE_CAPTION)
                              + 6, head_cy, "stale", size=t.SIZE_CAPTION,
                              color=t.WARNING))
    for name, offset in (("quit", 0.0), ("refresh", t.ICON_BUTTON_W)):
        cx = right - offset - t.ICON_BUTTON_W / 2
        shapes.append(t.Glyph(name, cx, head_cy, t.SIZE_ICON,
                              t.TEXT if hover != name else (1, 1, 1, 1)))
        hits.append(t.Hit(name, cx - t.ICON_BUTTON_W / 2,
                          head_cy - t.ICON_BUTTON_W / 2,
                          t.ICON_BUTTON_W, t.ICON_BUTTON_W))

    cursor = t.PAD + t.HEADER_H + t.SECTION_GAP
    accounts = list(snapshot.accounts) if snapshot is not None else []
    if snapshot is None:
        shapes.append(t.Label(t.PAD, cursor + t.STATE_ROW_H / 2,
                              error or "Loading usage…", size=t.SIZE_CAPTION,
                              color=t.WARNING if error else t.TEXT_SECONDARY,
                              max_width=right - t.PAD))
        cursor += t.STATE_ROW_H + t.CARD_GAP
    elif snapshot.active_account is None:
        shapes.append(t.Label(t.PAD, cursor + t.STATE_ROW_H / 2,
                              NO_ACCOUNTS if not accounts else UNREGISTERED,
                              size=t.SIZE_CAPTION, color=t.TEXT_SECONDARY,
                              max_width=right - t.PAD))
        cursor += t.STATE_ROW_H + t.CARD_GAP
    for account in accounts:
        cursor += _card(shapes, hits, account, cursor, now, hover) + t.CARD_GAP
    if accounts:
        cursor -= t.CARD_GAP

    cursor += t.SECTION_GAP
    foot_cy = cursor + t.FOOTER_H / 2
    label = f"v{version}" if version else ""
    if blocked_reason:
        label = (label + " · update held").strip(" ·")
    if label:
        shapes.append(t.Label(t.PAD, foot_cy, label, size=t.SIZE_CAPTION,
                              color=t.TEXT_TERTIARY))
    if pending_version:
        _button(shapes, hits, "update", right, foot_cy,
                f"Update to {pending_version}", accent=True, hover=hover)
    return t.Layout(t.WIDTH, cursor + t.FOOTER_H + t.PAD, shapes, hits)
