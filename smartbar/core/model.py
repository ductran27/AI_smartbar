"""Data model and presentation logic shared by all AI_smartbar UIs.

Every user-visible string (icon text, hover title, menu rows, macOS
menu-bar title) is produced here so both platform UIs render identically.

v3 semantics: every number a user sees is "% used" — the same scale as
Claude Code's /usage — and pills/bars FILL as tokens are spent.
Thresholds are used-based: a metric is yellow at or above SMARTBAR_YELLOW
% used, "low" (light red) at or above SMARTBAR_LOW, "critical" (dark red,
fires the switch alert) at or above SMARTBAR_RED, and purple ("full") once
exhausted at 100%. Gray is NOT part of that ramp: it means no measurement.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from smartbar.core.reset_countdown_format import parse_iso

DEFAULT_YELLOW_USED = 50.0
DEFAULT_LOW_USED = 75.0
DEFAULT_RED_USED = 90.0

# Glyphs and RGB for every status `color()` can return: the 5-step used ramp
# (green → yellow → low → critical → full) plus the off-ramp "gray", which
# means "no measurement at all" and nothing else. Renderers must not keep
# their own copy — a missing key here is a runtime crash in a UI we may not
# be able to run (tests/test_model.py asserts parity).
DOT = {"green": "🟢", "yellow": "🟡", "low": "🟠", "critical": "🔴",
       "full": "🟣", "gray": "⚪"}
RGB = {"green": (0.239, 0.745, 0.545), "yellow": (0.847, 0.651, 0.290),
       "low": (0.867, 0.478, 0.271), "critical": (0.851, 0.325, 0.310),
       "full": (0.486, 0.420, 0.910), "gray": (0.361, 0.400, 0.447)}

# cswap usageStatus values other than "ok", mapped to the short explanation
# UIs put on the account's card/row (instead of a bare "No usage data").
STATE_TEXT = {
    "relogin_required": "Re-login required — sign in as this account in Claude Code once",
    "token_expired": "Token expired — Claude Code refreshes it on next use",
    "keychain_unavailable": "Keychain locked — credentials unreadable",
    "no_credentials": "No stored credentials",
    "api_key": "API-key account — no subscription usage",
    # OpenAI provider only (core/codex.py): a remembered ChatGPT login that
    # is no longer the live one. Its numbers are read-only leftovers.
    "signed_out": "Signed out — usage from its last session",
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


def panel_pinned() -> bool:
    """True when the panel should stay on screen instead of popping over.

    `SMARTBAR_PANEL=always` turns the Linux panel into a permanent desktop
    readout: shown at startup, never auto-hidden, anchored to a corner. It
    exists because StatusNotifier hosts vary in what a click can do — XFCE's
    systray, for one, implements no `Activate` method at all, so left-click
    can never be routed to the app and the panel would otherwise always cost
    a gesture. Anything else (unset, "popover") keeps the popover behaviour.
    """
    return os.environ.get("SMARTBAR_PANEL", "").strip().lower() == "always"


@dataclass
class Metric:
    key: str            # "5h", "7d", "spend", or "scoped:<Name>"
    label: str          # "5h", "7d", "Spend", "Fable"
    short: str          # "5h", "7d", "$", "F"
    pct: float          # % used, as reported by cswap (same scale as /usage)
    resets_at: str = ""
    countdown: str = ""  # preformatted by cswap, e.g. "4h 3m"


@dataclass
class Account:
    number: int
    email: str
    org: str = ""
    active: bool = False
    ok: bool = True       # usageStatus == "ok" and usage data present
    status: str = "ok"    # raw cswap usageStatus (see STATE_TEXT)
    metrics: list = field(default_factory=list)
    # How many devices currently have THIS account as their live slot —
    # stamped by core/presence.apply_counts, 0 when nobody is on it or the
    # other devices could not be seen. cswap never reports it.
    devices: int = 0
    # Subscription plan badge ("20x", "5x", "Pro", "Free") — stamped by
    # core/plan.apply_plans from local label files; "" means unknown and
    # renders NO badge, same convention as devices == 0.
    plan: str = ""
    # "claude" (cswap slots) or "openai" (core/codex.py). Cards branch on
    # it — an OpenAI account has no switch button and no device badge.
    provider: str = "claude"
    # cswap's usageFetchedAt for THIS account: when its usage was actually
    # measured at the API. Per-account on purpose — cswap refreshes each
    # slot on its own plan, so an alternate parked on a long plan can be
    # arbitrarily older than the active one in the very same payload.
    # Anything asking "how old is this reading" (warmup's staleness gate)
    # must use the account's own stamp, never a snapshot-wide one.
    fetched_at: str = ""


@dataclass
class Snapshot:
    accounts: list = field(default_factory=list)
    # The one stamp the popover's "Updated" line shows. It is the ACTIVE
    # account's measurement time (see cswap.snapshot_stamp), because that is
    # the account /usage describes too — NOT an arbitrary account's. It is a
    # display aggregate and nothing else: per-account freshness lives on
    # Account.fetched_at, and anything gating on age must read that instead.
    fetched_at: str = ""
    schema_warning: str = ""
    # OpenAI/ChatGPT accounts ride a SEPARATE list, never merged into
    # `accounts`: the same address can be both a Claude and a ChatGPT
    # account, and a merged list would let plan/presence stamping bleed
    # across providers and a second active=True upend active_account,
    # registration, best_switch and the icon. UIs compose the tabs.
    openai: list = field(default_factory=list)

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
        return "full"   # purple: the limit is spent, not merely critical
    if used >= red_threshold():
        return "critical"
    if used >= low_threshold():
        return "low"
    if used >= yellow_threshold():
        return "yellow"
    return "green"


# "<n>h" / "<n>d" — cswap's own 5h/7d buckets and every window key
# core/codex.py._window_key can emit (it derives the very same shape from
# window_minutes, not just the two literals Claude Code happens to use), so
# one regex covers both providers instead of a table that would drift the
# moment Codex reports a window neither of us hardcoded.
_WINDOW_KEY = re.compile(r"(\d+)([hd])")


def window_seconds(key: str) -> float | None:
    """Length of the reset window a metric KEY names, in seconds, or None.

    Only "<n>h"/"<n>d" keys (Claude Code's "5h"/"7d", and whatever shape
    core/codex.py._window_key emits for a Codex rate-limit window — it
    follows this exact pattern) have a stated length. "spend" and every
    "scoped:<Name>" per-model bucket carry a reset TIME (resets_at) but no
    window LENGTH cswap tells us — there is no "the Fable bucket is N days
    wide" number anywhere in the payload — so they get None here rather
    than a guessed length pace_fraction() could silently be wrong about.
    """
    match = _WINDOW_KEY.fullmatch(key)
    if not match:
        return None
    amount, unit = match.groups()
    return float(amount) * (86400.0 if unit == "d" else 3600.0)


def pace_fraction(metric, now=None) -> float | None:
    """How far through its reset window `metric` is, 0..1, or None.

    None when window_seconds(metric.key) says the window has no stated
    length, when metric.resets_at is empty or unparseable, or when the
    reset has already passed (a window that's already over has nothing left
    to pace against). Otherwise `1 - (time left) / (window length)`, clamped
    to 0..1 — 0 right after a reset, 1 right before the next one, so the
    caret reads as "how far through this window are we", independent of how
    much of the budget is actually spent (that's the fill).
    """
    window = window_seconds(metric.key)
    if window is None:
        return None
    resets = parse_iso(metric.resets_at)
    if resets is None:
        return None
    now = now or datetime.now(timezone.utc)
    remaining = (resets - now).total_seconds()
    if remaining <= 0:
        return None
    return min(max(1.0 - remaining / window, 0.0), 1.0)


def general_worst(account):
    """Worst non-scoped metric (5h/7d/spend) — the account-wide limits."""
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

    Hollow means there is NO usable measurement behind the dot (see
    state_text); solid means the color is a real reading. Together with the
    purple "full" status this keeps the three states apart: purple = the
    limit is spent, hollow gray = we don't know, red = nearly there.
    """
    return "hollow" if worst(account) is None else "solid"


def dot_color(account) -> str:
    """Status color name for an account's dot — the worst metric's color.

    "gray" when there is nothing to measure, which is exactly when
    dot_style() says to draw the dot hollow.
    """
    metric = worst(account)
    return "gray" if metric is None else color(metric.pct)


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


def account_label(account) -> str:
    """The address, plan badge, and device count: "a@b.com · 20x (2)".

    Every UI names an account through here so the badges appear in all of
    them at once (mirrored by the Swift card header — pinned by
    TestPlanParity). A count of 0 prints nothing: an absent badge reads as
    "nobody is on it", whereas "(0)" on four idle cards is noise that also
    lies whenever the other devices simply could not be reached — see
    core/presence.py. An empty plan likewise prints nothing (unknown tier,
    managed API-key account, or SMARTBAR_PLANS=off — see core/plan.py).

    Appending rather than prefixing is deliberate: both the cairo painter
    and SwiftUI truncate a long address in the MIDDLE, so the badges
    survive even on a card too narrow to show the address itself.
    """
    label = account.email
    badge = getattr(account, "plan", "") or ""
    if badge:
        label = f"{label} · {badge}"
    count = getattr(account, "devices", 0) or 0
    return f"{label} ({count})" if count > 0 else label


def title_line(account) -> str:
    if account is None:
        return "AI smartbar — no active account"
    if not account.metrics:
        return f"{account_label(account)} — {state_text(account) or 'no usage data'}"
    return f"{account_label(account)} — {metrics_text(account)} used"


def menu_row(account) -> str:
    dot = "●" if account.active else "○"
    body = metrics_text(account) if account.metrics else (state_text(account) or "no data")
    return f"{dot} {account.number} {account_label(account)}   {body}"


def icon_text(account) -> str:
    m = worst(account)
    if m is None:
        return "?"
    return f"{m.short}{round(m.pct)}"


def macos_title(account) -> str:
    """Menu-bar text mirroring the tray badge: one dotted segment per row."""
    return " · ".join(f"{DOT[row_color]} {text}"
                      for text, row_color in icon_rows(account))
