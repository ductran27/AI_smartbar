"""Subscription-plan badges: which plan each account is on (20x/5x/Pro/Free).

Tiers are read from LOCAL label fields only — cswap's per-slot config
backups plus the live ~/.claude.json for the active login. No keychain,
no network, no token fields ever. Design note:
docs/superpowers/specs/2026-07-25-account-plan-badge-design.md
"""
from __future__ import annotations

import glob
import json
import os
import re

DEFAULT_BACKUP_DIR = "~/.claude-swap-backup"
DEFAULT_CLAUDE_JSON = "~/.claude.json"

_MULT = re.compile(r"_(\d+)x$")
_BADGES = (("enterprise", "Enterprise"), ("team", "Team"),
           ("pro", "Pro"), ("free", "Free"))


def _tokens(value: str) -> frozenset:
    """Split on non-alphanumerics so "claude_nonprofit" (contains "pro" as
    a substring, not a word) doesn't collide with the "pro" badge — the
    general form of the bug ab575fd fixed one string at a time."""
    return frozenset(t for t in re.split(r"[^a-z0-9]+", value) if t)


def enabled() -> bool:
    """False when SMARTBAR_PLANS=off — hides badges and skips all reads."""
    return os.environ.get("SMARTBAR_PLANS", "").strip().lower() != "off"


def backup_dir() -> str:
    return os.path.expanduser(
        os.environ.get("SMARTBAR_CSWAP_BACKUP_DIR", "") or DEFAULT_BACKUP_DIR)


def claude_json_path() -> str:
    return os.path.expanduser(
        os.environ.get("SMARTBAR_CLAUDE_JSON", "") or DEFAULT_CLAUDE_JSON)


def tier_label(rate_limit_tier=None, organization_type=None,
               subscription_type=None) -> str:
    """Anthropic tier strings -> short badge; "" means show nothing.

    "default_claude_max_20x" -> "20x" is the primary path; pro/free/team/
    enterprise are recognised as a whole WORD in either the tier or the
    org type (not a bare substring, so "claude_nonprofit" doesn't read as
    "Pro"); subscriptionType ("max"/"pro"/"free", from credential blobs)
    is the coarse fallback.
    """
    tier = (rate_limit_tier or "").strip().lower()
    match = _MULT.search(tier)
    if match:
        return f"{match.group(1)}x"
    org = (organization_type or "").strip().lower()
    for hay in (tier, org):
        tokens = _tokens(hay)
        for word, label in _BADGES:
            if word in tokens:
                return label
    sub = (subscription_type or "").strip()
    return sub.title() if sub else ""


# path -> ((mtime, size), (email, label) | None). Config backups are a
# couple hundred KB each and polled every 60-180s on Linux; the cache
# makes the steady state a stat() per file.
_cache: dict = {}


def _labelled(path: str):
    """(email, label) from one config-backup/claude.json, None if unusable."""
    try:
        stat = os.stat(path)
        key = (stat.st_mtime, stat.st_size)
        hit = _cache.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        with open(path, encoding="utf-8") as handle:
            oauth = (json.load(handle) or {}).get("oauthAccount") or {}
        email = (oauth.get("emailAddress") or "").strip()
        label = tier_label(oauth.get("organizationRateLimitTier"),
                           oauth.get("organizationType"),
                           oauth.get("subscriptionType"))
        value = (email, label) if email and label else None
        _cache[path] = (key, value)
        return value
    except (OSError, ValueError):
        return None


def plans_by_email(directory=None, claude_json=None) -> dict:
    """email -> badge label for every account whose tier is readable."""
    if not enabled():
        return {}
    directory = directory or backup_dir()
    plans: dict = {}
    pattern = os.path.join(directory, "configs", ".claude-config-*.json")
    for path in sorted(glob.glob(pattern)):
        pair = _labelled(path)
        if pair:
            plans[pair[0]] = pair[1]
    pair = _labelled(claude_json or claude_json_path())
    if pair:  # the live login is fresher than its backup copy
        plans[pair[0]] = pair[1]
    return plans


def apply_plans(snapshot, plans) -> None:
    """Stamp accounts with their plan badge, for model.account_label."""
    if snapshot is None:
        return
    for account in snapshot.accounts:
        account.plan = str((plans or {}).get(account.email, "") or "")
