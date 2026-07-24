"""Git plumbing for device presence: read the namespace, replace our ref.

Split from presence_runner.py the same way update_git.py is split from
update_runner.py, and it reuses that module's hardened environment — a
beat runs from a menu-bar app with launchd's bare PATH and no TTY, so
GIT_TERMINAL_PROMPT=0 is what turns "prompt for a password forever" into
a fast, logged failure.

Nothing here raises. Presence is a nicety on top of a usage meter: every
failure returns None/False and the caller carries on with what it knew.
"""
from __future__ import annotations

import logging
import subprocess

from smartbar import update_git
from smartbar.core import presence

# Deliberately far below update_git's 180s: a beat is spawned by the UI on
# a timer, and one wedged on a dead network must not still be running when
# the next one is due.
TIMEOUT = 25
LEAVE_TIMEOUT = 8      # runs while the user is quitting the app

log = logging.getLogger("ai-smartbar-presence")


def _run(args, timeout=TIMEOUT):
    """git in the repo; None when it could not be run at all."""
    try:
        return subprocess.run(
            [update_git.git_binary(), "-C", update_git.REPO_ROOT, *args],
            capture_output=True, text=True, timeout=timeout,
            env=update_git.env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("git %s: %s", " ".join(args[:2]), exc)
        return None


def read_remote():
    """(remote HEAD sha, [presence refs]) in ONE round trip, or None.

    None means "we could not see the remote", which the caller must keep
    distinct from "the remote listed no devices" — an empty namespace is a
    real answer, an unreachable one is not, and conflating them would make
    every badge vanish on a flaky network.

    HEAD comes back from the same call because the ref we publish has to
    point at an object the REMOTE already has. Using our own HEAD instead
    would, on a checkout with unpushed commits, quietly upload that private
    work as a hidden ref.
    """
    proc = _run(["ls-remote", "origin", "HEAD", presence.GLOB])
    if proc is None or proc.returncode != 0:
        if proc is not None:
            log.info("ls-remote failed: %s",
                     (proc.stderr or proc.stdout or "").strip()[:160])
        return None
    head, refs = "", []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref == "HEAD":
            head = sha
        elif ref.startswith("refs/smartbar/"):
            refs.append(ref)
    return head, refs


def _push(refspecs, atomic=True, timeout=TIMEOUT) -> bool:
    if not refspecs:
        return True
    args = ["push"] + (["--atomic"] if atomic else []) + ["origin"] + refspecs
    proc = _run(args, timeout=timeout)
    if proc is not None and proc.returncode == 0:
        return True
    if proc is not None:
        log.info("push %s failed: %s", " ".join(refspecs)[:120],
                 (proc.stderr or proc.stdout or "").strip()[:160])
    return False


def publish(sha: str, create: str, delete) -> bool:
    """Replace this device's ref: create the new one, drop the superseded.

    Atomic so the namespace never shows this device twice, and never shows
    it not at all. The create is listed first so that the non-atomic retry
    — for a server that refuses atomic pushes — still creates before it
    deletes, leaving us present rather than absent if it half-lands.

    `sha` must have come from read_remote(): pushing a ref to an object the
    remote already has transfers no objects at all, which is what makes a
    beat every five minutes free.
    """
    if not sha or not create:
        return False
    refspecs = [f"{sha}:{create}"] + [f":{ref}" for ref in delete or ()]
    if _push(refspecs):
        return True
    return _push(refspecs, atomic=False)


def sweep(refs) -> None:
    """Best-effort deletion of abandoned refs; never atomic, never checked.

    Housekeeping only: another device may have cleaned the same ref up
    first, and losing that race must not cost us our own heartbeat — which
    is exactly why this is a separate push from publish().
    """
    for ref in refs or ():
        _push([f":{ref}"], atomic=False)


def withdraw(refs) -> bool:
    """Delete our refs on the way out, so others stop counting us at once.

    Without this a quit would linger for the whole TTL. Short timeout: this
    runs while the user is closing the app, and a slow network must not
    hold the process open.
    """
    refs = [ref for ref in refs or () if ref]
    if not refs:
        return True
    return _push([f":{ref}" for ref in refs], atomic=False,
                 timeout=LEAVE_TIMEOUT)
