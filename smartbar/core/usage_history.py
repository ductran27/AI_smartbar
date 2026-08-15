"""Local daily-high usage history, one record per account per day.

The 30-day strip (popover_layout._strip_card / Sparkline.swift) wants to
answer "how close to the ceiling did this account run each day", not
"whatever the poll happened to catch it at" — a device that polls every 60s
would otherwise make the strip mostly a function of poll timing, not usage.
So every record() call folds the fresh snapshot's numbers into TODAY's
entry by taking the MAX seen so far per window, never appending a second
entry for a day already recorded.

File shape: {"records": [{"date": "YYYY-MM-DD", "provider": "claude",
"email": "a@b.com", "windows": {"7d": 93.0, "spend": 41.0}}], "version": 1}.
Every account in a snapshot gets its own record — the strip only ever
reads the active Claude account's "7d" series, but keeping every account's
history means switching accounts, or a future per-account strip, never
starts from a blank slate.

This runs on every poll (see tray_controller.TrayController._apply_snapshot,
which calls record() best-effort right after a fresh snapshot lands), so it
must never be able to take the popover down: a corrupt or unreadable file
degrades to an empty store rather than raising, and record() itself never
lets a write failure escape.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from smartbar.core import paths

FILE_NAME = "usage-history.json"
# One record per (account, day). Most devices carry one or two accounts, so
# this is a day cap in practice; a device with many accounts trims total
# storage before it trims any single account's full 90 days, which is the
# right trade for a "tiny local store" that must stay cheap to read on every
# poll rather than a per-account bucket scheme this stage does not need.
MAX_RECORDS = 90

log = logging.getLogger("ai-smartbar")


def _path(path=None) -> str:
    return path or os.path.join(paths.cache_dir(), FILE_NAME)


def _day_key(now) -> str:
    """Local calendar day, mirroring core.warmup._day_key: a device's user
    lives their "today" on their own clock, not UTC's."""
    return now.astimezone().strftime("%Y-%m-%d")


def load(path=None) -> dict:
    """The store, or an empty one — never raises.

    Called on every poll (via record(), below) and by the popover's own
    series() reader, so a truncated or hand-edited file must never be able
    to break either.
    """
    try:
        with open(_path(path), encoding="utf-8") as handle:
            store = json.load(handle)
        if isinstance(store, dict) and isinstance(store.get("records"), list):
            return store
    except (OSError, ValueError):
        pass
    return {"records": [], "version": 1}


def _save(store: dict, path=None) -> None:
    """Atomic write (tmp + os.replace), copied from
    smartbar.core.codex._save_registry: a concurrent reader (every UI polls
    this file too) must never see a half-written store, and a crash
    mid-write must never shred the history. Raises OSError; record() below
    is the only caller and treats it as best-effort.
    """
    target = _path(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = f"{target}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(store, sort_keys=True))
        os.replace(tmp, target)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def record(snapshot, now=None, path=None) -> None:
    """Fold one fresh snapshot's metrics into today's high-water marks.

    Best-effort by contract, not merely by convention: this is called from
    the UI thread's fresh-snapshot handler (TrayController._apply_snapshot),
    where an uncaught exception would take the whole refresh down with it —
    a history feature must never be able to cost a device its live usage
    display. Every step below already degrades quietly on its own (load()
    never raises, _save()'s tmp file is cleaned up on failure), so the
    broad except here is a second belt on top of both, not a substitute for
    either.
    """
    if snapshot is None:
        return
    try:
        now = now or datetime.now(timezone.utc)
        day = _day_key(now)
        store = load(path)
        records = [r for r in store.get("records", []) if isinstance(r, dict)]
        by_key = {(r.get("date"), r.get("provider"), r.get("email")): r
                  for r in records}
        accounts = (list(getattr(snapshot, "accounts", None) or [])
                    + list(getattr(snapshot, "openai", None) or []))
        for account in accounts:
            if not account.metrics:
                continue
            provider = getattr(account, "provider", "claude") or "claude"
            key = (day, provider, account.email)
            entry = by_key.get(key)
            if entry is None:
                entry = {"date": day, "provider": provider,
                         "email": account.email, "windows": {}}
                records.append(entry)
                by_key[key] = entry
            windows = entry.setdefault("windows", {})
            for metric in account.metrics:
                windows[metric.key] = max(windows.get(metric.key, 0.0),
                                          metric.pct)
        # Oldest-first prune: a fixed record cap rather than a 90-calendar-
        # day cutoff, so the store's own size — the thing that actually
        # costs a read on every poll — stays bounded regardless of how many
        # accounts this device has seen.
        records.sort(key=lambda r: r.get("date", ""))
        del records[:max(0, len(records) - MAX_RECORDS)]
        store["records"] = records
        store["version"] = 1
        _save(store, path)
    except Exception:
        log.exception("could not record usage history")


def series(provider: str, email: str, key: str, days: int = 30,
          path=None) -> list:
    """`days` floats-or-None ending TODAY, oldest first, for one account's
    one window — the exact shape the 30-day strip draws bar-for-bar.

    A day with no matching record (never polled that day, or the account
    did not exist yet) comes back None rather than 0.0 — there is a real
    difference between "0% used" and "never measured", and the strip's own
    stub rendering exists to say the second one honestly.
    """
    store = load(path)
    by_date = {}
    for record_ in store.get("records", []):
        if not isinstance(record_, dict):
            continue
        if record_.get("provider") != provider or record_.get("email") != email:
            continue
        windows = record_.get("windows")
        if not isinstance(windows, dict) or key not in windows:
            continue
        try:
            by_date[record_.get("date")] = float(windows[key])
        except (TypeError, ValueError):
            continue
    today = datetime.now(timezone.utc).astimezone().date()
    return [by_date.get((today - timedelta(days=offset)).strftime("%Y-%m-%d"))
            for offset in range(days - 1, -1, -1)]


def active_series(snapshot, days: int = 30, path=None) -> list:
    """The one series the strip draws: the ACTIVE Claude account's "7d".

    Which account and which window the strip shows is a policy decision,
    and it lives here rather than at each `popover_layout.build()` caller
    because there are three of them (both trays and the preview renderer).
    Spelling `series(active.provider, active.email, "7d")` out at each site
    is exactly how the two painted front-ends drift apart from each other,
    which is the failure this repo's parity tests exist to catch.

    Returns [] — not thirty Nones — when there is no active account to
    describe, so `_history_present` omits the card rather than drawing a
    row of stubs for an account the panel isn't showing.

    Best-effort like record(): this sits on the popover's build path, and a
    strip that cannot be drawn must cost the user a card, never the panel.
    """
    try:
        active = snapshot.active_account if snapshot is not None else None
        if active is None:
            return []
        return series(active.provider, active.email, "7d", days, path)
    except Exception:
        log.exception("could not read usage history")
        return []
