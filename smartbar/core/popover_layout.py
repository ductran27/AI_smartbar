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
from smartbar.core.reset_countdown_format import prefers_24_hour_clock

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


def restore_origin(saved, workareas, size):
    """A user-dragged origin, if it would still leave the panel grabbable.

    `saved` is the (x, y) a drag ended on — this run or a previous one —
    and `workareas`/`size` are as in pin_origin. Monitors come and go
    between sessions, so a remembered spot is honoured only while a decent
    slice of the panel's top edge — the part you grab to drag it — still
    lands inside some current work area (at least 60px of width and the
    top 24px of height). Anything else returns None and the panel re-parks
    in its default corner rather than stranding itself off-screen.
    """
    if not saved:
        return None
    try:
        x, y = int(saved[0]), int(saved[1])
    except (TypeError, ValueError, IndexError):
        return None
    panel_w, _panel_h = size
    for ax, ay, aw, ah in (a for a in workareas if a and a[2] > 0 and a[3] > 0):
        grab_w = min(x + panel_w, ax + aw) - max(x, ax)
        grab_h = min(y + 24, ay + ah) - max(y, ay)
        if grab_w >= 60 and grab_h >= 24:
            return (x, y)
    return None


def updated_label(fetched_at: str, now=None, hour24=None) -> str:
    """"Updated 2:02 PM" (or "Updated 14:02") from an ISO stamp, or "".

    Mirrors the Swift header, which asks SwiftUI's own `.formatted(time:
    .shortened)` and so already follows the Mac's Region setting; this
    side has no such API, so `hour24=None` (the default) asks
    prefers_24_hour_clock() to guess instead. Pass True/False to force one
    convention regardless of the guess (mainly for tests, where the
    machine running them must not change the expected output).
    """
    stamp = parse_iso(fetched_at)
    if stamp is None:
        return ""
    local = stamp.astimezone()
    if hour24 is None:
        hour24 = prefers_24_hour_clock()
    if hour24:
        return "Updated " + local.strftime("%H:%M")
    return "Updated " + local.strftime("%I:%M %p").lstrip("0")


def _lines_for_width(text, width, *, size=t.SIZE_CAPTION, bold=False,
                     cap=t.STATE_MAX_LINES) -> int:
    """How many lines `text` needs at `width`, capped at `cap`.

    Estimated from text_width() rather than measured, so card/row height
    stays a pure function of numbers already in the layout; the renderer
    wraps to the same limit (popover_draw._wrap). Every caller passes its
    OWN available width — a full-width top-level slot and a card's inner
    width are not the same number, and hardcoding one for both is exactly
    how FINDING 1 happened.
    """
    if width <= 0 or t.text_width(text, size, bold=bold) <= width:
        return 1
    return cap


def state_lines(account) -> int:
    """How many lines an account's explanatory caption needs inside a card
    (SwiftUI wraps at 2 via .lineLimit(2)).

    A blocked account's line makes room for a warn glyph in front of it
    (see _card_body), narrower by the same COUNTDOWN_ICON/
    COUNTDOWN_ICON_GAP the glyph and its gap reserve there — otherwise this
    could estimate one line for text that, indented past the glyph, wraps
    to two, silently overflowing the card height reserved for it.
    """
    text = model.state_text(account) or "No usage data"
    room = t.WIDTH - 2 * t.PAD - 2 * t.CARD_PAD_H
    if model.switch_blocked(account):
        room -= t.COUNTDOWN_ICON + t.COUNTDOWN_ICON_GAP
    return _lines_for_width(text, room)


def _confirm_question(account):
    """(question text, wrapped line count) for the remove-confirm header.

    Shared by confirm_header_height() and _card() so the height RESERVED
    for the question and the height it is actually drawn with cannot drift
    apart — the exact trap FINDING 1 hit with the top-level state labels.
    """
    room = t.WIDTH - 2 * t.PAD - 2 * t.CARD_PAD_H
    text = f"Remove {model.account_label(account)}?"
    lines = _lines_for_width(text, room, size=t.SIZE_EMAIL, bold=True,
                             cap=t.CONFIRM_MAX_LINES)
    return text, lines


def confirm_header_height(account) -> float:
    """Height of the remove-confirm header: the full, never-middle-
    truncated question (wrapped if a long address needs it) stacked above
    its own Remove/Keep button row — see _card()'s confirm branch."""
    _, lines = _confirm_question(account)
    question_h = t.CARD_HEADER_H + (lines - 1) * t.CONFIRM_LINE_H
    return question_h + t.CARD_INNER_GAP + t.BUTTON_H


def card_height(account, confirm=False) -> float:
    """Height of one account card: metrics, explanatory line, or (with
    `confirm=True`) the remove-confirm header in place of the normal one.

    The confirm header is allowed to be taller than the normal one — a
    deliberate one-time growth on an explicit user action, so the full
    account identity can be shown un-truncated (see FINDING 2). The rest
    of the card (its body) never reflows because of it.
    """
    if account.metrics:
        body = (len(account.metrics) * t.ROW_H
                + (len(account.metrics) - 1) * t.ROW_GAP)
    else:
        body = t.STATE_ROW_H + (state_lines(account) - 1) * t.STATE_LINE_H
    header = confirm_header_height(account) if confirm else t.CARD_HEADER_H
    return t.CARD_PAD_V * 2 + header + t.CARD_INNER_GAP + body


