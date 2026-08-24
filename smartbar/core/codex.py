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
import tempfile
from datetime import datetime, timezone

from smartbar.core import model
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
    """Bucket label for a window length — ROUNDED, not floored: real Codex
    payloads report 299 and 10079 minutes, which flooring mislabelled as
    4h/6d (and gave the pace math the wrong window length)."""
    if 290 <= minutes <= 310:
        return "5h"
    if 10020 <= minutes <= 10140:
        return "7d"
    if minutes >= 1440:
        return f"{max(1, round(minutes / 1440))}d"
    return f"{max(1, round(minutes / 60))}h"


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
    seen: dict = {}     # (limit_id, minutes) -> (event time, window, name)
    measured = None          # (event time, raw timestamp string)
    for path in _recent_rollouts(home, now):
        for raw_ts, limits in _events(path):
            at = parse_iso(raw_ts)
            if at is None or (cutoff_at is not None and at <= cutoff_at):
                continue
            limit_id = limits.get("limit_id") or "codex"
            limit_name = limits.get("limit_name") or ""
            contributed = False
            for window in (limits.get("primary"), limits.get("secondary")):
                if not isinstance(window, dict):
                    continue
                minutes = window.get("window_minutes")
                if not isinstance(minutes, (int, float)):
                    continue
                contributed = True
                key = (limit_id, int(minutes))
                if key not in seen or at > seen[key][0]:
                    seen[key] = (at, window, limit_name)
            # "when these numbers were measured" must only move when an
            # event actually carried a window — credits-only events used to
            # stamp measuredAt days after the real measurement.
            if contributed and (measured is None or at > measured[0]):
                measured = (at, raw_ts)

    def entry(label, short, window, at):
        resets = window.get("resets_at")
        pct = float(window.get("used_percent") or 0.0)
        # Older Codex payloads carry only resets_in_seconds; deriving the
        # absolute time from the event stamp keeps them from reading as
        # already-reset (0%) while the user may be at 90%.
        if not isinstance(resets, (int, float)):
            relative = window.get("resets_in_seconds")
            if isinstance(relative, (int, float)) and at is not None:
                resets = at.timestamp() + relative
        if isinstance(resets, (int, float)) and resets > now.timestamp():
            resets_at = _iso(resets)
        else:
            pct, resets_at = 0.0, ""   # the window already reset: budget back
        return {"label": label, "short": short, "pct": pct,
                "resets_at": resets_at}

    metrics: dict = {}
    scoped: dict = {}   # limit_id -> {minutes: (at, window, name)}
    for (limit_id, minutes), (at, window, name) in seen.items():
        if limit_id == "codex":
            key = _window_key(minutes)
            metrics[key] = entry(key, key, window, at)
        else:
            scoped.setdefault(limit_id, {})[minutes] = (at, window, name)
    for limit_id, windows in scoped.items():
        fallback = limit_id[6:] if limit_id.startswith("codex_") else limit_id
        # The weekly bucket is the scoped row (mirrors the Claude cards);
        # without one, the largest window this limit reported stands in.
        minutes = 10080 if 10080 in windows else max(windows)
        at, window, name = windows[minutes]
        # Codex supplies the human model name (limit_name); the limit_id is
        # an internal codename the user never sees in Codex itself.
        label = name or fallback.title()
        metrics[f"scoped:{label}"] = entry(label, label[:1].upper(),
                                           window, at)
    return metrics, (measured[1] if measured else "")


def _registry_path() -> str:
    cache = (os.environ.get("SMARTBAR_CACHE_DIR")
             or os.path.expanduser("~/.cache/ai-smartbar"))
    return os.path.join(cache, "openai-accounts.json")


