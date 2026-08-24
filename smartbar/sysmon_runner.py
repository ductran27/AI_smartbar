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
import time
from datetime import datetime, timezone

from smartbar.core import paths, sysmon, sysmon_probe

STREAM_MAX_SECONDS = 1800     # backstop: never stream longer than 30 min


# --- persistent state ------------------------------------------------------

def _state_file() -> str:
    """Resolved per call, never frozen at import — SMARTBAR_CACHE_DIR is set
    by tests and e2e fences after this module is already loaded (the exact
    trap paths.py was written to avoid)."""
    return os.path.join(paths.cache_dir(), "sysmon-state.json")


def load_state() -> dict:
    try:
        with open(_state_file()) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    path = _state_file()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(state, handle)
    os.replace(tmp, path)   # atomic: a concurrent reader never sees half


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
    state = load_state()
    prev_cpu = {int(k): v for k, v in state.get("prevCpu", {}).items()}
    ring = [tuple(entry) for entry in state.get("history", [])]
    minute = int(time.time() // 60)
    series = sysmon.history_series(ring, minute)
    view = sysmon.build_view(procs, cores, mem, load, prev_cpu,
                             datetime.now(timezone.utc), _my_uid(),
                             _own_pids(), series)
    if not side_effects:
        return view, [], []

    ring = sysmon.history_append(ring, minute, view["cpu"]["pct"])
    first_seen = dict(state.get("firstSeen", {}))
    now_epoch = time.time()
    junk = {row["token"] for row in view["leftovers"]["rows"]
            if row["kind"] == "junk"}
    for token in junk:
        first_seen.setdefault(token, now_epoch)
    for token in list(first_seen):
        if token not in junk:
            del first_seen[token]

    autokilled = []
    for token in sysmon.autokill_targets(view["leftovers"]["rows"], first_seen,
                                         now_epoch):
        row = next((r for r in view["leftovers"]["rows"]
                    if r["token"] == token), None)
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


def background_tick() -> dict:
    """One background poll → the full payload, side effects included."""
    view, alerts, autokilled = _build(side_effects=True)
    view["alerts"] = alerts
    view["autokilled"] = autokilled
    view["live"] = False
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
            time.sleep(interval)
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


def kill(token: str) -> tuple:
    """Kill the process (tree) a token names, with every core guard applied.

    A "group:" token (a folded Busy row) kills each member independently.
    SMARTBAR_SYSMON_KILL=off validates and reports but signals nothing — the
    dry-run tests and e2e fences depend on it. Otherwise: SIGTERM the whole
    tree, wait up to 3 s, then SIGKILL any survivor (esbuild's service loop
    ignores SIGTERM — found in the original battery-drain forensics)."""
    if token.startswith("group:"):
        results = [kill(member) for member in token[6:].split(",") if member]
        ok = all(result[0] for result in results)
        error = "; ".join(r[1] for r in results if not r[0])
        return ok, error

    table = _current_table()
    ok, error = sysmon.validate_kill(token, table, _my_uid(), _own_pids())
    if not ok:
        return False, error
    if os.environ.get("SMARTBAR_SYSMON_KILL", "").strip().lower() == "off":
        return True, ""   # dry-run

    pid, _ = sysmon.parse_token(token)
    targets = sysmon.tree_pids(pid, table)
    _signal(targets, signal.SIGTERM)
    deadline = time.time() + 3
    while time.time() < deadline:
        if not any(_alive(p) for p in targets):
            break
        time.sleep(0.2)
    survivors = [p for p in targets if _alive(p)]
    if survivors:
        _signal(survivors, signal.SIGKILL)
    return True, ""