def _button(shapes, hits, name, right, cy, text, *, enabled=True,
            accent=False, danger=False, hover="", tooltip=""):
    """Right-aligned pill button; returns its left edge."""
    bold = accent or danger
    width = t.text_width(text, t.SIZE_CAPTION, bold=bold) + t.BUTTON_PAD_H * 2
    x = right - width
    top = cy - t.BUTTON_H / 2
    if accent:
        fill = t.ACCENT_HOVER if hover == name else t.ACCENT
        border = None
    elif danger:
        fill = t.DANGER_HOVER if hover == name else t.DANGER
        border = None
    elif not enabled:
        fill, border = t.BUTTON_DISABLED, None
    else:
        fill = t.BUTTON_BG_HOVER if hover == name else t.BUTTON_BG
        border = t.BUTTON_BORDER
    shapes.append(t.Box(x, top, width, t.BUTTON_H, radius=t.BUTTON_H / 2,
                        fill=fill, stroke=border))
    shapes.append(t.Label(x + width / 2, cy, text, size=t.SIZE_CAPTION,
                          bold=bold, anchor="center",
                          color=t.TEXT if enabled else t.TEXT_TERTIARY))
    hits.append(t.Hit(name, x, top, width, t.BUTTON_H, enabled=enabled,
                      tooltip=tooltip))
    return x


def _bar(shapes, x, y, w, metric, now):
    """Track + proportional fill + pace caret for one metric, at a given
    x/y/width — factored out of _card_body so a second, narrower use (the
    Overview tab's compact per-account row, see _overview_card) draws the
    exact same caret maths rather than a second near-copy of it. Output is
    unchanged from before this was split out: same boxes, same order."""
    shapes.append(t.Box(x, y, w, t.BAR_H, radius=t.BAR_H / 2,
                        fill=t.BAR_TRACK))
    fraction = min(max(metric.pct, 0.0), 100.0) / 100.0
    if fraction > 0:
        shapes.append(t.Box(x, y, max(6.0, w * fraction), t.BAR_H,
                            radius=t.BAR_H / 2,
                            fill=t.status_rgba(model.color(metric.pct))))
    # See PACE's own comment (popover_theme.py) for why "how far through the
    # window" has to be a second mark rather than a second color on the fill.
    pace = model.pace_fraction(metric, now)
    if pace is not None:
        half = t.PACE_W / 2
        center = min(max(x + w * pace, x + half), x + w - half)
        shapes.append(t.Box(center - half, y, t.PACE_W, t.BAR_H,
                            radius=0.0, fill=t.PACE))


def _history_present(history) -> bool:
    """True once `history` holds at least one real reading.

    A fresh install (or an account never seen before) has no recorded day
    at all — every entry None — and that is the ONE case the strip card
    omits itself entirely for, rather than drawing thirty empty stubs (see
    _strip_card's own docstring for why a single None day, inside an
    otherwise-populated history, draws a stub instead of nothing).
    """
    return bool(history) and any(v is not None for v in history)


def strip_height(history) -> float:
    """Height of the 30-day usage-history strip card, or 0.0 when there is
    no history yet — folded into overview_height() so the panel's total
    height stays computable without building, the same relationship
    card_height() already has with _card()."""
    if not _history_present(history):
        return 0.0
    return t.CARD_PAD_V * 2 + t.OVERVIEW_LEAD_H + t.CARD_INNER_GAP + t.STRIP_H


def _strip_card(shapes, history, top) -> float:
    """The Overview tab's second card: one bar per day of the ACTIVE
    account's 7-day window, over the last 30 days (see
    core/usage_history.series, which `history` is the direct output of).

    A day with no recorded value draws a 1pt stub in BAR_TRACK rather than
    a bar of height 0 — "0% used" and "never measured" are different facts,
    and only the stub tells the truth about the second one. TODAY (the
    last entry) is drawn in TEXT chalk rather than the status ramp: it is
    still moving, so coloring it as though the day were over would claim a
    verdict on a reading that hasn't finished happening yet.
    """
    height = strip_height(history)
    left, right = t.PAD, t.WIDTH - t.PAD
    shapes.append(t.Box(left, top, right - left, height, radius=t.CARD_RADIUS,
                        fill=t.CARD_BG, stroke=t.CARD_BORDER, line_width=1.0))
    inner_l, inner_r = left + t.CARD_PAD_H, right - t.CARD_PAD_H

    head_cy = top + t.CARD_PAD_V + t.OVERVIEW_LEAD_H / 2
    shapes.append(t.Label(inner_l, head_cy, "Active account · 30 days",
                          size=t.SIZE_EMAIL, bold=True, color=t.TEXT))
    shapes.append(t.Label(inner_r, head_cy, "7-day window, % used",
                          size=t.SIZE_CAPTION, color=t.TEXT_TERTIARY,
                          anchor="right"))

    bars_top = top + t.CARD_PAD_V + t.OVERVIEW_LEAD_H + t.CARD_INNER_GAP
    baseline = bars_top + t.STRIP_H
    last = len(history) - 1
    for index, value in enumerate(history):
        x = inner_l + index * (t.STRIP_BAR_W + t.STRIP_GAP)
        if value is None:
            shapes.append(t.Box(x, baseline - 1.0, t.STRIP_BAR_W, 1.0,
                                radius=t.STRIP_BAR_W / 2, fill=t.BAR_TRACK))
            continue
        fraction = min(max(value, 0.0), 100.0) / 100.0
        bar_h = max(1.0, t.STRIP_H * fraction)
        color = t.TEXT if index == last else t.status_rgba(model.color(value))
        shapes.append(t.Box(x, baseline - bar_h, t.STRIP_BAR_W, bar_h,
                            radius=t.STRIP_BAR_W / 2, fill=color))
    return height


