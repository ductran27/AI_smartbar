"""Machine-wide Claude automatic-fallback guard for macOS and Linux/WSL.

The guard is deliberately tiny: one managed-settings drop-in containing only
``switchModelsOnFlag=false`` and ``fallbackModel=[]``.  This module owns the
filesystem policy and builds privileged commands as *data*; importing it never
runs ``sudo``, ``osascript``, ``pkexec`` or writes under a system policy root.

File inspection fails closed.  A green-ish static verdict requires readable,
root-owned, non-symlink managed files and no later drop-in, plist, or
``policyHelper`` evidence that could supersede the two values.  Server-managed
settings cannot be disproved from disk, so an otherwise complete static result
is ``protected_inconclusive`` until the separately persisted live check passes.
"""
from __future__ import annotations

import json
import os
import plistlib
import shlex
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # pwd does not exist on Windows; import must still reach unsupported JSON.
    import pwd
except ImportError:  # pragma: no cover - exercised by source-level win32 test
    pwd = None


DARWIN_MANAGED_ROOT = Path("/Library/Application Support/ClaudeCode")
LINUX_MANAGED_ROOT = Path("/etc/claude-code")
# Backwards-compatible name for callers that explicitly mean the documented
# macOS location.  New platform-aware callers use ``managed_root_for_platform``.
MANAGED_ROOT = DARWIN_MANAGED_ROOT
PKEXEC_PATH = Path("/usr/bin/pkexec")
BASE_NAME = "managed-settings.json"
DROPIN_NAME = "managed-settings.d"
POLICY_NAME = "99-ai-smartbar-auto-fallback-guard.json"
POLICY: Dict[str, Any] = {
    "switchModelsOnFlag": False,
    "fallbackModel": [],
}
POLICY_BYTES = b'{"switchModelsOnFlag":false,"fallbackModel":[]}\n'

BLOCKED = "blocked"
ENABLED = "enabled"
UNKNOWN = "unknown"

STATE_PROTECTED = "protected"
STATE_PROTECTED_INCONCLUSIVE = "protected_inconclusive"
STATE_ACTION_NEEDED = "action_needed"
STATE_NOT_PROTECTED = "not_protected"
STATE_UNSUPPORTED = "unsupported"
STATE_ERROR = "error"

MDM_DOMAIN = "com.anthropic.claudecode.plist"
DEFAULT_REMOTE_PATH = Path.home() / ".claude" / "remote-settings.json"


def platform_family(platform: Optional[str] = None) -> str:
    """Return ``darwin``, ``linux``, or ``unsupported``.

    WSL reports a Linux ``sys.platform`` and intentionally follows the same
    ``/etc/claude-code`` policy contract as a native Linux host.
    """

    value = sys.platform if platform is None else str(platform)
    if value == "darwin":
        return "darwin"
    if value.startswith("linux"):
        return "linux"
    return "unsupported"


def managed_root_for_platform(platform: Optional[str] = None) -> Path:
    """Return the fixed production policy root for a supported platform."""

    return (LINUX_MANAGED_ROOT if platform_family(platform) == "linux"
            else DARWIN_MANAGED_ROOT)


def _scope_for_platform(family: str) -> str:
    if family == "darwin":
        return "local Claude Code sessions on this Mac"
    if family == "linux":
        return "local Claude Code sessions in this Linux/WSL environment"
    return "local Claude Code sessions on this device"


def scope_for_platform(platform: Optional[str] = None) -> str:
    """Return the stable user-facing scope string for a report."""

    return _scope_for_platform(platform_family(platform))


def _owner_for_platform(family: str) -> str:
    return "root:root" if family == "linux" else "root:wheel"


def default_mdm_paths() -> Tuple[Path, ...]:
    """Documented device and per-user managed-preference candidates."""

    paths = [Path("/Library/Managed Preferences") / MDM_DOMAIN]
    try:
        username = pwd.getpwuid(os.getuid()).pw_name if pwd is not None else ""
    except (KeyError, OSError):
        username = ""
    if username:
        paths.append(Path("/Library/Managed Preferences") / username / MDM_DOMAIN)
    return tuple(paths)


