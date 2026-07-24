"""Data model and presentation logic shared by all AI_smartbar UIs.

Every user-visible string (icon text, hover title, menu rows, macOS
menu-bar title) is produced here so both platform UIs render identically.

v3 semantics: every number a user sees is "% used" — the same scale as
Claude Code's /usage — and pills/bars FILL as tokens are spent.
Thresholds are used-based: a metric is yellow at or above SMARTBAR_YELLOW
% used, "low" (light red) at or above SMARTBAR_LOW, "critical" (dark red,
fires the switch alert) at or above SMARTBAR_RED, gray once exhausted.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_YELLOW_USED = 50.0
DEFAULT_LOW_USED = 75.0
DEFAULT_RED_USED = 90.0

DOT = {"green": "🟢", "yellow": "🟡", "low": "🟠", "critical": "🔴", "gray": "⚪"}

# cswap usageStatus values other than "ok", mapped to the short explanation
# UIs put on the account's card/row (instead of a bare "No usage data").
STATE_TEXT = {
    "relogin_required": "Re-login required — sign in as this account in Claude Code once",
    "token_expired": "Token expired — Claude Code refreshes it on next use",
    "keychain_unavailable": "Keychain locked — credentials unreadable",
    "no_credentials": "No stored credentials",
    "api_key": "API-key account — no subscription usage",
}

# Slots whose STORED credential is dead. Switching to one would restore a
# credential Anthropic already rejected (the "my account suddenly logged
# out" trap), so UIs disable their switch action until it is re-captured.
DEAD_CREDENTIAL_STATUSES = frozenset({"relogin_required", "no_credentials"})


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _threshold(name: str, default: float) -> float:
    if "SMARTBAR_TEST_THRESHOLD" in os.environ:
        return _env_float("SMARTBAR_TEST_THRESHOLD", default)
    return _env_float(name, default)


def yellow_threshold() -> float:
    return _threshold("SMARTBAR_YELLOW", DEFAULT_YELLOW_USED)


def low_threshold() -> float:
    return _threshold("SMARTBAR_LOW", DEFAULT_LOW_USED)


def red_threshold() -> float:
    return _threshold("SMARTBAR_RED", DEFAULT_RED_USED)


@dataclass
class Metric:
    key: str            # "5h", "7d", or "scoped:<Name>"
    label: str          # "5h", "7d", "Fable"
    short: str          # "5h", "7d", "F"
    pct: float          # % used, as reported by cswap (same scale as /usage)
    resets_at: str = ""
    countdown: str = ""  # preformatted by cswap, e.g. "4h 3m"
    clock: str = ""


@dataclass
class Account:
    number: int
    email: str
    org: str = ""
    active: bool = False
    ok: bool = True       # usageStatus == "ok" and usage data present
    status: str = "ok"    # raw cswap usageStatus (see STATE_TEXT)
    metrics: list = field(default_factory=list)


@dataclass
class Snapshot:
    accounts: list = field(default_factory=list)
    fetched_at: str = ""
    schema_warning: str = ""

    @property
    def active_account(self):
        for acct in self.accounts:
            if acct.active:
                return acct
        return None


def worst(account):
    """The metric closest to its limit, or None without data."""
    if account is None or not account.metrics:
        return None
    return max(account.metrics, key=lambda m: m.pct)


def color(pct: float) -> str:
    """Status name for a used-% value."""
    used = max(0.0, pct)
    if used >= 100:
        return "gray"
    if used >= red_threshold():
        return "critical"
    if used >= low_threshold():
        return "low"
    if used >= yellow_threshold():
        return "yellow"
    return "green"


def general_worst(account):
    """Worst non-scoped metric (5h/7d) — the all-models limits."""
    if account is None:
        return None
    general = [m for m in account.metrics if not m.key.startswith("scoped:")]
    if not general:
        return None
    return max(general, key=lambda m: m.pct)


def scoped_worst(account):
    """Worst per-model (scoped) metric, e.g. the Fable weekly bucket."""
    if account is None:
        return None
    scoped = [m for m in account.metrics if m.key.startswith("scoped:")]
    if not scoped:
        return None
    return max(scoped, key=lambda m: m.pct)


def icon_rows(account):
    """Rows for text-based badges: [(text, color)], 1 or 2 rows, % used.

    Row 1 is the general all-models limit, row 2 the per-model bucket;
    each row carries its own threshold color.
    """
    rows = []
    for m in (general_worst(account), scoped_worst(account)):
        if m is not None:
            rows.append((f"{m.short}{round(m.pct)}", color(m.pct)))
    if not rows:
        rows.append(("?", "gray"))
    return rows


def pill_states(account):
    """States for the twin-pill icon: [(fraction_used, color)].

    General all-models pill first, then one pill per scoped (per-model)
    metric in cswap order. Pills FILL as tokens are spent (nearly full =
    nearly at the limit). Empty list when there is no data — renderers
    draw the hollow "?" state.
    """
    states = []
    general = general_worst(account)
    if general is not None:
        states.append((min(general.pct, 100.0) / 100.0, color(general.pct)))
    if account is not None:
        for m in account.metrics:
            if m.key.startswith("scoped:"):
                states.append((min(m.pct, 100.0) / 100.0, color(m.pct)))
    return states


def state_text(account) -> str:
    """Explanation shown when an account has no usable usage data."""
    if account.ok:
        return "" if account.metrics else "No usage data"
    return STATE_TEXT.get(account.status, "No usage data")


def dot_style(account) -> str:
    """"solid" or "hollow" for an account's status dot.

    v3 paints a dot gray at 100% used (exhausted) — the very same gray a
    dataless account gets, so "the Fable bucket is spent" and "this slot's
    credential is dead" rendered identically. Hollow means there is NO
    usable measurement behind the dot (see state_text); solid means the
    color is a real reading.
    """
    return "hollow" if worst(account) is None else "solid"


def switch_blocked(account) -> bool:
    """True when activating this slot would restore a dead credential."""
    return account.status in DEAD_CREDENTIAL_STATUSES


def needs_registration(snapshot) -> bool:
    """True when cswap answered but no slot matches the live login.

    Covers both a fresh /login with an unregistered account (all slots
    active=false) and a fresh install (no accounts at all) — in both cases
    `cswap add` registers the current login. Callers must only pass
    snapshots from a successful fetch.
    """
    return snapshot.active_account is None


def needs_recapture(snapshot) -> bool:
    """True when the live login's own slot reports a dead stored credential.

    Claude Code itself is signed in and working (the slot is active) while
    cswap's backup of it is dead — exactly the state `cswap add` heals by
    re-capturing the live credential.
    """
    account = snapshot.active_account
    return account is not None and switch_blocked(account)


def best_switch(snapshot):
    """Among non-active accounts with data, the one with most headroom."""
    candidates = [a for a in snapshot.accounts
                  if not a.active and a.ok and a.metrics and not switch_blocked(a)]
    if not candidates:
        return None
    return min(candidates, key=lambda a: worst(a).pct)


def metrics_text(account) -> str:
    return " · ".join(f"{m.short} {round(m.pct)}%" for m in account.metrics)


def title_line(account) -> str:
    if account is None:
        return "AI smartbar — no active account"
    if not account.metrics:
        return f"{account.email} — {state_text(account) or 'no usage data'}"
    return f"{account.email} — {metrics_text(account)} used"


def menu_row(account) -> str:
    dot = "●" if account.active else "○"
    body = metrics_text(account) if account.metrics else (state_text(account) or "no data")
    return f"{dot} {account.number} {account.email}   {body}"


def icon_text(account) -> str:
    m = worst(account)
    if m is None:
        return "?"
    return f"{m.short}{round(m.pct)}"


def macos_title(account) -> str:
    """Menu-bar text mirroring the tray badge: one dotted segment per row."""
    return " · ".join(f"{DOT[row_color]} {text}"
                      for text, row_color in icon_rows(account))
