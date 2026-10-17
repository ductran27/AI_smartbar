"""Decides when to run `cswap add` so stored credentials never rot.

Why this exists: Anthropic rotates OAuth refresh tokens. Claude Code keeps
the LIVE login's grant current, but cswap's per-slot backup only holds
whatever was captured at `add` time — once the live grant rotates past it,
the backup dies with invalid_grant and the slot turns `relogin_required`
("the account is gone"). `cswap add` on an already-registered account
re-captures the live credential in place (local-only: keychain + config
read, backup write, dead-token state cleared), so running it the moment
the active slot reports a dead backup heals that slot, and running it on
a login with no slot registers it.

No routine re-capture of a healthy login. claude-swap >= 0.26 resyncs the
active slot's backup to a rotated live credential itself, during the usage
fetch, and only once the server says whose token it is. `cswap add` takes
the identity from ~/.claude.json and the token from the keychain, and its
ownership check fails open offline: a 15-minute add that ran on wake,
after a stale Claude Code session had rewritten ~/.claude.json, stored one
account's token under another's slot, and that card then showed the other
account's usage.

Pure decision logic (monotonic clock in, action out) so it is unit-testable
and can be mirrored 1:1 by the Swift app. Callers execute the returned
action by running `cswap add` in the background.
"""
from __future__ import annotations

import os

from . import model

REGISTER_COOLDOWN = 600.0   # s; retry ceiling while add cannot succeed (logged out)
HEAL_COOLDOWN = 120.0       # s; retry ceiling for dead-credential healing

ACTION_REGISTER = "register"    # live login has no slot -> add registers it
ACTION_HEAL = "heal"            # active slot's stored credential is dead -> add re-captures


class RecapturePolicy:
    """Stateful pacing for the two `cswap add` triggers."""

    def __init__(self):
        self._last_register = None
        self._last_heal = None

    def action(self, snapshot, now: float):
        """Action to take for this (successful) snapshot, or None.

        `now` is a monotonic timestamp. Stamps are taken at decision time,
        so a failing `cswap add` cannot retry faster than its cooldown.
        """
        if os.environ.get("SMARTBAR_AUTO_ADD") == "off":
            return None
        if model.needs_registration(snapshot):
            if self._due(self._last_register, REGISTER_COOLDOWN, now):
                self._last_register = now
                return ACTION_REGISTER
            return None
        if os.environ.get("SMARTBAR_RECAPTURE") == "off":
            return None
        if model.needs_recapture(snapshot) \
                and self._due(self._last_heal, HEAL_COOLDOWN, now):
            self._last_heal = now
            return ACTION_HEAL
        return None

    @staticmethod
    def _due(last, interval: float, now: float) -> bool:
        return last is None or now - last >= interval
