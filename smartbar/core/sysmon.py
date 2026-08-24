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

import math
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


# --- process trees + guarded kill ------------------------------------------

def kill_token(proc) -> str:
    """The opaque handle a UI sends back to kill this process: pid + start
    epoch. The start time makes the token survive PID reuse — by the time a
    click arrives the pid may name a different process, and killing that one
    would be a real bug (this is why a raw pid is never enough)."""
    return f"{proc.pid}:{proc.start}"


def _children_map(table) -> dict:
    kids: dict = {}
    for proc in table.values():
        kids.setdefault(proc.ppid, []).append(proc.pid)
    return kids


def tree_pids(root: int, table) -> set:
    """`root` and every descendant present in `table` (breadth-first, cycle
    safe). A leftover row is a whole tree: a headless Chrome's cost lives in
    its GPU helper, and killing the root without the helpers leaves them
    burning."""
    kids = _children_map(table)
    seen, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in table:
            continue
        seen.add(pid)
        stack.extend(kids.get(pid, ()))
    return seen


def tree_cpu(root: int, table) -> float:
    return round(sum(table[pid].cpu for pid in tree_pids(root, table)), 1)


def tree_mem_mb(root: int, table) -> int:
    return round(sum(table[pid].rss_kb
                     for pid in tree_pids(root, table)) / 1024)


def parse_token(token: str):
    """(pid, start) from a "pid:start" token, or None if malformed."""
    pid_str, _, start_str = (token or "").partition(":")
    try:
        return int(pid_str), int(start_str)
    except (TypeError, ValueError):
        return None


def validate_kill(token: str, table, my_uid: int, own_pids) -> tuple:
    """(ok, reason) — may this token be killed right now?

    Refuses, in order: a malformed token, a pid no longer present, a pid
    whose start time no longer matches the token (PID reuse), the app's own
    process tree, another user's process, and a live session. The checks run
    against a FRESH table at kill time, never the snapshot the row was drawn
    from — the world moves between drawing a row and clicking it.
    """
    parsed = parse_token(token)
    if parsed is None:
        return False, f"malformed kill token {token!r}"
    pid, start = parsed
    proc = table.get(pid)
    if proc is None:
        return False, "that process is already gone"
    if proc.start and start and proc.start != start:
        return False, "that PID was reused by another process — refusing"
    if pid in own_pids:
        return False, "refusing to kill AI smartbar itself"
    if my_uid >= 0 and proc.uid != my_uid:
        return False, "that process belongs to another user"
    if is_session_exe(proc.args):
        return False, "that is a live Claude/Codex session — close it there"
    return True, ""


# --- display formatting ----------------------------------------------------

MAX_CORE_COLUMNS = 32
HISTORY_LEN = 60
_DEV_KEYWORDS = ("serve-dist.mjs", "serve.mjs", "http.server", "live-server",
                 "vite", "next", "webpack", "uvicorn", "flask")


def format_age(seconds: int) -> str:
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} m"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def _bundle_or_base(args: str) -> str:
    """The Mac .app bundle name if the exe is bundled, else argv[0]'s base."""
    exe = exe_token(args)
    m = re.search(r"/([^/]+)\.app/", exe)
    return m.group(1) if m else _basename(exe)


def display_name(proc) -> str:
    """A short, human name for a process row."""
    args = proc.args
    rule = _rule_for(args)
    if rule is not None:
        if rule.label == "headless Chrome (CDP)":
            base = "Chromium" if "Chromium" in exe_token(args) else "Google Chrome"
            return f"{base} (headless)"
        if rule.label == "esbuild service":
            return "esbuild --service"
        if rule.label == "puppeteer Chrome for Testing":
            return "Chrome for Testing"
        if rule.label == "playwright browser":
            return "playwright browser"
        if rule.label == "Claude Code shell snapshot":
            return "shell snapshot"
        if rule.label == "dev server":
            interp = _basename(exe_token(args).split(" ", 1)[0])
            for keyword in _DEV_KEYWORDS:
                if re.search(rf"\b{re.escape(keyword)}\b", args):
                    return f"{interp} {_basename(keyword)}"
            return interp
    return _bundle_or_base(args)


def display_sub(proc) -> str:
    """A secondary identifier: the pid, plus a profile or port when there is
    one worth naming."""
    args = proc.args
    m = re.search(r"/tmp/cdp-prof-(\d+)", args)
    if m:
        return f"pid {proc.pid} · cdp-prof-{m.group(1)}"
    m = re.search(r"(?:--port[= ]|:)(\d{2,5})\b", args)
    if m and _rule_for(args) is not None:
        return f"pid {proc.pid} · :{m.group(1)}"
    return f"pid {proc.pid}"


def _mem_text(kb: int) -> str:
    mb = kb / 1024
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{round(mb)} MB"


