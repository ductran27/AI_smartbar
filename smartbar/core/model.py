"""Data model and presentation logic shared by all AI_smartbar UIs.

Every user-visible string (icon text, hover title, menu rows, macOS
menu-bar title) is produced here so both platform UIs render identically.

v2 semantics: every number a user sees is "% left" (tokens remaining);
pills/bars drain as tokens are spent. Thresholds are remaining-based:
a metric is yellow at or below SMARTBAR_YELLOW % left, "low" (light red)
at or below SMARTBAR_LOW, "critical" (dark red, fires the switch alert)
at or below SMARTBAR_RED, gray when nothing is left.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_YELLOW_LEFT = 50.0
DEFAULT_LOW_LEFT = 25.0
DEFAULT_RED_LEFT = 10.0

DOT = {"green": "🟢", "yellow": "🟡", "low": "🟠", "critical": "🔴", "gray": "⚪"}


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
    return _threshold("SMARTBAR_YELLOW", DEFAULT_YELLOW_LEFT)


def low_threshold() -> float:
    return _threshold("SMARTBAR_LOW", DEFAULT_LOW_LEFT)


def red_threshold() -> float:
    return _threshold("SMARTBAR_RED", DEFAULT_RED_LEFT)


@dataclass
class Metric:
    key: str            # "5h", "7d", or "scoped:<Name>"
    label: str          # "5h", "7d", "Fable"
    short: str          # "5h", "7d", "F"
    pct: float          # % used, as reported by cswap
    resets_at: str = ""
    countdown: str = ""  # preformatted by cswap, e.g. "4h 3m"
    clock: str = ""

    @property
    def left(self) -> float:
        """% of the window remaining, clamped at 0."""
        return max(0.0, 100.0 - self.pct)


@dataclass
class Account:
    number: int
    email: str
    org: str = ""
    active: bool = False
    ok: bool = True     # usageStatus == "ok" and usage data present
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
    """Status name for a used-% value, judged on what's left."""
    left = max(0.0, 100.0 - pct)
    if left <= 0:
        return "gray"
    if left <= red_threshold():
        return "critical"
    if left <= low_threshold():
        return "low"
    if left <= yellow_threshold():
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
    """Rows for text-based badges: [(text, color)], 1 or 2 rows, % left.

    Row 1 is the general all-models limit, row 2 the per-model bucket;
    each row carries its own threshold color.
    """
    rows = []
    for m in (general_worst(account), scoped_worst(account)):
        if m is not None:
            rows.append((f"{m.short}{round(m.left)}", color(m.pct)))
    if not rows:
        rows.append(("?", "gray"))
    return rows


def pill_states(account):
    """States for the twin-pill icon: [(fraction_left, color)].

    General all-models pill first, then one pill per scoped (per-model)
    metric in cswap order. Empty list when there is no data — renderers
    draw the hollow "?" state.
    """
    states = []
    general = general_worst(account)
    if general is not None:
        states.append((general.left / 100.0, color(general.pct)))
    if account is not None:
        for m in account.metrics:
            if m.key.startswith("scoped:"):
                states.append((m.left / 100.0, color(m.pct)))
    return states


def needs_registration(snapshot) -> bool:
    """True when cswap answered but no slot matches the live login.

    Covers both a fresh /login with an unregistered account (all slots
    active=false) and a fresh install (no accounts at all) — in both cases
    `cswap add` registers the current login. Callers must only pass
    snapshots from a successful fetch.
    """
    return snapshot.active_account is None


def best_switch(snapshot):
    """Among non-active accounts with data, the one with most headroom."""
    candidates = [a for a in snapshot.accounts if not a.active and a.ok and a.metrics]
    if not candidates:
        return None
    return min(candidates, key=lambda a: worst(a).pct)


def metrics_text(account) -> str:
    return " · ".join(f"{m.short} {round(m.left)}%" for m in account.metrics)


def title_line(account) -> str:
    if account is None:
        return "AI smartbar — no active account"
    if not account.metrics:
        return f"{account.email} — no usage data"
    return f"{account.email} — {metrics_text(account)}"


def menu_row(account) -> str:
    dot = "●" if account.active else "○"
    body = metrics_text(account) if account.metrics else "no data"
    return f"{dot} {account.number} {account.email}   {body}"


def icon_text(account) -> str:
    m = worst(account)
    if m is None:
        return "?"
    return f"{m.short}{round(m.left)}"


def macos_title(account) -> str:
    """Menu-bar text mirroring the tray badge: one dotted segment per row."""
    return " · ".join(f"{DOT[row_color]} {text}"
                      for text, row_color in icon_rows(account))
