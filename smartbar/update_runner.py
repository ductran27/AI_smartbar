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
from smartbar.core import paths, portable, update

CACHE_DIR = paths.cache_dir()
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
        with open(STATE_FILE, encoding="utf-8") as handle:
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
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=1)
        os.replace(tmp, STATE_FILE)
    except OSError:
        log.exception("could not persist state")
        try:
            os.unlink(tmp)
        except OSError:
            pass


def pending_for_ui():
    """(version waiting, why one is held back) for a tray row — never raises.

    All three front-ends carried a byte-for-byte copy of this: load_state(),
    read "reason" when the action is blocked, then update.pending_version().
    None of it is toolkit-bound, so it had no reason to exist three times —
    and the macOS copy had already dropped the blocked half, which is how
    that front-end ended up unable to say why an update was being withheld.

    The broad except is deliberate and inherited: a truncated or absent state
    file must never be able to take a tray down over a menu row.
    """
    try:
        state = load_state()
        blocked = (state.get("reason", "")
                   if state.get("action") == update.BLOCKED else "")
        return update.pending_version(state), blocked
    except Exception:
        log.exception("could not read the update state")
        return "", ""


def _win32_notify(title: str, body: str) -> None:
    """Best-effort Windows balloon notification for the headless update pass.

    UNVERIFIED on real Windows: nothing in this checkout can spawn or
    inspect an actual win32 process, so this has only been exercised by
    mocking subprocess.run (see tests/test_notify_windows.py). Treat it as
    a first pass that still needs a real Windows run to confirm the balloon
    actually renders.

    Design constraints:

    * No new dependency. `windows-toasts` (or any other WinRT wrapper) is
      not installable in this repo's update model -- a self-update that
      first needs `pip install` to tell the user it ran is backwards -- so
      this shells out to PowerShell, which every Windows box already ships,
      the same zero-dependency move macOS's osascript and Linux's
      notify-send make a few lines away. System.Windows.Forms.NotifyIcon
      needs no AppUserModelID/Start-Menu-shortcut registration, unlike
      Windows.UI.Notifications.ToastNotificationManager, which routinely
      refuses to fire from a bare unregistered host process.
    * `title`/`body` are UNTRUSTED -- `body` in particular can carry an
      account label pulled from cswap's config, i.e. attacker-influenced
      data if that config is ever compromised. Neither is ever spliced into
      the PowerShell command *text*. They are appended as their own argv
      elements after `-Command`, which PowerShell binds positionally to
      `$args[0]`/`$args[1]` inside the script -- the same "argument, not
      characters-in-a-string" contract every other subprocess.run() argv
      slot already gets. There is no quoting/escaping step for a crafted
      title or body to break out of, because the two are never joined into
      one string PowerShell has to parse as code.
    * portable.no_window() suppresses the console flash a GUI-less update
      pass should never produce.
    * pwsh (7+) is preferred, falling back to the always-present
      powershell.exe (5.1); if neither is on PATH this logs and returns --
      same as a missing notify-send today -- rather than raising.
    * The whole body (including the shutil.which probe, not just the
      subprocess call) is one broad try/except. shutil.which() itself can
      raise on this codebase's own test harness -- tests fake win32 by
      setting `update_runner.sys.platform = "win32"`, but `sys` is a single
      process-wide module object, so that assignment is visible to every
      other importer of `sys` too, including the stdlib's own shutil,
      which then takes its win32 branch on a real macOS/Linux interpreter
      that has no `_winapi` module and throws AttributeError. That is a
      test-harness artifact, not something real Windows would ever do (its
      _winapi always exists there) -- but "notification failure must never
      break an update pass" has to hold under that artifact too, not just
      under the failures anticipated when this was written, so the catch
      here is intentionally Exception rather than a narrower tuple.
    """
    try:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            log.info("no PowerShell found; skipping Windows notification")
            return
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -AssemblyName System.Drawing; "
            "$n = New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon = [System.Drawing.SystemIcons]::Information; "
            "$n.Visible = $true; "
            "$n.ShowBalloonTip(10000, $args[0], $args[1], "
            "[System.Windows.Forms.ToolTipIcon]::Info); "
            "Start-Sleep -Milliseconds 5500; "
            "$n.Dispose()"
        )
        subprocess.run([shell, "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-Command", script,
                        title, body],
                       timeout=15, check=False, **portable.no_window())
    except Exception:
        log.exception("Windows notification failed")