def _overview_row_key(account):
    """Sort key for _overview_card's rows: most headroom first, then
    accounts with no usable data last (see model.worst's None-without-data
    contract) — (0, pct) always sorts before (1, 0.0)."""
    metric = model.worst(account)
    return (0, metric.pct) if metric is not None else (1, 0.0)


def overview_height(snapshot, history=None) -> float:
    """Height of the Overview tab's whole body: the account-summary card,
    plus (stage 06) the 30-day usage-history strip card below it when
    `history` has anything to show. Kept alongside card_height() so the
    panel height stays computable without building — see build()'s
    `selected == "overview"` branch, which renders exactly this via
    _overview_card() and, conditionally, _strip_card().
    """
    accounts = list(snapshot.accounts) if snapshot is not None else []
    openai = list(getattr(snapshot, "openai", []) or []) if snapshot else []
    count = len(accounts) + len(openai)
    body = (count * t.OVERVIEW_ROW_H + max(count - 1, 0) * t.OVERVIEW_ROW_GAP
            if count else 0.0)
    height = t.CARD_PAD_V * 2 + t.OVERVIEW_LEAD_H + t.CARD_INNER_GAP + body
    if _history_present(history):
        height += t.CARD_GAP + strip_height(history)
    return height


def _overview_card(shapes, snapshot, top, now):
    """The Overview tab's one card: a lead line naming the account with the
    most headroom (model.best_switch — Claude-only, since a switch can only
    ever target a Claude slot), then one row per account, both providers
    merged into a single list ranked by how much headroom each has left.

    Rows are read-only in this stage — no switch/remove hits, on purpose
    (the stage-05 brief). A row's bar+caret is drawn by the SAME `_bar`
    helper _card_body's metric rows use, just narrower, so the pace maths
    can't drift between the two call sites.
    """
    accounts = list(snapshot.accounts)
    openai = list(getattr(snapshot, "openai", []) or [])
    rows = sorted(accounts + openai, key=_overview_row_key)

    height = overview_height(snapshot)
    left, right = t.PAD, t.WIDTH - t.PAD
    shapes.append(t.Box(left, top, right - left, height, radius=t.CARD_RADIUS,
                        fill=t.CARD_BG, stroke=t.CARD_BORDER, line_width=1.0))
    inner_l, inner_r = left + t.CARD_PAD_H, right - t.CARD_PAD_H

    lead_cy = top + t.CARD_PAD_V + t.OVERVIEW_LEAD_H / 2
    suggestion = model.best_switch(snapshot)
    if suggestion is not None:
        w = model.worst(suggestion)
        # "Best switch", NOT "most headroom": best_switch only ever
        # considers non-active CLAUDE slots, because switching is the one
        # thing this app does and it can only ever target a Claude slot.
        # The rows below are ranked across BOTH providers and include the
        # active account, so the two genuinely disagree — an OpenAI login
        # or the account you are already on can sit above this one. Naming
        # the lead line after what it actually computes is what keeps that
        # from reading as a sorting bug.
        lead = (f"Best switch: {model.account_address(suggestion)} — "
                f"{round(w.pct)}% used")
    else:
        # True whether there are simply no other Claude accounts to offer,
        # or every one of them is blocked/data-less — best_switch collapses
        # those cases on purpose (see its own docstring), and this is
        # honest about all of them without claiming to know which.
        lead = "No account to switch to"
    shapes.append(t.Label(inner_l, lead_cy, lead, size=t.SIZE_EMAIL, bold=True,
                          color=t.TEXT, max_width=inner_r - inner_l))

    body_top = top + t.CARD_PAD_V + t.OVERVIEW_LEAD_H + t.CARD_INNER_GAP
    pct_r = inner_r
    bar_r = inner_r - t.OVERVIEW_PCT_W - t.OVERVIEW_GAP
    bar_l = bar_r - t.OVERVIEW_BAR_W
    for index, account in enumerate(rows):
        row_top = body_top + index * (t.OVERVIEW_ROW_H + t.OVERVIEW_ROW_GAP)
        row_cy = row_top + t.OVERVIEW_ROW_H / 2
        provider = getattr(account, "provider", "claude") or "claude"
        mark_cx = inner_l + t.OVERVIEW_MARK_W / 2
        shapes.append(t.Glyph(provider, mark_cx, row_cy, t.OVERVIEW_MARK_W,
                              t.TEXT_TERTIARY))
        dot_cx = inner_l + t.OVERVIEW_MARK_W + t.OVERVIEW_GAP + t.DOT_R
        shapes.append(t.Dot(dot_cx, row_cy, t.DOT_R,
                            t.status_rgba(model.dot_color(account)),
                            hollow=model.dot_style(account) == "hollow"))
        address_x = dot_cx + t.DOT_R + t.OVERVIEW_GAP
        shapes.append(t.Label(address_x, row_cy, model.account_address(account),
                              size=t.SIZE_CAPTION, color=t.TEXT,
                              max_width=bar_l - t.OVERVIEW_GAP - address_x))
        metric = model.worst(account)
        if metric is None:
            blocked = model.switch_blocked(account)
            color = t.WARNING if blocked else t.TEXT_SECONDARY
            # The short state name, right-anchored across BOTH the bar and
            # percentage columns rather than squeezed into the bar's own
            # 56pt: there is no bar and no percentage to collide with, and
            # the full sentence truncated inside 56pt rendered as
            # "Re-lo…once" — see model.state_summary.
            shapes.append(t.Label(pct_r, row_cy,
                                  model.state_summary(account)
                                  or "No usage data",
                                  size=t.SIZE_CAPTION, color=color,
                                  anchor="right", max_width=pct_r - bar_l))
        else:
            _bar(shapes, bar_l, row_cy - t.BAR_H / 2, t.OVERVIEW_BAR_W,
                metric, now)
            color = t.TEXT_SPENT if metric.pct >= 100 else (1, 1, 1, 0.8)
            shapes.append(t.Label(pct_r, row_cy, f"{round(metric.pct)}%",
                                  size=t.SIZE_ROW_VALUE, mono=True,
                                  anchor="right", color=color))
    return height


