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


def _button(shapes, hits, s, name, right, cy, text, *, enabled=True,
            accent=False, danger=False, hover="", tooltip=""):
    """Right-aligned pill button; returns its left edge."""
    bold = accent or danger
    width = t.text_width(text, t.SIZE_CAPTION, bold=bold) + t.BUTTON_PAD_H * 2
    x = right - width
    top = cy - t.BUTTON_H / 2
    # A filled button takes the scheme's accent_text, NOT its ink: `text` is
    # near-black on light, which on a saturated blue or red fill is the one
    # combination that reads as a rendering bug rather than a style.
    label_color = s.text if enabled else s.text_tertiary
    if accent:
        fill = s.accent_hover if hover == name else s.accent
        border = None
        label_color = s.accent_text
    elif danger:
        fill = s.danger_hover if hover == name else s.danger
        border = None
        label_color = s.accent_text
    elif not enabled:
        fill, border = s.button_disabled, None
    else:
        fill = s.button_bg_hover if hover == name else s.button_bg
        border = s.button_border
    shapes.append(t.Box(x, top, width, t.BUTTON_H, radius=t.BUTTON_H / 2,
                        fill=fill, stroke=border))
    shapes.append(t.Label(x + width / 2, cy, text, size=t.SIZE_CAPTION,
                          bold=bold, anchor="center", color=label_color))
    hits.append(t.Hit(name, x, top, width, t.BUTTON_H, enabled=enabled,
                      tooltip=tooltip))
    return x


def _bar(shapes, s, x, y, w, metric, now):
    """Track + proportional fill + pace caret for one metric, at a given
    x/y/width. Factored out of _card_body and kept that way: the caret
    maths is worth having in exactly one place."""
    shapes.append(t.Box(x, y, w, t.BAR_H, radius=t.BAR_H / 2,
                        fill=s.bar_track))
    fraction = min(max(metric.pct, 0.0), 100.0) / 100.0
    if fraction > 0:
        # The floor is the bar's own height, so the smallest possible fill is
        # a dot the track's width rather than a sliver clipped by its own
        # corner radius. Derived from BAR_H rather than hardcoded, so it
        # cannot fall out of step with the track if the bar is resized.
        shapes.append(t.Box(x, y, max(t.BAR_H, w * fraction), t.BAR_H,
                            radius=t.BAR_H / 2,
                            fill=s.status_rgba(model.color(metric.pct))))
    # See Scheme.pace's own comment (popover_theme.py) for why "how far
    # through the window" has to be a second MARK rather than a second
    # colour on the fill.
    pace = model.pace_fraction(metric, now)
    if pace is not None:
        half = t.PACE_W / 2
        center = min(max(x + w * pace, x + half), x + w - half)
        shapes.append(t.Box(center - half, y, t.PACE_W, t.BAR_H,
                            radius=0.0, fill=s.pace))


