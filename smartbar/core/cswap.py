"""Thin subprocess wrapper around the claude-swap CLI (the data engine)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from . import portable
from .model import Account, Metric, Snapshot

TIMEOUT = 30
PRIMER_TIMEOUT = 25
COMBINED_TIMEOUT = 40

# Force-freshen claude-swap's usage store, using its own auto-engine
# collector convention: an explicit fetch set makes the store's atomic
# reserve() use stale-OR-plan-due eligibility, which is the sanctioned way
# to beat the 3-min serve TTL (it harvests the 60s "urgent" plans near the
# limit and refreshes alternates parked on long plans). A fresh and
# not-yet-due account is still served from the store, so the sustained
# per-token API rate can never exceed claude-swap's measured budget.
# Runs under the pipx venv python (see venv_python); any failure is
# non-fatal — the follow-up `cswap list` serves last-good data regardless.
# Keep in sync with the copy in macos-swift CswapClient.primerCode.
PRIMER_CODE = """\
import sys
try:
    from claude_swap.switcher import ClaudeAccountSwitcher
    switcher = ClaudeAccountSwitcher()
    numbers = {a.number for a in switcher.accounts_snapshot(fetch=set()).accounts}
    if numbers:
        switcher.accounts_snapshot(fetch=numbers)
except Exception as exc:
    sys.stderr.write("primer: %s\\n" % exc)
    sys.exit(1)
"""

# Prime AND list in ONE interpreter boot: the primer body above, then the
# real `cswap list --json` in-process (cli.main prints the canonical JSON
# to stdout). Halves the per-poll process spawns vs primer + list. Exit 97
# marks "claude_swap internals moved" so callers latch off and fall back.
# Keep in sync with the copy in macos-swift CswapClient.combinedCode.
COMBINED_CODE = """\
import sys
try:
    from claude_swap.switcher import ClaudeAccountSwitcher
    switcher = ClaudeAccountSwitcher()
    numbers = {a.number for a in switcher.accounts_snapshot(fetch=set()).accounts}
    if numbers:
        switcher.accounts_snapshot(fetch=numbers)
    from claude_swap import cli
    sys.argv = ["cswap", "list", "--json"]
    cli.main()
except SystemExit:
    raise
except Exception as exc:
    sys.stderr.write("combined: %s\\n" % exc)
    sys.exit(97)
