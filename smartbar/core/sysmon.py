"""Pure policy for the System tab — no OS calls, no graphics, unit-testable.

Mirrors the codex.py / plan.py shape: every rule, threshold, kill-token
check, auto-kill decision and display string lives here; the probe
(sysmon_probe.py) reads the machine, the runner (sysmon_runner.py) does the
side effects, the renderers draw. Keeping this module free of subprocess and
ctypes is what lets the classification and kill-guard logic be tested from a
plain `ps`-style fixture on any OS.

Rules are anchored on the EXECUTABLE PATH, never on free argv text: a rule
matched against the whole command line would classify the scanner's own
shell as the very thing its arguments happen to mention (a `grep esbuild
--service` process is not an esbuild service). The feasibility spike hit
exactly this, so `_rule_for` matches `exe` against argv[0] and the optional
`flag` against the REST of the argv only.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


def enabled() -> bool:
    """False when SMARTBAR_SYSMON=off — hides the tab and skips all sampling."""
    return os.environ.get("SMARTBAR_SYSMON", "").strip().lower() != "off"


def _env_float(name: str, fallback: float) -> float:
    try:
        return float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return fallback


def hot_threshold() -> float:
    """% CPU (over two consecutive samples) that puts a process in Busy."""
    return _env_float("SMARTBAR_SYSMON_HOT", 50.0)


def interval() -> int:
    """Background sample period in seconds, floored at 15."""
    return max(15, int(_env_float("SMARTBAR_SYSMON_INTERVAL", 60.0)))


def autokill_enabled() -> bool:
    """True only when SMARTBAR_SYSMON_AUTOKILL=on — default OFF on purpose:
    automatically killing processes is a strong action, so it is opt-in even
    though the allowlist is narrow."""
    return os.environ.get("SMARTBAR_SYSMON_AUTOKILL", "").strip().lower() == "on"


def notify_enabled() -> bool:
    """False when SMARTBAR_SYSMON_NOTIFY=off — silences leftover alerts."""
    return os.environ.get("SMARTBAR_SYSMON_NOTIFY", "").strip().lower() != "off"


@dataclass
class Proc:
    """One raw process sample, as the probe hands it to the policy layer.

    `cpu` is the percentage over the sample window (the probe computes it as
    Δ cumulative CPU time / Δ wall time); `start` is the process start epoch,
    used to build a kill token that survives PID reuse (0 = unknown).
    """
    pid: int
    ppid: int
    uid: int
    rss_kb: int
    elapsed: int          # seconds since the process started
    cpu: float            # % CPU over the sample window
    args: str             # full command line (argv joined by spaces)
    start: int = 0        # start epoch; 0 when the probe could not read it


# --- classification --------------------------------------------------------

@dataclass
class Rule:
    """One classification rule. `exe` matches the EXECUTABLE token only
    (argv[0], which for an interpreter is `node`/`python…`); `flag` (when
    set) matches anywhere in the full command line. The split is the whole
    point: a process is the thing it RUNS, not a thing its arguments name."""
    kind: str             # "junk" or "idle"
    label: str            # human name for the foot line / logs
    exe: str              # regex against the exe token
    flag: str = ""        # regex against the full args ("" = always true)


# JUNK: never legitimate as an ORPHAN (a live parent downgrades to "watch").
# Anchored on argv[0] so `grep esbuild --service` (exe = a shell) never
# matches. First match wins, so order these specific-first.
JUNK_RULES = [
    Rule("junk", "esbuild service", r"(^|/)esbuild$", r"(^|\s)--service"),
    Rule("junk", "puppeteer Chrome for Testing", r"Chrome for Testing$", r""),
    Rule("junk", "headless Chrome (CDP)", r"(Google Chrome|Chromium)$",
         r"--headless.*--user-data-dir=(/private)?/tmp/cdp-prof-"),
    Rule("junk", "playwright browser", r"ms-playwright/", r"--headless"),
    Rule("junk", "Claude Code shell snapshot", r"(^|/)(z|ba)?sh$",
         r"shell-snapshots/"),
]
# LEFTOVER: an orphaned dev server. Killable, but NEVER auto-killed — an
# intentionally detached server is indistinguishable from a forgotten one.
LEFTOVER_RULES = [
    # An interpreter names its script in argv[1..], so the exe token here is
    # "…/node /path/to/vite.js" — match the interpreter followed by a space
    # (or the end), not anchored to the token's end the way a plain binary is.
    Rule("idle", "dev server", r"(^|/)(node|python[0-9.]*|bun|deno)(\s|$)",
         r"\b(vite|serve-dist\.mjs|serve\.mjs|http\.server|live-server|"
         r"next|webpack|uvicorn|flask)\b"),
]
_SESSION_EXE = re.compile(r"(^|/)(claude|codex)$")


def exe_token(args: str) -> str:
    """The executable portion of a command line: everything up to the first
    " -" (where arguments begin). Executable paths — bundle paths with
    spaces included ("…/Google Chrome") — almost never contain " -", while
    arguments start with a dash, so this recovers argv[0] (plus an
    interpreter's script path) without a real argv array to split."""
    cut = args.find(" -")
    return (args if cut < 0 else args[:cut]).strip()


def _rule_for(args: str):
    exe = exe_token(args)
    for rule in JUNK_RULES + LEFTOVER_RULES:
        if re.search(rule.exe, exe) and (not rule.flag
                                         or re.search(rule.flag, args)):
            return rule
    return None


def is_session_exe(args: str) -> bool:
    """True for a `claude`/`codex` process itself (its descendants are made
    sessions in build_view, which alone has the process tree)."""
    return bool(_SESSION_EXE.search(exe_token(args)))


def classify(proc, orphan: bool, cpu: float, prev_cpu: float, my_uid: int):
    """The kind of one process, or None (unremarkable, not listed).

    * "system"  — another user's process (never killable)
    * "session" — a claude/codex process
    * "junk"    — matches a never-legitimate rule AND is an orphan
    * "watch"   — that same rule but with a live parent (counted, not shown)
    * "idle"    — an orphaned dev server (killable, never automatic)
    * "hot"     — >= the hot threshold in BOTH this sample and the last
    """
    if my_uid >= 0 and proc.uid != my_uid:
        return "system"
    if is_session_exe(proc.args):
        return "session"
    rule = _rule_for(proc.args)
    if rule is not None:
        if rule.kind == "junk":
            return "junk" if orphan else "watch"
        return "idle" if orphan else "watch"
    if cpu >= hot_threshold() and prev_cpu >= hot_threshold():
        return "hot"
    return None
