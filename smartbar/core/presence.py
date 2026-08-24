"""Device presence: how many devices are live on each Claude account.

Pure policy — no subprocess, no network, no clock of its own; every
function is handed `now`. smartbar/presence_runner.py collects the facts
(what the remote lists, what our own snapshot says) and executes what
comes back, exactly as update_runner.py does for core/update.py.

WHY A GIT REF. These devices share no server, and the count is only
useful if it is right when the machines are on *different networks* —
which rules out LAN discovery. The one authenticated channel every device
already has is the repo itself: install/linux.sh and install/macos-update.sh
both refuse to install an updater until they have proven `git ls-remote`
works with no prompt. So each device parks one ref under refs/smartbar/,
and the whole payload lives in the ref NAME:

    refs/smartbar/p1/<device>/<label>/<epoch>/<active-account-key>

pointing at a sha the REMOTE already has. Consequences that make this
cheap enough to run every five minutes forever:

  * the push transfers zero objects, so it adds nothing to the repo and
    never gives `git gc` anything to do;
  * reading is one `git ls-remote` — no fetch, nothing written locally;
  * ref names are disjoint per device, so two devices cannot conflict;
  * refs/smartbar/* is outside refs/heads and refs/tags: invisible in
    GitHub's UI, not fetched by a clone, untouched by `fetch --tags
    --prune`, and invisible to install/release.sh's clean+synced gate.

Emails never leave the machine — a ref carries sha256(email)[:16], and
the mapping back to an address happens locally against accounts this
device already knows.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sys
import uuid
from dataclasses import dataclass

SCHEMA = "p1"
NAMESPACE = "refs/smartbar/" + SCHEMA + "/"
GLOB = "refs/smartbar/*"           # the only pattern ls-remote is asked for

DEFAULT_INTERVAL = 300.0           # seconds between heartbeats
FUTURE_MAX = 86400.0               # a clock this far ahead is not believed
DEAD_AFTER = 2592000.0             # 30 days unheard-from: the ref is litter
MAX_SWEEP = 20                     # litter cleared per beat (keeps pushes small)
KEY_LEN = 16                       # hex chars of the account hash
LABEL_MAX = 24
NO_ACTIVE = "-"                    # ref component when no slot is active

_NOT_LABEL = re.compile(r"[^a-z0-9-]+")
# Device ids are hex today (new_device_id), but the parser only needs each
# component to be lowercase, bounded and free of "/" — being stricter than
# that would make a device with any other id shape invisible to everyone.
_DEVICE = r"[a-z0-9]{1,32}"
_DEVICE_RE = re.compile("^" + _DEVICE + "$")
_REF_RE = re.compile(
    r"^refs/smartbar/{schema}/({device})/([a-z0-9-]{{1,{label}}})"
    r"/(\d{{1,12}})/({none}|[0-9a-f]{{{key}}})$".format(
        schema=SCHEMA, device=_DEVICE, label=LABEL_MAX, key=KEY_LEN,
        none=re.escape(NO_ACTIVE)))


def enabled() -> bool:
    """The kill switch for remote writes. "off", "0", "false" and "no" all
    disable — a user reaching for any usual falsy spelling must not keep
    publishing to the remote by surprise."""
    value = os.environ.get("SMARTBAR_PRESENCE", "").strip().lower()
    return value not in ("off", "0", "false", "no")


def interval() -> float:
    try:
        value = float(os.environ["SMARTBAR_PRESENCE_INTERVAL"])
        if not math.isfinite(value):
            raise ValueError(value)     # inf crashed every tray at int()
        return max(60.0, value)
    except (KeyError, ValueError):
        return DEFAULT_INTERVAL


def ttl() -> float:
    """How long a device counts after its last beat.

    Three missed beats by default. Floors: two LOCAL intervals (a window
    below that drops this device between its own heartbeats), and three
    DEFAULT intervals — because this window judges OTHER devices, whose
    cadence this device's config says nothing about. One machine setting
    SMARTBAR_PRESENCE_INTERVAL=60 used to compute a 180 s window and
    declare every default 300 s device dead for two minutes of each cycle.
    """
    beat = interval()
    floor = 3 * DEFAULT_INTERVAL
    try:
        value = float(os.environ["SMARTBAR_PRESENCE_TTL"])
        if not math.isfinite(value):
            raise ValueError(value)
        return max(2 * beat, floor, value)
    except (KeyError, ValueError):
        return max(3 * beat, floor)


def account_key(email: str) -> str:
    """Stable per-account id for the wire — never the address itself."""
    normal = (email or "").strip().lower()
    if not normal:
        return ""
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:KEY_LEN]


def new_device_id() -> str:
    return uuid.uuid4().hex[:12]


def valid_device_id(text) -> bool:
    """True for an id that can actually appear in a ref we publish.

    A stored id that fails this would silently make the device unable to
    announce itself, so the runner mints a fresh one instead.
    """
    return bool(_DEVICE_RE.match((text or "").strip()))


def platform_tag() -> str:
    """"mac" / "linux", to go in front of the hostname in a device's label.

    Deliberately part of the LABEL rather than a new component in the ref.
    The label is display-only — a device's identity is its id — so every
    existing p1 reader accepts a prefixed label unchanged. Adding a component
    would change the ref SHAPE, and an older device's decoder rejects a shape
    it does not know: it would quietly stop counting every upgraded device
    until it upgraded too, which is the exact undercount this feature exists
    to prevent. A cosmetic field is the right place for cosmetic information.
    """
    raw = sys.platform
    if raw == "darwin":
        return "mac"
    if raw.startswith("linux"):
        return "linux"
    if raw == "win32":
        return "win"
    return sanitize_label(raw)


def sanitize_label(text: str) -> str:
    """A hostname reduced to something git will accept in a ref name.

    Lowercase [a-z0-9-] only, which satisfies every git ref rule at once
    (no space, no ~^:?*[\\, no "..", no leading dot, no "@{") and dodges
    case-folding collisions on case-insensitive checkouts. "" becomes
    "device", so a machine with an unusable hostname still publishes.
    """
    base = (text or "").strip().lower().split(".")[0]
    clean = _NOT_LABEL.sub("-", base).strip("-")[:LABEL_MAX].strip("-")
    return clean or "device"


@dataclass
class Beacon:
    """One device's claim, decoded from a ref name."""
    device: str
    label: str
    epoch: int           # the publisher's UTC clock at beat time
    active: str          # account_key of its live slot, or "" for none
    ref: str


def encode_ref(device: str, label: str, epoch, active: str) -> str:
    return "{}{}/{}/{}/{}".format(NAMESPACE, device, sanitize_label(label),
                                  int(epoch), active or NO_ACTIVE)


def decode_ref(ref: str):
    """A Beacon, or None for anything that is not ours to read.

    Foreign, malformed and future-schema refs all decode to None rather
    than raising: the namespace is shared with whatever a later version
    writes, and an unreadable ref must never take a beat down.
    """
    match = _REF_RE.match((ref or "").strip())
    if not match:
        return None
    device, label, epoch, active = match.groups()
    return Beacon(device=device, label=label, epoch=int(epoch),
                  active="" if active == NO_ACTIVE else active, ref=ref)


def decode_all(refs) -> list:
    return [b for b in (decode_ref(r) for r in refs or ()) if b is not None]


def newest_by_device(beacons) -> dict:
    """One Beacon per device, the highest epoch winning.

    A device publishes by creating its new ref and deleting the old one in
    a single atomic push, but a push that half-lands (or a beat killed
    between the two) can leave a second ref behind. Collapsing by device
    here is what makes that leak cosmetic instead of a double count.
    """
    best = {}
    for beacon in beacons:
        current = best.get(beacon.device)
        if current is None or beacon.epoch > current.epoch:
            best[beacon.device] = beacon
    return best


def observe(beacons, previous, now) -> dict:
    """Track, on OUR clock, when each device's ref last changed.

    The epoch inside a ref is written by the publisher, so a device whose
    clock is slow looks permanently expired and would silently vanish from
    the count — an undercount, the failure mode that is hardest to notice.
    But a ref NAME that changed between two of our polls proves liveness
    without trusting anybody's clock, because only a live device rewrites
    it. That is the rescue is_live() falls back on.

    Only a ref that CHANGED between two of our reads records a time. Merely
    seeing one for the first time proves nothing — the ref of a machine
    that died a year ago looks exactly like the ref of one beating right
    now, so stamping first sight would resurrect every abandoned device for
    a full window after any fresh install or cleared cache.

    Devices that stopped publishing drop out of the returned map.
    """
    seen = {}
    for device, beacon in newest_by_device(beacons).items():
        before = previous.get(device) if isinstance(previous, dict) else None
        record = {"ref": beacon.ref}
        if isinstance(before, dict):
            if before.get("ref") != beacon.ref:
                record["at"] = now                       # watched it move
            elif before.get("at") is not None:
                record["at"] = before["at"]              # keep what we had
        seen[device] = record
    return seen


def is_live(beacon, now, seen, window) -> bool:
    """Is this device still using the account it claims?

    Two independent tests, either one sufficient:
      * its own epoch is inside the window (a mildly fast clock reads as a
        negative age and still counts — but one a day ahead is treated as
        broken, or a device with a wrecked RTC would count forever);
      * we watched its ref change inside the window, which needs no
        agreement about what time it is.
    """
    age = now - beacon.epoch
    if -FUTURE_MAX <= age <= window:
        return True
    record = (seen or {}).get(beacon.device)
    if not isinstance(record, dict) or record.get("at") is None:
        return False
    try:
        return (now - float(record["at"])) <= window
    except (TypeError, ValueError):
        return False


def live_devices(beacons, now, seen, window) -> list:
    """Every device still counted, newest ref per device, ordered by label."""
    live = [b for b in newest_by_device(beacons).values()
            if is_live(b, now, seen, window)]
    return sorted(live, key=lambda b: (b.label, b.device))


def device_counts(live, emails, self_device: str, self_active: str) -> dict:
    """{email: devices currently on it}, for the accounts THIS device knows.

    Our own beacon is ignored and replaced by `self_active` from the live
    snapshot, so the number stays right on a device whose credential can
    read the remote but not push to it — it still sees everyone else, and
    never has to wait for its own ref to round-trip.

    Accounts nobody is on are absent rather than zero: the renderer draws
    no badge, and an absent badge cannot be mistaken for a measured zero.
    """
    by_key = {}
    for beacon in live:
        if beacon.device == self_device or not beacon.active:
            continue
        by_key[beacon.active] = by_key.get(beacon.active, 0) + 1
    if self_active:
        by_key[self_active] = by_key.get(self_active, 0) + 1
    counts = {}
    for email in emails or ():
        total = by_key.get(account_key(email), 0)
        if total > 0:
            counts[email] = total
    return counts


def own_stale_refs(beacons, self_device: str, keep: str) -> list:
    """Our superseded refs — the delete half of the atomic replace.

    Deliberately kept apart from litter(): these two go in SEPARATE pushes,
    because an atomic push fails as a unit and a housekeeping delete that
    loses a race must never be able to stop this device republishing itself.
    """
    return [b.ref for b in beacons
            if b.device == self_device and b.ref != keep]


def litter(beacons, self_device: str, now) -> list:
    """Other devices' refs abandoned long enough to be junk.

    A month of silence, which no sleeping laptop survives; without this the
    namespace grows by one ref for every machine ever retired. Capped so a
    single beat can never turn into an enormous push.
    """
    old = sorted(b.ref for b in beacons
                 if b.device != self_device and (now - b.epoch) > DEAD_AFTER)
    return old[:MAX_SWEEP]


def counts_for_state(fresh, previous, checked_at, now, window):
    """What the UI should show, given whether this beat could read the remote.

    A failed read is not the same as an empty answer. While the last good
    read is still inside the window it remains the best estimate, so it
    stands; once it ages out we stop claiming to know and every badge
    disappears. Showing "(1)" from local knowledge alone would be the one
    genuinely dishonest option — it asserts "only this device" precisely
    when we cannot see the others.
    """
    if fresh is not None:
        return fresh
    if checked_at and (now - checked_at) <= window:
        return previous or {}
    return {}


def apply_counts(snapshot, counts) -> None:
    """Stamp accounts with their device count, for model.account_label."""
    if snapshot is None:
        return
    for account in snapshot.accounts:
        account.devices = int((counts or {}).get(account.email, 0) or 0)
