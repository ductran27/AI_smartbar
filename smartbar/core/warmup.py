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
# After this many failed pings in a row an account pauses until the next
# day: a broken environment must not eat the daily cap or spam alerts.
MAX_CONSECUTIVE_FAILURES = 3


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
    """spec "23-05" (wraps midnight) or "13-15"; empty/garbage = never.

    The hour is read off the LOCAL clock, not the UTC one. Callers hand this
    a UTC-aware `now` (warmup_runner uses datetime.now(timezone.utc)), so
    reading now.hour directly meant "23-05" silenced 23:00-05:00 UTC -- on a
    UTC+7 device that is 06:00-12:00 in the morning the user is actually at
    the keyboard, and warmups fired at 3am instead. _day_key() below already
    localises for exactly this reason; this is the same rule, applied to the
    setting a user is far more likely to notice getting wrong.
    """
    if not spec:
        return False
    try:
        start, end = (int(part) for part in spec.split("-", 1))
    except ValueError:
        return False
    hour = now.astimezone().hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _day_key(now) -> str:
    return now.astimezone().strftime("%Y-%m-%d")


def attempts_today(state: dict, email: str, now) -> int:
    return state.get("days", {}).get(_day_key(now), {}).get(email, 0)


def consecutive_failures(state: dict, email: str, now) -> int:
    """Today's unbroken failure streak; a new day resets it to 0."""
    entry = state.get("fail", {}).get(email)
    if not isinstance(entry, dict) or entry.get("day") != _day_key(now):
        return 0
    try:
        return int(entry.get("count", 0))
    except (TypeError, ValueError):
        return 0


def should_warm(account, now, state, fetched_at):
    """(bool, reason). fetched_at: aware datetime of the snapshot, or None."""
    if in_quiet_hours(os.environ.get("SMARTBAR_WARMUP_QUIET", ""), now):
        return False, "quiet hours"
    if fetched_at is None or now - fetched_at > timedelta(minutes=MAX_SNAPSHOT_AGE_MINUTES):
        return False, "snapshot stale or unknown age"
    if five_hour_metric(account) is None:
        status = getattr(account, "status", "") or ""
        if status == "relogin_required":
            return False, "re-login required (stored credential dead)"
        if status and status != "ok":
            return False, f"no usage data ({status})"
        return False, "no 5h usage data"
    if not window_idle(account, now):
        return False, "window running"
    email = account.email
    streak = consecutive_failures(state, email, now)
    if streak >= MAX_CONSECUTIVE_FAILURES:
        return False, f"{streak} failures in a row — paused until tomorrow"
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


def record_failure(state: dict, email: str, now) -> int:
    """Bump the consecutive-failure streak; returns the new count."""
    fail = state.setdefault("fail", {})
    entry = fail.get(email)
    if not isinstance(entry, dict) or entry.get("day") != _day_key(now):
        entry = {"count": 0}
    count = int(entry.get("count", 0)) + 1
    fail[email] = {"count": count, "day": _day_key(now)}
    return count


def record_success(state: dict, email: str) -> None:
    """A ping went through — clear the failure streak."""
    state.get("fail", {}).pop(email, None)


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
    for section in ("last", "fail"):
        entries = state.get(section, {})
        for email in [e for e in entries if e not in known]:
            del entries[email]


def warmed_successfully(account, now) -> bool:
    """Post-ping verification: the 5h window is now running."""
    metric = five_hour_metric(account)
    if metric is None:
        return False
    resets = parse_iso(metric.resets_at)
    return resets is not None and resets > now
