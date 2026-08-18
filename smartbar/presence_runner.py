"""One presence heartbeat: read the namespace, republish this device, save.

Invoked as `ai-smartbar --presence-beat` from whichever UI is running (the
Linux tray, the Swift app), every SMARTBAR_PRESENCE_INTERVAL seconds. The
UI never speaks git: it spawns this and reads the small JSON state file on
its normal poll, exactly the arrangement update_runner.py already uses for
the update badge. That is what keeps the Swift side a file reader instead
of a second implementation of all of this.

Because the beat is spawned by a UI that has JUST fetched, it takes the
account list on stdin rather than running cswap again — a heartbeat costs
one ls-remote and one push, and no usage-API traffic whatsoever.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import time
from datetime import datetime, timezone

from smartbar import presence_git
from smartbar.core import paths, portable, presence

CACHE_DIR = paths.cache_dir()
CONFIG_DIR = paths.config_dir()
STATE_FILE = os.path.join(CACHE_DIR, "presence-state.json")
LOCK_FILE = os.path.join(CACHE_DIR, "presence.lock")
LOG_FILE = os.path.join(CACHE_DIR, "presence.log")
ID_FILE = os.path.join(CONFIG_DIR, "device-id")
LOG_MAX = 200_000

log = logging.getLogger("ai-smartbar-presence")


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state() -> dict:
    try:
        with open(STATE_FILE) as handle:
            state = json.load(handle)
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def save_state(state: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=".presence-state-")
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, indent=1)
        os.replace(tmp, STATE_FILE)
    except OSError:
        log.exception("could not persist presence state")


def device_id() -> str:
    """This machine's id — minted once, then stable for good.

    Kept in ~/.config rather than the checkout or the cache, so that two
    clones on one machine are ONE device (a machine cannot be "using" an
    account twice), and so clearing caches does not mint a new identity
    that would haunt the namespace until its ref ages out.
    """
    try:
        with open(ID_FILE) as handle:
            existing = handle.read().strip()
        if presence.valid_device_id(existing):
            return existing
    except OSError:
        pass
    fresh = presence.new_device_id()
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".device-id-")
        with os.fdopen(fd, "w") as handle:
            handle.write(fresh + "\n")
        os.replace(tmp, ID_FILE)
    except OSError:
        log.warning("could not persist the device id; using a volatile one")
    return fresh


def device_label() -> str:
    """Short name shown by --presence-status, so "(3)" can be checked.

    Platform first, then the hostname: `mac-laptop`, `linux-thinkpad`. A
    beacon otherwise carries no hint of what a machine IS, so "which of my
    devices is that, and is my Linux box even in the loop?" could not be
    answered from the count at all — including by me, when checking whether
    this feature works across operating systems.

    SMARTBAR_PRESENCE_LABEL overrides the whole thing, prefix included (set
    it empty to publish nothing but "device" — the count works the same, only
    the diagnostic gets less readable).
    """
    raw = os.environ.get("SMARTBAR_PRESENCE_LABEL")
    if raw is not None:
        return presence.sanitize_label(raw)
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    # sanitize_label truncates, so the prefix is what survives on a machine
    # with a very long name — which is the part worth keeping.
    return presence.sanitize_label("{}-{}".format(presence.platform_tag(), host))


def _accounts():
    """(active email, all emails) — from the UI on stdin, else from cswap.

    isatty() guards the fallback: run by hand from a terminal, a blind
    stdin read would hang forever waiting for an EOF nobody will send.
    """
    payload = None
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                payload = json.loads(raw)
    except (OSError, ValueError):
        payload = None
    if isinstance(payload, dict):
        emails = [e for e in payload.get("accounts") or [] if isinstance(e, str)]
        active = payload.get("active") or ""
        if active and active not in emails:
            emails.append(active)
        return active, emails
    try:
        from smartbar.core import cswap
        snapshot = cswap.fetch()
    except Exception:
        log.info("no account list on stdin and cswap is unavailable")
        return "", []
    account = snapshot.active_account
    return (account.email if account else ""), [a.email for a in snapshot.accounts]


def _setup_log() -> None:
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX:
            os.remove(LOG_FILE)
    except OSError:
        pass
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")


def _lock():
    """The open handle, or None when another beat already holds it.

    Delegates the flock/msvcrt.locking split to core.portable.lock() so this
    module never touches `fcntl` — which does not exist on Windows — at all,
    module scope or otherwise.
    """
    return portable.lock(LOCK_FILE)


def _leave(state: dict) -> int:
    """Withdraw this device so the others stop counting it immediately.

    The ref we published is remembered in the state file precisely so this
    needs no ls-remote: it runs while the user is quitting the app.
    """
    ref = state.get("myRef") or ""
    if not ref:
        return 0
    ok = presence_git.withdraw([ref])
    if ok:
        state["myRef"] = ""
        state["published"] = False
        state["counts"] = {}
        save_state(state)
    log.info("withdrew %s: %s", ref, "ok" if ok else "FAILED")
    return 0 if ok else 1


def _report(state: dict, live, me: str, active: str) -> None:
    """The diagnostic behind `--presence-status`: why the badge says what it says.

    A count is only trustworthy if it can be checked, and on a screen "(2)"
    looks identical whether it is right or wrong — so name the devices.
    """
    counts = state.get("counts") or {}
    remote = state.get("remoteError") or "readable"
    publish = ("ok" if state.get("published")
               else "NOT PUBLISHED — the other devices cannot see this one")
    listed = ", ".join(f"{email} ({n})" for email, n in sorted(counts.items()))
    print('device    {} "{}"'.format(me, state.get("label", "")))
    print("remote    " + remote)
    print("publish   " + publish)
    print("cadence   beat {}s, counted for {}s after the last beat".format(
        int(presence.interval()), int(presence.ttl())))
    print()
    print("counts    " + (listed or "(nobody — or the remote was never read)"))
    print()
    print("live devices:")
    now = time.time()
    print("  {:<24} {:<34} this device".format(
        state.get("label", "?"), active or "(no active slot)"))
    for beacon in live:
        if beacon.device == me:
            continue
        known = next((email for email in counts
                      if presence.account_key(email) == beacon.active), "")
        print("  {:<24} {:<34} {}s ago".format(
            beacon.label, known or "(an account not on this device)",
            max(0, int(now - beacon.epoch))))


def run_once(*, leave: bool = False, report: bool = False) -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)
    _setup_log()
    if not presence.enabled():
        if report:
            print("presence is off (SMARTBAR_PRESENCE=off)")
        return 0
    lock = _lock()
    if lock is None:
        log.info("another beat is in progress; skipping")
        return 0

    state = load_state()
    if leave:
        return _leave(state)

    now = time.time()
    window = presence.ttl()
    me, label = device_id(), device_label()
    active_email, emails = _accounts()
    active_key = presence.account_key(active_email)

    remote = presence_git.read_remote()
    if remote is None:
        # Unreachable is NOT "nobody is there": hold the last good answer
        # while it is still inside the window, then stop claiming to know.
        held = presence.counts_for_state(None, state.get("counts"),
                                         state.get("checkedAt", 0), now, window)
        state.update({"counts": held, "remoteError": "cannot reach origin",
                      "updatedAt": _stamp(now)})
        save_state(state)
        log.info("remote unreachable; holding %d count(s)", len(held))
        if report:
            _report(state, [], me, active_email)
        return 1

    head, refs = remote
    beacons = presence.decode_all(refs)
    seen = presence.observe(beacons, state.get("seen"), now)
    live = presence.live_devices(beacons, now, seen, window)
    counts = presence.device_counts(live, emails, me, active_key)

    published = state.get("published", False)
    my_ref = state.get("myRef", "")
    if not report:
        new_ref = presence.encode_ref(me, label, int(now), active_key)
        published = presence_git.publish(
            head, new_ref, presence.own_stale_refs(beacons, me, new_ref))
        if published:
            my_ref = new_ref

    changed = counts != (state.get("counts") or {})
    # We are live by definition; our own ref is only in `live` when a beat
    # has already published one, so count it once either way.
    others = [b for b in live if b.device != me]
    state.update({"v": 1, "deviceId": me, "label": label,
                  "updatedAt": _stamp(now), "checkedAt": now,
                  "counts": counts, "seen": seen, "remoteError": "",
                  "published": published, "myRef": my_ref,
                  "liveDevices": len(others) + 1})
    save_state(state)
    if changed or not published:
        log.info("%d live device(s); counts=%s; published=%s",
                 state["liveDevices"], counts, published)
    if not report:
        presence_git.sweep(presence.litter(beacons, me, now))
    else:
        _report(state, live, me, active_email)
    return 0 if published or report else 1
