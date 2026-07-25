"""OpenAI/ChatGPT accounts for the OpenAI tab, read from Codex CLI's files.

Everything is local: who is signed in (and their plan) comes from the label
claims inside ~/.codex/auth.json, usage comes from the rate-limit snapshots
Codex writes into its own session rollouts. Token strings are decoded only
far enough to read two claims and never leave `login()` — no network, no
keychain, nothing written under the codex home. Design note:
docs/superpowers/specs/2026-07-25-openai-provider-tabs-design.md
"""
from __future__ import annotations

import base64
import glob
import json
import os
from datetime import datetime, timezone

from smartbar.core.reset_countdown_format import parse_iso

DEFAULT_CODEX_HOME = "~/.codex"

# Rollout files grow to hundreds of MB; only their tails are ever read, and
# only recently-touched files are considered (a weekly window is fully
# covered by 8 days of files).
TAIL_BYTES = 262144
MAX_FILES = 16
RECENT_DAYS = 8

# chatgpt_plan_type -> badge. Unknown plans fall back to title-case so a new
# tier degrades to something readable instead of hiding the account.
_PLANS = {"free": "Free", "plus": "Plus", "pro": "Pro", "prolite": "Pro Lite",
          "team": "Team", "enterprise": "Enterprise", "edu": "Edu",
          "business": "Business"}
_AUTH_CLAIM = "https://api.openai.com/auth"


def enabled() -> bool:
    """False when SMARTBAR_OPENAI=off — hides the tab and skips all reads."""
    return os.environ.get("SMARTBAR_OPENAI", "").strip().lower() != "off"


def codex_home() -> str:
    return os.path.expanduser(
        os.environ.get("SMARTBAR_CODEX_HOME", "") or DEFAULT_CODEX_HOME)


def plan_label(plan_type) -> str:
    """"prolite" -> "Pro Lite"; unknown -> title-case; empty -> no badge."""
    plan = (plan_type or "").strip().lower()
    if not plan:
        return ""
    return _PLANS.get(plan, plan.title())


def _claims(id_token: str) -> dict:
    """The JWT's payload claims (unverified — labels, not authentication)."""
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def login():
    """(email, plan badge) of the live ChatGPT login, or None.

    Reads ONLY the email and plan-type claims; the token strings themselves
    never leave this function. `auth_mode: apikey` (or anything unreadable)
    is "no ChatGPT login" — API-key use has no subscription windows.
    """
    try:
        with open(os.path.join(codex_home(), "auth.json"),
                  encoding="utf-8") as handle:
            auth = json.load(handle) or {}
        token = (auth.get("tokens") or {}).get("id_token") or ""
        if token.count(".") != 2:
            return None
        claims = _claims(token)
        email = (claims.get("email") or "").strip()
        plan = plan_label((claims.get(_AUTH_CLAIM) or {})
                          .get("chatgpt_plan_type"))
        return (email, plan) if email else None
    except (OSError, ValueError, IndexError):
        return None


# path -> ((mtime, size), [(timestamp, rate_limits), ...]). Steady state is
# a stat() per recent file; only a grown file re-reads its tail.
_scan_cache: dict = {}


def _tail_lines(path: str) -> list:
    """The last TAIL_BYTES of a file as whole lines (first partial dropped)."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - TAIL_BYTES))
        data = handle.read()
    if size > TAIL_BYTES:
        data = data.split(b"\n", 1)[-1]
    return data.decode("utf-8", "replace").splitlines()


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _events(path: str) -> list:
    """Rate-limit events in a rollout's tail — [(timestamp, rate_limits)]."""
    try:
        stat = os.stat(path)
        key = (stat.st_mtime, stat.st_size)
        hit = _scan_cache.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        events = []
        for line in _tail_lines(path):
            if "rate_limit" not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get("payload") or {}
            limits = (payload.get("rate_limits")
                      or (payload.get("info") or {}).get("rate_limits"))
            if isinstance(limits, dict):
                events.append((event.get("timestamp") or "", limits))
        _scan_cache[path] = (key, events)
        return events
    except OSError:
        return []


def _recent_rollouts(home: str, now) -> list:
    """Recently-touched rollout paths, newest first, capped."""
    pattern = os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl")
    aged = []
    floor = now.timestamp() - RECENT_DAYS * 86400
    for path in glob.glob(pattern):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= floor:
            aged.append((mtime, path))
    aged.sort(reverse=True)
    return [path for _mtime, path in aged[:MAX_FILES]]


def _window_key(minutes: int) -> str:
    if minutes == 300:
        return "5h"
    if minutes == 10080:
        return "7d"
    if minutes >= 1440:
        return f"{minutes // 1440}d"
    return f"{max(1, minutes // 60)}h"


def rate_limits(home=None, cutoff="", now=None):
    """Latest %-used per window from the rollout tails.

    Returns ({metric key: {label, short, pct, resets_at}}, measured_at).
    Events at or before `cutoff` are ignored — they belong to whoever was
    signed in before the current login (rollouts carry no account id). A
    window whose reset time has passed reads 0% with no countdown: the
    budget is back, and showing the old number would lie. ({}, "") when
    nothing is readable.
    """
    home = home or codex_home()
    now = now or datetime.now(timezone.utc)
    cutoff_at = parse_iso(cutoff)
    seen: dict = {}          # (limit_id, minutes) -> (event time, window)
    measured = None          # (event time, raw timestamp string)
    for path in _recent_rollouts(home, now):
        for raw_ts, limits in _events(path):
            at = parse_iso(raw_ts)
            if at is None or (cutoff_at is not None and at <= cutoff_at):
                continue
            limit_id = limits.get("limit_id") or "codex"
            for window in (limits.get("primary"), limits.get("secondary")):
                if not isinstance(window, dict):
                    continue
                minutes = window.get("window_minutes")
                if not isinstance(minutes, (int, float)):
                    continue
                key = (limit_id, int(minutes))
                if key not in seen or at > seen[key][0]:
                    seen[key] = (at, window)
            if measured is None or at > measured[0]:
                measured = (at, raw_ts)

    def entry(label, short, window):
        resets = window.get("resets_at")
        pct = float(window.get("used_percent") or 0.0)
        if isinstance(resets, (int, float)) and resets > now.timestamp():
            resets_at = _iso(resets)
        else:
            pct, resets_at = 0.0, ""   # the window already reset: budget back
        return {"label": label, "short": short, "pct": pct,
                "resets_at": resets_at}

    metrics: dict = {}
    scoped: dict = {}        # limit_id -> {minutes: (at, window)}
    for (limit_id, minutes), (at, window) in seen.items():
        if limit_id == "codex":
            key = _window_key(minutes)
            metrics[key] = entry(key, key, window)
        else:
            scoped.setdefault(limit_id, {})[minutes] = (at, window)
    for limit_id, windows in scoped.items():
        name = limit_id[6:] if limit_id.startswith("codex_") else limit_id
        name = name.title()
        # The weekly bucket is the scoped row (mirrors the Claude cards);
        # without one, the largest window this limit reported stands in.
        minutes = 10080 if 10080 in windows else max(windows)
        metrics[f"scoped:{name}"] = entry(name, name[:1].upper(),
                                          windows[minutes][1])
    return metrics, (measured[1] if measured else "")


def accounts(now=None) -> list:
    """[model.Account] for the OpenAI tab; [] when disabled or empty."""
    if not enabled():
        return []
    return []
