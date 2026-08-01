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
_FALLBACK_GUARD_UNSET = object()


def fallback_guard_presentation(report=None, *, busy="", error=""):
    """Return ``(status label, tone, action)`` from runner-owned JSON.

    This is deliberately presentation-only.  It never reads managed settings
    or decides whether a policy is effective; the fallback-guard runner owns
    those decisions and publishes the stable ``state`` field consumed here.
    ``action`` is ``enable``, ``verify`` or ``""`` while an operation is busy.
    A failed live check is the one state where the static route fields matter:
    when both remain blocked, the useful retry is Verify, not reinstalling the
    already-effective fragment.
    """
    if busy:
        return ({
            "status": "Checking…",
            "enable": "Installing…",
            "verify": "Verifying…",
            "remove": "Removing…",
        }.get(busy, "Working…"), "neutral", "")
    static_routes_blocked = (
        isinstance(report, dict)
        and report.get("safetyAutoFallback") == "blocked"
        and report.get("availabilityAutoFallback") == "blocked")
    if error:
        return ("Action needed", "warning",
                "verify" if static_routes_blocked else "enable")
    if not isinstance(report, dict):
        return "Checking…", "neutral", ""

    state = report.get("state")
    if state == "protected":
        return "Protected", "good", "verify"
    if state == "protected_inconclusive":
        return "Protected + inconclusive", "caution", "verify"
    if state == "not_protected":
        return "Not protected", "neutral", "enable"

    # ``action_needed`` can mean either a broken/conflicting static policy or
    # a failed live probe.  The runner's route verdicts distinguish those
    # without recreating any precedence or filesystem logic in the UI.
    action = "verify" if static_routes_blocked else "enable"
    return "Action needed", "warning", action


def _fallback_tone_color(tone):
    return {
        "good": t.status_rgba("green"),
        "caution": t.status_rgba("yellow"),
        "warning": t.WARNING,
        "danger": t.DANGER,
    }.get(tone, t.TEXT_TERTIARY)


def _fallback_value(value):
    return {"blocked": "Blocked", "enabled": "Enabled",
            "unknown": "Unknown"}.get(value, "Unknown")


def _fallback_detail_rows(report):
    """Human-readable detail rows sourced only from the runner report."""
    if not isinstance(report, dict):
        return [("Status check has not completed.", t.TEXT_SECONDARY)]

    rows = [
        ("Safety handoff: %s" % _fallback_value(
            report.get("safetyAutoFallback")), t.TEXT_SECONDARY),
        ("Availability chain: %s" % _fallback_value(
            report.get("availabilityAutoFallback")), t.TEXT_SECONDARY),
    ]
    live = report.get("lastLiveCheck")
    probes = live.get("probes") if isinstance(live, dict) else []
    manual_verified = any(
        isinstance(probe, dict)
        and probe.get("name") == "manual_opus"
        and probe.get("outcome") == "OPUS_OK"
        for probe in (probes or []))
    manual_restricted = report.get("manualOpusRestrictedByGuard")
    if manual_restricted is True:
        manual = "Restricted by guard"
        manual_color = t.DANGER
    elif manual_verified:
        manual = "Available (live verified)"
        manual_color = t.status_rgba("green")
    elif manual_restricted is False:
        # False means only that this guard does not restrict manual Opus.  It
        # is not proof that the service was available, so do not overclaim it.
        manual = "Not restricted by guard"
        manual_color = t.TEXT_SECONDARY
    else:
        manual = "Unknown"
        manual_color = t.TEXT_TERTIARY
    rows.append(("Manual Opus: %s" % manual, manual_color))

    if isinstance(live, dict):
        parts = [str(live.get("status") or "unknown").capitalize()]
        cost = live.get("totalCostUsd")
        if isinstance(cost, (int, float)):
            parts.append("$%.3f" % cost)
        limit = live.get("budgetLimitUsd")
        if isinstance(limit, (int, float)):
            parts.append("$%.2f limit" % limit)
        if live.get("checkedAt"):
            parts.append(str(live["checkedAt"]))
        rows.append(("Live check: " + " · ".join(parts), t.TEXT_SECONDARY))
        for probe in (probes or []):
            if not isinstance(probe, dict):
                continue
            models = probe.get("observedModels")
            if isinstance(models, list) and models:
                observed = ", ".join(str(value) for value in models)
            elif isinstance(models, str) and models:
                observed = models
            else:
                observed = "no model reported"
            rows.append(("%s: %s · %s" % (
                probe.get("name") or "probe",
                probe.get("outcome") or "unknown", observed),
                t.TEXT_SECONDARY))
    else:
        rows.append(("Live check: Not run", t.TEXT_SECONDARY))

    if report.get("claudeVersion"):
        rows.append(("Claude Code: %s" % report["claudeVersion"],
                     t.TEXT_SECONDARY))
    if report.get("activeManagedSource"):
        rows.append(("Managed source: %s" % report["activeManagedSource"],
                     t.TEXT_SECONDARY))
    if report.get("scope"):
        rows.append(("Scope: %s" % report["scope"], t.TEXT_SECONDARY))
    if report.get("policyPath"):
        rows.append(("Policy: %s" % report["policyPath"], t.TEXT_TERTIARY))
    details = report.get("details")
    if isinstance(details, list) and details:
        rows.append((str(details[0]), t.WARNING))
    return rows