def _card(shapes, hits, account, top, now, hover, confirm=""):
    """One account card: dot, email, ACTIVE chip or Make Active, metric rows.

    Hovering a non-active card reveals a small ✕ (hit "remove:<p>:<id>");
    `confirm` naming this card swaps the header for a two-row "Remove
    <full label>?" question over its own [Remove][Keep] button row
    (mirror of AccountCardView's hover/confirm states). That block is
    allowed to be TALLER than the normal one-line header — see
    confirm_header_height(): the full identity, un-truncated, is worth a
    one-time height change on an explicit user action; the card's body
    never reflows because of it.
    """
    provider = getattr(account, "provider", "claude") or "claude"
    ident = account.number if provider == "claude" else account.email
    pid = f"{provider}:{ident}"
    # A stale confirm token can name a card that turned active under it
    # (data refresh) — the active guard here keeps that card unremovable.
    is_confirm = confirm == pid and not account.active
    height = card_height(account, confirm=is_confirm)
    left, right = t.PAD, t.WIDTH - t.PAD
    # The whole card is a non-action hover region (see t.Hit) — appended
    # FIRST so every real button inside it wins hit-testing.
    hits.append(t.Hit(f"card:{pid}", left, top, right - left, height))
    shapes.append(t.Box(
        left, top, right - left, height, radius=t.CARD_RADIUS, fill=t.CARD_BG,
        stroke=t.CARD_BORDER, line_width=1.0))
    if account.active:
        # Drawn AFTER the card so it sits on top, and deliberately inset
        # into the card's own horizontal padding rather than shifting
        # inner_l — becoming active must never reflow a row.
        shapes.append(t.Box(left, top + t.RAIL_INSET, t.RAIL_W,
                            height - t.RAIL_INSET * 2, radius=t.RAIL_W / 2,
                            fill=t.RAIL))

    inner_l, inner_r = left + t.CARD_PAD_H, right - t.CARD_PAD_H

    if is_confirm:
        question, lines = _confirm_question(account)
        question_h = t.CARD_HEADER_H + (lines - 1) * t.CONFIRM_LINE_H
        shapes.append(t.Label(inner_l, top + t.CARD_PAD_V + question_h / 2,
                              question, size=t.SIZE_EMAIL, bold=True,
                              color=t.TEXT, max_width=inner_r - inner_l,
                              max_lines=lines))
        button_cy = (top + t.CARD_PAD_V + question_h + t.CARD_INNER_GAP
                     + t.BUTTON_H / 2)
        keep_l = _button(shapes, hits, "cancel-remove", inner_r, button_cy,
                         "Keep", hover=hover, tooltip="Keep this account")
        # Wording from AccountCardView.confirmHeader's Remove button's
        # .help(), which differs by provider: an OpenAI card has no
        # re-registration story.
        confirm_tip = (
            "Forget this card (labels and last numbers). Signing in with "
            "Codex brings it back" if provider == "openai" else
            f"Deletes claude-swap's stored credential backup for slot "
            f"{account.number}. Signing in as this account re-registers it")
        _button(shapes, hits, f"confirm-remove:{pid}", keep_l - 6, button_cy,
                "Remove", hover=hover, danger=True, tooltip=confirm_tip)
        header_h = question_h + t.CARD_INNER_GAP + t.BUTTON_H
        _card_body(shapes, account, top, now, inner_l, inner_r,
                   header_h=header_h)
        return height

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
    elif provider != "claude":
        # No switcher exists for a ChatGPT login: a remembered account is a
        # read-only card, so the header keeps the full width instead.
        control_l = inner_r
    else:
        # A dead stored credential must not be switchable: activating it
        # would restore a login Anthropic already rejected.
        blocked = model.switch_blocked(account)
        # Wording from AccountCardView.cardHeader's "Make Active" button's
        # .help().
        switch_tip = (
            f"Stored credential is dead — switching would log Claude Code "
            f"out. {model.state_text(account)}." if blocked else
            f"Switch Claude Code to {account.email}")
        control_l = _button(shapes, hits, f"switch:{account.number}", inner_r,
                            head_cy, "Make Active", hover=hover,
                            enabled=not blocked, tooltip=switch_tip)

    # The plan/device badge used to ride inside account_label as plain text;
    # now it gets its own quiet micro-chip so the address line stays just
    # the address. Sitting right of the address and left of the ACTIVE
    # chip / Make Active button, same neutral fill as a disabled control —
    # it is a fact about the account, not something to press.
    badge = model.account_badge(account)
    if badge:
        badge_w = t.text_width(badge, t.SIZE_CHIP) + 14
        badge_x = control_l - 6 - badge_w
        shapes.append(t.Box(badge_x, head_cy - t.CHIP_H / 2, badge_w,
                            t.CHIP_H, radius=t.CHIP_H / 2,
                            fill=t.BUTTON_DISABLED))
        shapes.append(t.Label(badge_x + badge_w / 2, head_cy, badge,
                              size=t.SIZE_CHIP, anchor="center",
                              color=t.TEXT_SECONDARY))
        control_l = badge_x

    # The ✕ exists only while the pointer is on this card (any of its hover
    # names) and never on the active card — the live login would just be
    # re-registered, so offering to remove it would be a lie. Its gutter is
    # reserved UNCONDITIONALLY on a removable card, though: reserving it
    # only while hovering (the old behaviour) let a long address wrap to
    # the full width, then re-truncate the instant the pointer arrived
    # (FINDING 4).
    removable = not account.active
    label_r = control_l - 8 - (t.REMOVE_HIT + 6 if removable else 0)
    on_card = hover in (f"card:{pid}", f"remove:{pid}",
                        f"switch:{account.number}")
    if removable and on_card:
        cx = control_l - 8 - t.REMOVE_HIT / 2
        shapes.append(t.Glyph("close", cx, head_cy, t.REMOVE_ICON,
                              t.TEXT if hover == f"remove:{pid}"
                              else t.TEXT_TERTIARY))
        # Wording from AccountCardView.cardHeader's remove (✕) button's
        # .help().
        hits.append(t.Hit(f"remove:{pid}", cx - t.REMOVE_HIT / 2,
                          head_cy - t.REMOVE_HIT / 2,
                          t.REMOVE_HIT, t.REMOVE_HIT,
                          tooltip=f"Remove {account.email} from AI smartbar"))

    shapes.append(t.Label(inner_l + t.DOT_R * 2 + 7, head_cy,
                          model.account_address(account),
                          size=t.SIZE_EMAIL, bold=True, color=t.TEXT,
                          max_width=label_r - (inner_l + t.DOT_R * 2 + 7)))

    _card_body(shapes, account, top, now, inner_l, inner_r)
    return height


