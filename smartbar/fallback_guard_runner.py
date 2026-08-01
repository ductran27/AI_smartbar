"""CLI orchestration for the macOS and Linux/WSL fallback guard.

The privileged surface is intentionally one list-form ``osascript`` (macOS)
or ``pkexec`` (Linux/WSL) process per mutation.  Root shell text comes only
from :mod:`smartbar.core.fallback_guard`; this module never builds it from CLI
input and never uses ``shell=True``.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from smartbar import fallback_guard_verify
from smartbar.core import fallback_guard
from smartbar import warmup_runner


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INCONCLUSIVE = 2
EXIT_NOT_PROTECTED = 10
ADMIN_TIMEOUT_SECONDS = 300


def _claude_version(*, claude_path: Optional[str] = None,
                    run_process: Callable[..., Any] = subprocess.run) -> str:
    binary = claude_path or warmup_runner.claude_binary()
    if not binary:
        return ""
    try:
        completed = run_process(
            [binary, "--version"], capture_output=True, text=True,
            timeout=15, check=False)
    except Exception:
        return ""
    if int(getattr(completed, "returncode", 1)) != 0:
        return ""
    return (getattr(completed, "stdout", "") or "").strip()


def _blank_report(message: str, *, managed_root=None,
                  platform: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "state": fallback_guard.STATE_ERROR,
        "protected": False,
        "safetyAutoFallback": fallback_guard.UNKNOWN,
        "availabilityAutoFallback": fallback_guard.UNKNOWN,
        "manualOpusRestrictedByGuard": False,
        "scope": fallback_guard.scope_for_platform(platform),
        "claudeVersion": "",
        "activeManagedSource": "unknown",
        "policyPath": str(fallback_guard.policy_path(
            managed_root, platform=platform)),
        "details": [message],
        "lastLiveCheck": None,
    }


def error_report(message: str) -> Dict[str, Any]:
    """Public full-schema last resort for launcher-level exceptions."""

    return _blank_report(message)


def status(*, managed_root=None, mdm_paths=None, remote_path=None,
           required_uid: int = 0,
           required_gid: int = 0, platform: Optional[str] = None,
           state_path: Optional[str] = None, claude_path: Optional[str] = None,
           run_process: Callable[..., Any] = subprocess.run) -> Dict[str, Any]:
    try:
        version = _claude_version(claude_path=claude_path,
                                  run_process=run_process)
        last_check = fallback_guard_verify.load_last_check(state_path)
        return fallback_guard.inspect_guard(
            managed_root=managed_root, mdm_paths=mdm_paths,
            remote_path=remote_path, required_uid=required_uid,
            required_gid=required_gid, platform=platform,
            claude_version=version, last_live_check=last_check or None)
    except Exception as exc:
        return _blank_report("Fallback guard inspection failed: %s" % exc,
                             managed_root=managed_root, platform=platform)


def _error_report(report: Dict[str, Any], message: str,
                  *, state: str = fallback_guard.STATE_ERROR) -> Dict[str, Any]:
    failed = dict(report)
    failed.update(ok=False, state=state, protected=False)
    failed["details"] = [message] + list(report.get("details") or [])
    return failed


def _clear_live_state(state_path: Optional[str]) -> str:
    path = state_path or fallback_guard_verify.default_state_path()
    try:
        os.unlink(path)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return "Could not clear the stale live-check state: %s" % exc
    return ""


def _touch_user_settings(path_value=None) -> str:
    path = Path(path_value) if path_value is not None \
        else Path.home() / ".claude" / "settings.json"
    try:
        info = os.lstat(str(path))
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return "Could not inspect %s for reload: %s" % (path, exc)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return "Did not touch %s for reload because it is not a regular non-symlink file." % path
    try:
        os.utime(str(path), None, follow_symlinks=False)
    except OSError as exc:
        return "Could not nudge Claude settings reload at %s: %s" % (path, exc)
    return ""


def _linux_pkexec_path(path_value=None, *,
                       required_uid: int = 0) -> Tuple[Optional[Path], str]:
    """Resolve only the fixed PolicyKit binary and return an actionable error."""

    path = (Path(path_value) if path_value is not None
            else fallback_guard.PKEXEC_PATH)
    try:
        info = os.lstat(str(path))
    except FileNotFoundError:
        return None, (
            "pkexec is unavailable at %s. Install PolicyKit (polkit) and run "
            "from a session with an authentication agent." % path)
    except OSError as exc:
        return None, "Could not inspect pkexec at %s: %s" % (path, exc)
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) \
            or not (mode & 0o111):
        return None, ("pkexec at %s is not a regular non-symlink executable."
                      % path)
    if info.st_uid != required_uid or mode & 0o022:
        return None, (
            "pkexec at %s is not securely root-owned and non-writable by "
            "group/others." % path)
    return path, ""


def _run_admin(command: fallback_guard.RootCommand,
               run_process: Callable[..., Any]) -> Tuple[str, str]:
    try:
        completed = run_process(
            list(command.argv), capture_output=True, text=True,
            timeout=ADMIN_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return "error", "Administrator authorization timed out."
    except OSError as exc:
        return "error", "Could not start administrator authorization: %s" % exc
    stderr = (getattr(completed, "stderr", "") or "").strip()
    stdout = (getattr(completed, "stdout", "") or "").strip()
    if int(getattr(completed, "returncode", 1)) == 0:
        return "ok", ""
    combined = "\n".join(part for part in (stderr, stdout) if part)
    returncode = int(getattr(completed, "returncode", 1))
    lowered = combined.lower()
    is_pkexec = Path(command.argv[0]).name == "pkexec"
    if (is_pkexec and returncode == 126) or "(-128)" in combined \
            or "user canceled" in lowered or "user cancelled" in lowered \
            or "request dismissed" in lowered:
        return "cancelled", "Administrator authorization was cancelled."
    return "error", combined or "Administrator command failed."


def _exit_for_status(report: Dict[str, Any]) -> int:
    if report.get("state") in (fallback_guard.STATE_ERROR,
                                fallback_guard.STATE_UNSUPPORTED):
        return EXIT_ERROR
    return EXIT_OK if report.get("protected") is True else EXIT_NOT_PROTECTED


def run(action: str, *, managed_root=None, mdm_paths=None, remote_path=None,
        required_uid: int = 0, required_gid: int = 0,
        platform: Optional[str] = None, state_path: Optional[str] = None,
        claude_path: Optional[str] = None,
        user_settings_path=None,
        pkexec_path=None,
        run_process: Callable[..., Any] = subprocess.run) -> Tuple[Dict[str, Any], int]:
    """Run one CLI action and return ``(JSON report, exit code)``.

    Injection points are for contained tests.  The public CLI supplies none of
    them, so its privileged target is always the fixed platform fragment.
    """

    current = status(
        managed_root=managed_root, mdm_paths=mdm_paths, remote_path=remote_path,
        required_uid=required_uid, required_gid=required_gid,
        platform=platform, state_path=state_path, claude_path=claude_path,
        run_process=run_process)
    if action == "status":
        return current, _exit_for_status(current)
    if current.get("state") == fallback_guard.STATE_UNSUPPORTED:
        return current, EXIT_ERROR

    if action == "verify":
        # The persisted live result is evidence *about* the static policy, not
        # part of the static preflight.  A previous failed probe must remain
        # visible in status but must never permanently prevent a user from
        # rerunning verification after fixing the policy or upgrading Claude.
        static_report = fallback_guard.inspect_guard(
            managed_root=managed_root, mdm_paths=mdm_paths,
            remote_path=remote_path,
            required_uid=required_uid, required_gid=required_gid,
            platform=platform, claude_version=current.get("claudeVersion", ""),
            last_live_check=None)
        try:
            check = fallback_guard_verify.run_verification(
                state_path=state_path, claude_path=claude_path,
                run_process=run_process,
                static_guard_check=lambda: static_report.get("protected") is True)
        except Exception as exc:
            return _error_report(current,
                                 "Live fallback verification failed: %s" % exc), EXIT_ERROR
        report = status(
            managed_root=managed_root, mdm_paths=mdm_paths,
            remote_path=remote_path,
            required_uid=required_uid, required_gid=required_gid,
            platform=platform, state_path=state_path, claude_path=claude_path,
            run_process=run_process)
        outcome = check.get("status")
        if outcome == "passed":
            return report, EXIT_OK
        if outcome == "inconclusive":
            return report, EXIT_INCONCLUSIVE
        return report, EXIT_NOT_PROTECTED

    static_report = fallback_guard.inspect_guard(
        managed_root=managed_root, mdm_paths=mdm_paths,
        remote_path=remote_path, required_uid=required_uid,
        required_gid=required_gid, platform=platform,
        claude_version=current.get("claudeVersion", ""), last_live_check=None)

    if action == "enable":
        allowed, reason = fallback_guard.enable_allowed(
            managed_root=managed_root, platform=platform)
        if not allowed:
            return _error_report(current, reason,
                                 state=fallback_guard.STATE_ACTION_NEEDED), EXIT_INCONCLUSIVE
        # Canonical, secure and already effective: avoid a pointless password
        # prompt, but still invalidate old proof and nudge existing sessions.
        if static_report.get("protected") is True:
            warnings = [_clear_live_state(state_path),
                        _touch_user_settings(user_settings_path)]
            refreshed = status(
                managed_root=managed_root, mdm_paths=mdm_paths,
                remote_path=remote_path, required_uid=required_uid,
                required_gid=required_gid, platform=platform,
                state_path=state_path, claude_path=claude_path,
                run_process=run_process)
            refreshed["details"].extend(item for item in warnings if item)
            return refreshed, EXIT_OK
        resolved_pkexec = None
        if fallback_guard.platform_family(platform) == "linux":
            resolved_pkexec, problem = _linux_pkexec_path(
                pkexec_path, required_uid=required_uid)
            if problem:
                return _error_report(current, problem), EXIT_ERROR
        command = fallback_guard.enable_command(
            managed_root=managed_root, platform=platform,
            pkexec_path=resolved_pkexec)
    elif action == "remove":
        allowed, reason = fallback_guard.removal_allowed(
            managed_root=managed_root, required_uid=required_uid,
            required_gid=required_gid, platform=platform)
        if not allowed:
            return _error_report(current, reason,
                                 state=fallback_guard.STATE_ACTION_NEEDED), EXIT_INCONCLUSIVE
        if not os.path.lexists(str(fallback_guard.policy_path(
                managed_root, platform=platform))):
            warnings = [_clear_live_state(state_path),
                        _touch_user_settings(user_settings_path)]
            current["details"].extend(item for item in warnings if item)
            return current, EXIT_OK
        resolved_pkexec = None
        if fallback_guard.platform_family(platform) == "linux":
            resolved_pkexec, problem = _linux_pkexec_path(
                pkexec_path, required_uid=required_uid)
            if problem:
                return _error_report(current, problem), EXIT_ERROR
        command = fallback_guard.remove_command(
            managed_root=managed_root, platform=platform,
            pkexec_path=resolved_pkexec)
    else:
        return _error_report(current, "Unknown fallback-guard action %r." % action), EXIT_ERROR

    try:
        outcome, message = _run_admin(command, run_process)
    except Exception as exc:
        return _error_report(current,
                             "Administrator command failed: %s" % exc), EXIT_ERROR
    if outcome == "cancelled":
        return _error_report(current, message,
                             state=fallback_guard.STATE_ACTION_NEEDED), EXIT_INCONCLUSIVE
    if outcome == "error":
        return _error_report(current, message), EXIT_ERROR

    warnings = [_clear_live_state(state_path),
                _touch_user_settings(user_settings_path)]
    refreshed = status(
        managed_root=managed_root, mdm_paths=mdm_paths,
        remote_path=remote_path,
        required_uid=required_uid, required_gid=required_gid,
        platform=platform, state_path=state_path, claude_path=claude_path,
        run_process=run_process)
    refreshed["details"].extend(item for item in warnings if item)
    if action == "enable" and refreshed.get("protected") is not True:
        return _error_report(
            refreshed,
            "Administrator command completed, but effective fallback protection could not be verified.",
            state=fallback_guard.STATE_ACTION_NEEDED), EXIT_ERROR
    # Removal is an operation success even though its resulting status is
    # intentionally not protected.
    return refreshed, EXIT_OK


__all__ = ["EXIT_ERROR", "EXIT_INCONCLUSIVE", "EXIT_NOT_PROTECTED",
           "EXIT_OK", "error_report", "run", "status"]
