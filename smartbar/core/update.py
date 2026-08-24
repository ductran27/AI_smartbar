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
MIN_CHECK_INTERVAL = 300.0         # a fetch per 5 min is already generous
MAX_REF_FAILURES = 3               # per UTC day, per target ref

UPDATE = "update"                  # UpdatePlan.action values
CURRENT = "current"
BLOCKED = "blocked"

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REF_ABBREV = 7                     # git's own default abbreviation

# Installers re-run after a successful checkout, in apply order: the UI
# this device actually runs, then the side agents (whose plist bodies only
# change on re-install), then the updater's own agent last.
APPLY_ORDER = ("macos_swift", "macos_python", "linux", "windows", "warmup",
               "update_agent")
INSTALLERS = {
    "macos_swift": "install/macos-swift.sh",
    "macos_python": "install/macos.sh",
    "linux": "install/linux.sh",
    "windows": "install/windows.ps1",
    "warmup": "install/macos-warmup.sh",
    "update_agent": "install/macos-update.sh",
}


def enabled() -> bool:
    return os.environ.get("SMARTBAR_UPDATE", "").strip().lower() != "off"


def channel(default: str = CHANNEL_RELEASE, fallback: str = "") -> str:
    """This device's channel: environment first, then `fallback`, then default.

    Same resolution order as check_interval, for a sharper reason: the two
    channels disagree about what "up to date" MEANS, so guessing wrong does
    not degrade an answer, it inverts it.

    The channel is deliberately not a config.env key (device_config.RESERVED)
    — it is baked into the agent the installers write. So a process launched
    BY that agent has it, and any other process has nothing: a manual
    `--check-update` from the popover, or a hand-run in a terminal, silently
    fell back to `release`. That is how a main-channel device answered
    "0.11.0 available" to its popover and "already up to date" to its own
    agent, in the same minute, from the same checkout — and then wrote the
    losing answer into the state file both of them read.

    `fallback` closes it: run_once passes the channel the previous run
    recorded, so an unconfigured caller inherits the device's real channel
    instead of assuming one. An explicit environment value still wins, so
    `--channel` and the agent's own plist keep overriding it.
    """
    raw = os.environ.get("SMARTBAR_UPDATE_CHANNEL", "").strip().lower()
    if raw in CHANNELS:
        return raw
    hint = (fallback or "").strip().lower()
    return hint if hint in CHANNELS else default


def check_interval(fallback: str = "") -> float:
    """Seconds between scheduled update checks, floored at MIN_CHECK_INTERVAL.

    Resolved at INSTALL time, because what it actually becomes is the
    LaunchAgent's StartInterval / the systemd timer's period / the crontab
    spacing — nothing reads it at runtime. Hence the order: this process's
    environment first (an explicit `SMARTBAR_UPDATE_INTERVAL=… ./install/…`),
    then `fallback`, which is the device's config.env value, then the default.

    The floor exists because the check is a `git fetch` against the remote,
    and a device hammering it every few seconds gains nothing.
    """
    for raw in (os.environ.get("SMARTBAR_UPDATE_INTERVAL"), fallback):
        try:
            return max(MIN_CHECK_INTERVAL, float(raw))
        except (TypeError, ValueError):
            continue
    return DEFAULT_CHECK_INTERVAL


def cron_spec(seconds: float) -> str:
    """A crontab time spec for "check roughly every `seconds`".

    The cron fallback is what a Linux box without a systemd user session gets,
    and cron has minute resolution and no notion of an interval — so this is
    the closest honest approximation. Sub-hourly becomes a minute step;
    anything else runs at a fixed offset past every Nth hour, and the offset is
    not zero so that every device does not wake on the hour together.
    """
    minutes = max(1, int(round((seconds or 0) / 60.0)))
    if minutes < 60:
        return f"*/{minutes} * * * *"
    return f"17 */{min(23, max(1, int(round(minutes / 60.0))))} * * *"


def minutes_spec(seconds: float) -> int:
    """Whole minutes for a Task Scheduler repetition interval.

    Task Scheduler's repetition trigger has no sub-minute resolution, so this
    is the Windows analogue of cron_spec — same rounding, same floor. The
    floor is defensive rather than the normal case: by the time a caller
    reaches this, check_interval() has already applied MIN_CHECK_INTERVAL
    (300 s / 5 min), so 1 only fires if that floor is ever lowered.
    """
    return max(1, int(round((seconds or 0) / 60.0)))


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
                failures=0, applied_ref=""):
    """Decide what (if anything) this device should check out.

    `reset` is the repair hammer and the ONLY flag allowed to discard local
    work; `force` re-applies the target even when already sitting on it.

    `applied_ref` is the sha the installers last ran for — see the main
    channel's use of it below. It is ignored on the release channel, where
    the target is a tag and `appliedVersion` already answers the same
    question.
    """
    if channel == CHANNEL_MAIN:
        if not state.remote_main:
            return UpdatePlan(BLOCKED, reason="origin/main not fetched")
        if state.branch != "main":
            return UpdatePlan(BLOCKED, reason=(
                "channel=main needs the main branch checked out (on "
                f"{state.branch or 'a detached HEAD'})"))
        target_ref, target_version, detach = state.remote_main, "", False
        # Sitting on the target sha is not the same as having BUILT it. An
        # update is checkout-then-install, and a checkout that arrives by
        # other means — a hand-run `git pull` on a dev box — satisfies the
        # first half only, leaving the installers (and so the macOS app
        # bundle) a commit behind while this reported "already up to date".
        # An UNRECORDED applied_ref still counts as converged: it means the
        # state file predates this field, not that the bundle is stale, and
        # the first apply after that backfills it.
        at_target = (bool(state.head) and state.head == state.remote_main
                     and applied_ref in ("", state.head))
    else:
        tag = newest_tag(state.tags)
        if tag is None:
            return UpdatePlan(BLOCKED, reason="no vX.Y.Z release tags found")
        target_ref, target_version, detach = tag, tag.lstrip("v"), True
        # Same built-vs-checked-out rule as channel=main: an apply that died
        # between the checkout and the installers (power loss mid-build, the
        # agent killed) leaves HEAD at the tag with the OLD app installed —
        # "tag in head_tags" alone then said "already up to date" forever.
        # An unrecorded applied_ref still counts as converged (a state file
        # that predates the field; the first apply backfills it).
        at_target = (tag in (state.head_tags or ())
                     and applied_ref in ("", state.head))
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

    # --reset behaves like force here: the README sells it as "discard local
    # drift and re-install from scratch", and a device already sitting on
    # the target is exactly where a re-install is being asked for.
    if at_target and not force and not reset:
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


