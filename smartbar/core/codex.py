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
import json
import os

DEFAULT_CODEX_HOME = "~/.codex"

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


def accounts(now=None) -> list:
    """[model.Account] for the OpenAI tab; [] when disabled or empty."""
    if not enabled():
        return []
    return []