def _fallback_guard(shapes, hits, report, top, hover, *, busy="", error="",
                    expanded=False, advanced=False, remove_confirm=False):
    """Paint the Auto fallback card and return its exact height."""
    status, tone, action = fallback_guard_presentation(
        report, busy=busy, error=error)
    inner_l = t.PAD + t.FALLBACK_PAD_H
    inner_r = t.WIDTH - t.PAD - t.FALLBACK_PAD_H
    detail_width = inner_r - inner_l
    detail_specs = []
    if expanded:
        rows = _fallback_detail_rows(report)
        if error:
            rows = [(error, t.WARNING)] + rows
        for text, color in rows:
            lines = _lines_for_width(text, detail_width, cap=2)
            height = t.FALLBACK_LINE_H + (lines - 1) * t.STATE_LINE_H
            detail_specs.append((text, color, lines, height))

    height = t.FALLBACK_ROW_H
    if expanded:
        height += t.FALLBACK_PAD_V
        height += sum(spec[3] + t.FALLBACK_DETAIL_GAP
                      for spec in detail_specs)
        height += t.BUTTON_H + t.FALLBACK_DETAIL_GAP
        if advanced:
            height += t.BUTTON_H + t.FALLBACK_DETAIL_GAP
            if remove_confirm:
                height += t.FALLBACK_LINE_H + t.FALLBACK_DETAIL_GAP

    shapes.append(t.Box(t.PAD, top, t.WIDTH - 2 * t.PAD, height,
                        radius=t.FALLBACK_RADIUS, fill=t.CARD_BG,
                        stroke=t.CARD_BORDER))
    # Appended before the real buttons so those win reverse-order hit testing.
    hits.append(t.Hit("fallback-details", t.PAD, top,
                      t.WIDTH - 2 * t.PAD, t.FALLBACK_ROW_H,
                      tooltip=("Hide fallback details" if expanded
                               else "Show fallback details")))

    title_y = top + 11.0
    status_y = top + 27.0
    shapes.append(t.Label(inner_l, title_y, "Auto fallback",
                          size=t.SIZE_CAPTION, bold=True, color=t.TEXT))
    shapes.append(t.Label(
        inner_l + t.text_width("Auto fallback", t.SIZE_CAPTION, bold=True) + 4,
        title_y, "▾" if expanded else "▸", size=t.SIZE_CAPTION,
        color=t.TEXT_TERTIARY))
    dot_x = inner_l + t.DOT_R
    shapes.append(t.Dot(dot_x, status_y, t.DOT_R,
                        _fallback_tone_color(tone)))
    action_text = "Verify" if action == "verify" else "Protect"
    action_width = (t.text_width(action_text, t.SIZE_CAPTION,
                                 bold=action == "enable")
                    + t.BUTTON_PAD_H * 2) if action else 0
    status_max = max(20.0, inner_r - (dot_x + t.DOT_R + 6)
                     - (action_width + 8 if action else 0))
    shapes.append(t.Label(dot_x + t.DOT_R + 6, status_y, status,
                          size=t.SIZE_CAPTION,
                          color=_fallback_tone_color(tone),
                          max_width=status_max))
    if action:
        _button(shapes, hits, "fallback-" + action, inner_r,
                top + t.FALLBACK_ROW_H / 2, action_text,
                accent=action == "enable", hover=hover,
                tooltip=("Install machine-wide automatic-fallback protection"
                         if action == "enable" else
                         "Run the explicit, cost-bounded live verification"))

    if not expanded:
        return height

    cursor = top + t.FALLBACK_ROW_H + t.FALLBACK_PAD_V
    for text, color, lines, row_h in detail_specs:
        shapes.append(t.Label(inner_l, cursor + row_h / 2, text,
                              size=t.SIZE_CAPTION, color=color,
                              max_width=detail_width, max_lines=lines))
        cursor += row_h + t.FALLBACK_DETAIL_GAP

    advanced_name = "fallback-advanced"
    shapes.append(t.Label(inner_l, cursor + t.BUTTON_H / 2,
                          "Advanced %s" % ("▾" if advanced else "▸"),
                          size=t.SIZE_CAPTION, color=(
                              t.TEXT if hover == advanced_name
                              else t.TEXT_TERTIARY)))
    hits.append(t.Hit(advanced_name, inner_l, cursor, detail_width,
                      t.BUTTON_H, tooltip=("Hide removal controls" if advanced
                                          else "Show removal controls")))
    cursor += t.BUTTON_H + t.FALLBACK_DETAIL_GAP

    if advanced:
        if remove_confirm:
            warning_y = cursor + t.FALLBACK_LINE_H / 2
            shapes.append(t.Label(inner_l, warning_y,
                                  "Remove machine-wide protection?",
                                  size=t.SIZE_CAPTION, color=t.WARNING))
            cursor += t.FALLBACK_LINE_H + t.FALLBACK_DETAIL_GAP
            keep_l = _button(shapes, hits, "fallback-cancel-remove", inner_r,
                             cursor + t.BUTTON_H / 2, "Keep", hover=hover,
                             tooltip="Keep automatic-fallback protection")
            _button(shapes, hits, "fallback-confirm-remove", keep_l - 6,
                    cursor + t.BUTTON_H / 2, "Remove", danger=True,
                    hover=hover,
                    tooltip="Authorize removal of AI smartbar's policy")
        else:
            _button(shapes, hits, "fallback-remove", inner_r,
                    cursor + t.BUTTON_H / 2, "Remove protection…",
                    hover=hover,
                    tooltip="Begin the two-step protection removal")
    return height


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
    (SwiftUI wraps at 2 via .lineLimit(2))."""
    text = model.state_text(account) or "No usage data"
    room = t.WIDTH - 2 * t.PAD - 2 * t.CARD_PAD_H
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
        stroke=t.CARD_BORDER_ACTIVE if account.active else t.CARD_BORDER,
        line_width=1.5 if account.active else 1.0))

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
        # Wording from AccountCardView.swift:184-186's .help(), which
        # differs by provider: an OpenAI card has no re-registration story.
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
        # Wording from AccountCardView.swift:159-161's .help().
        switch_tip = (
            f"Stored credential is dead — switching would log Claude Code "
            f"out. {model.state_text(account)}." if blocked else
            f"Switch Claude Code to {account.email}")
        control_l = _button(shapes, hits, f"switch:{account.number}", inner_r,
                            head_cy, "Make Active", hover=hover,
                            enabled=not blocked, tooltip=switch_tip)

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
        # Wording from AccountCardView.swift:139's .help().
        hits.append(t.Hit(f"remove:{pid}", cx - t.REMOVE_HIT / 2,
                          head_cy - t.REMOVE_HIT / 2,
                          t.REMOVE_HIT, t.REMOVE_HIT,
                          tooltip=f"Remove {account.email} from AI smartbar"))

    shapes.append(t.Label(inner_l + t.DOT_R * 2 + 7, head_cy,
                          model.account_label(account),
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
        shapes.append(t.Label(inner_l, body_top + block_h / 2,
                              model.state_text(account) or "No usage data",
                              size=t.SIZE_CAPTION,
                              color=t.WARNING if blocked else t.TEXT_SECONDARY,
                              max_width=inner_r - inner_l, max_lines=lines))
        return

    bar_l = inner_l + t.LABEL_W + t.BAR_GAP
    value_w = t.VALUE_PCT_W + t.VALUE_COUNTDOWN_W
    bar_w = inner_r - value_w - t.BAR_GAP - bar_l
    pct_r = inner_r - t.VALUE_COUNTDOWN_W
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
        color = t.TEXT_SPENT if metric.pct >= 100 else (1, 1, 1, 0.8)
        # Percentage and countdown are two INDEPENDENTLY right-anchored
        # labels, not one concatenated string right-anchored at inner_r —
        # a single string makes the percentage slide sideways every time
        # the countdown's length changes (e.g. "1h 0m" -> "59m"), which is
        # exactly what FINDING 3 measured (a 19pt swing on the "·").
        shapes.append(t.Label(pct_r, cy, f"{round(metric.pct)}%",
                              size=t.SIZE_ROW_VALUE, mono=True,
                              anchor="right", color=color))
        if countdown:
            shapes.append(t.Label(inner_r, cy, f" · {countdown}",
                                  size=t.SIZE_ROW_VALUE, mono=True,
                                  anchor="right", color=color))


def build(snapshot, *, version="", pending_version="", blocked_reason="",
          fetched_at="", stale=False, error="", now=None, hover="",
          provider="", confirm="", action_error="", refreshing=False,
          stale_reason="", fallback_guard=_FALLBACK_GUARD_UNSET,
          fallback_busy="", fallback_expanded=False,
          fallback_advanced=False, fallback_remove_confirm=False,
          fallback_error="") -> t.Layout:
    """Positioned primitives + hit rects for the whole popover.

    `provider` selects the visible tab ("claude"/"openai"); "" auto-resolves
    to Claude when it has accounts, else OpenAI. The tab row itself exists
    only when BOTH providers have accounts — a single-provider machine gets
    exactly the layout it always had.

    `confirm` names the card whose removal awaits confirmation
    ("<provider>:<id>", the suffix of its "remove:" hit); that card's
    header becomes the Remove/Keep question. A token naming a card that
    is not rendered — or that turned active — is simply ignored.

    `action_error` renders a dismissible one-line banner (hit
    "dismiss-error") under the header/tabs for the most recent switch or
    remove failure — mirrors PopoverView.swift:28's `switchError ??
    removeError`. `refreshing` dims the ⟳ glyph and disables its hit so a
    second click cannot queue a second fetch while one is in flight
    (mirrors `.disabled(store.isRefreshing)`).

    `stale_reason` is why the last refresh failed, surfaced on hover of
    the "stale" marker (mirrors `store.lastError`); `blocked_reason` is
    likewise surfaced on hover of the footer's "update held" label
    (mirrors `.help("Update held back: …")`). Both were previously
    computed and then thrown away (FINDING 7).

    ``fallback_guard`` is intentionally opt-in. Linux passes the runner's
    report (including ``None`` while its first asynchronous status call is in
    flight), while callers that omit it retain their historical geometry.
    This keeps the shared Windows painter stable until that platform gains a
    supported policy backend.
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
        # hover — mirrors store.lastError on PopoverView.swift:206.
        stale_w = t.text_width("stale", t.SIZE_CAPTION)
        hits.append(t.Hit("stale", stale_x, head_cy - t.ICON_BUTTON_W / 2,
                          stale_w, t.ICON_BUTTON_W,
                          tooltip=(stale_reason
                                  or "last refresh failed; showing old data")))
    for name, offset in (("quit", 0.0), ("refresh", t.ICON_BUTTON_W)):
        cx = right - offset - t.ICON_BUTTON_W / 2
        # Refreshing dims the glyph AND disables its hit (mirrors
        # .disabled(store.isRefreshing), PopoverView.swift:220) so a
        # second click while a fetch is in flight cannot queue another.
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
    guard_visible = fallback_guard is not _FALLBACK_GUARD_UNSET
    if guard_visible:
        cursor += _fallback_guard(
            shapes, hits, fallback_guard, cursor, hover,
            busy=fallback_busy, error=fallback_error,
            expanded=fallback_expanded, advanced=fallback_advanced,
            remove_confirm=fallback_remove_confirm) + t.SECTION_GAP
    accounts = list(snapshot.accounts) if snapshot is not None else []
    openai = list(getattr(snapshot, "openai", []) or []) if snapshot else []
    selected = provider or ("openai" if openai and not accounts else "claude")
    if accounts and openai:
        # The tab row is part of the header block, not a section of its
        # own, so it sits TAB_TOP_GAP under the title instead of a full
        # SECTION_GAP (mirrored by PopoverView's nested header VStack).
        if not guard_visible:
            cursor = t.PAD + t.HEADER_H + t.TAB_TOP_GAP
        x = t.PAD
        cy = cursor + t.TAB_H / 2
        for name, text in (("claude", "Claude"), ("openai", "OpenAI")):
            current = name == selected
            width = (t.text_width(text, t.SIZE_CAPTION, bold=current)
                     + t.BUTTON_PAD_H * 2)
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
            shapes.append(t.Label(x + width / 2, cy, text,
                                  size=t.SIZE_CAPTION, bold=current,
                                  anchor="center", color=color))
            hits.append(t.Hit(hit_name, x, cy - t.BUTTON_H / 2, width,
                              t.BUTTON_H, tooltip=f"Show {text} accounts"))
            x += width + t.TAB_GAP
        cursor += t.TAB_H + t.SECTION_GAP
    if action_error:
        # One error line for whichever card action failed most recently —
        # mirrors PopoverView.swift:28's `switchError ?? removeError`, in
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
    cards = accounts if selected == "claude" else openai
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
        shapes.append(t.Label(t.PAD, foot_cy, label, size=t.SIZE_CAPTION,
                              color=t.TEXT_TERTIARY))
        if blocked_reason:
            # Non-action hit: PopoverView.swift:136 shows the actual
            # reason on hover rather than a bare "update held" label.
            label_w = t.text_width(label, t.SIZE_CAPTION)
            hits.append(t.Hit("update-held", t.PAD, foot_cy - t.FOOTER_H / 2,
                              label_w, t.FOOTER_H,
                              tooltip=f"Update held back: {blocked_reason}"))
    if pending_version:
        _button(shapes, hits, "update", right, foot_cy,
                f"Update to {pending_version}", accent=True, hover=hover,
                tooltip="Fetch, rebuild and restart AI smartbar")
    return t.Layout(t.WIDTH, cursor + t.FOOTER_H + t.PAD, shapes, hits)
