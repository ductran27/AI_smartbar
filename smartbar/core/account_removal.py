"""One removal entry point for both providers — the shared guard rules.

Every UI removes an account through here (the Swift app via
`ai-smartbar --remove-account provider:id`, the painted trays in-process),
so "the active account is refused" and each provider's semantics exist
exactly once:

- ``claude:<slot>`` — `cswap remove` deletes the slot, its stored
  credential backup included; signing in as that account re-registers it.
- ``openai:<email>`` — the remembered card is dropped from smartbar's own
  registry; nothing under the codex home is touched, and signing in with
  Codex brings the card back.

Results come back as ``(ok, error)`` rather than raises because both the
CLI and the trays want a displayable answer, never a traceback.
"""
from __future__ import annotations

from smartbar.core import codex, cswap


def remove(spec: str) -> tuple:
    """Remove the account named by ``provider:identifier``.

    Returns ``(True, "")`` on success, ``(False, reason)`` otherwise —
    the reason is user-facing text (it lands in the popover's error line).
    """
    provider, _, identifier = (spec or "").partition(":")
    try:
        if provider == "claude":
            if not identifier.isdigit():
                # Emails can hit cswap's interactive ambiguity prompt when
                # one address fills two slots; the UIs always send numbers.
                return False, (f"claude removal wants a slot number, "
                               f"got {identifier!r}")
            cswap.remove_account(int(identifier))
        elif provider == "openai":
            if not identifier:
                return False, "openai removal wants an email"
            codex.remove_account(identifier)
        else:
            return False, (f"unknown provider {provider!r} "
                           "(want claude:<slot> or openai:<email>)")
    except (cswap.CswapError, ValueError, OSError) as exc:
        return False, str(exc)
    return True, ""