def _card_body(shapes, account, top, now, inner_l, inner_r,
               header_h=t.CARD_HEADER_H):
    """Metric rows, or the explanatory line on a data-less card — shared by
    the normal header and the (possibly taller) remove-confirm header."""
    body_top = top + t.CARD_PAD_V + header_h + t.CARD_INNER_GAP
    if not account.metrics:
        blocked = model.switch_blocked(account)
        lines = state_lines(account)
        block_h = t.STATE_ROW_H + (lines - 1) * t.STATE_LINE_H
        color = t.WARNING if blocked else t.TEXT_SECONDARY
        text_l = inner_l
        if blocked:
            # A dead credential is the one data-less state that needs a
            # glyph as well as a color — WARNING alone reads as just this
            # account's usual shade, not "you need to act". Same
            # icon/gap the countdown's clock uses, reused rather than
            # giving this its own pair of constants for the same job.
            shapes.append(t.Glyph("warn", inner_l + t.COUNTDOWN_ICON / 2,
                                  body_top + block_h / 2, t.COUNTDOWN_ICON,
                                  color))
            text_l = inner_l + t.COUNTDOWN_ICON + t.COUNTDOWN_ICON_GAP
        shapes.append(t.Label(text_l, body_top + block_h / 2,
                              model.state_text(account) or "No usage data",
                              size=t.SIZE_CAPTION, color=color,
                              max_width=inner_r - text_l, max_lines=lines))
        return

    bar_l, bar_w = inner_l, inner_r - inner_l
    pct_r = inner_r - t.VALUE_COUNTDOWN_W
    for index, metric in enumerate(account.metrics):
        row_top = body_top + index * (t.ROW_H + t.ROW_GAP)
        label_cy = row_top + t.ROW_LABEL_H / 2
        shapes.append(t.Label(inner_l, label_cy, metric.label,
                              size=t.SIZE_ROW_LABEL, bold=True, color=t.TEXT,
                              max_width=t.LABEL_W))
        # Countdown recomputed from the absolute reset time so an old
        # snapshot still shows a live wait (mirror of Metric.liveCountdown).
        countdown = remaining_text(metric.resets_at, now) or metric.countdown
        color = t.TEXT_SPENT if metric.pct >= 100 else (1, 1, 1, 0.8)
        # Percentage and countdown are two INDEPENDENTLY right-anchored
        # labels, not one concatenated string right-anchored at inner_r —
        # a single string makes the percentage slide sideways every time
        # the countdown's length changes (e.g. "1h 0m" -> "59m"), which is
        # exactly what FINDING 3 measured (a 19pt swing on the "·").
        shapes.append(t.Label(pct_r, label_cy, f"{round(metric.pct)}%",
                              size=t.SIZE_ROW_VALUE, mono=True,
                              anchor="right", color=color))
        if countdown:
            # The leading " · " is gone: a clock glyph fills the space
            # that space used to reserve, so "· 🕐 2h 5m" never doubles up
            # the separator.
            countdown_text = f" {countdown}"
            shapes.append(t.Label(inner_r, label_cy, countdown_text,
                                  size=t.SIZE_ROW_VALUE, mono=True,
                                  anchor="right", color=color))
            # The clock sits immediately left of wherever the countdown
            # text actually STARTS, not the reserved column's nominal edge
            # (inner_r - VALUE_COUNTDOWN_W) — that column is sized for the
            # widest realistic countdown ("23h 59m"), so anchoring there
            # would leave a visible gap before a short one like "9m". The
            # layout has no font engine, so t.text_width is the same
            # estimate the countdown label itself is measured by.
            text_w = t.text_width(countdown_text, t.SIZE_ROW_VALUE, mono=True)
            clock_cx = inner_r - text_w - t.COUNTDOWN_ICON_GAP - t.COUNTDOWN_ICON / 2
            shapes.append(t.Glyph("clock", clock_cx, label_cy,
                                  t.COUNTDOWN_ICON, color))

        bar_top = row_top + t.ROW_LABEL_H + t.ROW_LABEL_GAP
        _bar(shapes, bar_l, bar_top, bar_w, metric, now)


