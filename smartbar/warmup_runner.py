"""Warmup runner: one gate-checked pass over all registered accounts.

Invoked by `ai-smartbar --warmup-once` from launchd/cron every ~10 min.
Security: no credentials touched — `cswap run <n>` provides the account
context and the official `claude` CLI owns auth. Fixed one-char prompt,
one turn, output discarded, every attempt logged, failures notified
(first failure and the giving-up notice only — no half-hourly spam).

launchd quirk that broke v1: agents get a bare PATH, and cswap resolves
`claude` itself via shutil.which — so every subprocess env must carry a
PATH that contains the claude CLI. Everything after `--` in `cswap run`
is passed to claude as ARGUMENTS (cswap picks the binary), so the claude
path itself must never appear there.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from smartbar.core import branding, cswap, paths, portable, warmup
from smartbar.core.cswap import CswapError

CACHE_DIR = paths.cache_dir()
STATE_FILE = os.path.join(CACHE_DIR, "warmup-state.json")
LOCK_FILE = os.path.join(CACHE_DIR, "warmup.lock")
LOG_FILE = os.path.join(CACHE_DIR, "warmup.log")
PING_TIMEOUT = 120

log = logging.getLogger("ai-smartbar-warmup")


def claude_binary():
    """The `claude` CLI: env override, then PATH, then well-known installs.

    Both the fallback list and the test for "is this candidate runnable"
    are platform-specific. On POSIX `os.access(candidate, os.X_OK)` is a
    real permission check; Windows has no execute bit — a file is
    executable if its extension says so — so that test degrades into a
    clumsy existence check, and the POSIX candidate paths are wrong there
    anyway (nothing installs into /opt/homebrew/bin). Windows installs of
    the CLI land under %APPDATA%\\npm (a global npm install) or
    %LOCALAPPDATA%\\Programs\\claude (the packaged installer), named
    claude.cmd or claude.exe depending on which, so the win32 arm walks
    both directories against every extension PATHEXT lists and confirms
    with os.path.isfile — which is what actually matters there.
    """
    override = os.environ.get("SMARTBAR_CLAUDE")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    if sys.platform == "win32":
        directories = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            directories.append(os.path.join(appdata, "npm"))
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            directories.append(os.path.join(localappdata, "Programs", "claude"))
        exts = [ext for ext
                in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
                if ext]
        # Lower-cased because what npm and the installer actually write is
        # "claude.cmd", not PATHEXT's traditional upper case. On Windows the
        # case would not matter, but this suite also runs on case-SENSITIVE
        # CI filesystems, where an uppercase candidate misses the real file.
        for directory in directories:
            for ext in [""] + [e.lower() for e in exts]:
                candidate = os.path.join(directory, "claude" + ext)
                if os.path.isfile(candidate):
                    return candidate
        return None
    for candidate in (os.path.expanduser("~/.local/bin/claude"),
                      "/opt/homebrew/bin/claude",
                      "/usr/local/bin/claude"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def env_with_claude_on_path(claude: str) -> dict:
    """Subprocess env whose PATH is guaranteed to resolve `claude`.

    Prepends the resolved binary's own directory plus the usual install
    dirs to whatever PATH launchd handed us (deduplicated, order kept).

    Which dirs those are, and what to fall back to when PATH is missing
    altogether, are both platform-specific: launchd's bare "/usr/bin:/bin"
    default is a POSIX thing, and ~/.local/bin, /opt/homebrew/bin and
    /usr/local/bin are POSIX locations nothing on Windows ever populates.
    The win32 equivalent is the same %APPDATA%\\npm /
    %LOCALAPPDATA%\\Programs\\claude pair claude_binary() searches, with
    %SystemRoot% as the fallback instead of a path that resolves nothing.
    """
    env = dict(os.environ)
    claude_dir = os.path.dirname(os.path.abspath(claude))
    if sys.platform == "win32":
        prepend = [claude_dir]
        appdata = os.environ.get("APPDATA")
        if appdata:
            prepend.append(os.path.join(appdata, "npm"))
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            prepend.append(os.path.join(localappdata, "Programs", "claude"))
        default_path = os.environ.get("SystemRoot", "C:\\Windows")
    else:
        prepend = [claude_dir,
                   os.path.expanduser("~/.local/bin"),
                   "/opt/homebrew/bin", "/usr/local/bin"]
        default_path = "/usr/bin:/bin"
    parts = []
    for part in prepend + env.get("PATH", default_path).split(os.pathsep):
        if part and part not in parts:
            parts.append(part)
    env["PATH"] = os.pathsep.join(parts)
    return env


def ping_argv(account_number: int, extra: list) -> list:
    """`cswap run <n> -- <claude args>`. cswap resolves the claude binary
    itself; post-`--` tokens are claude's arguments only.

    --strict-mcp-config keeps the ping MCP-free: without it every warmup
    booted ALL user-scope MCP servers, and Serena's dashboard popped a
    browser tab on each unattended run (~10/day; found 2026-08-18). With
    the flag and no --mcp-config, zero MCP servers load — the ping only
    needs to start the 5h window, and it starts faster too."""
    return [cswap._binary(), "run", str(account_number), "--",
            *extra, "-p", ".", "--max-turns", "1", "--strict-mcp-config"]


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
    """Best-effort desktop notification, one native mechanism per platform.

    Every branch is subprocess.run(..., check=False) under a blanket
    `except OSError`, because a notification failing must never fail the
    warmup run itself. That swallowing is exactly what makes a MISSING
    branch dangerous: with no win32 arm the else branch shells out to
    notify-send, which is not there, and the FileNotFoundError it raises is
    an OSError the handler below eats silently — every warmup notification
    on Windows would vanish leaving nothing in the log to say why. The
    win32 arm drives the WinRT toast API through PowerShell: the closest
    dependency-free analogue to osascript, needing no extra package, only
    the interpreter every Windows box already ships.
    """
    if os.environ.get("SMARTBAR_WARMUP_NOTIFY", "") == "off":
        return
    try:
        if sys.platform == "darwin":
            script = ('display notification "{}" with title "{}"'
                      .format(body.replace('"', '\\"'), title.replace('"', '\\"')))
            subprocess.run(["/usr/bin/osascript", "-e", script],
                           timeout=10, check=False)
        elif sys.platform == "win32":
            # '' is how a single quote is escaped inside a PowerShell
            # single-quoted string; nothing else needs escaping there.
            quoted_title = title.replace("'", "''")
            quoted_body = body.replace("'", "''")
            script = (
                "[Windows.UI.Notifications.ToastNotificationManager, "
                "Windows.UI.Notifications, ContentType=WindowsRuntime] > $null; "
                "$xml = [Windows.UI.Notifications.ToastNotificationManager]"
                "::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$t = $xml.GetElementsByTagName('text'); "
                f"$t.Item(0).AppendChild($xml.CreateTextNode('{quoted_title}'))"
                " > $null; "
                f"$t.Item(1).AppendChild($xml.CreateTextNode('{quoted_body}'))"
                " > $null; "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                "[Windows.UI.Notifications.ToastNotificationManager]"
                "::CreateToastNotifier('AI smartbar').Show($toast)")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                            "-Command", script],
                           timeout=10, check=False, **portable.no_window())
        else:
            # -a/-i name the sender and give it the project's logo; the
            # WinRT arm above already names itself via CreateToastNotifier.
            # An icon-theme miss degrades to an iconless notification.
            subprocess.run(["notify-send", "-u", "normal",
                            "-a", branding.APP_NAME, "-i", branding.ICON_NAME,
                            title, body],
                           timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        # TimeoutExpired is a SubprocessError, not an OSError. Every branch
        # above sets timeout=10, so the bare OSError this replaces could not
        # catch a notifier that HANGS -- only one that fails to start. This
        # runs inside run_once()'s `for account in snap.accounts` loop, and a
        # session-less timer/cron run with no D-Bus daemon is exactly where
        # notify-send hangs, so the escape aborted the whole warmup batch
        # part-way through instead of skipping one notification.
        log.exception("notification failed")


def ping(account_number: int, claude: str) -> tuple[bool, str]:
    """One minimal message as the given account. (ok, detail)."""
    env = env_with_claude_on_path(claude)
    proc = None
    for extra in (["--model", "haiku"], []):  # haiku first, plain retry
        try:
            proc = subprocess.run(ping_argv(account_number, extra),
                                  capture_output=True, text=True,
                                  timeout=PING_TIMEOUT, env=env,
                                  **portable.no_window())
        except subprocess.TimeoutExpired:
            return False, f"ping timed out after {PING_TIMEOUT}s"
        except OSError as exc:
            return False, f"could not run ping: {exc}"
        if proc.returncode == 0:
            return True, "ok (haiku)" if extra else "ok"
        detail = (proc.stderr or proc.stdout or "").strip()[:160]
    return False, f"claude exited rc={proc.returncode}: {detail}"


def run_once() -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    # Bound to a local for the rest of the run on purpose: dropping the last
    # reference to the handle releases the lock (see core.portable.lock).
    lock = portable.lock(LOCK_FILE)
    if lock is None:
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
    state = load_state()
    warmup.prune_state(state, [a.email for a in snap.accounts], now)
    failures = 0

    for account in snap.accounts:
        # This account's OWN measurement time, never the snapshot's display
        # aggregate: cswap refreshes each slot on its own plan, so one shared
        # stamp both skipped every account whenever the stamped one happened
        # to be stale, and warmed accounts whose 5h window was hours old.
        ok, reason = warmup.should_warm(account, now, state,
                                        warmup.parse_iso(account.fetched_at))
        if not ok:
            log.info("skip #%s %s: %s", account.number, account.email, reason)
            continue
        warmup.record_attempt(state, account.email, now)
        save_state(state)  # persist BEFORE the ping: a crash must not re-ping
        sent, detail = ping(account.number, claude)
        if not sent:
            failures += 1
            streak = warmup.record_failure(state, account.email, now)
            save_state(state)
            log.error("warmup #%s %s failed: %s", account.number, account.email, detail)
            # Notify on the first failure of a streak and on the giving-up
            # notice; the streak gate silences everything in between.
            if streak == 1:
                notify_failure("AI smartbar warmup failed",
                               f"{account.email}: {detail}")
            elif streak == warmup.MAX_CONSECUTIVE_FAILURES:
                notify_failure("AI smartbar warmup paused",
                               f"{account.email}: {streak} failures in a row — "
                               "paused until tomorrow (see warmup.log)")
            continue
        warmup.record_success(state, account.email)
        save_state(state)
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
