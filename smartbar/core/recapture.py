"""Decides when to run `cswap add` so stored credentials never rot.

Why this exists: Anthropic rotates OAuth refresh tokens. Claude Code keeps
the LIVE login's grant current, but cswap's per-slot backup only holds
whatever was captured at `add` time — once the live grant rotates past it,
the backup dies with invalid_grant and the slot turns `relogin_required`
("the account is gone"). `cswap add` on an already-registered account
re-captures the live credential in place (local-only: keychain + config
read, backup write, dead-token state cleared), so running it regularly
keeps the current login's backup on the newest rotation, and running it
the moment the active slot reports a dead backup heals that slot.

Pure decision logic (monotonic clock in, action out) so it is unit-testable
and can be mirrored 1:1 by the Swift app. Callers execute the returned
action by running `cswap add` in the background.
"""
from __future__ import annotations

import os

from . import model

REGISTER_COOLDOWN = 600.0   # s; retry ceiling while add cannot succeed (logged out)
HEAL_COOLDOWN = 120.0       # s; retry ceiling for dead-credential healing
RECAPTURE_INTERVAL = 900.0  # s; periodic re-capture of the live login's credential

ACTION_REGISTER = "register"    # live login has no slot -> add registers it
ACTION_HEAL = "heal"            # active slot's stored credential is dead -> add re-captures
ACTION_REFRESH = "refresh"      # routine re-capture keeps the backup on the newest rotation


class RecapturePolicy:
    """Stateful pacing for the three `cswap add` triggers."""

    def __init__(self):
        self._last_register = None
        self._last_heal = None
        self._last_refresh = None

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
        if model.needs_recapture(snapshot):
            if self._due(self._last_heal, HEAL_COOLDOWN, now):
                self._last_heal = now
                self._last_refresh = now  # a heal IS a re-capture
                return ACTION_HEAL
            return None
        if self._last_refresh is None:
            # First healthy snapshot only sets the baseline: registration/
            # heal already covered anything urgent, so the first routine
            # re-capture can wait a full interval (and a fresh registration
            # is not immediately followed by a pointless second add).
            self._last_refresh = now
            return None
        if now - self._last_refresh >= RECAPTURE_INTERVAL:
            self._last_refresh = now
            return ACTION_REFRESH
        return None

    @staticmethod
    def _due(last, interval: float, now: float) -> bool:
        return last is None or now - last >= interval
