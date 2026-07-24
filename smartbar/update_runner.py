"""Update runner: one gate-checked self-update pass.

Invoked by `ai-smartbar --update` from the update LaunchAgent / systemd
timer (every 6 h and at login), from the popover's upgrade button, or by
hand. All decisions live in smartbar/core/update.py; this file only
executes them — and it is deliberately TRANSACTIONAL, because a bad
release must never be able to leave a device with no menu bar at all: the
app bundle is backed up, and any install or verification failure rolls the
checkout and the binary back and restarts the old app.

The apply step re-runs whichever real installers this device has, since
they already rebuild, rewrite the launchd/systemd units and restart. That
also means agent-body changes (v3's baked warmup PATH, say) finally
propagate by themselves instead of needing the manual re-install the
README used to ask for.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from smartbar import update_git
from smartbar.core import update

CACHE_DIR = (os.environ.get("SMARTBAR_CACHE_DIR")
             or os.path.expanduser("~/.cache/ai-smartbar"))
STATE_FILE = os.path.join(CACHE_DIR, "update-state.json")
LOCK_FILE = os.path.join(CACHE_DIR, "update.lock")
LOG_FILE = os.path.join(CACHE_DIR, "update.log")

INSTALL_TIMEOUT = 1200   # a cold `swift build -c release` is genuinely slow
VERIFY_TIMEOUT = 30

HOME = os.path.expanduser("~")
APP_BUNDLE = os.path.join(HOME, "Applications", "AI_smartbar.app")
BUNDLE_BACKUP = APP_BUNDLE + ".prev"
AGENT_DIR = os.path.join(HOME, "Library", "LaunchAgents")
APP_LABEL = "com.ductran.ai-smartbar"
WARMUP_LABEL = APP_LABEL + ".warmup"
UPDATE_LABEL = APP_LABEL + ".update"

# Exit codes: 0 applied/current, 1 failed, 2 blocked by policy,
# 10 update available (--check-update only).
EXIT_BLOCKED = 2
EXIT_AVAILABLE = 10

log = logging.getLogger("ai-smartbar-update")


def load_state() -> dict:
    try:
        with open(STATE_FILE) as handle:
            state = json.load(handle)
        if isinstance(state, dict):
            state.setdefault("failures", {})
            return state
    except (OSError, ValueError):
        pass
    return {"failures": {}}


def save_state(state: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=".update-state-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, indent=1)
        os.replace(tmp, STATE_FILE)
    except OSError:
        log.exception("could not persist state")
        try:
            os.unlink(tmp)
        except OSError:
            pass


def notify(title: str, body: str) -> None:
    if os.environ.get("SMARTBAR_UPDATE_NOTIFY", "") == "off":
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


def _read_text(path: str) -> str:
    try:
        with open(path) as handle:
            return handle.read()
    except OSError:
        return ""


def present_installers() -> dict:
    """Which install shapes exist here — probed, never assumed.

    SMARTBAR_UPDATE_TARGETS overrides the probe with an explicit
    comma-separated list of installer keys. tests/e2e-update.sh sets it so a
    test run can never reach the real LaunchAgents of the machine it runs on.
    """
    override = os.environ.get("SMARTBAR_UPDATE_TARGETS", "").strip()
    if override:
        keys = [key.strip() for key in override.split(",") if key.strip()]
        return {key: True for key in keys if key in update.INSTALLERS}
    if sys.platform != "darwin":
        return {"linux": (
            os.path.exists(os.path.join(HOME, ".config/autostart/ai-smartbar.desktop"))
            or os.path.lexists(os.path.join(HOME, ".local/bin/ai-smartbar")))}
    body = _read_text(os.path.join(AGENT_DIR, APP_LABEL + ".plist"))
    swift = APP_BUNDLE in body
    python_ui = "ai-smartbar/venv" in body
    # Bundle present but its agent is gone: re-installing is the repair.
    if not swift and not python_ui and os.path.isdir(APP_BUNDLE):
        swift = True
    return {
        "macos_swift": swift,
        "macos_python": python_ui,
        "warmup": os.path.exists(os.path.join(AGENT_DIR, WARMUP_LABEL + ".plist")),
        "update_agent": os.path.exists(os.path.join(AGENT_DIR, UPDATE_LABEL + ".plist")),
    }


def backup_bundle() -> bool:
    if not os.path.isdir(APP_BUNDLE):
        return False
    shutil.rmtree(BUNDLE_BACKUP, ignore_errors=True)
    try:
        shutil.copytree(APP_BUNDLE, BUNDLE_BACKUP, symlinks=True)
    except OSError:
        log.exception("could not back up the app bundle")
        return False
    return True


def restore_bundle() -> None:
    if not os.path.isdir(BUNDLE_BACKUP):
        return
    shutil.rmtree(APP_BUNDLE, ignore_errors=True)
    try:
        shutil.move(BUNDLE_BACKUP, APP_BUNDLE)
    except OSError:
        log.exception("could not restore the app bundle")


def drop_backup() -> None:
    shutil.rmtree(BUNDLE_BACKUP, ignore_errors=True)


def agent_status(label: str):
    """(pid, last_exit_status) from `launchctl list`; (None, None) if absent."""
    try:
        proc = subprocess.run(["/bin/launchctl", "list", label],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if proc.returncode != 0:
        return None, None
    pid = last = None
    for line in proc.stdout.splitlines():
        found = re.search(r"=\s*(-?\d+)", line)
        if not found:
            continue
        if '"PID"' in line:
            pid = int(found.group(1))
        elif '"LastExitStatus"' in line:
            last = int(found.group(1))
    return pid, last


def kickstart(label: str) -> None:
    """Restart a LaunchAgent job — brings a restored binary back up."""
    subprocess.run(["/bin/launchctl", "kickstart", "-k",
                    f"gui/{os.getuid()}/{label}"],
                   capture_output=True, timeout=60, check=False)


def run_installer(key: str) -> str:
    """Re-run one installer. Returns "" on success, else the failure detail."""
    relative = update.INSTALLERS[key]
    script = os.path.join(update_git.REPO_ROOT, relative)
    if not os.access(script, os.X_OK):
        return f"{relative} is missing or not executable"
    env = update_git.env()
    # Signals install/macos-update.sh not to unload the very job we run in.
    env["SMARTBAR_UPDATE_APPLY"] = "1"
    try:
        proc = subprocess.run([script], cwd=update_git.REPO_ROOT, env=env,
                              capture_output=True, text=True,
                              timeout=INSTALL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{relative}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return f"{relative} exited {proc.returncode}: {detail}"
    log.info("ran %s", relative)
    return ""


def verify(targets) -> str:
    """"" when the updated device looks alive, else why it does not."""
    launcher = os.path.join(update_git.REPO_ROOT, "bin", "ai-smartbar")
    try:
        proc = subprocess.run([sys.executable, launcher, "--version"],
                              capture_output=True, text=True,
                              timeout=VERIFY_TIMEOUT, env=update_git.env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"launcher unusable: {exc}"
    if proc.returncode != 0:
        return (f"`ai-smartbar --version` exited {proc.returncode}: "
                + (proc.stderr or "").strip()[:160])
    if "macos_swift" in targets:
        binary = os.path.join(APP_BUNDLE, "Contents", "MacOS", "AISmartbar")
        if not os.access(binary, os.X_OK):
            return "app binary missing after build"
        # A binary that crashes on launch is the case that would brick the
        # menu bar: launchd then reports no PID and a non-zero exit status.
        for _ in range(6):
            pid, last = agent_status(APP_LABEL)
            if pid:
                return ""
            if last:
                return f"app exited immediately (status {last})"
            time.sleep(2)
        # No pid and no failure status: don't roll back a healthy update on
        # a launchctl reporting quirk — just say so in the log.
        log.warning("app agent reported no pid after restart")
    return ""


def _plan(repo, state, channel, force, reset):
    """Plan twice: the brake is keyed by target ref, which planning reveals."""
    provisional = update.plan_update(repo, channel=channel, force=force,
                                     reset=reset)
    failures = (update.failure_count(state, provisional.target_ref)
                if provisional.target_ref else 0)
    return update.plan_update(repo, channel=channel, force=force, reset=reset,
                              failures=failures)


def check_now():
    """One user-requested check, as an update.CheckOutcome.

    Both UIs call this (through `--check-update --json`) rather than deciding
    for themselves, so the wording and the honesty rules exist exactly once.
    That matters here more than usual: a second copy in Swift is how the macOS
    and Linux sides came to disagree about the presence staleness window.

    The `ran` argument is the whole reason this is a function and not four
    lines in a UI. run_once() returns 0 both when a device is genuinely
    current AND when another update run already holds the lock (or updates are
    switched off) — in which case it returns before writing anything. So the
    only proof a check actually looked is that checkedAt moved.
    """
    from smartbar.core import update as update_core
    before = str(load_state().get("checkedAt") or "")
    failed = False
    try:
        # run_once prints a human line to stdout; the caller wants only JSON
        # there, so send it to stderr where it still shows up in a log.
        with contextlib.redirect_stdout(sys.stderr):
            code = run_once(check_only=True)
        failed = code not in (0, EXIT_AVAILABLE)
    except Exception:
        log.exception("update check failed")
        failed = True
    state = load_state()
    blocked = (state.get("reason", "")
               if state.get("action") == "blocked" else "")
    return update_core.check_outcome(
        pending=update_core.pending_version(state), blocked=blocked,
        failed=failed, ran=str(state.get("checkedAt") or "") != before)


def run_once(*, reset: bool = False, force: bool = False,
             check_only: bool = False) -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not update.enabled():
        log.info("updates disabled (SMARTBAR_UPDATE=off)")
        return 0
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.info("another update run is in progress; skipping")
        return 0

    channel = update.channel()
    state = load_state()
    # The app bundle is a copy and does not know where the checkout lives;
    # this is how the popover's upgrade button finds the runner when a device
    # has opted out of the update agent.
    state["repoRoot"] = update_git.REPO_ROOT
    try:
        update_git.fetch()
        repo = update_git.repo_state()
    except update_git.GitError as exc:
        log.error("cannot read the repo: %s", exc)
        return 1

    plan = _plan(repo, state, channel, force, reset)
    state.update(update.ui_state(plan, repo.version))
    save_state(state)
    log.info("channel=%s head=%s v%s -> %s (%s)", channel, repo.head[:8],
             repo.version or "?", plan.action, plan.reason)

    if check_only:
        print(f"{plan.action}: {plan.reason}")
        return EXIT_AVAILABLE if plan.should_apply else 0
    if not plan.should_apply:
        if plan.action == update.BLOCKED:
            log.warning("not updating: %s", plan.reason)
            print(f"blocked: {plan.reason}", file=sys.stderr)
            return EXIT_BLOCKED
        return 0

    targets = update.apply_targets(present_installers())
    log.info("applying %s via: %s", plan.target_ref,
             ", ".join(targets) or "no installers detected")
    prev_head, prev_branch = repo.head, repo.branch
    bundled = backup_bundle() if "macos_swift" in targets else False

    try:
        rescue = update_git.checkout(plan, reset=reset)
    except update_git.GitError as exc:
        streak = update.record_failure(state, plan.target_ref)
        save_state(state)
        log.error("checkout of %s failed (attempt %s): %s",
                  plan.target_ref, streak, exc)
        return 1
    if rescue:
        log.warning("local work parked — recover with: git stash apply %s", rescue)
    elif reset and repo.dirty:
        log.error("--reset could not park local changes (git stash create "
                  "failed); they are gone")

    failure = ""
    for key in targets:
        failure = run_installer(key)
        if failure:
            break
    if not failure:
        failure = verify(targets)

    if failure:
        log.error("update to %s failed: %s — rolling back", plan.target_ref, failure)
        update_git.restore(prev_head, prev_branch)
        if bundled:
            restore_bundle()      # the previous binary, already known good
            kickstart(APP_LABEL)
        else:
            for key in targets:   # cheap for the non-Swift shapes
                run_installer(key)
        streak = update.record_failure(state, plan.target_ref)
        state.update(update.ui_state(plan, update_git.version_in_checkout()))
        save_state(state)
        notify("AI smartbar update failed",
               f"{plan.target_ref}: {failure[:110]} — rolled back "
               f"(attempt {streak} of {update.MAX_REF_FAILURES})")
        return 1

    drop_backup()
    new_version = update_git.version_in_checkout()
    update.clear_failures(state, plan.target_ref)
    state.update(update.ui_state(_plan(update_git.repo_state(), state, channel,
                                       False, False),
                                 new_version, applied=new_version))
    save_state(state)
    log.info("updated to %s (version %s)", plan.target_ref, new_version)
    notify("AI smartbar updated",
           f"Now on {plan.target_version or plan.target_ref[:8]} — app restarted")
    return 0