def _fold_cores(cores: list) -> list:
    """Per-core percentages capped at MAX_CORE_COLUMNS, averaging adjacent
    cores into a single column when there are more (a 64-core box shows 32
    two-core columns, not 64 slivers)."""
    values = [round(c) for c in cores]
    if len(values) <= MAX_CORE_COLUMNS:
        return values
    size = math.ceil(len(values) / MAX_CORE_COLUMNS)
    return [round(sum(values[i:i + size]) / len(values[i:i + size]))
            for i in range(0, len(values), size)]


# --- the display-ready payload ---------------------------------------------

def _session_pids(procs, table) -> set:
    """Every pid that is a claude/codex process OR a descendant of one — a
    session's whole tree (MCP servers, shells, helpers) is off the kill path
    and out of the leftover/idle buckets, which per-process classify() cannot
    know on its own."""
    out: set = set()
    for proc in procs:
        if is_session_exe(proc.args):
            out |= tree_pids(proc.pid, table)
    return out


def build_view(procs, cores, mem, load, prev_cpu, now, my_uid, own_pids,
               history) -> dict:
    """Turn one sample into the FINAL display payload the renderers draw and
    Swift decodes verbatim. Every string is formed here; no consumer maps."""
    table = {p.pid: p for p in procs}
    sessions = _session_pids(procs, table)

    kinds = {}
    for proc in procs:
        orphan = proc.ppid == 1
        kind = classify(proc, orphan, proc.cpu, prev_cpu.get(proc.pid, 0.0),
                        my_uid)
        if proc.pid in sessions:
            kind = "session"
        kinds[proc.pid] = kind

    # --- leftovers: one row per orphaned junk/idle ROOT (a whole tree) -----
    leftover_members: set = set()
    left_rows = []
    hot = hot_threshold()
    for proc in procs:
        if kinds[proc.pid] not in ("junk", "idle"):
            continue
        leftover_members |= tree_pids(proc.pid, table)
        cpu = tree_cpu(proc.pid, table)
        burning = kinds[proc.pid] == "junk" and cpu >= hot
        cpu_text = f"{int(cpu)}%" if cpu >= 1 else "idle"
        left_rows.append({
            "token": kill_token(proc),
            "kind": kinds[proc.pid],
            "name": display_name(proc),
            "sub": display_sub(proc),
            "meta": f"orphan · {format_age(proc.elapsed)} · {cpu_text}",
            "burning": burning,
            "cores": round(cpu / 100, 1),
            "mem": tree_mem_mb(proc.pid, table),
            "age": proc.elapsed,
        })
    left_rows.sort(key=lambda r: (not r["burning"], -r["cores"]))
    burning_rows = [r for r in left_rows if r["burning"]]
    if burning_rows:
        chip = (f"{len(burning_rows)} burning · "
                f"{sum(r['cores'] for r in burning_rows):.1f} cores")
    elif left_rows:
        chip = f"{len(left_rows)} idle"
    else:
        chip = ""
    more = max(0, len(left_rows) - 8)
    watched = sum(1 for k in kinds.values() if k == "watch")
    foot = (f"Auto-kill {'on' if autokill_enabled() else 'off'} · "
            f"junk rules: {len(JUNK_RULES)}")
    if watched:
        foot += f" · {watched} watched"

    # --- busy: top folded processes (excludes leftover trees) --------------
    folds = {}
    for proc in procs:
        if proc.pid in leftover_members:
            continue
        name = display_name(proc)
        fold = folds.setdefault(name, {"cpu": 0.0, "mem": 0, "pids": [],
                                       "kinds": set(), "procs": []})
        fold["cpu"] += proc.cpu
        fold["mem"] += proc.rss_kb
        fold["pids"].append(proc.pid)
        fold["kinds"].add(kinds[proc.pid])
        fold["procs"].append(proc)
    busy_rows = []
    for name, fold in folds.items():
        if fold["cpu"] < 1:
            continue
        if "session" in fold["kinds"]:
            kind = "session"
        elif fold["kinds"] == {"system"}:
            kind = "system"
        else:
            kind = "hot"
        killable = kind not in ("session", "system")
        procs_sorted = sorted(fold["procs"], key=lambda p: -p.cpu)
        if len(procs_sorted) == 1:
            token = kill_token(procs_sorted[0])
        else:
            token = "group:" + ",".join(kill_token(p) for p in procs_sorted)
        count = len(procs_sorted)
        busy_rows.append({
            "token": token,
            "kind": kind,
            "name": name,
            "sub": f"×{count}" if count > 1 else "",
            "count": count,
            "cpu": round(fold["cpu"]),
            "mem": round(fold["mem"] / 1024),
            "meta": f"{round(fold['cpu'])}% · {_mem_text(fold['mem'])}",
            "killable": killable,
        })
    busy_rows.sort(key=lambda r: -r["cpu"])
    busy_rows = busy_rows[:6]

    # --- vitals ------------------------------------------------------------
    folded_cores = _fold_cores(cores)
    cpu_pct = round(sum(cores) / len(cores)) if cores else 0
    claude_n = sum(1 for p in procs if re.search(r"(^|/)claude$",
                                                 exe_token(p.args)))
    codex_n = sum(1 for p in procs if re.search(r"(^|/)codex$",
                                                exe_token(p.args)))
    caption_parts = []
    if claude_n:
        caption_parts.append(f"{claude_n} claude")
    if codex_n:
        caption_parts.append(f"{codex_n} codex")
    caption_parts.append(f"{len(procs)} procs")
    cpu_caption = " · ".join(caption_parts)

    total_gb = mem.get("totalBytes", 0) / 2**30
    used_gb = mem.get("usedBytes", 0) / 2**30
    mem_caption = f"{used_gb:.1f} / {total_gb:.0f} GB"
    comp = mem.get("compressedBytes")
    if comp:
        mem_caption += f" · {comp / 2**30:.1f} GB compressed"

    hist = list(history)
    present = [p for p in hist if p is not None]
    peak = max(present) if present else 0
    last = present[-1] if present else 0

    load0, load1, load2 = (list(load) + [0, 0, 0])[:3]
    machine_caption = (f"{len(cores)} cores · {total_gb:.0f} GB · "
                       f"load {load0:.1f} · {load1:.1f} · {load2:.1f}")

    return {
        "sampledAt": now.strftime("%H:%M"),
        "machine": {"caption": machine_caption},
        "cpu": {"pct": cpu_pct, "cores": folded_cores, "caption": cpu_caption},
        "history": {"pct": hist, "peakText": f"peak {peak}%", "lastPct": last},
        "mem": {"pct": mem.get("pct", 0.0), "caption": mem_caption},
        "leftovers": {"chip": chip, "rows": left_rows[:8], "more": more,
                      "foot": foot},
        "busy": {"caption": f"≥ {int(hot)}% CPU over two samples",
                 "rows": busy_rows},
    }


