"""Reset-time parsing and live countdown formatting.

cswap recomputes ``countdown``/``clock`` from ``resetsAt`` when ``list
--json`` runs, so they are exact at fetch time — but they freeze inside our
snapshot and drift while it is displayed. Renderers recompute the countdown
from the absolute ``resetsAt`` with these helpers so the wait a user reads
is live. Format mirrors claude-swap's ``oauth.format_reset``: "44m",
"1h 44m", "6d 13h", clamped at "0m".
"""
from __future__ import annotations

import locale
import os
import sys
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


def _windows_prefers_24_hour() -> bool:
    """The Control Panel's actual per-user setting, via ctypes (stdlib).

    Windows shells routinely leave LANG/LC_TIME unset, so the POSIX path
    below has nothing to read; GetLocaleInfoW asks the OS directly instead.
    """
    try:
        import ctypes
        # LOCALE_USER_DEFAULT, LOCALE_ITIME (winnls.h): 0 = 12-hour "tt",
        # 1 = 24-hour "HH" in the user's Control Panel short-time format.
        get_locale_info = ctypes.windll.kernel32.GetLocaleInfoW
        buf = ctypes.create_unicode_buffer(8)
        # 0x23 is LOCALE_ITIME (12/24-hour). 0x21 — used here before — is
        # LOCALE_IDATE, the short-DATE order, which made ja-JP read as
        # 12-hour and DMY locales as 24-hour regardless of their clock.
        n = get_locale_info(0x0400, 0x00000023, buf, len(buf))
        return n > 0 and buf.value.strip() == "1"
    except Exception:
        return False


def prefers_24_hour_clock() -> bool:
    """Best-effort: does the local convention favor "14:02" over "2:02 PM"?

    Dependency-free and meant to work on macOS, Linux AND Windows on
    Python 3.9, so there is no single stdlib call that just answers this:

    - ``SMARTBAR_CLOCK=24``/``=12`` is an explicit override (same
      convention as core/model.py's SMARTBAR_YELLOW/LOW/RED), for whenever
      the guess below gets it wrong.
    - Windows has no ``locale.nl_langinfo`` at all, so it goes through
      ``GetLocaleInfoW`` instead — the real Control Panel setting.
    - POSIX's ``nl_langinfo(T_FMT)`` answers exactly, but only for the
      locale category Python is CURRENTLY running under, which starts as
      "C" until something calls ``setlocale``. We peek at the env-derived
      locale and restore the previous one immediately after reading it —
      brief, and nothing else in this codebase does locale-sensitive
      formatting, but it is process-global state, hence the narrow
      try/finally instead of leaving it changed.
    - Any failure along the way falls back to today's behaviour: 12-hour.
    """
    override = os.environ.get("SMARTBAR_CLOCK", "").strip()
    if override in ("24", "12"):
        return override == "24"
    if sys.platform == "win32":
        return _windows_prefers_24_hour()
    fmt = _posix_time_format()
    # glibc's en_US t_fmt is the ALIAS "%r" — a 12-hour format containing
    # neither %I nor %l literally, which the plain substring test misread
    # as 24-hour for every US Linux user.
    if not fmt or "%r" in fmt:
        return False
    return "%I" not in fmt and "%l" not in fmt


def _posix_time_format() -> str:
    """The locale's T_FMT, or "" when it cannot be read."""
    try:
        previous = locale.setlocale(locale.LC_TIME)
        try:
            locale.setlocale(locale.LC_TIME, "")
            return locale.nl_langinfo(locale.T_FMT) or ""
        finally:
            locale.setlocale(locale.LC_TIME, previous)
    except (locale.Error, AttributeError, ValueError):
        return ""