def _card(shapes, hits, s, account, top, now, hover, confirm=""):
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
        left, top, right - left, height, radius=t.CARD_RADIUS, fill=s.card_bg,
        stroke=s.card_border, line_width=1.0))
    if account.active:
        # Drawn AFTER the card so it sits on top, and deliberately inset
        # into the card's own horizontal padding rather than shifting
        # inner_l — becoming active must never reflow a row.
        shapes.append(t.Box(left, top + t.RAIL_INSET, t.RAIL_W,
                            height - t.RAIL_INSET * 2, radius=t.RAIL_W / 2,
                            fill=s.rail))

    inner_l, inner_r = left + t.CARD_PAD_H, right - t.CARD_PAD_H

    if is_confirm:
        question, lines = _confirm_question(account)
        question_h = t.CARD_HEADER_H + (lines - 1) * t.CONFIRM_LINE_H
        shapes.append(t.Label(inner_l, top + t.CARD_PAD_V + question_h / 2,
                              question, size=t.SIZE_EMAIL, bold=True,
                              color=s.text, max_width=inner_r - inner_l,
                              max_lines=lines))
        button_cy = (top + t.CARD_PAD_V + question_h + t.CARD_INNER_GAP
                     + t.BUTTON_H / 2)
        keep_l = _button(shapes, hits, s, "cancel-remove", inner_r, button_cy,
                         "Keep", hover=hover, tooltip="Keep this account")
        # Wording from AccountCardView.confirmHeader's Remove button's
        # .help(), which differs by provider: an OpenAI card has no
        # re-registration story.
        confirm_tip = (
            "Forget this card (labels and last numbers). Signing in with "
            "Codex brings it back" if provider == "openai" else
            f"Deletes claude-swap's stored credential backup for slot "
            f"{account.number}. Signing in as this account re-registers it")
        _button(shapes, hits, s, f"confirm-remove:{pid}", keep_l - 6,
                button_cy, "Remove", hover=hover, danger=True,
                tooltip=confirm_tip)
        header_h = question_h + t.CARD_INNER_GAP + t.BUTTON_H
        _card_body(shapes, s, account, top, now, inner_l, inner_r,
                   header_h=header_h)
        return height

    head_cy = top + t.CARD_PAD_V + t.CARD_HEADER_H / 2
    dot_color = s.status_rgba(model.dot_color(account))
    shapes.append(t.Dot(inner_l + t.DOT_R, head_cy, t.DOT_R, dot_color,
                        hollow=model.dot_style(account) == "hollow"))

    if account.active:
        chip_w = t.text_width("ACTIVE", t.SIZE_CHIP, bold=True) + 14
        chip_x = inner_r - chip_w
        shapes.append(t.Box(chip_x, head_cy - t.CHIP_H / 2, chip_w, t.CHIP_H,
                            radius=t.CHIP_H / 2,
                            fill=s.status_rgba("green")))
        shapes.append(t.Label(chip_x + chip_w / 2, head_cy, "ACTIVE",
                              size=t.SIZE_CHIP, bold=True, anchor="center",
                              color=s.accent_text))
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
        control_l = _button(shapes, hits, s, f"switch:{account.number}",
                            inner_r, head_cy, "Make Active", hover=hover,
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
                            fill=s.button_disabled))
        shapes.append(t.Label(badge_x + badge_w / 2, head_cy, badge,
                              size=t.SIZE_CHIP, anchor="center",
                              color=s.text_secondary))
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
                              s.text if hover == f"remove:{pid}"
                              else s.text_tertiary))
        # Wording from AccountCardView.cardHeader's remove (✕) button's
        # .help().
        hits.append(t.Hit(f"remove:{pid}", cx - t.REMOVE_HIT / 2,
                          head_cy - t.REMOVE_HIT / 2,
                          t.REMOVE_HIT, t.REMOVE_HIT,
                          tooltip=f"Remove {account.email} from AI smartbar"))

    shapes.append(t.Label(inner_l + t.DOT_R * 2 + 7, head_cy,
                          model.account_address(account),
                          size=t.SIZE_EMAIL, bold=True, color=s.text,
                          max_width=label_r - (inner_l + t.DOT_R * 2 + 7)))

    _card_body(shapes, s, account, top, now, inner_l, inner_r)
    return height


def _card_body(shapes, s, account, top, now, inner_l, inner_r,
               header_h=t.CARD_HEADER_H):
    """Metric rows, or the explanatory line on a data-less card — shared by
    the normal header and the (possibly taller) remove-confirm header.

    A metric row is two stacked lines: label/pct/countdown, then the bar.
    See ROW_LABEL_H's comment in popover_theme for why the readout stayed on
    the label's line rather than moving under the bar.
    """
    body_top = top + t.CARD_PAD_V + header_h + t.CARD_INNER_GAP
    if not account.metrics:
        blocked = model.switch_blocked(account)
        lines = state_lines(account)
        block_h = t.STATE_ROW_H + (lines - 1) * t.STATE_LINE_H
        color = s.warning if blocked else s.text_secondary
        text_l = inner_l
        if blocked:
            # A dead credential is the one data-less state that needs a
            # glyph as well as a color — warning alone reads as just this
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
                              size=t.SIZE_ROW_LABEL, bold=True, color=s.text,
                              max_width=t.LABEL_W))
        # Countdown recomputed from the absolute reset time so an old
        # snapshot still shows a live wait (mirror of Metric.liveCountdown).
        countdown = remaining_text(metric.resets_at, now) or metric.countdown
        # One ink for the whole readout: percentage and countdown are one
        # fact read left to right, so a step between them would imply a
        # hierarchy the row does not have. It comes from the scheme rather
        # than the ink-with-alpha literal it used to be, because that
        # literal was white and a white readout is invisible on a light card.
        color = s.text_spent if metric.pct >= 100 else s.text
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
            clock_cx = (inner_r - text_w - t.COUNTDOWN_ICON_GAP
                        - t.COUNTDOWN_ICON / 2)
            shapes.append(t.Glyph("clock", clock_cx, label_cy,
                                  t.COUNTDOWN_ICON, color))

        bar_top = row_top + t.ROW_LABEL_H + t.ROW_LABEL_GAP
        _bar(shapes, s, bar_l, bar_top, bar_w, metric, now)