def short_ref(ref: str) -> str:
    """A full sha abbreviated for display; anything else returned as-is.

    Deliberately narrow: on channel=release `pendingRef` holds a TAG, and
    truncating `v0.11.0` to `v0.11.` would be worse than not shortening at all.
    """
    return ref[:REF_ABBREV] if _FULL_SHA.match(ref) else ref


def pending_version(state: dict) -> str:
    """What a UI should offer to move to, or "" — from the runner's state.

    Usually a release ("0.4.0"). On channel=main it is a short sha, because
    that channel's target IS a commit: ui_state leaves `pendingVersion` empty
    there and puts the target in `pendingRef`. Reading only the version field
    meant every front-end — the Mac popover, both trays, the preview — told a
    main-channel device it was up to date while its own updater had already
    decided to rebuild it, so the upgrade button and the icon badge could
    never appear on that channel at all.

    No "already running this" guard here, unlike the Mac's reader: these UIs
    run FROM the checkout, so there is no built artifact that can lag it.
    """
    pending = state.get("pendingVersion") or ""
    if isinstance(pending, str) and pending:
        return pending
    ref = state.get("pendingRef") or ""
    return short_ref(ref) if isinstance(ref, str) else ""


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
                  failed: bool = False, ran: bool = True,
                  current: str = "", disabled: bool = False) -> CheckOutcome:
    """Turn a finished manual check into something honest to show.

    `ran` exists because a check that never happened must not be reported as
    "up to date". `update_runner.run_once` returns 0 both when a device really
    is current AND when another update run already holds the lock (or updates
    are switched off) — so the caller compares the state file's checkedAt
    before and after, and passes ran=False when nothing moved.

    `current` exists because "available" has to mean CLICKABLE. Every front
    end refuses to offer an upgrade to the version it is already running —
    that is a no-op that would restart the app for nothing — so a plan naming
    that version leaves the user reading "⬆ 0.11.0 available" beside a footer
    with no button, and a notification pointing at a control that was never
    drawn. The suppression is decided HERE rather than in each UI so the
    wording and the rule cannot drift apart: if there is nothing to click,
    this must not say there is.
    """
    if disabled:
        # Distinct from every other outcome: with SMARTBAR_UPDATE=off,
        # run_once returns before writing state, and the checkedAt-moved
        # heuristic below misread that as "another run is in progress".
        return CheckOutcome("✕ Updates off", "AI smartbar",
                            "Updates are off on this device "
                            "(SMARTBAR_UPDATE=off in config.env).")
    if failed:
        return CheckOutcome("✕ Check failed", "AI smartbar",
                            "Could not check for updates. See "
                            "~/.cache/ai-smartbar/update.log")
    if not ran:
        return CheckOutcome("… Check busy", "AI smartbar",
                            "An update run is already in progress — "
                            "try again in a moment.")
    if pending and pending != current:
        # pendingVersion is only ever set for an actionable update (ui_state
        # clears it otherwise), so this cannot collide with `blocked`.
        #
        # The body names the control rather than the surface: this same text
        # is read on a Linux/Windows tray row, a cairo popover button and the
        # Mac's SwiftUI footer. It used to say "in the tray menu", which the
        # Mac app has never had — it sends the user looking for a menu item
        # that does not exist. "Update to X" is the one label all four
        # actually render.
        return CheckOutcome(f"⬆ {pending} available", "AI smartbar update",
                            f"{pending} is ready. Open AI smartbar and pick "
                            f"“Update to {pending}” to apply it now.",
                            found=True)
    if blocked:
        return CheckOutcome("✕ Update held back", "AI smartbar update",
                            "An update is waiting but held back: " + blocked)
    return CheckOutcome("✓ Up to date", "AI smartbar",
                        "No new release — this device is current.")


def ui_state(plan, version: str, now=None, applied: str = "",
             applied_ref: str = "", channel: str = "") -> dict:
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
    if channel:
        # The device's own answer to "which channel am I on", left where the
        # next run can find it. Only the agent's process reliably HAS the
        # setting (it is baked into the agent, not into config.env), so
        # recording it here is what lets a manual check resolve the same
        # channel instead of assuming one — see channel()'s `fallback`.
        payload["channel"] = channel
    if applied:
        payload["appliedVersion"] = applied
        payload["appliedAt"] = payload["checkedAt"]
    if applied_ref:
        # The sha the installers just ran for. appliedVersion cannot stand in
        # for it on channel=main, where versions only move on a release and
        # so say nothing about which commit the bundle was built from.
        payload["appliedRef"] = applied_ref
    return payload
