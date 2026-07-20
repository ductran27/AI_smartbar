"""Thin subprocess wrapper around the claude-swap CLI (the data engine)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from .model import Account, Metric, Snapshot

TIMEOUT = 30


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


def fetch() -> Snapshot:
    return parse_snapshot(_run(["list", "--json"]))


def switch(number: int) -> None:
    _run(["switch", str(number)])