"""

_combined_unsupported = False  # latched for this process on exit 97


class CswapError(Exception):
    """Any failure talking to or parsing output from cswap."""


#: Windows CreateProcess cannot launch a .bat/.cmd directly (they carry no
#: PE header), so the OS silently reruns it as `cmd.exe /c <full argv>` even
#: for this module's shell=False subprocess.run() calls — cmd.exe then
#: re-parses that whole line, giving &, |, ^, <, >, % and quotes a meaning
#: POSIX never gave them (the "BatBadBut" class of bug, still open as of
#: CPython 3.13: list2cmdline() only implements MSVCRT-style backslash/quote
#: escaping, nothing cmd.exe-aware). Refusing the extension is the whole
#: mitigation — see device_config.py's _BAD_VALUE_WIN comment for why the
#: hazard has to be caught here rather than at config-parsing time.
#:
#: Deliberately NOT applied to SMARTBAR_CLAUDE, the other setting that becomes
#: argv[0]: npm installs Claude Code AS claude.cmd on Windows, so warmup_runner
#: hunts for exactly that extension (see its win32 discovery arm). Refusing it
#: there would reject the normal install rather than a suspicious one. The
#: asymmetry is the point -- for cswap a .bat is unusual (pipx and uv both emit
#: .exe shims), and cswap is the one whose argv carries an account email back
#: out of `list --json`, so it is the one where a re-parse has something to bite.
_SHELL_REPARSED_EXTS = (".bat", ".cmd")


def _reject_shell_reparse(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if sys.platform == "win32" and ext in _SHELL_REPARSED_EXTS:
        raise CswapError(
            f"cswap resolved to a batch file ({path}); refusing to run it "
            "because Windows reruns .bat/.cmd through cmd.exe, which "
            "re-parses the whole argv. Point SMARTBAR_CSWAP at cswap.exe "
            "(or a non-batch launcher) instead."
        )
    return path


def _binary() -> str:
    override = os.environ.get("SMARTBAR_CSWAP")
    if override:
        return _reject_shell_reparse(override)
    # shutil.which already honours PATHEXT on Windows, so a bare "cswap" on
    # PATH resolves to cswap.exe (or .bat/.cmd) with no change needed here.
    found = shutil.which("cswap")
    if found:
        return _reject_shell_reparse(found)
    fallback = os.path.expanduser("~/.local/bin/cswap")
    if os.path.exists(fallback):
        return _reject_shell_reparse(fallback)
    fallback_exe = os.path.expanduser("~/.local/bin/cswap.exe")
    if os.path.exists(fallback_exe):
        return fallback_exe
    raise CswapError("cswap binary not found (install claude-swap)")


def venv_python() -> str | None:
    """Interpreter that can import claude_swap, or None.

    On POSIX, pipx installs cswap as a shell launcher whose exec line names
    the venv python (`'exec' '/…/pipx/venvs/claude-swap/bin/python' …`), so
    that path is parsed straight out of the launcher's own source text.

    On Windows there is nothing to parse: pipx (and uv) install `cswap.exe`,
    a compiled PE launcher stub whose first bytes are a binary header rather
    than shell script text, so the exec-line regex can never match it. This
    probes the well-known venv layouts pipx and uv each use for a
    `claude-swap` tool install instead, returning whichever
    `Scripts\\python.exe` exists first.

    SMARTBAR_CSWAP_PYTHON overrides on every platform. None (compiled binary
    with a different layout, mock script, moved venv) simply disables the
    primer — never an error.
    """
    override = os.environ.get("SMARTBAR_CSWAP_PYTHON")
    if override:
        return override
    if sys.platform == "win32":
        for candidate in ("~/.local/pipx/venvs/claude-swap/Scripts/python.exe",
                          "~/.local/share/uv/tools/claude-swap/Scripts/python.exe"):
            path = os.path.expanduser(candidate)
            if os.path.exists(path):
                return path
        return None
    try:
        with open(_binary(), "rb") as handle:
            head = handle.read(512).decode("utf-8", errors="ignore")
    except (OSError, CswapError):
        return None
    match = re.search(r"'(/[^']*/bin/python[^']*)'", head)
    if match and os.path.exists(match.group(1)):
        return match.group(1)
    return None


def prime_fresh() -> bool:
    """Best-effort store freshen (see PRIMER_CODE); True when it ran clean."""
    python = venv_python()
    if python is None:
        return False
    try:
        proc = subprocess.run([python, "-c", PRIMER_CODE], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=PRIMER_TIMEOUT, **portable.no_window())
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def fetch_combined() -> str | None:
    """Prime + list JSON from one venv-python process; None → fall back."""
    global _combined_unsupported
    if _combined_unsupported:
        return None
    python = venv_python()
    if python is None:
        return None
    try:
        proc = subprocess.run([python, "-c", COMBINED_CODE], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=COMBINED_TIMEOUT, **portable.no_window())
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode == 97:
        _combined_unsupported = True  # internals moved; stop retrying this process
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout


def _run(args, feed=None):
    # encoding= is not redundant with text=True: text=True alone decodes
    # using the locale codec, which is cp1252 on a stock Windows, and a
    # non-ASCII account email would then raise UnicodeDecodeError — which
    # neither except clause below catches.
    kwargs = dict(capture_output=True, text=True, encoding="utf-8",
                  errors="replace", timeout=TIMEOUT, **portable.no_window())
    if feed is not None:
        kwargs["input"] = feed   # answers an interactive [y/N] prompt
    try:
        proc = subprocess.run([_binary(), *args], **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise CswapError(f"cswap {' '.join(args)} timed out after {TIMEOUT}s") from exc
    except OSError as exc:
        raise CswapError(f"failed to run cswap: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        raise CswapError(f"cswap {' '.join(args)} failed (rc={proc.returncode}): {detail}")
    return proc.stdout


def _metric(key, label, short, raw) -> Metric:
    return Metric(key=key, label=label, short=short,
                  pct=float(raw.get("pct", 0.0)),
                  resets_at=raw.get("resetsAt", ""),
                  countdown=raw.get("countdown", ""))


def snapshot_stamp(accounts) -> str:
    """The one "Updated" time a popover shows, from per-account stamps.

    The ACTIVE account's, because that is the account Claude Code's /usage
    describes too — the same rule the Swift app's Snapshot.dataDate applies,
    which is why the two UIs now stamp a payload identically. With no active
    slot, the newest reading anything has is the closest honest answer.

    Taking whichever account happened to come first is what this replaces:
    cswap refreshes each slot on its own plan, so slot 1 can be three hours
    older than the active one in the same payload, and the popover would
    then claim data was measured long before it actually was.

    max() on the raw strings is deliberate, and safe for a checkable reason
    rather than a hopeful one: claude_swap/json_output.py builds the field as
    `datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
    .replace("+00:00", "Z")`, so every value is UTC, second-precision and
    fixed-width ("2026-07-20T01:45:03Z"). Lexical order IS chronological
    order there, and no parsing or timezone guessing is needed. A future
    schema that emitted a real offset would break that, which is what
    schema_warning above exists to shout about.

    Only the no-active-slot path uses max() at all; the normal path is the
    exact-match loop, which cares about ordering not one bit.
    """
    for account in accounts:
        if account.active and account.fetched_at:
            return account.fetched_at
    return max((a.fetched_at for a in accounts if a.fetched_at), default="")


def parse_snapshot(text: str) -> Snapshot:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CswapError(f"cswap returned invalid JSON: {exc}") from exc
    snap = Snapshot()
    version = data.get("schemaVersion")
    if version != 1:
        snap.schema_warning = f"unexpected cswap schemaVersion {version!r}"
    for raw in data.get("accounts", []):
        usage = raw.get("usage")
        status = raw.get("usageStatus") or ""
        acct = Account(number=int(raw.get("number", 0)),
                       email=raw.get("email", "?"),
                       org=raw.get("organizationName", ""),
                       active=bool(raw.get("active", False)),
                       ok=status == "ok" and isinstance(usage, dict),
                       status=status,
                       fetched_at=raw.get("usageFetchedAt") or "")
        if acct.ok:
            if "fiveHour" in usage:
                acct.metrics.append(_metric("5h", "5h", "5h", usage["fiveHour"]))
            if "sevenDay" in usage:
                acct.metrics.append(_metric("7d", "7d", "7d", usage["sevenDay"]))
            if "spend" in usage:
                acct.metrics.append(_metric("spend", "Spend", "$", usage["spend"]))
            for scoped in usage.get("scoped", []):
                name = scoped.get("name") or "?"
                acct.metrics.append(_metric(f"scoped:{name}", name,
                                            name[:1].upper() or "?", scoped))
        snap.accounts.append(acct)
    snap.fetched_at = snapshot_stamp(snap.accounts)
    return snap


def fetch(fresh: bool = False) -> Snapshot:
    """Snapshot from cswap; fresh=True force-freshens the store first.

    Fresh path: one combined prime+list process. When that is unavailable
    (no venv python, version drift, timeout) fall back to the old two-step
    behavior — best-effort prime, then the plain binary list.
    """
    if fresh:
        combined = fetch_combined()
        if combined is not None:
            try:
                return parse_snapshot(combined)
            except CswapError:
                pass  # malformed combined output — the binary list decides
        elif _combined_unsupported:
            prime_fresh()  # combined can never run here: old two-step behavior
    return parse_snapshot(_run(["list", "--json"]))


def switch(number: int) -> None:
    _run(["switch", str(number)])


def remove_account(number: int) -> None:
    """Delete a managed slot — its stored credential backup included.

    The ACTIVE slot is refused: it is the live login, so auto-registration
    would re-add it within a minute and the removal could only ever look
    like a silent failure. cswap's [y/N] prompt (its CLI has no --yes
    flag) is answered on stdin, and the slot NUMBER is sent rather than
    the email, which can hit an interactive ambiguity prompt when one
    address fills two slots. cswap's own refusals — e.g. a live
    `cswap run` session holding the slot — surface as CswapError.
    """
    snap = fetch()
    target = next((a for a in snap.accounts if a.number == number), None)
    if target is None:
        raise CswapError(f"no account #{number} to remove")
    if target.active:
        raise CswapError("the active account cannot be removed — make "
                         "another account active first")
    _run(["remove", str(number)], feed="y\n")


def add() -> None:
    """Register or re-capture the current login as a managed account.

    `cswap add` without a slot never prompts: a new account auto-assigns
    the next slot, an already-registered one refreshes its stored
    credential in place (and clears its dead-token state), and a
    logged-out state fails cleanly ("Please log in first") — which
    callers treat as a normal skip.
    """
    _run(["add"])