@dataclass(frozen=True)
class RootCommand:
    """One administrator-authorized command, ready for ``subprocess.run``."""

    action: str
    argv: Tuple[str, ...]
    shell_script: str

    def as_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "argv": list(self.argv),
                "shellScript": self.shell_script}


@dataclass
class _Source:
    path: Path
    exists: bool = False
    symlink: bool = False
    regular: bool = False
    uid: Optional[int] = None
    gid: Optional[int] = None
    mode: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    raw: Optional[bytes] = None
    error: str = ""

    @property
    def secure(self) -> bool:
        return (self.exists and self.regular and not self.symlink
                and self.uid == 0 and self.gid == 0 and self.mode == 0o644)


def _policy_path(root: Path) -> Path:
    return root / DROPIN_NAME / POLICY_NAME


def policy_path(managed_root: Optional[os.PathLike] = None,
                platform: Optional[str] = None) -> Path:
    """The fixed app fragment path (``managed_root`` is test-only injection)."""

    root = (Path(managed_root) if managed_root is not None
            else managed_root_for_platform(platform))
    return _policy_path(root)


def _mode_text(mode: Optional[int]) -> str:
    return "unknown" if mode is None else format(mode, "04o")


def _read_json_source(path: Path) -> _Source:
    source = _Source(path=path)
    try:
        info = os.lstat(str(path))
    except FileNotFoundError:
        return source
    except OSError as exc:
        source.error = "cannot inspect %s: %s" % (path, exc)
        return source

    source.exists = True
    source.symlink = stat.S_ISLNK(info.st_mode)
    source.regular = stat.S_ISREG(info.st_mode)
    source.uid, source.gid = info.st_uid, info.st_gid
    source.mode = stat.S_IMODE(info.st_mode)
    if source.symlink:
        source.error = "%s is a symlink" % path
        return source
    if not source.regular:
        source.error = "%s is not a regular file" % path
        return source
    try:
        with open(path, "rb") as handle:
            source.raw = handle.read()
        parsed = json.loads(source.raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        source.error = "cannot parse %s as JSON: %s" % (path, exc)
        return source
    if not isinstance(parsed, dict):
        source.error = "%s must contain a JSON object" % path
        return source
    source.data = parsed
    return source


def _directory_problem(path: Path, required_uid: int,
                       required_gid: int, owner_text: str) -> str:
    try:
        info = os.lstat(str(path))
    except FileNotFoundError:
        return "%s is missing" % path
    except OSError as exc:
        return "cannot inspect %s: %s" % (path, exc)
    if stat.S_ISLNK(info.st_mode):
        return "%s is a symlink" % path
    if not stat.S_ISDIR(info.st_mode):
        return "%s is not a directory" % path
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid != required_uid or info.st_gid != required_gid:
        return "%s must be owned by %s" % (path, owner_text)
    if mode != 0o755:
        return "%s has mode %s; expected 0755" % (path, _mode_text(mode))
    return ""


def _policy_is_exact(data: Optional[Dict[str, Any]]) -> bool:
    return bool(
        isinstance(data, dict)
        and set(data) == {"switchModelsOnFlag", "fallbackModel"}
        and data.get("switchModelsOnFlag") is False
        and isinstance(data.get("fallbackModel"), list)
        and not data["fallbackModel"]
    )


def _value_state(key: str, value: Any) -> str:
    if key == "switchModelsOnFlag":
        if value is False:
            return BLOCKED
        if value is True:
            return ENABLED
        return UNKNOWN
    if isinstance(value, list):
        return BLOCKED if not value else ENABLED
    return UNKNOWN


def _scan_file_sources(root: Path) -> Tuple[List[_Source], List[str], bool]:
    """Base then visible ``*.json`` drop-ins, exactly Claude's merge order."""

    details: List[str] = []
    complete = True
    sources: List[_Source] = []
    base = _read_json_source(root / BASE_NAME)
    if base.exists or base.error:
        sources.append(base)

    directory = root / DROPIN_NAME
    try:
        info = os.lstat(str(directory))
    except FileNotFoundError:
        return sources, details, complete
    except OSError as exc:
        return sources, ["cannot scan %s: %s" % (directory, exc)], False
    if stat.S_ISLNK(info.st_mode):
        return sources, ["%s is a symlink" % directory], False
    if not stat.S_ISDIR(info.st_mode):
        return sources, ["%s is not a directory" % directory], False
    try:
        names = sorted(entry.name for entry in os.scandir(str(directory))
                       if not entry.name.startswith(".")
                       and entry.name.endswith(".json"))
    except OSError as exc:
        return sources, ["cannot scan %s: %s" % (directory, exc)], False
    for name in names:
        sources.append(_read_json_source(directory / name))
    return sources, details, complete


def _extract_plist_settings(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    raw = value.get("Settings", value.get("managedSettings", value))
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def _read_plist(path: Path, required_uid: int) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    try:
        info = os.lstat(str(path))
    except FileNotFoundError:
        return False, None, ""
    except OSError as exc:
        return True, None, "cannot inspect managed plist %s: %s" % (path, exc)
    if stat.S_ISLNK(info.st_mode):
        return True, None, "managed plist %s is a symlink" % path
    if not stat.S_ISREG(info.st_mode):
        return True, None, "managed plist %s is not a regular file" % path
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid != required_uid or mode & 0o022:
        return True, None, ("managed plist %s is not securely root-owned "
                            "(owner=%s mode=%s)" %
                            (path, info.st_uid, _mode_text(mode)))
    try:
        with open(path, "rb") as handle:
            settings = _extract_plist_settings(plistlib.load(handle))
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        return True, None, "cannot parse managed plist %s: %s" % (path, exc)
    if settings is None:
        return True, None, "managed plist %s has an unknown settings shape" % path
    return True, settings, ""


def _live_status(last_live_check: Optional[Dict[str, Any]],
                 claude_version: str) -> str:
    if not isinstance(last_live_check, dict):
        return ""
    status_value = last_live_check.get("status")
    checked_version = last_live_check.get("claudeVersion")
    if status_value not in ("passed", "failed", "inconclusive"):
        return ""
    if status_value == "passed" \
            and (not claude_version or checked_version != claude_version):
        return "inconclusive"
    return status_value


def inspect_guard(*, managed_root: Optional[os.PathLike] = None,
                  mdm_paths: Optional[Sequence[os.PathLike]] = None,
                  remote_path: Optional[os.PathLike] = None,
                  required_uid: int = 0, required_gid: int = 0,
                  platform: Optional[str] = None, claude_version: str = "",
                  last_live_check: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the JSON-ready effective guard report without changing anything.

    ``managed_root``/``mdm_paths`` and required IDs are injectable exclusively
    so unit tests can exercise the real traversal in a temporary directory.
    Production callers use the fixed platform system location and root IDs.
    """

    current_platform = sys.platform if platform is None else platform
    family = platform_family(current_platform)
    root = (Path(managed_root) if managed_root is not None
            else managed_root_for_platform(current_platform))
    target_path = _policy_path(root)
    owner_text = _owner_for_platform(family)
    live = last_live_check if isinstance(last_live_check, dict) else None
    base_report: Dict[str, Any] = {
        "ok": True,
        "state": STATE_NOT_PROTECTED,
        "protected": False,
        "safetyAutoFallback": UNKNOWN,
        "availabilityAutoFallback": UNKNOWN,
        "manualOpusRestrictedByGuard": False,
        "scope": _scope_for_platform(family),
        "claudeVersion": claude_version or "",
        "activeManagedSource": "unknown",
        "policyPath": str(target_path),
        "details": [],
        "lastLiveCheck": live,
    }
    if family == "unsupported":
        base_report.update(ok=False, state=STATE_UNSUPPORTED,
                           details=["Machine-wide fallback guard is supported on macOS and Linux/WSL only."])
        return base_report

    sources, scan_details, scan_complete = _scan_file_sources(root)
    details = list(scan_details)
    source_by_path = {source.path: source for source in sources}
    target = source_by_path.get(target_path, _read_json_source(target_path))

    root_problem = _directory_problem(root, required_uid, required_gid,
                                      owner_text)
    dropin_problem = _directory_problem(root / DROPIN_NAME,
                                         required_uid, required_gid,
                                         owner_text)
    target_secure = bool(
        target.regular and not target.symlink
        and target.uid == required_uid and target.gid == required_gid
        and target.mode == 0o644
    )
    target_exact = _policy_is_exact(target.data)
    target_canonical = target.raw == POLICY_BYTES

    if target.error:
        details.append(target.error)
    elif target.exists:
        if not target_exact:
            details.append("The app policy must contain exactly switchModelsOnFlag=false and fallbackModel=[].")
        if target.uid != required_uid or target.gid != required_gid:
            details.append("The app policy must be owned by %s." % owner_text)
        if target.mode != 0o644:
            details.append("The app policy has mode %s; expected 0644." % _mode_text(target.mode))
        if target_exact and not target_canonical:
            details.append("The policy is effective but no longer has the app's canonical bytes; removal will refuse it.")
    else:
        details.append("The machine-wide app policy is not installed.")
    for problem in (root_problem, dropin_problem):
        if problem:
            details.append(problem)

    effective: Dict[str, Any] = {}
    providers: Dict[str, _Source] = {}
    source_unknown = not scan_complete
    conflicts: List[str] = []
    target_seen = False
    helper_present = False
    for source in sources:
        if source.path == target_path:
            target_seen = True
        if source.error:
            details.append(source.error)
            # A malformed visible managed sibling can make Claude reject or
            # partially load the file tier.  Never infer merge-through.
            source_unknown = True
            continue
        if source.data is not None and "policyHelper" in source.data:
            helper_present = True
        if not source.data:
            continue
        for key in ("switchModelsOnFlag", "fallbackModel"):
            if key not in source.data:
                continue
            if source.path != target_path:
                relation = "overrides" if target_seen else "is overridden by"
                conflicts.append("%s sets %s and %s the app policy." %
                                 (source.path, key, relation))
            effective[key] = source.data[key]
            providers[key] = source

    plist_settings: Optional[Dict[str, Any]] = None
    plist_present = False
    plist_unknown = False
    if family == "darwin":
        plist_paths = (default_mdm_paths() if mdm_paths is None
                       else tuple(Path(p) for p in mdm_paths))
    else:
        # Linux/WSL has no macOS managed-preference tier.  In particular, a
        # stray plist in a mounted tree must not outrank the Linux file source.
        plist_paths = ()
    for path_value in plist_paths:
        present, settings, error = _read_plist(Path(path_value), required_uid)
        if not present:
            continue
        plist_present = True
        if error:
            details.append(error)
            plist_unknown = True
        elif settings:
            if plist_settings is not None:
                details.append("Multiple managed plist indicators are present; active values cannot be proven offline.")
                plist_unknown = True
            plist_settings = settings

    # A non-empty remote cache is a documented higher managed source.  Empty
    # `{}` means no server policy and is not an indicator.  The cache belongs
    # to the logged-in user, so root ownership is neither expected nor useful;
    # it still must be a regular non-symlink JSON object.
    remote = _read_json_source(Path(remote_path) if remote_path is not None
                               else DEFAULT_REMOTE_PATH)
    remote_present = False
    remote_unknown = False
    remote_settings: Optional[Dict[str, Any]] = None
    if remote.exists or remote.error:
        if remote.error:
            details.append(remote.error.replace("cannot parse", "cannot parse remote managed cache"))
            remote_unknown = True
        elif remote.data:
            remote_present = True
            remote_settings = remote.data

    active_source = "file" if sources else "unknown"
    chosen = effective
    chosen_providers = providers
    higher_unknown = source_unknown
    if plist_present:
        active_source = "plist"
        if plist_unknown or plist_settings is None:
            higher_unknown = True
            chosen = {}
            chosen_providers = {}
        elif plist_settings:
            chosen = plist_settings
            chosen_providers = {}
    if remote_present or remote_unknown:
        active_source = "remote"
        if remote_unknown or remote_settings is None:
            higher_unknown = True
            chosen = {}
        else:
            chosen = remote_settings
            # A valid higher remote source makes plist uncertainty irrelevant,
            # but a malformed visible file sibling remains fail-closed by the
            # guard's explicit static-integrity contract.
            higher_unknown = source_unknown
        chosen_providers = {}
    # policyHelper outranks all static sources and its output is intentionally
    # not executed during an inspection.
    if helper_present \
            or (plist_settings is not None
                and "policyHelper" in plist_settings) \
            or (remote_settings is not None
                and "policyHelper" in remote_settings):
        active_source = "policyHelper"
        higher_unknown = True
        chosen = {}
        chosen_providers = {}
        details.append("policyHelper can replace file-based managed settings; its output was not executed.")

    safety = _value_state("switchModelsOnFlag", chosen.get("switchModelsOnFlag"))
    availability = _value_state("fallbackModel", chosen.get("fallbackModel"))

    provider_secure = True
    if active_source == "file":
        for key in ("switchModelsOnFlag", "fallbackModel"):
            provider = chosen_providers.get(key)
            if provider is None or not (provider.regular and not provider.symlink
                                        and provider.uid == required_uid
                                        and provider.gid == required_gid
                                        and provider.mode == 0o644):
                provider_secure = False
    elif active_source not in ("plist", "remote"):
        provider_secure = False

    values_blocked = safety == BLOCKED and availability == BLOCKED
    protected = bool(values_blocked and provider_secure and not higher_unknown)
    # If the app file supplies either value, its own minimal shape and secure
    # directory chain are part of the guarantee—not cosmetic hygiene.
    app_supplies = any(provider is target for provider in chosen_providers.values())
    if app_supplies and (not target_exact or not target_secure
                         or root_problem or dropin_problem):
        protected = False
    # A higher source may currently supply the right values, but a corrupt
    # dormant app fragment would become active the instant that source is
    # removed.  Do not let a remote/plist pass paint that latent failure green.
    if target.exists and (not target_exact or not target_secure
                          or root_problem or dropin_problem):
        protected = False

    live_status = _live_status(live, claude_version or "")
    if protected and live_status == "passed":
        state_value = STATE_PROTECTED
    elif protected:
        state_value = STATE_PROTECTED_INCONCLUSIVE
    elif (target.exists or plist_present or remote_present or remote_unknown
          or helper_present or conflicts or higher_unknown):
        state_value = STATE_ACTION_NEEDED
    else:
        state_value = STATE_NOT_PROTECTED

    if live_status == "failed":
        protected = False
        state_value = STATE_ACTION_NEEDED
        details.insert(0, "The last live verification failed.")
    elif live_status == "inconclusive" and protected:
        state_value = STATE_PROTECTED_INCONCLUSIVE

    if conflicts:
        details.extend(conflicts)
    if protected and not details:
        details.append("Both automatic fallback paths are blocked by managed settings.")
    if protected and state_value == STATE_PROTECTED_INCONCLUSIVE:
        details.append("Static policy is complete; run live verification to confirm the active managed source and serving behavior.")

    base_report.update(
        state=state_value,
        protected=protected,
        safetyAutoFallback=safety if not higher_unknown else UNKNOWN,
        availabilityAutoFallback=availability if not higher_unknown else UNKNOWN,
        activeManagedSource=active_source,
        details=list(dict.fromkeys(details)),
    )
    return base_report


def is_protected() -> bool:
    """Fail-closed static preflight used before any paid live probes."""

    return inspect_guard().get("protected") is True


def removal_allowed(*, managed_root: Optional[os.PathLike] = None,
                    required_uid: int = 0, required_gid: int = 0,
                    platform: Optional[str] = None) -> Tuple[bool, str]:
    """Whether this exact app-owned, unmodified fragment may be removed."""

    family = platform_family(platform)
    root = (Path(managed_root) if managed_root is not None
            else managed_root_for_platform(platform))
    source = _read_json_source(_policy_path(root))
    if not source.exists:
        return True, ""
    if source.symlink or not source.regular:
        return False, "Refusing removal: the app policy is not a regular non-symlink file."
    if source.uid != required_uid or source.gid != required_gid or source.mode != 0o644:
        return False, ("Refusing removal: the app policy is not %s mode 0644."
                       % _owner_for_platform(family))
    if source.raw != POLICY_BYTES:
        return False, "Refusing removal: the app policy was modified after installation."
    return True, ""


def enable_allowed(*, managed_root: Optional[os.PathLike] = None,
                   platform: Optional[str] = None) -> Tuple[bool, str]:
    """Refuse to overwrite any existing target not byte-for-byte ours.

    Canonical bytes with changed metadata are allowed: the atomic enable plan
    repairs owner/mode.  Symlinks, non-regular files, and admin edits are not
    silently replaced even though the filename belongs to this app.
    """

    root = (Path(managed_root) if managed_root is not None
            else managed_root_for_platform(platform))
    source = _read_json_source(_policy_path(root))
    if not source.exists:
        return True, ""
    if source.symlink or not source.regular:
        return False, "Refusing enable: the app policy target is not a regular non-symlink file."
    if source.raw != POLICY_BYTES:
        return False, "Refusing enable: the existing app policy was modified by an administrator."
    return True, ""


def _applescript_string(value: str) -> str:
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def _osascript_plan(action: str, shell_script: str, *, confirm: bool = False) -> RootCommand:
    lines = []
    if confirm:
        lines.append('display dialog "Remove fallback protection from this Mac?" '
                     'buttons {"Cancel", "Remove"} default button "Cancel" '
                     'cancel button "Cancel" with icon caution')
    lines.append("do shell script %s with administrator privileges" %
                 _applescript_string(shell_script))
    return RootCommand(action, ("/usr/bin/osascript", "-e", "\n".join(lines)),
                       shell_script)


def _pkexec_plan(action: str, shell_script: str,
                 pkexec_path: Optional[os.PathLike] = None) -> RootCommand:
    """Build one list-form PolicyKit invocation with no persistent helper."""

    binary = Path(pkexec_path) if pkexec_path is not None else PKEXEC_PATH
    return RootCommand(action, (str(binary), "/bin/sh", "-c", shell_script),
                       shell_script)


def enable_command(*, managed_root: Optional[os.PathLike] = None,
                   platform: Optional[str] = None,
                   pkexec_path: Optional[os.PathLike] = None) -> RootCommand:
    """Build the one-shot, atomic administrator command; do not execute it."""

    family = platform_family(platform)
    if family == "unsupported":
        raise ValueError("fallback guard is unsupported on %s" %
                         (sys.platform if platform is None else platform))
    root = (Path(managed_root) if managed_root is not None
            else managed_root_for_platform(platform))
    directory = root / DROPIN_NAME
    target = _policy_path(root)
    qroot, qdir, qtarget = map(lambda p: shlex.quote(str(p)),
                               (root, directory, target))
    content = shlex.quote(POLICY_BYTES.decode("ascii").rstrip("\n"))
    if family == "darwin":
        script = "\n".join((
            "set -eu",
            "root=%s" % qroot,
            "directory=%s" % qdir,
            "target=%s" % qtarget,
            "[ ! -L \"$root\" ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: managed root is a symlink' >&2; exit 40; }",
            "[ ! -L \"$directory\" ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: drop-in directory is a symlink' >&2; exit 40; }",
            "/bin/mkdir -p \"$directory\"",
            "/usr/sbin/chown root:wheel \"$root\" \"$directory\"",
            "/bin/chmod 0755 \"$root\" \"$directory\"",
            "tmp=$(/usr/bin/mktemp \"$directory/.ai-smartbar-fallback-guard.XXXXXX\")",
            "trap '/bin/rm -f \"$tmp\"' EXIT HUP INT TERM",
            "/usr/bin/printf '%s\\n' %s > \"$tmp\"" % ("%s", content),
            "/usr/sbin/chown root:wheel \"$tmp\"",
            "/bin/chmod 0644 \"$tmp\"",
            "if [ -e \"$target\" ] || [ -L \"$target\" ]; then",
            "  [ ! -L \"$target\" ] && [ -f \"$target\" ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing non-regular or symlink policy' >&2; exit 41; }",
            "  /usr/bin/cmp -s \"$tmp\" \"$target\" || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing modified policy' >&2; exit 41; }",
            "fi",
            "/bin/mv -f \"$tmp\" \"$target\"",
            "trap - EXIT HUP INT TERM",
        ))
        return _osascript_plan("enable", script)

    script = "\n".join((
        "set -eu",
        "root=%s" % qroot,
        "directory=%s" % qdir,
        "target=%s" % qtarget,
        "if [ -e \"$root\" ] || [ -L \"$root\" ]; then",
        "  [ ! -L \"$root\" ] && [ -d \"$root\" ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing non-directory or symlink managed root' >&2; exit 40; }",
        "else /bin/mkdir \"$root\"; fi",
        "/usr/bin/chown root:root \"$root\"",
        "/bin/chmod 0755 \"$root\"",
        "[ \"$(/usr/bin/stat -c '%u:%g:%a' \"$root\")\" = '0:0:755' ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: managed root owner or mode verification failed' >&2; exit 40; }",
        "if [ -e \"$directory\" ] || [ -L \"$directory\" ]; then",
        "  [ ! -L \"$directory\" ] && [ -d \"$directory\" ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing non-directory or symlink drop-in directory' >&2; exit 40; }",
        "else /bin/mkdir \"$directory\"; fi",
        "/usr/bin/chown root:root \"$directory\"",
        "/bin/chmod 0755 \"$directory\"",
        "[ \"$(/usr/bin/stat -c '%u:%g:%a' \"$directory\")\" = '0:0:755' ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: drop-in directory owner or mode verification failed' >&2; exit 40; }",
        "tmp=$(/usr/bin/mktemp \"$directory/.ai-smartbar-fallback-guard.XXXXXX\")",
        "trap '/bin/rm -f \"$tmp\"' EXIT HUP INT TERM",
        "/usr/bin/printf '%s\\n' %s > \"$tmp\"" % ("%s", content),
        "/usr/bin/chown root:root \"$tmp\"",
        "/bin/chmod 0644 \"$tmp\"",
        "[ \"$(/usr/bin/stat -c '%u:%g:%a' \"$tmp\")\" = '0:0:644' ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: temporary policy owner or mode verification failed' >&2; exit 40; }",
        "if [ -e \"$target\" ] || [ -L \"$target\" ]; then",
        "  [ ! -L \"$target\" ] && [ -f \"$target\" ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing non-regular or symlink policy' >&2; exit 41; }",
        "  /usr/bin/cmp -s \"$tmp\" \"$target\" || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing modified policy' >&2; exit 41; }",
        "fi",
        "/bin/mv -f \"$tmp\" \"$target\"",
        "trap - EXIT HUP INT TERM",
        "[ ! -L \"$target\" ] && [ -f \"$target\" ] && [ \"$(/usr/bin/stat -c '%u:%g:%a' \"$target\")\" = '0:0:644' ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: installed policy verification failed' >&2; exit 40; }",
    ))
    return _pkexec_plan("enable", script, pkexec_path)


def remove_command(*, managed_root: Optional[os.PathLike] = None,
                   platform: Optional[str] = None,
                   pkexec_path: Optional[os.PathLike] = None) -> RootCommand:
    """Build one confirmed administrator command with root-side revalidation."""

    family = platform_family(platform)
    if family == "unsupported":
        raise ValueError("fallback guard is unsupported on %s" %
                         (sys.platform if platform is None else platform))
    root = (Path(managed_root) if managed_root is not None
            else managed_root_for_platform(platform))
    directory = root / DROPIN_NAME
    target = _policy_path(root)
    qroot, qdir, qtarget = (shlex.quote(str(root)), shlex.quote(str(directory)),
                            shlex.quote(str(target)))
    content = shlex.quote(POLICY_BYTES.decode("ascii").rstrip("\n"))
    if family == "darwin":
        script = "\n".join((
            "set -eu",
            "root=%s" % qroot,
            "directory=%s" % qdir,
            "target=%s" % qtarget,
            "if [ ! -e \"$target\" ] && [ ! -L \"$target\" ]; then exit 0; fi",
            "[ ! -L \"$root\" ] && [ ! -L \"$directory\" ] && [ ! -L \"$target\" ] && [ -f \"$target\" ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing non-regular or symlink policy' >&2; exit 41; }",
            "[ \"$(/usr/bin/stat -f '%u:%g:%Lp' \"$target\")\" = '0:0:644' ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing policy with changed owner or mode' >&2; exit 41; }",
            "expected=$(/usr/bin/mktemp \"$directory/.ai-smartbar-remove-check.XXXXXX\")",
            "trap '/bin/rm -f \"$expected\"' EXIT HUP INT TERM",
            "/usr/bin/printf '%s\\n' %s > \"$expected\"" % ("%s", content),
            "/usr/bin/cmp -s \"$expected\" \"$target\" || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing modified policy' >&2; exit 41; }",
            "/bin/rm -f \"$target\"",
            "/bin/rm -f \"$expected\"",
            "trap - EXIT HUP INT TERM",
        ))
        return _osascript_plan("remove", script, confirm=True)

    script = "\n".join((
        "set -eu",
        "root=%s" % qroot,
        "directory=%s" % qdir,
        "target=%s" % qtarget,
        "if [ ! -e \"$target\" ] && [ ! -L \"$target\" ]; then exit 0; fi",
        "[ ! -L \"$root\" ] && [ -d \"$root\" ] && [ ! -L \"$directory\" ] && [ -d \"$directory\" ] && [ ! -L \"$target\" ] && [ -f \"$target\" ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing non-regular or symlink policy path' >&2; exit 41; }",
        "[ \"$(/usr/bin/stat -c '%u:%g:%a' \"$root\")\" = '0:0:755' ] && [ \"$(/usr/bin/stat -c '%u:%g:%a' \"$directory\")\" = '0:0:755' ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing policy under changed directory owner or mode' >&2; exit 41; }",
        "[ \"$(/usr/bin/stat -c '%u:%g:%a' \"$target\")\" = '0:0:644' ] || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing policy with changed owner or mode' >&2; exit 41; }",
        "expected=$(/usr/bin/mktemp \"$directory/.ai-smartbar-remove-check.XXXXXX\")",
        "trap '/bin/rm -f \"$expected\"' EXIT HUP INT TERM",
        "/usr/bin/printf '%s\\n' %s > \"$expected\"" % ("%s", content),
        "/usr/bin/cmp -s \"$expected\" \"$target\" || { /usr/bin/printf '%s\\n' 'ai-smartbar: refusing modified policy' >&2; exit 41; }",
        "/bin/rm -f \"$target\"",
        "/bin/rm -f \"$expected\"",
        "trap - EXIT HUP INT TERM",
    ))
    return _pkexec_plan("remove", script, pkexec_path)


__all__ = [
    "BLOCKED", "DARWIN_MANAGED_ROOT", "ENABLED", "LINUX_MANAGED_ROOT",
    "MANAGED_ROOT", "PKEXEC_PATH", "UNKNOWN", "POLICY", "POLICY_BYTES",
    "POLICY_NAME", "RootCommand", "default_mdm_paths", "enable_allowed",
    "enable_command", "inspect_guard", "is_protected",
    "managed_root_for_platform", "platform_family", "policy_path",
    "removal_allowed", "remove_command", "scope_for_platform",
]
