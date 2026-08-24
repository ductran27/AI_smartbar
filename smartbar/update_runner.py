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
import signal
import time
import urllib.request

from smartbar import update_git
from smartbar.core import branding, paths, portable, update

CACHE_DIR = paths.cache_dir()
STATE_FILE = os.path.join(CACHE_DIR, "update-state.json")
LOCK_FILE = os.path.join(CACHE_DIR, "update.lock")
LOG_FILE = os.path.join(CACHE_DIR, "update.log")

INSTALL_TIMEOUT = 1200   # a cold `swift build -c release` is genuinely slow
FETCH_FAILURE_NOTIFY_AT = 3   # consecutive failed fetches before notifying
VERIFY_TIMEOUT = 30
RELEASE_NOTES_TIMEOUT = 5    # a slow GitHub must never stall the update pass
RELEASE_NOTES_MAX_CHARS = 200

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
        for attempt in range(3):
            try:
                os.replace(tmp, STATE_FILE)
                break
            except PermissionError:
                # Windows: a tray holding the state file open blocks the
                # swap; a short retry beats silently keeping stale state.
                if attempt == 2:
                    raise
                time.sleep(0.2)
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
      one string PowerShell has to parse as code. The icon path is a third
      such slot ($args[2]) for the same reason, and it is Test-Path'd rather
      than trusted: an -Icon pointing at nothing throws out of the whole
      script, losing the notification the icon was only meant to decorate.
      GetHicon() leaks its handle by design here -- the process is about to
      exit, and DestroyIcon needs a P/Invoke declaration that would cost
      more than the handle does.
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
            "if ($args[2] -and (Test-Path -LiteralPath $args[2])) { try { "
            "$b = New-Object System.Drawing.Bitmap $args[2]; "
            "$n.Icon = [System.Drawing.Icon]::FromHandle($b.GetHicon()) "
            "} catch { } }; "
            "$n.Visible = $true; "
            "$n.ShowBalloonTip(10000, $args[0], $args[1], "
            "[System.Windows.Forms.ToolTipIcon]::Info); "
            "Start-Sleep -Milliseconds 5500; "
            "$n.Dispose()"
        )
        subprocess.run([shell, "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-Command", script,
                        title, body, branding.icon_path()],
                       timeout=15, check=False, **portable.no_window())
    except Exception:
        log.exception("Windows notification failed")