def build(snapshot, *, version="", pending_version="", blocked_reason="",
          fetched_at="", stale=False, error="", now=None, hover="",
          provider="", confirm="", action_error="", refreshing=False,
          stale_reason="", history=None) -> t.Layout:
    """Positioned primitives + hit rects for the whole popover.

    `provider` selects the visible tab ("claude"/"openai"/"overview"); ""
    auto-resolves to Claude when it has accounts, else OpenAI — exactly the
    resolution this always did, UNCHANGED by Overview's arrival. Overview is
    opt-in only, reachable by an explicit "overview", never the auto-resolved
    default: a returning user must not find their panel rearranged out from
    under them by an update they didn't ask for.

    The tab row itself now appears whenever there is MORE THAN ONE account in
    total, across both providers — not, as before, only when both providers
    have at least one — with `tab:overview` always first, followed by
    whichever of Claude/OpenAI actually has accounts. A single-provider
    machine with several accounts therefore now gets a two-tab row (Overview
    + its one provider) where it previously got no tab row at all; a machine
    with exactly one account total still gets none, same as before.

    `confirm` names the card whose removal awaits confirmation
    ("<provider>:<id>", the suffix of its "remove:" hit); that card's
    header becomes the Remove/Keep question. A token naming a card that
    is not rendered — or that turned active — is simply ignored.

    `action_error` renders a dismissible one-line banner (hit
    "dismiss-error") under the header/tabs for the most recent switch or
    remove failure — mirrors PopoverView.body's `switchError ??
    removeError`. `refreshing` dims the ⟳ glyph and disables its hit so a
    second click cannot queue a second fetch while one is in flight
    (mirrors `.disabled(store.isRefreshing)`).

    `stale_reason` is why the last refresh failed, surfaced on hover of
    the "stale" marker (mirrors `store.lastError`); `blocked_reason` is
    likewise surfaced on hover of the footer's "update held" label
    (mirrors `.help("Update held back: …")`). Both were previously
    computed and then thrown away (FINDING 7).

    `history` (stage 06) is the active Claude account's own
    `usage_history.series(..., "7d")` result — 30 floats-or-None, oldest
    first, ending today — already computed by the caller. build() stays
    pure and does no file I/O of its own (see this module's own docstring),
    the same reason `now` is injected rather than read off the wall clock;
    the difference is `history` has no meaningful "read it yourself"
    default, so callers that never pass it simply render the Overview tab
    without its strip card, same as a fresh install with nothing recorded.
    """
    now = now or datetime.now(timezone.utc)
    shapes, hits = [], []
    right = t.WIDTH - t.PAD

    head_cy = t.PAD + t.HEADER_H / 2
    title = "AI smartbar"
    shapes.append(t.Label(t.PAD, head_cy, title, size=t.SIZE_TITLE,
                          bold=True, color=t.TEXT))
    updated = updated_label(fetched_at, now)
    # Both offsets below are DERIVED from the module's own text_width()
    # estimate, never hardcoded — popover_layout has no font engine, so an
    # estimate is the only width it can know, and deriving the "stale"
    # marker's position from that SAME estimate (rather than the
    # renderer's real, narrower measurement) keeps the two offsets
    # consistent with EACH OTHER even though neither matches cairo exactly.
    updated_x = (t.PAD + t.text_width(title, t.SIZE_TITLE, bold=True)
                 + t.HEADER_LABEL_GAP)
    if updated:
        shapes.append(t.Label(updated_x, head_cy, updated,
                              size=t.SIZE_CAPTION, color=t.TEXT_TERTIARY))
    if stale:
        stale_x = (updated_x + t.text_width(updated, t.SIZE_CAPTION)
                   + t.HEADER_LABEL_GAP)
        shapes.append(t.Label(stale_x, head_cy, "stale", size=t.SIZE_CAPTION,
                              color=t.WARNING))
        # Non-action hit (see t.Hit) purely so a front-end can show WHY on
        # hover — mirrors store.lastError on PopoverView.header's
        # stale-icon .help().
        stale_w = t.text_width("stale", t.SIZE_CAPTION)
        hits.append(t.Hit("stale", stale_x, head_cy - t.ICON_BUTTON_W / 2,
                          stale_w, t.ICON_BUTTON_W,
                          tooltip=(stale_reason
                                  or "last refresh failed; showing old data")))
    for name, offset in (("quit", 0.0), ("refresh", t.ICON_BUTTON_W)):
        cx = right - offset - t.ICON_BUTTON_W / 2
        # Refreshing dims the glyph AND disables its hit (mirrors
        # .disabled(store.isRefreshing) on PopoverView.header's refresh
        # button) so a second click while a fetch is in flight cannot
        # queue another.
        busy = refreshing and name == "refresh"
        color = (t.TEXT_TERTIARY if busy
                 else (1, 1, 1, 1) if hover == name else t.TEXT)
        shapes.append(t.Glyph(name, cx, head_cy, t.SIZE_ICON, color))
        tip = "Refresh now" if name == "refresh" else "Quit AI smartbar"
        hits.append(t.Hit(name, cx - t.ICON_BUTTON_W / 2,
                          head_cy - t.ICON_BUTTON_W / 2,
                          t.ICON_BUTTON_W, t.ICON_BUTTON_W,
                          enabled=not busy, tooltip=tip))

    cursor = t.PAD + t.HEADER_H + t.SECTION_GAP
    accounts = list(snapshot.accounts) if snapshot is not None else []
    openai = list(getattr(snapshot, "openai", []) or []) if snapshot else []
    selected = provider or ("openai" if openai and not accounts else "claude")
    if len(accounts) + len(openai) > 1:
        # The tab row is part of the header block, not a section of its
        # own, so it sits TAB_TOP_GAP under the title instead of a full
        # SECTION_GAP (mirrored by PopoverView's nested header VStack).
        cursor = t.PAD + t.HEADER_H + t.TAB_TOP_GAP
        x = t.PAD
        cy = cursor + t.TAB_H / 2
        # Overview is always offered once there is more than one account to
        # summarise; a provider only gets its own pill when it actually has
        # accounts to show under it — an empty provider tab would be a
        # button to a blank list.
        tabs = [("overview", "Overview")]
        if accounts:
            tabs.append(("claude", "Claude"))
        if openai:
            tabs.append(("openai", "OpenAI"))
        for name, text in tabs:
            current = name == selected
            label_w = t.text_width(text, t.SIZE_CAPTION, bold=current)
            width = t.TAB_MARK + t.TAB_MARK_GAP + label_w + t.BUTTON_PAD_H * 2
            hit_name = f"tab:{name}"
            # Faded / not-faded, not colored: the selected provider is full
            # strength and the other recedes (mirrored by tabButton in
            # PopoverView.swift).
            if current:
                fill, color = t.TAB_BG_SELECTED, t.TEXT
            elif hover == hit_name:
                fill, color = t.TAB_BG_HOVER, t.TEXT
            else:
                fill, color = t.TAB_BG, t.TEXT_TERTIARY
            shapes.append(t.Box(x, cy - t.BUTTON_H / 2, width, t.BUTTON_H,
                                radius=t.BUTTON_H / 2, fill=fill))
            # The mark sits BESIDE the label, never instead of it — a tab
            # has to stay readable to anyone who doesn't recognise the
            # provider's mark on sight, so it never becomes an icon-only
            # button. It takes the label's own color, so a faded tab reads
            # as faded mark-and-all rather than the mark competing with the
            # fade as a second signal.
            mark_cx = x + t.BUTTON_PAD_H + t.TAB_MARK / 2
            shapes.append(t.Glyph(name, mark_cx, cy, t.TAB_MARK, color))
            label_x = x + t.BUTTON_PAD_H + t.TAB_MARK + t.TAB_MARK_GAP
            shapes.append(t.Label(label_x, cy, text, size=t.SIZE_CAPTION,
                                  bold=current, anchor="left", color=color))
            hits.append(t.Hit(hit_name, x, cy - t.BUTTON_H / 2, width,
                              t.BUTTON_H, tooltip=f"Show {text} accounts"))
            x += width + t.TAB_GAP
        cursor += t.TAB_H + t.SECTION_GAP
    if action_error:
        # One error line for whichever card action failed most recently —
        # mirrors PopoverView.body's `switchError ?? removeError`, in
        # the same spot: under the header/tabs, above the account list.
        gutter = t.REMOVE_HIT + 6   # dismiss "x", same math as a card's ✕
        text_w = right - t.PAD - gutter
        lines = _lines_for_width(action_error, text_w)
        block_h = t.STATE_ROW_H + (lines - 1) * t.STATE_LINE_H
        err_cy = cursor + block_h / 2
        shapes.append(t.Label(t.PAD, err_cy, action_error,
                              size=t.SIZE_CAPTION, color=t.WARNING,
                              max_width=text_w, max_lines=lines))
        dx = right - t.REMOVE_HIT / 2
        shapes.append(t.Glyph("close", dx, err_cy, t.REMOVE_ICON,
                              t.TEXT if hover == "dismiss-error"
                              else t.TEXT_TERTIARY))
        hits.append(t.Hit("dismiss-error", dx - t.REMOVE_HIT / 2,
                          err_cy - t.REMOVE_HIT / 2, t.REMOVE_HIT,
                          t.REMOVE_HIT, tooltip="Dismiss"))
        cursor += block_h + t.CARD_GAP
    # No account cards on the Overview tab (it draws its own single
    # summary card below); `cards` empty there makes the ordinary per-card
    # loop that follows a no-op without a separate guard on it.
    cards = [] if selected == "overview" else (
        accounts if selected == "claude" else openai)
    if snapshot is None:
        text_ = error or "Loading usage…"
        lines = _lines_for_width(text_, right - t.PAD)
        block_h = t.STATE_ROW_H + (lines - 1) * t.STATE_LINE_H
        shapes.append(t.Label(t.PAD, cursor + block_h / 2, text_,
                              size=t.SIZE_CAPTION,
                              color=t.WARNING if error else t.TEXT_SECONDARY,
                              max_width=right - t.PAD, max_lines=lines))
        cursor += block_h + t.CARD_GAP
    elif selected == "claude" and snapshot.active_account is None:
        # cswap registration is a Claude story; the OpenAI tab never begs
        # the user to sign in to Claude Code. max_lines=2 (SwiftUI wraps
        # at 2 here too) plus the matching height growth is FINDING 1:
        # NO_ACCOUNTS needs ~355pt in a 308pt slot, and without both it
        # used to middle-truncate into nonsense.
        text_ = NO_ACCOUNTS if not accounts else UNREGISTERED
        lines = _lines_for_width(text_, right - t.PAD)
        block_h = t.STATE_ROW_H + (lines - 1) * t.STATE_LINE_H
        shapes.append(t.Label(t.PAD, cursor + block_h / 2, text_,
                              size=t.SIZE_CAPTION, color=t.TEXT_SECONDARY,
                              max_width=right - t.PAD, max_lines=lines))
        cursor += block_h + t.CARD_GAP
    elif selected == "overview":
        cursor += _overview_card(shapes, snapshot, cursor, now)
        if _history_present(history):
            cursor += t.CARD_GAP
            cursor += _strip_card(shapes, history, cursor)
    for account in cards:
        cursor += _card(shapes, hits, account, cursor, now, hover,
                        confirm) + t.CARD_GAP
    if cards:
        cursor -= t.CARD_GAP

    cursor += t.SECTION_GAP
    foot_cy = cursor + t.FOOTER_H / 2
    label = f"v{version}" if version else ""
    if blocked_reason:
        label = (label + " · update held").strip(" ·")
    if label:
        label_x = t.PAD
        if blocked_reason:
            # Mirrors PopoverView.footer's "pause.circle" SF Symbol in
            # front of "Update held" — same icon/gap the countdown's clock
            # and a blocked card's warn triangle use, reused rather than
            # a fourth pair of constants for the same "glyph beside a line
            # of text" job.
            shapes.append(t.Glyph("pause", t.PAD + t.COUNTDOWN_ICON / 2,
                                  foot_cy, t.COUNTDOWN_ICON, t.TEXT_TERTIARY))
            label_x = t.PAD + t.COUNTDOWN_ICON + t.COUNTDOWN_ICON_GAP
        shapes.append(t.Label(label_x, foot_cy, label, size=t.SIZE_CAPTION,
                              color=t.TEXT_TERTIARY))
        if blocked_reason:
            # Non-action hit: PopoverView.footer's "Update held" label's
            # .help() shows the actual reason on hover rather than a bare
            # "update held" label. Starts at t.PAD, not label_x, so the
            # glyph is inside the hoverable area too.
            label_w = t.text_width(label, t.SIZE_CAPTION)
            hits.append(t.Hit("update-held", t.PAD, foot_cy - t.FOOTER_H / 2,
                              label_x - t.PAD + label_w, t.FOOTER_H,
                              tooltip=f"Update held back: {blocked_reason}"))
    if pending_version:
        _button(shapes, hits, "update", right, foot_cy,
                f"Update to {pending_version}", accent=True, hover=hover,
                tooltip="Fetch, rebuild and restart AI smartbar")
    return t.Layout(t.WIDTH, cursor + t.FOOTER_H + t.PAD, shapes, hits)