# --- history ring, auto-kill decision, alerts ------------------------------

AUTOKILL_MIN_AGE = 300      # seconds a junk orphan must persist before auto-kill


def history_append(ring, minute: int, pct: int):
    """Append (minute, pct) to the ring, or update the last entry when it is
    the same minute (a poll fires more than once a minute); cap at
    HISTORY_LEN. Returns a NEW list — the caller persists it."""
    ring = list(ring)
    if ring and ring[-1][0] == minute:
        ring[-1] = (minute, pct)
    else:
        ring.append((minute, pct))
    return ring[-HISTORY_LEN:]


def history_series(ring, now_minute: int, span: int = HISTORY_LEN) -> list:
    """The last `span` minutes as a plain list ending at `now_minute`, with a
    missed minute (sleep, app not running) left as None rather than smeared —
    a gap is honest about "nothing was sampled then"."""
    by_minute = dict(ring)
    start = now_minute - span + 1
    return [by_minute.get(start + i) for i in range(span)]


def autokill_targets(rows, first_seen, now_monotonic: float) -> list:
    """Kill tokens auto-kill should act on this tick: only `junk` rows, only
    when they have persisted at least AUTOKILL_MIN_AGE seconds (measured from
    when this process first saw them, so a fresh start does not insta-kill),
    and only when SMARTBAR_SYSMON_AUTOKILL=on. Idle dev servers are never
    automatic."""
    if not autokill_enabled():
        return []
    out = []
    for row in rows:
        if row.get("kind") != "junk":
            continue
        seen = first_seen.get(row["token"])
        if seen is not None and now_monotonic - seen >= AUTOKILL_MIN_AGE:
            out.append(row["token"])
    return out


def alerts(rows, autokilled) -> list:
    """Notifications for this tick. One per auto-killed process (what and how
    much it was costing), or — when auto-kill is off and leftovers are
    burning — a single "come clean these up" nudge. Silent when
    SMARTBAR_SYSMON_NOTIFY=off."""
    if not notify_enabled():
        return []
    out = []
    for killed in autokilled:
        out.append({
            "title": f"Killed {killed['name']}",
            "body": (f"{killed['cores']:.1f} cores · "
                     f"{format_age(killed['age'])} — an orphaned process a "
                     f"dead session left behind."),
        })
    if not autokill_enabled():
        burning = [r for r in rows if r.get("burning")]
        if burning:
            cores = sum(r["cores"] for r in burning)
            out.append({
                "title": f"{len(burning)} leftovers burning {cores:.0f} cores",
                "body": "Dead sessions left processes running. Open the "
                        "System tab to kill them.",
            })
    return out
