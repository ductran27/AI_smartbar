"""Orchestration for the System tab: assemble a sample into the display
payload, keep the small persistent state (history ring, previous-CPU map,
first-seen times for the auto-kill grace period), and carry out a guarded
kill. The pure policy is in core/sysmon.py and the machine reads are in
core/sysmon_probe.py; this layer is the only one that writes state, signals
processes and talks to the clock.

Two entry points feed the UIs:
  * background_tick() — one 60 s poll: builds the payload AND does the side
    effects (history, auto-kill, state). The macOS app spawns this via
    `--sysmon --json`; the painted trays call it in-process.
  * stream() — one JSON line per second while the System tab is open: the
    LIVE view, deliberately WITHOUT side effects (the background tick owns
    history and auto-kill). It self-exits when its parent dies so a closed
    popover can never leave a sampler burning — the very failure this whole
    feature exists to catch.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
from datetime import datetime

from smartbar.core import paths, portable, sysmon, sysmon_probe

STREAM_MAX_SECONDS = 1800     # backstop: never stream longer than 30 min


# --- persistent state ------------------------------------------------------

def _state_file() -> str:
    """Resolved per call, never frozen at import — SMARTBAR_CACHE_DIR is set
    by tests and e2e fences after this module is already loaded (the exact
    trap paths.py was written to avoid)."""
    return os.path.join(paths.cache_dir(), "sysmon-state.json")


def _lock_file() -> str:
    """Mutex for the read-modify-write of sysmon-state.json — resolved per
    call for the same SMARTBAR_CACHE_DIR reason as _state_file()."""
    return os.path.join(paths.cache_dir(), "sysmon.lock")


def load_state() -> dict:
    try:
        with open(_state_file()) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    path = _state_file()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    # A per-call temp name: a fixed "<path>.tmp" let two overlapping ticks
    # (the timer and a kill's refresh) write the same file — one os.replace
    # then raised or promoted the other's half-written state.
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".sysmon-state-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle)
        os.replace(tmp, path)   # atomic: a reader never sees half
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- helpers ---------------------------------------------------------------

def _my_uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else -1


def _own_pids() -> set:
    """This process and its parent — the sampler and the app that spawned it
    must never be offered as kill targets."""
    pids = {os.getpid()}
    try:
        pids.add(os.getppid())
    except OSError:
        pass
    return pids


def _current_table() -> dict:
    procs, *_ = sysmon_probe.sample(interval=0.0)
    return {proc.pid: proc for proc in procs}


def _build(side_effects: bool):
    """(view, alerts, autokilled). With side_effects, also append history,
    run auto-kill and persist state; without, read-only (the live stream)."""
    # A short window is enough to read per-process CPU%; the 60 s cadence is
    # the runner's schedule, not this in-tick sample's width.
    procs, cores, mem, load = sysmon_probe.sample(interval=0.5)
    # Hold a lock across the load_state → compute → save_state window (the
    # auto-kill wait alone spans up to 3 s). A kill's re-poll can spawn a
    # second background_tick while the 60 s timer's own tick is still in
    # flight; both blind-save the whole state object, so the loser's
    # os.replace silently clobbers the winner's history sample or reverts a
    # firstSeen entry (resetting the auto-kill grace clock). If another tick
    # holds it, degrade to a read-only build — a display payload with no side
    # effects — exactly as presence skips an overlapping beat.
    handle = portable.lock(_lock_file()) if side_effects else None
    if side_effects and handle is None:
        side_effects = False
    try:
        state = load_state()
        prev_cpu = {int(k): v for k, v in state.get("prevCpu", {}).items()}
        ring = [tuple(entry) for entry in state.get("history", [])]
        minute = int(time.time() // 60)
        if side_effects:
            # Append THIS tick's point before building the series, or the
            # newest history column is always a gap and lastPct lags one poll.
            pct = round(sum(cores) / len(cores)) if cores else 0
            ring = sysmon.history_append(ring, minute, pct)
        series = sysmon.history_series(ring, minute)
        view = sysmon.build_view(procs, cores, mem, load, prev_cpu,
                                 datetime.now(), _my_uid(),
                                 _own_pids(), series)
        if not side_effects:
            return view, [], []

        # Auto-kill and its grace tracking act on the FULL junk set the view
        # carries, not the 8 rows the panel displays — a 9th junk orphan used
        # to fall off the tracked set every tick and never age into
        # eligibility.
        first_seen = dict(state.get("firstSeen", {}))
        now_epoch = time.time()
        junk_rows = view["leftovers"]["junk"]
        junk = {row["token"] for row in junk_rows}
        for token in junk:
            first_seen.setdefault(token, now_epoch)
        for token in list(first_seen):
            if token not in junk:
                del first_seen[token]

        autokilled = []
        for token in sysmon.autokill_targets(junk_rows, first_seen, now_epoch):
            row = next((r for r in junk_rows if r["token"] == token), None)
            ok, _ = kill(token)
            if ok and row is not None:
                autokilled.append({"name": row["name"], "cores": row["cores"],
                                   "age": row["age"]})
                first_seen.pop(token, None)
        alerts = sysmon.alerts(view["leftovers"]["rows"], autokilled)

        save_state({"history": [list(entry) for entry in ring],
                    "prevCpu": {str(proc.pid): proc.cpu for proc in procs},
                    "firstSeen": first_seen})
        return view, alerts, autokilled
    finally:
        if handle is not None:
            handle.close()


def background_tick() -> dict:
    """One background poll → the full payload, side effects included."""
    view, alerts, autokilled = _build(side_effects=True)
    view["alerts"] = alerts
    view["autokilled"] = autokilled
    view["live"] = False
    # The device's configured cadence rides in the payload so the macOS app
    # can honour SMARTBAR_SYSMON_INTERVAL without reading the variable
    # itself (the parity rule: Swift maps nothing).
    view["pollInterval"] = sysmon.interval()
    return view


# --- streaming -------------------------------------------------------------

def _parent_gone() -> bool:
    try:
        return os.getppid() == 1
    except OSError:
        return False


def stream(out=None, interval: float = 1.0, stop=None) -> int:
    """Emit one JSON line per second until the parent dies, the pipe closes,
    `stop()` returns True, or the 30-minute backstop trips. Display only."""
    out = out or sys.stdout
    start = time.monotonic()
    while True:
        if stop is not None and stop():
            break
        if _parent_gone() or time.monotonic() - start > STREAM_MAX_SECONDS:
            break
        view, _, _ = _build(side_effects=False)
        view["live"] = True
        try:
            out.write(json.dumps(view) + "\n")
            out.flush()
        except (BrokenPipeError, ValueError):
            break   # the reader (a closed popover) went away
        if interval:
            # The sample itself already took ~0.5 s of the cadence.
            time.sleep(max(0.0, interval - 0.5))
    return 0


# --- guarded kill ----------------------------------------------------------

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _signal(pids, sig) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def _session_set(table) -> set:
    """Every pid inside a live claude/codex tree — never a kill target,
    even when it sits inside the tree (or group) being killed."""
    out: set = set()
    for proc in table.values():
        if sysmon.is_session_exe(proc.args):
            out |= sysmon.tree_pids(proc.pid, table)
    return out


def _terminate(targets) -> None:
    """SIGTERM the set, wait up to 3 s, then SIGKILL survivors (esbuild's
    service loop ignores SIGTERM — the original battery-drain forensics).
    One round for the whole set: killing tree members one-by-one re-sampled
    the table between members and reported the already-dead rest as
    failures. On Windows SIGTERM is already TerminateProcess (unconditional)
    and probing liveness with os.kill(pid, 0) would itself terminate the
    probed process, so the wait loop is POSIX-only."""
    _signal(targets, signal.SIGTERM)
    if sys.platform == "win32":
        return
    deadline = time.time() + 3
    while time.time() < deadline:
        if not any(_alive(p) for p in targets):
            break
        time.sleep(0.2)
    survivors = [p for p in targets if _alive(p)]
    if survivors:
        _signal(survivors, getattr(signal, "SIGKILL", signal.SIGTERM))


def kill(token: str) -> tuple:
    """Kill what a token names, with every core guard applied.

    Three shapes, decided by the layer that built the row:
      * "pid:start"           — one process (a Busy row's single member).
      * "group:a:s,b:s"       — the listed members only, no tree expansion
                                (a folded Busy row; killing members' whole
                                trees took down login shells and sessions).
      * "tree:pid:start"      — the root and its descendants (a leftover
                                row), MINUS any live session's tree, the
                                bar's own pids and other users' pids.
    A member that is already gone counts as success inside group/tree — the
    goal state is "not running". SMARTBAR_SYSMON_KILL=off validates and
    reports but signals nothing (dry-run for tests and e2e fences)."""
    table = _current_table()
    my_uid = _my_uid()
    own = _own_pids()
    dry = os.environ.get("SMARTBAR_SYSMON_KILL", "").strip().lower() == "off"

    if token.startswith("group:"):
        members = [m for m in token[6:].split(",") if m]
        targets, errors = [], []
        for member in members:
            ok, error = sysmon.validate_kill(member, table, my_uid, own)
            if ok:
                targets.append(sysmon.parse_token(member)[0])
            elif "already gone" not in error:
                errors.append(error)
        if not targets and not errors:
            return False, "that process is already gone"
        if not targets:
            return False, "; ".join(errors)
        if not dry:
            _terminate(set(targets))
        return True, ""

    tree = token.startswith("tree:")
    bare = token[5:] if tree else token
    ok, error = sysmon.validate_kill(bare, table, my_uid, own)
    if not ok:
        return False, error
    if dry:
        return True, ""

    pid, _ = sysmon.parse_token(bare)
    if tree:
        sessions = _session_set(table)
        targets = {p for p in sysmon.tree_pids(pid, table)
                   if p not in sessions and p not in own
                   and (my_uid < 0 or table[p].uid == my_uid)
                   and not sysmon.is_self_exe(table[p].args)}
    else:
        targets = {pid}
    _terminate(targets)
    return True, ""
