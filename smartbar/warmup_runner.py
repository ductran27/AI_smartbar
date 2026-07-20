"""Warmup runner: one gate-checked pass over all registered accounts.

Invoked by `ai-smartbar --warmup-once` from launchd/cron every ~10 min.
Security: no credentials touched — `cswap run <n>` provides the account
context and the official `claude` CLI owns auth. Fixed one-char prompt,
one turn, output discarded, every attempt logged, failures notified.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from smartbar.core import cswap, warmup
from smartbar.core.cswap import CswapError

CACHE_DIR = (os.environ.get("SMARTBAR_CACHE_DIR")
             or os.path.expanduser("~/.cache/ai-smartbar"))
STATE_FILE = os.path.join(CACHE_DIR, "warmup-state.json")
LOCK_FILE = os.path.join(CACHE_DIR, "warmup.lock")
LOG_FILE = os.path.join(CACHE_DIR, "warmup.log")
PING_TIMEOUT = 120

log = logging.getLogger("ai-smartbar-warmup")


def claude_binary():
    override = os.environ.get("SMARTBAR_CLAUDE")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (os.path.expanduser("~/.local/bin/claude"),
                      "/opt/homebrew/bin/claude"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {"days": {}, "last": {}}


def save_state(state: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=".warmup-state-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        log.exception("could not persist state")
        try:
            os.unlink(tmp)
        except OSError:
            pass


def notify_failure(title: str, body: str) -> None:
    if os.environ.get("SMARTBAR_WARMUP_NOTIFY", "") == "off":
        return
    try:
        if sys.platform == "darwin":
            script = ('display notification "{}" with title "{}"'
                      .format(body.replace('"', '\\"'), title.replace('"', '\\"')))
            subprocess.run(["/usr/bin/osascript", "-e", script],
                           timeout=10, check=False)
        else:
            subprocess.run(["notify-send", "-u", "normal", title, body],
                           timeout=10, check=False)
    except OSError:
        log.exception("notification failed")


def ping(account_number: int, claude: str) -> tuple[bool, str]:
    """One minimal message as the given account. (ok, detail)."""
    base = [cswap._binary(), "run", str(account_number), "--", claude]
    tail = ["-p", ".", "--max-turns", "1"]
    for extra in (["--model", "haiku"], []):  # haiku first, plain retry
        try:
            proc = subprocess.run(base + extra + tail, capture_output=True,
                                  text=True, timeout=PING_TIMEOUT)
        except subprocess.TimeoutExpired:
            return False, f"ping timed out after {PING_TIMEOUT}s"
        except OSError as exc:
            return False, f"could not run ping: {exc}"
        if proc.returncode == 0:
            return True, "ok" if not extra else "ok (haiku)"
        detail = (proc.stderr or proc.stdout or "").strip()[:160]
    return False, f"claude exited rc={proc.returncode}: {detail}"


def run_once() -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.info("another warmup run is in progress; skipping")
        return 0

    claude = claude_binary()
    if claude is None:
        log.error("claude CLI not found (set SMARTBAR_CLAUDE)")
        return 1
    try:
        snap = cswap.fetch()
    except CswapError as exc:
        log.warning("cswap fetch failed: %s", exc)
        return 1

    now = datetime.now(timezone.utc)
    fetched_at = warmup.parse_iso(snap.fetched_at)
    state = load_state()
    warmup.prune_state(state, [a.email for a in snap.accounts], now)
    failures = 0

    for account in snap.accounts:
        ok, reason = warmup.should_warm(account, now, state, fetched_at)
        if not ok:
            log.info("skip #%s %s: %s", account.number, account.email, reason)
            continue
        warmup.record_attempt(state, account.email, now)
        save_state(state)  # persist BEFORE the ping: a crash must not re-ping
        sent, detail = ping(account.number, claude)
        if not sent:
            failures += 1
            log.error("warmup #%s %s failed: %s", account.number, account.email, detail)
            notify_failure("AI smartbar warmup failed",
                           f"{account.email}: {detail}")
            continue
        # Verify the window actually started.
        verified = False
        try:
            fresh = cswap.fetch()
            fresh_account = next((a for a in fresh.accounts
                                  if a.email == account.email), None)
            verified = warmup.warmed_successfully(fresh_account,
                                                  datetime.now(timezone.utc))
        except CswapError as exc:
            log.warning("verification fetch failed: %s", exc)
        if verified:
            log.info("warmed #%s %s (%s) — 5h window started",
                     account.number, account.email, detail)
        else:
            # Ping went through but usage data does not show a window yet
            # (API/cache lag is common). Cooldown prevents a re-ping storm.
            log.warning("warmed #%s %s (%s) — window not visible yet",
                        account.number, account.email, detail)
    return 1 if failures else 0
