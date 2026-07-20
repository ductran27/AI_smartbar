"""Data model and presentation logic shared by all AI_smartbar UIs.

Every user-visible string (icon text, hover title, menu rows, macOS
menu-bar title) is produced here so both platform UIs render identically.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_YELLOW = 70.0
DEFAULT_RED = 90.0

DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "gray": "⚪"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def yellow_threshold() -> float:
    if "SMARTBAR_TEST_THRESHOLD" in os.environ:
        return _env_float("SMARTBAR_TEST_THRESHOLD", DEFAULT_YELLOW)
    return _env_float("SMARTBAR_YELLOW", DEFAULT_YELLOW)


def red_threshold() -> float:
    if "SMARTBAR_TEST_THRESHOLD" in os.environ:
        return _env_float("SMARTBAR_TEST_THRESHOLD", DEFAULT_RED)
    return _env_float("SMARTBAR_RED", DEFAULT_RED)


@dataclass
class Metric:
    key: str            # "5h", "7d", or "scoped:<Name>"
    label: str          # "5h", "7d", "Fable"
    short: str          # "5h", "7d", "F"
    pct: float
    resets_at: str = ""
    countdown: str = ""  # preformatted by cswap, e.g. "4h 3m"
    clock: str = ""


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
    if pct >= red_threshold():
        return "red"
    if pct >= yellow_threshold():
        return "yellow"
    return "green"


def best_switch(snapshot):
    """Among non-active accounts with data, the one with most headroom."""
    candidates = [a for a in snapshot.accounts if not a.active and a.ok and a.metrics]
    if not candidates:
        return None
    return min(candidates, key=lambda a: worst(a).pct)


def metrics_text(account) -> str:
    return " · ".join(f"{m.short} {round(m.pct)}%" for m in account.metrics)


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
    return f"{m.short}{round(m.pct)}"


def macos_title(account) -> str:
    m = worst(account)
    if m is None:
        return "⚪ –"
    return f"{DOT[color(m.pct)]} {m.short} {round(m.pct)}%"
