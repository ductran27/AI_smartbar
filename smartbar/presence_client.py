"""The UI side of presence: spawn a beat, read the counts it left behind.

Kept out of the tray so both Python UIs get this in a few lines each, and
so the rule that a UI NEVER speaks git stays visible: everything here
either launches bin/ai-smartbar or reads a small JSON file.

The beat is spawned detached and never waited on. It talks to the network,
and the caller is a GTK main loop or a menu-bar callback — a slow remote
must not be able to freeze the thing the user is actually looking at.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time

from smartbar.core import portable, presence

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.realpath(os.path.abspath(__file__))))
LAUNCHER = os.path.join(REPO_ROOT, "bin", "ai-smartbar")

log = logging.getLogger("ai-smartbar")


def counts() -> dict:
    """{email: devices on it} from the last beat; {} when we cannot know.

    A cheap local file read, safe to call on every poll. An empty result
    is the honest answer for "the remote was never reachable", and it
    renders as no badge at all rather than a made-up "(1)".

    Opting out hides the badges immediately rather than leaving whatever
    the last beat happened to write on screen for ever.
    """
    if not presence.enabled():
        return {}
    try:
        from smartbar import presence_runner
        state = presence_runner.load_state()
        # The same freshness rule the runner applies, enforced again here:
        # the file on disk may be from a session that ended yesterday, and
        # it keeps sitting there if beats stop happening at all (no git,
        # say). Counts older than the window are not an answer.
        checked = float(state.get("checkedAt") or 0)
        if not checked or (time.time() - checked) > presence.ttl():
            return {}
        found = state.get("counts")
        return found if isinstance(found, dict) else {}
    except Exception:
        log.exception("could not read the presence state")
        return {}


def _spawn(args, payload: str) -> None:
    try:
        # portable.spawn_detached picks start_new_session=True on POSIX (the
        # byte-identical behaviour this had before) or the DETACHED_PROCESS /
        # CREATE_NEW_PROCESS_GROUP pair on win32, where start_new_session is
        # not a valid Popen keyword at all.
        proc = portable.spawn_detached([sys.executable, LAUNCHER, *args],
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
    except OSError:
        log.exception("could not start %s", " ".join(args))
        return
    try:
        proc.stdin.write(payload.encode("utf-8"))
        proc.stdin.close()
    except (OSError, ValueError):
        log.info("presence beat closed its input early")


def beat(snapshot=None) -> None:
    """Announce this device, handing over the accounts we already fetched.

    Passing them on stdin is what keeps a heartbeat free of usage-API
    traffic: the beat would otherwise have to run cswap itself just to
    learn which slot is active.
    """
    if not presence.enabled():
        return
    active = snapshot.active_account if snapshot is not None else None
    _spawn(["--presence-beat"], json.dumps({
        "active": active.email if active is not None else "",
        "accounts": [a.email for a in snapshot.accounts] if snapshot else [],
    }))


def leave() -> None:
    """Withdraw this device so the others stop counting it at once.

    Only for a deliberate quit. It is NOT wired to SIGTERM: the updater
    stops and restarts the UI, and a withdrawal racing the new instance's
    first beat could delete the ref that instance had just published,
    hiding a device that never actually went away.
    """
    if not presence.enabled():
        return
    _spawn(["--presence-leave"], "")