def _load_registry() -> dict:
    try:
        with open(_registry_path(), encoding="utf-8") as handle:
            reg = json.load(handle)
        return reg if isinstance(reg, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_registry(reg: dict) -> None:
    """Atomic registry write (tmp + os.replace).

    A concurrent reader — every UI polls this file — must never see a
    half-written registry, and a crash mid-write must never shred the list
    of remembered accounts. Raises OSError; callers decide whether that is
    fatal (a removal) or ignorable (a routine sync on a read-only cache).
    """
    path = _registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # mkstemp, not a pid-keyed name: two threads of ONE process (the UI
    # thread's sync and a removal worker) shared the same tmp path and one
    # os.replace could promote the other's half-written file.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                               prefix=".openai-accounts-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(reg, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def remove_account(email: str) -> None:
    """Forget a remembered ChatGPT account — registry entry only.

    Nothing under the codex home is ever touched, so the address reappears
    the moment it signs in with Codex again. The LIVE login is refused
    rather than removed: _sync would re-register it on the next poll, so
    removing it could only ever look like a silent failure.
    """
    reg = _load_registry()
    accounts = reg.get("accounts") or {}
    if email not in accounts:
        raise ValueError(f"no remembered OpenAI account {email!r}")
    if (reg.get("active") or "") == email:
        raise ValueError("this is the live Codex login — sign out in "
                         "Codex first, then remove the card it leaves")
    del accounts[email]
    _save_registry(reg)


def _sync(now) -> dict:
    """Fold the live login + fresh rate limits into the registry.

    The registry remembers every ChatGPT account seen on this machine —
    labels and numbers ONLY (email, plan badge, %-used snapshots), never
    anything from the token fields. Saved only when the content actually
    changed, so a quiet poll costs no write.

    Attribution: rollouts carry no account id, so when the login changes
    the predecessor's numbers freeze as they stand and a cutoff timestamp
    keeps its traffic from bleeding into the new login. A cold start (empty
    registry) has no predecessor, so existing history belongs to the
    current login.
    """
    reg = _load_registry()
    before = json.dumps(reg, sort_keys=True)
    now_iso = _iso(now.timestamp())
    live = login()
    if live:
        email, badge = live
        if reg.get("active") != email:
            previous = reg.get("active") or ""
            if previous in (reg.get("accounts") or {}):
                reg["accounts"][previous]["lastSeen"] = now_iso
            reg["cutoff"] = now_iso if reg.get("accounts") else ""
            reg["active"] = email
        entry = reg.setdefault("accounts", {}).setdefault(
            email, {"lastSeen": now_iso})
        entry["plan"] = badge   # the claim is fresher than any old event
        metrics, measured = rate_limits(cutoff=reg.get("cutoff", ""), now=now)
        if metrics:
            entry["metrics"] = metrics
            entry["measuredAt"] = measured
    elif reg.get("active"):
        previous = reg["active"]
        if previous in (reg.get("accounts") or {}):
            reg["accounts"][previous]["lastSeen"] = now_iso
        reg["active"] = ""
        reg["cutoff"] = now_iso
    after = json.dumps(reg, sort_keys=True)
    if after != before:
        try:
            _save_registry(reg)
        except OSError:
            pass                # a read-only cache must not break the tab
    return reg


def _rows(entry: dict, active: bool, now) -> list:
    """Stored window snapshots -> ordered model.Metric rows.

    A signed-out account drops windows whose reset time has passed — that
    budget came back while nobody was watching, so the old number would be
    a lie. The live login keeps its idle rows (rate_limits already zeroed
    them) so the card still names its windows.
    """
    stored = entry.get("metrics") or {}
    keys = [k for k in ("5h", "7d") if k in stored]
    keys += sorted(k for k in stored
                   if k not in ("5h", "7d") and not k.startswith("scoped:"))
    keys += sorted(k for k in stored if k.startswith("scoped:"))
    rows = []
    for key in keys:
        window = stored[key]
        resets_at = window.get("resets_at") or ""
        pct = float(window.get("pct") or 0.0)
        resets = parse_iso(resets_at)
        if not active:
            if resets is None or resets <= now:
                continue
        elif resets is None or resets <= now:
            # README: "a window whose reset time passes while idle reads
            # 0%". rate_limits zeroes this only while rollouts still carry
            # the event; once they age out (laptop closed over the reset)
            # the stored 61% would sit on the ACTIVE card forever.
            pct, resets_at = 0.0, ""
        rows.append(model.Metric(
            key=key, label=window.get("label") or key,
            short=window.get("short") or key,
            pct=pct, resets_at=resets_at))
    return rows


def accounts(now=None) -> list:
    """[model.Account] for the OpenAI tab: live login first, then the
    remembered (signed-out, read-only) accounts by last-seen. [] when
    disabled, or when this machine has never had a ChatGPT login."""
    if not enabled():
        return []
    now = now or datetime.now(timezone.utc)
    reg = _sync(now)
    entries = reg.get("accounts") or {}
    active_email = reg.get("active") or ""
    ordered = [(active_email, True)] if active_email in entries else []
    ordered += [(email, False) for email in
                sorted((e for e in entries if e != active_email),
                       key=lambda e: entries[e].get("lastSeen") or "",
                       reverse=True)]
    out = []
    for number, (email, is_active) in enumerate(ordered, 1):
        entry = entries[email]
        account = model.Account(
            number=number, email=email, active=is_active, ok=is_active,
            status="ok" if is_active else "signed_out",
            metrics=_rows(entry, is_active, now))
        account.plan = entry.get("plan") or ""
        account.provider = "openai"
        out.append(account)
    return out


def payload(now=None) -> dict:
    """The `--openai --json` body: FINAL display data, Swift maps nothing."""
    accts = accounts(now)
    entries = _load_registry().get("accounts") or {}
    return {"accounts": [{
        "email": account.email,
        "plan": account.plan,
        "active": account.active,
        "status": account.status,
        "stateText": model.state_text(account),
        "updatedAt": entries.get(account.email, {}).get("measuredAt", ""),
        "metrics": [{"key": m.key, "label": m.label, "short": m.short,
                     "pct": m.pct, "resetsAt": m.resets_at}
                    for m in account.metrics],
    } for account in accts]}
