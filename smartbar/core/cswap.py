"""Thin subprocess wrapper around the claude-swap CLI (the data engine)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from .model import Account, Metric, Snapshot

TIMEOUT = 30
PRIMER_TIMEOUT = 25

# Force-freshen claude-swap's usage store before a list, using its own
# auto-engine collector convention: an explicit fetch set makes the store's
# atomic reserve() use stale-OR-plan-due eligibility, which is the sanctioned
# way to beat the 3-min serve TTL (it harvests the 60s "urgent" plans near
# the limit and refreshes alternates parked on long plans). A fresh and
# not-yet-due account is still served from the store, so the sustained
# per-token API rate can never exceed claude-swap's measured budget.
# Runs under the pipx venv python (see venv_python); any failure is
# non-fatal — the follow-up `cswap list` serves last-good data regardless.
# Keep in sync with the copy in macos-swift CswapClient.primerCode.
PRIMER_CODE = """\
import sys
try:
    from claude_swap.switcher import ClaudeAccountSwitcher
    switcher = ClaudeAccountSwitcher()
    numbers = {a.number for a in switcher.accounts_snapshot(fetch=set()).accounts}
    if numbers:
        switcher.accounts_snapshot(fetch=numbers)
except Exception as exc:
    sys.stderr.write("primer: %s\\n" % exc)
    sys.exit(1)
"""


class CswapError(Exception):
    """Any failure talking to or parsing output from cswap."""


def _binary() -> str:
    override = os.environ.get("SMARTBAR_CSWAP")
    if override:
        return override
    found = shutil.which("cswap")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/cswap")
    if os.path.exists(fallback):
        return fallback
    raise CswapError("cswap binary not found (install claude-swap)")


def venv_python() -> str | None:
    """Interpreter that can import claude_swap, or None.

    pipx installs cswap as a launcher whose exec line names the venv python
    (`'exec' '/…/pipx/venvs/claude-swap/bin/python' …`); parse it out.
    SMARTBAR_CSWAP_PYTHON overrides. None (compiled binary, mock script,
    moved venv) simply disables the primer — never an error.
    """
    override = os.environ.get("SMARTBAR_CSWAP_PYTHON")
    if override:
        return override
    try:
        with open(_binary(), "rb") as handle:
            head = handle.read(512).decode("utf-8", errors="ignore")
    except (OSError, CswapError):
        return None
    match = re.search(r"'(/[^']*/bin/python[^']*)'", head)
    if match and os.path.exists(match.group(1)):
        return match.group(1)
    return None


def prime_fresh() -> bool:
    """Best-effort store freshen (see PRIMER_CODE); True when it ran clean."""
    python = venv_python()
    if python is None:
        return False
    try:
        proc = subprocess.run([python, "-c", PRIMER_CODE], capture_output=True,
                              text=True, timeout=PRIMER_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _run(args):
    try:
        proc = subprocess.run([_binary(), *args], capture_output=True,
                              text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise CswapError(f"cswap {' '.join(args)} timed out after {TIMEOUT}s") from exc
    except OSError as exc:
        raise CswapError(f"failed to run cswap: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        raise CswapError(f"cswap {' '.join(args)} failed (rc={proc.returncode}): {detail}")
    return proc.stdout


def _metric(key, label, short, raw) -> Metric:
    return Metric(key=key, label=label, short=short,
                  pct=float(raw.get("pct", 0.0)),
                  resets_at=raw.get("resetsAt", ""),
                  countdown=raw.get("countdown", ""),
                  clock=raw.get("clock", ""))


def parse_snapshot(text: str) -> Snapshot:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CswapError(f"cswap returned invalid JSON: {exc}") from exc
    snap = Snapshot()
    version = data.get("schemaVersion")
    if version != 1:
        snap.schema_warning = f"unexpected cswap schemaVersion {version!r}"
    for raw in data.get("accounts", []):
        usage = raw.get("usage")
        acct = Account(number=int(raw.get("number", 0)),
                       email=raw.get("email", "?"),
                       org=raw.get("organizationName", ""),
                       active=bool(raw.get("active", False)),
                       ok=raw.get("usageStatus") == "ok" and isinstance(usage, dict))
        if acct.ok:
            if "fiveHour" in usage:
                acct.metrics.append(_metric("5h", "5h", "5h", usage["fiveHour"]))
            if "sevenDay" in usage:
                acct.metrics.append(_metric("7d", "7d", "7d", usage["sevenDay"]))
            for scoped in usage.get("scoped", []):
                name = scoped.get("name") or "?"
                acct.metrics.append(_metric(f"scoped:{name}", name,
                                            name[:1].upper() or "?", scoped))
        snap.accounts.append(acct)
        if not snap.fetched_at and raw.get("usageFetchedAt"):
            snap.fetched_at = raw["usageFetchedAt"]
    return snap


def fetch(fresh: bool = False) -> Snapshot:
    """Snapshot from `cswap list --json`; fresh=True primes the store first."""
    if fresh:
        prime_fresh()
    return parse_snapshot(_run(["list", "--json"]))


def switch(number: int) -> None:
    _run(["switch", str(number)])


def add() -> None:
    """Register the current login as a managed account.

    `cswap add` without a slot never prompts: a new account auto-assigns
    the next slot, an already-registered one just refreshes its stored
    credential, and a logged-out state fails cleanly ("Please log in
    first") — which callers treat as a normal skip.
    """
    _run(["add"])