def notify(title: str, body: str) -> None:
    if os.environ.get("SMARTBAR_UPDATE_NOTIFY", "") == "off":
        return
    try:
        if sys.platform == "darwin":
            script = ('display notification "{}" with title "{}"'
                      .format(body.replace('"', '\\"'), title.replace('"', '\\"')))
            subprocess.run(["/usr/bin/osascript", "-e", script],
                           timeout=10, check=False)
        elif sys.platform == "win32":
            _win32_notify(title, body)
        else:
            subprocess.run(["notify-send", "-u", "normal", title, body],
                           timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        # TimeoutExpired is a SubprocessError, NOT an OSError, so the bare
        # OSError this replaces let a hung osascript/notify-send escape. Both
        # calls above set timeout=, so the exception is reachable, and notify()
        # is called from run_once()'s failure arm where an escape loses the
        # exit code the caller acts on.
        log.exception("notification failed")


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _win_startup_shortcut() -> str:
    """Path D7 installs the Startup `.lnk` at, without shelling out for it.

    `[Environment]::GetFolderPath('Startup')` — what windows.ps1 itself uses
    to write the shortcut — is documented by Microsoft to resolve to exactly
    this path for the per-user Startup folder, so present_installers() can
    stat it directly instead of spawning a PowerShell child just to answer
    "is this device installed" on every single update pass.
    """
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", "AI smartbar.lnk")


def _win_task_file() -> str:
    """Path Task Scheduler stores the D7 "AI smartbar update" task at.

    Every registered task, regardless of which account created it, gets an
    XML definition under `%SystemRoot%\\System32\\Tasks` named after the
    task. Statting that file is the same "cheap, side-effect-free" probe as
    the Startup shortcut above, rather than a `schtasks /query` subprocess.
    """
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(system_root, "System32", "Tasks", "AI smartbar update")


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
    if sys.platform == "win32":
        # Either signal proves D7 ran here before; neither needs a child
        # process, which the linux arm below also avoids for the same
        # every-update-pass frequency reason.
        return {"windows": (os.path.exists(_win_startup_shortcut())
                            or os.path.exists(_win_task_file()))}
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


def restore_bundle() -> bool:
    """Put the known-good bundle back; True only if it is actually there after.

    Returns a bool for the same reason backup_bundle() above does -- the
    caller has to know. The rmtree() runs BEFORE the move that can fail, so
    a failed restore leaves no bundle at all, and run_once()'s rollback arm
    would otherwise walk straight on to kickstart() a LaunchAgent whose
    program it has just deleted. That is precisely the "a bad release must
    never leave a device with no menu bar" outcome this module exists to
    prevent, reached through the code written to prevent it.
    """
    if not os.path.isdir(BUNDLE_BACKUP):
        return False
    shutil.rmtree(APP_BUNDLE, ignore_errors=True)
    try:
        shutil.move(BUNDLE_BACKUP, APP_BUNDLE)
    except OSError:
        log.exception("could not restore the app bundle")
        return False
    return True


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
    if sys.platform == "win32":
        # Defensive, not live: run_once() only calls this when `bundled` is
        # true, and `bundled` can only be true when "macos_swift" is among
        # targets, which never happens on win32. Guarding anyway means the
        # win32 port keeps working even if that invariant ever changes,
        # instead of crashing on os.getuid() — a function win32 CPython
        # does not define at all.
        log.warning("kickstart() has no launchctl equivalent on win32")
        return
    try:
        subprocess.run(["/bin/launchctl", "kickstart", "-k",
                        f"gui/{os.getuid()}/{label}"],
                       capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        # agent_status(), run_installer() and verify() all already catch this
        # pair; this call did not. It is reached from run_once()'s rollback
        # arm, and an escape there skips the record_failure/save_state/notify
        # that follow it -- the update fails with nothing written down, no
        # notification, and a non-zero exit the caller never sees.
        log.exception("could not kickstart %s", label)


def run_installer(key: str) -> str:
    """Re-run one installer. Returns "" on success, else the failure detail."""
    relative = update.INSTALLERS[key]
    script = os.path.join(update_git.REPO_ROOT, relative)
    if script.endswith(".ps1"):
        # os.access(script, os.X_OK) is meaningless here: a `.ps1` carries
        # no executable bit on Windows, so an installer that is perfectly
        # runnable would fail this check every time. Existence is the only
        # thing worth asking; PowerShell itself decides whether it can run.
        if not os.path.isfile(script):
            return f"{relative} is missing"
        # pwsh (PowerShell 7+) is preferred when present; the bundled
        # `powershell.exe` (5.1) is what every stock Windows install has.
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return "neither pwsh nor powershell was found on PATH"
        argv = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", script]
    else:
        if not os.access(script, os.X_OK):
            return f"{relative} is missing or not executable"
        argv = [script]
    env = update_git.env()
    # Signals install/macos-update.sh not to unload the very job we run in.
    env["SMARTBAR_UPDATE_APPLY"] = "1"
    try:
        proc = subprocess.run(argv, cwd=update_git.REPO_ROOT, env=env,
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
    """"" when the updated device looks alive, else why it does not.

    The Windows half of this is a deliberate no-op, and that is the D6
    judgement call: unlike the macos_swift arm below, which reads a PID
    launchd already tracks for us via `agent_status`, nothing here has a PID
    for the win32 tray to begin with — D7 starts it from a Scheduled Task or
    a Startup shortcut, neither of which records one. The only stdlib-only
    proxies available (matching `tasklist` by image name, which any other
    `pythonw.exe` on the machine would also satisfy and prove nothing; or a
    WMI command-line query, slow enough on a cold query and fragile enough
    as a new subprocess surface that a transient failure there would look
    identical to a genuinely broken update) are not reliable enough to hang
    a rollback decision on. Reporting "dead" here when the tray is actually
    fine would trigger run_once()'s rollback of a perfectly good update —
    strictly worse than the alternative of occasionally missing a real
    crash — so this arm only logs and leaves the return value healthy.
    """
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
    if sys.platform == "win32" and "windows" in targets:
        # See the docstring above: no reliable stdlib-only tray liveness
        # check exists, so this is intentionally "trust --version and move
        # on" rather than inventing one that could false-positive a rollback.
        log.info("skipping tray liveness check on win32 (see verify() docstring)")
    return ""


def _plan(repo, state, channel, force, reset, applied_ref=None):
    """Plan twice: the brake is keyed by target ref, which planning reveals.

    `applied_ref` defaults to whatever the last successful apply recorded;
    the post-update re-plan passes the NEW head explicitly, because the
    state it is handed still carries the old one.
    """
    if applied_ref is None:
        applied_ref = str(state.get("appliedRef") or "")
    provisional = update.plan_update(repo, channel=channel, force=force,
                                     reset=reset, applied_ref=applied_ref)
    failures = (update.failure_count(state, provisional.target_ref)
                if provisional.target_ref else 0)
    return update.plan_update(repo, channel=channel, force=force, reset=reset,
                              failures=failures, applied_ref=applied_ref)


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
        failed=failed, ran=str(state.get("checkedAt") or "") != before,
        # What run_once just wrote as this checkout's version. A plan naming
        # it is an upgrade to where we already are, which every UI refuses to
        # draw a button for — so it must not be announced as available.
        current=str(state.get("currentVersion") or ""))


def run_once(*, reset: bool = False, force: bool = False,
             check_only: bool = False) -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not update.enabled():
        log.info("updates disabled (SMARTBAR_UPDATE=off)")
        return 0
    # Bound for the rest of this function's lifetime on purpose: closing the
    # handle — or dropping the last reference to it — releases the lock, so
    # it cannot be a throwaway expression. The original `lock = open(...)`
    # local kept the flock alive exactly the same way.
    lock_handle = portable.lock(LOCK_FILE)
    if lock_handle is None:
        log.info("another update run is in progress; skipping")
        return 0

    # State first: it carries the channel the last run resolved, which is the
    # only way a process launched WITHOUT the setting (the popover's manual
    # check, a hand-run in a terminal) can find the device's real channel.
    state = load_state()
    channel = update.channel(fallback=str(state.get("channel") or ""))
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
    state.update(update.ui_state(plan, repo.version, channel=channel))
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
            if restore_bundle():  # the previous binary, already known good
                kickstart(APP_LABEL)
            else:
                # Nothing to restart: kickstarting the agent now would only
                # respawn a job whose program is missing, in a loop.
                log.error("app bundle not restored — leaving the agent down")
        else:
            for key in targets:   # cheap for the non-Swift shapes
                run_installer(key)
        streak = update.record_failure(state, plan.target_ref)
        state.update(update.ui_state(plan, update_git.version_in_checkout(),
                                     channel=channel))
        save_state(state)
        notify("AI smartbar update failed",
               f"{plan.target_ref}: {failure[:110]} — rolled back "
               f"(attempt {streak} of {update.MAX_REF_FAILURES})")
        return 1

    drop_backup()
    new_repo = update_git.repo_state()
    new_version = update_git.version_in_checkout()
    update.clear_failures(state, plan.target_ref)
    state.update(update.ui_state(_plan(new_repo, state, channel, False, False,
                                       applied_ref=new_repo.head),
                                 new_version, applied=new_version,
                                 applied_ref=new_repo.head, channel=channel))
    save_state(state)
    log.info("updated to %s (version %s)", plan.target_ref, new_version)
    notify("AI smartbar updated",
           f"Now on {plan.target_version or plan.target_ref[:8]} — app restarted")
    return 0
