"""Auto window-starter ("warmup") gate logic — pure, no I/O.

The Claude 5-hour window starts at the first message; keeping one running
means the budget reset is always as early as possible. The runner warms an
account only when this gate says so. See
docs/superpowers/specs/2026-07-19-ai-smartbar-warmup-design.md.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from .reset_countdown_format import parse_iso  # re-exported: gate + runner use it

DEFAULT_DAILY_CAP = 6
COOLDOWN_MINUTES = 30
MAX_SNAPSHOT_AGE_MINUTES = 30
STATE_KEEP_DAYS = 7


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def five_hour_metric(account):
    if account is None or not account.ok:
        return None
    for metric in account.metrics:
        if metric.key == "5h":
            return metric
    return None


def window_idle(account, now) -> bool:
    """True when the account's 5h window is not running.

    An empty resets_at or one at/before now means the reported window has
    ended — the account is idle regardless of the reported pct (which
    describes the ended window). Unparseable resets_at is treated as
    running (conservative: never ping on data we can't read).
    """
    metric = five_hour_metric(account)
    if metric is None:
        return False
    if not metric.resets_at:
        return True
    resets = parse_iso(metric.resets_at)
    if resets is None:
        return False
    return resets <= now


def in_quiet_hours(spec: str, now) -> bool:
    """spec "23-05" (wraps midnight) or "13-15"; empty/garbage = never."""
    if not spec:
        return False
    try:
        start, end = (int(part) for part in spec.split("-", 1))
    except ValueError:
        return False
    hour = now.hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _day_key(now) -> str:
    return now.astimezone().strftime("%Y-%m-%d")


def attempts_today(state: dict, email: str, now) -> int:
    return state.get("days", {}).get(_day_key(now), {}).get(email, 0)


def should_warm(account, now, state, fetched_at):
    """(bool, reason). fetched_at: aware datetime of the snapshot, or None."""
    if in_quiet_hours(os.environ.get("SMARTBAR_WARMUP_QUIET", ""), now):
        return False, "quiet hours"
    if fetched_at is None or now - fetched_at > timedelta(minutes=MAX_SNAPSHOT_AGE_MINUTES):
        return False, "snapshot stale or unknown age"
    if not window_idle(account, now):
        return False, "window running (or no readable 5h data)"
    email = account.email
    last = state.get("last", {}).get(email)
    if last is not None and now - datetime.fromtimestamp(last, tz=timezone.utc) \
            < timedelta(minutes=COOLDOWN_MINUTES):
        return False, "cooldown"
    cap = _env_int("SMARTBAR_WARMUP_DAILY_CAP", DEFAULT_DAILY_CAP)
    if attempts_today(state, email, now) >= cap:
        return False, f"daily cap ({cap}) reached"
    return True, "idle window, all gates passed"


def record_attempt(state: dict, email: str, now) -> None:
    day = state.setdefault("days", {}).setdefault(_day_key(now), {})
    day[email] = day.get(email, 0) + 1
    state.setdefault("last", {})[email] = now.timestamp()


def prune_state(state: dict, current_emails, now) -> None:
    """Drop day buckets older than STATE_KEEP_DAYS and unknown emails."""
    keep_from = _day_key(now - timedelta(days=STATE_KEEP_DAYS))
    known = set(current_emails)
    days = state.get("days", {})
    for day in [d for d in days if d < keep_from]:
        del days[day]
    for day in days.values():
        for email in [e for e in day if e not in known]:
            del day[email]
    last = state.get("last", {})
    for email in [e for e in last if e not in known]:
        del last[email]


def warmed_successfully(account, now) -> bool:
    """Post-ping verification: the 5h window is now running."""
    metric = five_hour_metric(account)
    if metric is None:
        return False
    resets = parse_iso(metric.resets_at)
    return resets is not None and resets > now
