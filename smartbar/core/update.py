"""Self-update decision logic — pure functions, no subprocess, no I/O.

`smartbar/update_runner.py` collects the git/filesystem facts, hands them
here as a RepoState, and executes whatever UpdatePlan comes back. Keeping
every decision in this module means the parts that can do real damage —
clobbering uncommitted work, walking a device backwards, retrying a
poisoned release forever — are unit-tested without touching a real repo.

Channels:
  release (default) — the device tracks the newest `vX.Y.Z` tag and checks
      it out DETACHED on purpose: a consumer device is pinned to a
      release, and its HEAD says which one.
  main              — the device follows origin/main, fast-forward only.
      This is the development checkout's channel: it never rewrites
      history and never discards work in progress.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

CHANNEL_RELEASE = "release"
CHANNEL_MAIN = "main"
CHANNELS = (CHANNEL_RELEASE, CHANNEL_MAIN)

DEFAULT_CHECK_INTERVAL = 21600.0   # 6 h between scheduled checks
MAX_REF_FAILURES = 3               # per UTC day, per target ref

UPDATE = "update"                  # UpdatePlan.action values
CURRENT = "current"
BLOCKED = "blocked"

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

# Installers re-run after a successful checkout, in apply order: the UI
# this device actually runs, then the side agents (whose plist bodies only
# change on re-install), then the updater's own agent last.
APPLY_ORDER = ("macos_swift", "macos_python", "linux", "warmup", "update_agent")
INSTALLERS = {
    "macos_swift": "install/macos-swift.sh",
    "macos_python": "install/macos.sh",
    "linux": "install/linux.sh",
    "warmup": "install/macos-warmup.sh",
    "update_agent": "install/macos-update.sh",
}


def enabled() -> bool:
    return os.environ.get("SMARTBAR_UPDATE", "").strip().lower() != "off"


def channel(default: str = CHANNEL_RELEASE) -> str:
    raw = os.environ.get("SMARTBAR_UPDATE_CHANNEL", "").strip().lower()
    return raw if raw in CHANNELS else default


def check_interval() -> float:
    try:
        return max(300.0, float(os.environ["SMARTBAR_UPDATE_INTERVAL"]))
    except (KeyError, ValueError):
        return DEFAULT_CHECK_INTERVAL


def parse_version(text):
    """(major, minor, patch) from "v1.2.3"/"1.2.3", or None if not semver."""
    match = _SEMVER.match((text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def newest_tag(tags):
    """The highest-semver tag, ignoring anything that is not vX.Y.Z."""
    best = None
    for tag in tags or ():
        parsed = parse_version(tag)
        if parsed is not None and (best is None or parsed > best[0]):
            best = (parsed, tag)
    return best[1] if best else None


@dataclass
class RepoState:
    head: str = ""                                 # HEAD sha
    branch: str = ""                               # "" when detached
    dirty: bool = False                            # tracked files modified
    unpushed: int = 0                              # commits ahead of upstream
    tags: list = field(default_factory=list)       # all tags known locally
    head_tags: list = field(default_factory=list)  # tags pointing AT HEAD
    remote_main: str = ""                          # origin/main sha
    version: str = ""                              # __version__ in checkout


@dataclass
class UpdatePlan:
    action: str
    target_ref: str = ""
    target_version: str = ""
    reason: str = ""
    detach: bool = False    # release channel checks a tag out detached

    @property
    def should_apply(self) -> bool:
        return self.action == UPDATE


def plan_update(state, *, channel=CHANNEL_RELEASE, force=False, reset=False,
                failures=0):
    """Decide what (if anything) this device should check out.

    `reset` is the repair hammer and the ONLY flag allowed to discard local
    work; `force` re-applies the target even when already sitting on it.
    """
    if channel == CHANNEL_MAIN:
        if not state.remote_main:
            return UpdatePlan(BLOCKED, reason="origin/main not fetched")
        if state.branch != "main":
            return UpdatePlan(BLOCKED, reason=(
                "channel=main needs the main branch checked out (on "
                f"{state.branch or 'a detached HEAD'})"))
        target_ref, target_version, detach = state.remote_main, "", False
        at_target = bool(state.head) and state.head == state.remote_main
    else:
        tag = newest_tag(state.tags)
        if tag is None:
            return UpdatePlan(BLOCKED, reason="no vX.Y.Z release tags found")
        target_ref, target_version, detach = tag, tag.lstrip("v"), True
        at_target = tag in (state.head_tags or ())
        # Never walk a device backwards: a checkout whose own version is
        # newer than the newest tag is ahead of the release line (the dev
        # box between a version bump and its tag).
        local = parse_version(state.version)
        if not at_target and not reset and local is not None \
                and local > parse_version(tag):
            return UpdatePlan(CURRENT, target_ref=tag,
                              target_version=target_version,
                              reason=f"local {state.version} is ahead of "
                                     f"newest release {tag}")

    if at_target and not force:
        return UpdatePlan(CURRENT, target_ref=target_ref,
                          target_version=target_version,
                          reason="already up to date")
    if failures >= MAX_REF_FAILURES and not force:
        return UpdatePlan(BLOCKED, target_ref=target_ref,
                          target_version=target_version,
                          reason=f"{target_ref} failed {failures} times today "
                                 "— not retrying (use --force)")
    if not reset:
        if state.dirty:
            return UpdatePlan(BLOCKED, target_ref=target_ref,
                              target_version=target_version,
                              reason="working tree has local changes (commit "
                                     "them, or re-run with --reset)")
        if state.unpushed:
            return UpdatePlan(BLOCKED, target_ref=target_ref,
                              target_version=target_version,
                              reason=f"{state.unpushed} unpushed commit(s) "
                                     "(push them, or re-run with --reset)")
    return UpdatePlan(UPDATE, target_ref=target_ref,
                      target_version=target_version, detach=detach,
                      reason=("reset to " if reset else "update to ") + target_ref)


def apply_targets(present) -> list:
    """Installer keys to re-run after checkout, in apply order.

    Re-running the real installers IS the apply step: they rebuild, rewrite
    plists and restart, and every one of them is idempotent — so agent-body
    changes (like v3's baked warmup PATH) propagate without the manual
    re-install the README used to demand. `present` maps installer key ->
    whether that shape is installed here; the runner does the probing.
    """
    return [key for key in APPLY_ORDER if present.get(key)]


def _day(now=None) -> str:
    stamp = now or datetime.now(timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%d")


def failure_count(state: dict, ref: str, now=None) -> int:
    return int(state.get("failures", {}).get(_day(now), {}).get(ref, 0))


def record_failure(state: dict, ref: str, now=None) -> int:
    """Count a failed apply of `ref` today; returns the new streak.

    Only today's bucket is kept: yesterday's poison must not brake a
    device that a fixed release could rescue.
    """
    days = state.setdefault("failures", {})
    today = _day(now)
    bucket = days.setdefault(today, {})
    bucket[ref] = int(bucket.get(ref, 0)) + 1
    for stale in [key for key in days if key != today]:
        del days[stale]
    return bucket[ref]


def clear_failures(state: dict, ref: str, now=None) -> None:
    state.get("failures", {}).get(_day(now), {}).pop(ref, None)


def pending_version(state: dict) -> str:
    """The release a UI should offer, or "" — read from the runner's state."""
    pending = state.get("pendingVersion") or ""
    return pending if isinstance(pending, str) else ""


@dataclass
class CheckOutcome:
    """What to say after the user asked, by hand, whether an update is waiting.

    Three surfaces, because a tray menu closes the moment you click a row: the
    notification is the only immediate feedback, `label` is what the row says
    when the menu is next opened, and `found` lets a caller decide whether to
    do anything else.
    """
    label: str
    title: str
    body: str
    found: bool = False


def check_outcome(*, pending: str = "", blocked: str = "",
                  failed: bool = False, ran: bool = True) -> CheckOutcome:
    """Turn a finished manual check into something honest to show.

    `ran` exists because a check that never happened must not be reported as
    "up to date". `update_runner.run_once` returns 0 both when a device really
    is current AND when another update run already holds the lock (or updates
    are switched off) — so the caller compares the state file's checkedAt
    before and after, and passes ran=False when nothing moved.
    """
    if failed:
        return CheckOutcome("✕ Check failed", "AI smartbar",
                            "Could not check for updates. See "
                            "~/.cache/ai-smartbar/update.log")
    if not ran:
        return CheckOutcome("… Check busy", "AI smartbar",
                            "An update run is already in progress — "
                            "try again in a moment.")
    if pending:
        # pendingVersion is only ever set for an actionable update (ui_state
        # clears it otherwise), so this cannot collide with `blocked`.
        return CheckOutcome(f"⬆ {pending} available", "AI smartbar update",
                            f"{pending} is ready. Pick “⬆ Update to {pending}” "
                            "in the tray menu to apply it now.", found=True)
    if blocked:
        return CheckOutcome("✕ Update held back", "AI smartbar update",
                            "An update is waiting but held back: " + blocked)
    return CheckOutcome("✓ Up to date", "AI smartbar",
                        "No new release — this device is current.")


def ui_state(plan, version: str, now=None, applied: str = "") -> dict:
    """The JSON blob the UIs read to offer their one-click upgrade button."""
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload = {
        "currentVersion": version,
        "checkedAt": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": plan.action,
        "reason": plan.reason,
        "pendingVersion": plan.target_version if plan.should_apply else "",
        "pendingRef": plan.target_ref if plan.should_apply else "",
    }
    if applied:
        payload["appliedVersion"] = applied
        payload["appliedAt"] = payload["checkedAt"]
    return payload