def notify(title: str, body: str) -> None:
    if os.environ.get("SMARTBAR_UPDATE_NOTIFY", "") == "off":
        return
    try:
        if sys.platform == "darwin":
            # Backslashes must be escaped BEFORE quotes: escaping quotes
            # first leaves a trailing "\" in the body/title free to escape
            # our own closing quote instead of staying a literal character,
            # breaking out of the AppleScript string.
            def _applescript_escape(text: str) -> str:
                return text.replace("\\", "\\\\").replace('"', '\\"')
            script = ('display notification "{}" with title "{}"'
                      .format(_applescript_escape(body), _applescript_escape(title)))
            subprocess.run(["/usr/bin/osascript", "-e", script],
                           timeout=10, check=False)
        elif sys.platform == "win32":
            _win32_notify(title, body)
        else:
            # -a/-i are what put this project's own name and logo on the
            # notification instead of leaving it anonymous. -i takes the
            # icon-theme name install/linux.sh publishes the asset under;
            # a theme miss degrades to no icon, which is what we had.
            subprocess.run(["notify-send", "-u", "normal",
                            "-a", branding.APP_NAME, "-i", branding.ICON_NAME,
                            title, body],
                           timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        # TimeoutExpired is a SubprocessError, NOT an OSError, so the bare
        # OSError this replaces let a hung osascript/notify-send escape. Both
        # calls above set timeout=, so the exception is reachable, and notify()
        # is called from run_once()'s failure arm where an escape loses the
        # exit code the caller acts on.
        log.exception("notification failed")


def _github_repo_from_url(url: str) -> str:
    """OWNER/REPO from a GitHub origin remote URL, or "" if it is not one.

    Ports install/release.sh's github_repo_from_url bash function: the
    updater runs this same derivation on every device (release.sh only ever
    runs it on the maintainer's machine), so it has to stand on its own.
    """
    for prefix in ("https://github.com/", "git@github.com:",
                   "ssh://git@github.com/"):
        if url.startswith(prefix):
            answer = url[len(prefix):]
            break
    else:
        return ""
    if answer.endswith(".git"):
        answer = answer[:-len(".git")]
    return answer if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", answer) else ""


def _fetch_release_notes(tag: str) -> str:
    """Best-effort GitHub release body for `tag`; "" on ANY failure.

    A GitHub outage, rate limit, or a private-repo device with no working
    credential must never block or fail the update pass over what is purely
    cosmetic text for a notification -- hence the blanket except.
    """
    try:
        origin = update_git.git("remote", "get-url", "origin", check=False)
        repo = _github_repo_from_url(origin)
        if not repo:
            return ""
        url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json",
                          "User-Agent": "ai-smartbar-updater"})
        with urllib.request.urlopen(request, timeout=RELEASE_NOTES_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return str(payload.get("body") or "").strip()
    except Exception:
        log.exception("could not fetch release notes for %s", tag)
        return ""


def _git_log_summary(prev_head: str, new_head: str) -> str:
    """One-line-per-commit summary of what channel=main just pulled in.

    channel=main has no GitHub Release object to ask about -- its target is
    a commit, not a tag -- so this stays entirely local and network-free.
    """
    return update_git.git("log", "--oneline", f"{prev_head}..{new_head}",
                          check=False)


def _release_notes(plan, channel: str, prev_head: str, new_head: str) -> str:
    """Short "what changed" summary for the update-applied notification.

    release: the tagged GitHub Release's notes, fetched over the network.
    main: a local `git log --oneline` between the two heads instead, since
    a bare commit has no Release object to ask.

    Wrapped in its own broad except on top of the two helpers already being
    careful: this is cosmetic text, and notify() runs unconditionally right
    after -- nothing here may ever be the reason that call is skipped.
    """
    try:
        if channel == update.CHANNEL_RELEASE:
            notes = _fetch_release_notes(plan.target_ref)
        else:
            notes = _git_log_summary(prev_head, new_head)
        notes = notes.strip()
        if len(notes) > RELEASE_NOTES_MAX_CHARS:
            notes = notes[:RELEASE_NOTES_MAX_CHARS - 1].rstrip() + "…"
        return notes
    except Exception:
        log.exception("could not build the release-notes summary")
        return ""


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


def run_installer(key: str, channel: str = "",
                  no_auto_update: bool = False) -> str:
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
    if no_auto_update and key in ("macos_swift", "macos_python", "linux",
                                  "windows"):
        # This device opted out of the update agent; a bare re-run of the
        # UI installer defaults AUTO_UPDATE=1 and would silently re-enrol it.
        argv.append("--no-auto-update")
    env = update_git.env()
    # Signals install/macos-update.sh not to unload the very job we run in.
    env["SMARTBAR_UPDATE_APPLY"] = "1"
    # The agent's own baked SMARTBAR_UPDATE_INTERVAL is the PREVIOUS value;
    # letting it through regenerated the timer with the old number, so an
    # interval change in config.env converged one update late.
    env.pop("SMARTBAR_UPDATE_INTERVAL", None)
    if channel:
        env["SMARTBAR_UPDATE_CHANNEL"] = channel
    kwargs = {}
    if sys.platform != "win32":
        # Own process group: on timeout, kill the INSTALLER'S CHILDREN too.
        # subprocess's timeout kills only bash and orphaned the `swift
        # build` underneath it — a CPU burner of the exact class the System
        # tab hunts.
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, cwd=update_git.REPO_ROOT, env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, **kwargs)
        try:
            out, err = proc.communicate(timeout=INSTALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            if sys.platform != "win32":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (OSError, PermissionError):
                    pass
            proc.kill()
            proc.communicate()
            return f"{relative}: timed out after {INSTALL_TIMEOUT}s"
    except OSError as exc:
        return f"{relative}: {exc}"
    if proc.returncode != 0:
        detail = (err or out or "").strip()[:200]
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
    if not update_core.enabled():
        return update_core.check_outcome(disabled=True)

    def _stamp():
        try:
            return os.stat(STATE_FILE).st_mtime_ns
        except OSError:
            return 0
    # checkedAt has 1-second resolution; pair it with the state file's
    # mtime_ns so two checks inside the same wall-clock second still count
    # as "ran".
    before = (str(load_state().get("checkedAt") or ""), _stamp())
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
        failed=failed,
        ran=(str(state.get("checkedAt") or ""), _stamp()) != before,
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
        # Never silent: this exact arm swallowed five days of failed 6-hour
        # ticks (moved tags after the history rewrite) with no notification
        # and no state write — so the popover kept a stale answer and the
        # manual check said "Check busy" forever.
        log.error("cannot read the repo: %s", exc)
        streak = int(state.get("fetchFailures") or 0) + 1
        state["fetchFailures"] = streak
        state["checkedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime())
        state["action"] = update.BLOCKED
        state["reason"] = f"cannot reach the repo: {str(exc)[:160]}"
        save_state(state)
        if streak == FETCH_FAILURE_NOTIFY_AT:
            notify("AI smartbar update",
                   f"Update checks are failing ({streak} in a row): "
                   f"{str(exc)[:120]}")
        return 1
    if state.pop("fetchFailures", None):
        save_state(state)   # the streak is over; forget it

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

    present = present_installers()
    targets = update.apply_targets(present)
    # Only darwin can cheaply prove the opt-out (the agent is a plist this
    # probe already read); elsewhere the installers keep their own defaults.
    no_auto = sys.platform == "darwin" and not present.get("update_agent")
    log.info("applying %s via: %s", plan.target_ref,
             ", ".join(targets) or "no installers detected")
    notify("AI smartbar",
           f"Updating to {plan.target_version or plan.target_ref[:8]}…")
    prev_head, prev_branch = repo.head, repo.branch
    bundled = backup_bundle() if "macos_swift" in targets else False

    try:
        rescue = update_git.checkout(plan, reset=reset)
    except update_git.GitError as exc:
        streak = update.record_failure(state, plan.target_ref)
        state.update(update.ui_state(plan, update_git.version_in_checkout(),
                                     channel=channel))
        save_state(state)
        log.error("checkout of %s failed (attempt %s): %s",
                  plan.target_ref, streak, exc)
        notify("AI smartbar update failed",
               f"{plan.target_ref}: could not check out — {str(exc)[:110]} "
               f"(attempt {streak} of {update.MAX_REF_FAILURES})")
        return 1
    if rescue:
        log.warning("local work parked — recover with: git stash apply %s", rescue)
    elif reset and repo.dirty:
        log.error("--reset could not park local changes (git stash create "
                  "failed); they are gone")

    failure = ""
    for key in targets:
        failure = run_installer(key, channel=channel, no_auto_update=no_auto)
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
                run_installer(key, channel=channel, no_auto_update=no_auto)
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
    body = f"Now on {plan.target_version or plan.target_ref[:8]} — app restarted"
    notes = _release_notes(plan, channel, prev_head, new_repo.head)
    if notes:
        body += f"\n{notes}"
    notify("AI smartbar updated", body)
    return 0