def build(snapshot, *, version="", pending_version="", blocked_reason="",
          fetched_at="", stale=False, error="", now=None, hover="",
          provider="", confirm="", action_error="", refreshing=False,
          stale_reason="", scheme=t.DARK) -> t.Layout:
    """Positioned primitives + hit rects for the whole popover.

    `scheme` is the appearance to paint — a t.Scheme, or the name of one
    ("dark"/"light") for front-ends that just pass through whatever the host
    reports. Only colour depends on it: the geometry below is identical in
    both, so nothing about WHERE anything lands can differ between
    appearances, and a hit rect measured in one is valid in the other.

    `provider` selects the visible tab ("claude"/"openai"); "" auto-resolves
    to Claude when it has accounts, else OpenAI.

    The tab row exists to choose BETWEEN providers, so it appears only when
    both of them actually have accounts — one provider means one pill, which
    is a button that changes nothing.

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
    """
    now = now or datetime.now(timezone.utc)
    s = scheme if isinstance(scheme, t.Scheme) else t.scheme_for(scheme)
    shapes, hits = [], []
    right = t.WIDTH - t.PAD

    head_cy = t.PAD + t.HEADER_H / 2
    title = "AI smartbar"
    shapes.append(t.Label(t.PAD, head_cy, title, size=t.SIZE_TITLE,
                          bold=True, color=s.text))
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
                              size=t.SIZE_CAPTION, color=s.text_tertiary))
    if stale:
        stale_x = (updated_x + t.text_width(updated, t.SIZE_CAPTION)
                   + t.HEADER_LABEL_GAP)
        shapes.append(t.Label(stale_x, head_cy, "stale", size=t.SIZE_CAPTION,
                              color=s.warning))
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
        # Header chrome sits a step back from the content it frames — the
        # cards are what you came to read — and brightens to full ink only
        # under the pointer. The old "pure white on hover" was a dark-only
        # literal: on a light scheme it would have hovered to invisible.
        color = (s.text_tertiary if busy
                 else s.text if hover == name else s.text_secondary)
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
    if accounts and openai:
        # The tab row is part of the header block, not a section of its
        # own, so it sits TAB_TOP_GAP under the title instead of a full
        # SECTION_GAP (mirrored by PopoverView's nested header VStack).
        cursor = t.PAD + t.HEADER_H + t.TAB_TOP_GAP
        x = t.PAD
        cy = cursor + t.TAB_H / 2
        for name, text in (("claude", "Claude"), ("openai", "OpenAI")):
            current = name == selected
            label_w = t.text_width(text, t.SIZE_CAPTION, bold=current)
            width = t.TAB_MARK + t.TAB_MARK_GAP + label_w + t.BUTTON_PAD_H * 2
            hit_name = f"tab:{name}"
            # Faded / not-faded, not colored: the selected provider is full
            # strength and the other recedes (mirrored by tabButton in
            # PopoverView.swift). A filled accent pill was tried and reverted
            # — it was the loudest thing on a panel whose only job is to
            # colour-code how much budget is left, and it won that contest.
            if current:
                fill, color = s.tab_bg_selected, s.text
            elif hover == hit_name:
                fill, color = s.tab_bg_hover, s.text
            else:
                fill, color = s.tab_bg, s.text_tertiary
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
                                  bold=current, color=color))
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
                              size=t.SIZE_CAPTION, color=s.warning,
                              max_width=text_w, max_lines=lines))
        dx = right - t.REMOVE_HIT / 2
        shapes.append(t.Glyph("close", dx, err_cy, t.REMOVE_ICON,
                              s.text if hover == "dismiss-error"
                              else s.text_tertiary))
        hits.append(t.Hit("dismiss-error", dx - t.REMOVE_HIT / 2,
                          err_cy - t.REMOVE_HIT / 2, t.REMOVE_HIT,
                          t.REMOVE_HIT, tooltip="Dismiss"))
        cursor += block_h + t.CARD_GAP
    cards = accounts if selected == "claude" else openai
    if snapshot is None:
        text_ = error or "Loading usage…"
        lines = _lines_for_width(text_, right - t.PAD)
        block_h = t.STATE_ROW_H + (lines - 1) * t.STATE_LINE_H
        shapes.append(t.Label(t.PAD, cursor + block_h / 2, text_,
                              size=t.SIZE_CAPTION,
                              color=s.warning if error else s.text_secondary,
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
                              size=t.SIZE_CAPTION, color=s.text_secondary,
                              max_width=right - t.PAD, max_lines=lines))
        cursor += block_h + t.CARD_GAP
    for account in cards:
        cursor += _card(shapes, hits, s, account, cursor, now, hover,
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
                                  foot_cy, t.COUNTDOWN_ICON, s.text_tertiary))
            label_x = t.PAD + t.COUNTDOWN_ICON + t.COUNTDOWN_ICON_GAP
        shapes.append(t.Label(label_x, foot_cy, label, size=t.SIZE_CAPTION,
                              color=s.text_tertiary))
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
        _button(shapes, hits, s, "update", right, foot_cy,
                f"Update to {pending_version}", accent=True, hover=hover,
                tooltip="Fetch, rebuild and restart AI smartbar")
    return t.Layout(t.WIDTH, cursor + t.FOOTER_H + t.PAD, shapes, hits,
                    background=s.window_bg)
