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

    "default_claude_max_20x" -> "20x" is the primary path; pro/free/team
    are recognised in either the tier or the org type; subscriptionType
    ("max"/"pro"/"free", from credential blobs) is the coarse fallback.
    """
    tier = (rate_limit_tier or "").strip().lower()
    match = _MULT.search(tier)
    if match:
        return f"{match.group(1)}x"
    org = (organization_type or "").strip().lower()
    for hay in (tier, org):
        if not hay:
            continue
        if "enterprise" in hay or "team" in hay:
            return "Team"
        if "pro" in hay:
            return "Pro"
        if "free" in hay:
            return "Free"
    sub = (subscription_type or "").strip()
    return sub.title() if sub else ""
