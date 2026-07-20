"""Reset-time parsing and live countdown formatting.

cswap recomputes ``countdown``/``clock`` from ``resetsAt`` when ``list
--json`` runs, so they are exact at fetch time — but they freeze inside our
snapshot and drift while it is displayed. Renderers recompute the countdown
from the absolute ``resetsAt`` with these helpers so the wait a user reads
is live. Format mirrors claude-swap's ``oauth.format_reset``: "44m",
"1h 44m", "6d 13h", clamped at "0m".
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_iso(text: str):
    """ISO timestamp -> aware UTC datetime, or None. Naive input = UTC.

    Accepts a trailing "Z" (cswap emits it for usageFetchedAt): the system
    python3 launchd uses can predate 3.11, where fromisoformat lacks
    Z-suffix support.
    """
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def remaining_text(resets_at: str, now=None) -> str:
    """Live countdown to ``resets_at``, or "" when unparseable.

    Callers fall back to cswap's fetch-time string on "" — stale beats
    blank.
    """
    resets = parse_iso(resets_at)
    if resets is None:
        return ""
    if now is None:
        now = datetime.now(timezone.utc)
    total = max(0, int((resets - now).total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
