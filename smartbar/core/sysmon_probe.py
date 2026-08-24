"""OS plumbing for the System tab: sample per-core CPU, memory and the
process table on each platform, and the pure parsers that turn their text
output into numbers.

Split from sysmon.py on purpose — everything here touches the machine
(subprocess, ctypes, /proc), so it is the part that cannot run in a unit test
on an arbitrary OS. The PARSERS, though, are pure and are tested against real
captured `ps`/`vm_stat` output; only the thin live-sampling wrappers need a
real host. Per-process CPU% is a delta of cumulative CPU time between two
snapshots (there is no per-interval CPU column in `ps`), which is why
`sample()` takes two `ps` reads a short wall-time apart.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

from smartbar.core import sysmon

_PS_ARGS = ["ps", "-Axwwo", "pid=,ppid=,uid=,rss=,etime=,time=,lstart=,args="]


# --- pure parsers ----------------------------------------------------------

def cpu_seconds(text: str) -> float:
    """Cumulative CPU time from ps TIME ("[[dd-]hh:]mm:]ss.cs"); minutes and
    hours are NOT capped at 60 (ps prints "213:23.48" for 213 minutes), and
    procps prefixes days as "1-02:03:04" once a process has burned a day of
    CPU — dropping those rows made long-lived sessions vanish."""
    days = 0
    if "-" in text:
        day_str, text = text.split("-", 1)
        days = int(day_str)
    parts = text.split(":")
    seconds = float(parts[-1])
    if len(parts) >= 2:
        seconds += int(parts[-2]) * 60
    if len(parts) >= 3:
        seconds += int(parts[-3]) * 3600
    return days * 86400 + seconds


def etime_seconds(text: str) -> int:
    """Elapsed time from ps ELAPSED ("[[dd-]hh:]mm:ss")."""
    days = 0
    if "-" in text:
        day_str, text = text.split("-", 1)
        days = int(day_str)
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return days * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_lstart(text: str) -> int:
    """Process start epoch from ps lstart ("Sun Aug  2 16:19:43 2026"), or 0.

    lstart is LOCAL time, so this uses mktime; callers only ever DIFF two of
    these or use them to detect PID reuse, never as an absolute wall clock."""
    normalized = " ".join(text.split())
    try:
        return int(time.mktime(time.strptime(normalized, "%a %b %d %H:%M:%S %Y")))
    except (ValueError, OverflowError):
        return 0


def parse_ps(text: str) -> list:
    """Rows from `ps -Axwwo pid=,ppid=,uid=,rss=,etime=,time=,lstart=,args=`.

    lstart is five whitespace tokens (weekday, month, day, HH:MM:SS, year),
    so the first eleven fields are fixed and args (which itself contains
    spaces) is the remainder — split with maxsplit=11."""
    rows = []
    for line in text.splitlines():
        fields = line.split(None, 11)
        if len(fields) < 12:
            continue
        pid, ppid, uid, rss, etime, cputime = fields[:6]
        lstart = " ".join(fields[6:11])
        args = fields[11]
        try:
            rows.append({
                "pid": int(pid), "ppid": int(ppid), "uid": int(uid),
                "rss_kb": int(rss), "elapsed": etime_seconds(etime),
                "cpu_seconds": cpu_seconds(cputime),
                "start": parse_lstart(lstart), "args": args,
            })
        except ValueError:
            continue
    return rows


def cpu_percent(pid: int, prev: dict, cur: dict, wall: float) -> float:
    """% CPU for one pid = Δ cumulative CPU time / wall time × 100. 0 when the
    pid was not in the previous sample (no baseline to diff against) — a fresh
    process reads 0 for one tick rather than a false spike."""
    if wall <= 0 or pid not in prev or pid not in cur:
        return 0.0
    return max(0.0, (cur[pid] - prev[pid]) / wall * 100.0)


def parse_vm_stat(text: str, total_bytes: int) -> dict:
    """Used memory from `vm_stat`, the way Activity Monitor counts it:
    (anonymous − purgeable + wired + compressor) × page size. The earlier
    active+wired+compressor sum undercounted by ~10 GB against Activity
    Monitor on this very machine (54% vs 68%); anonymous-minus-purgeable is
    what its "App Memory" actually is. Falls back to the old sum when the
    vm_stat build predates the "Anonymous pages" line."""
    page_match = re.search(r"page size of (\d+) bytes", text)
    page = int(page_match.group(1)) if page_match else 4096
    pages = {}
    for line in text.splitlines()[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            try:
                pages[name.strip()] = int(value.strip().rstrip("."))
            except ValueError:
                continue
    if "Anonymous pages" in pages:
        app_pages = max(0, pages["Anonymous pages"]
                        - pages.get("Pages purgeable", 0))
    else:
        app_pages = pages.get("Pages active", 0)
    used_pages = (app_pages
                  + pages.get("Pages wired down", 0)
                  + pages.get("Pages occupied by compressor", 0))
    used = used_pages * page
    return {
        "totalBytes": total_bytes,
        "usedBytes": used,
        "compressedBytes": pages.get("Pages occupied by compressor", 0) * page,
        "pct": round(100.0 * used / total_bytes, 1) if total_bytes else 0.0,
    }


# --- macOS per-core CPU via Mach (ctypes; freed with vm_deallocate) ---------

_PROCESSOR_CPU_LOAD_INFO = 2   # ticks per core: user, system, idle, nice


def core_ticks():
    """Per-core cumulative CPU ticks [(user, system, idle, nice), …] on macOS,
    or None if unavailable. Diff two of these to get per-core busy%."""
    if sys.platform != "darwin":
        return None
    import ctypes
    import ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    libc.mach_host_self.restype = ctypes.c_uint
    libc.host_processor_info.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_int)),
        ctypes.POINTER(ctypes.c_uint)]
    libc.host_processor_info.restype = ctypes.c_int
    libc.vm_deallocate.argtypes = [ctypes.c_uint, ctypes.c_void_p,
                                   ctypes.c_ulong]
    ncpu = ctypes.c_uint(0)
    info = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_uint(0)
    rc = libc.host_processor_info(libc.mach_host_self(),
                                  _PROCESSOR_CPU_LOAD_INFO, ctypes.byref(ncpu),
                                  ctypes.byref(info), ctypes.byref(count))
    if rc != 0:
        return None
    ticks = [info[i] for i in range(count.value)]
    try:
        task = ctypes.c_uint.in_dll(libc, "mach_task_self_")
        libc.vm_deallocate(task, ctypes.cast(info, ctypes.c_void_p),
                           count.value * ctypes.sizeof(ctypes.c_int))
    except (ValueError, OSError):
        pass
    return [tuple(ticks[i * 4:i * 4 + 4]) for i in range(ncpu.value)]


def core_busy(before, after) -> list:
    """Per-core busy% between two core_ticks() readings."""
    out = []
    for (u1, s1, i1, n1), (u2, s2, i2, n2) in zip(before, after):
        busy = (u2 - u1) + (s2 - s1) + (n2 - n1)
        total = busy + (i2 - i1)
        out.append(round(100.0 * busy / total, 1) if total > 0 else 0.0)
    return out


def _run(cmd) -> str:
    # LC_ALL=C: `ps -o lstart` is locale-formatted; a German LANG turned
    # every start time into "So.  2 Aug." → parse_lstart 0 → the PID-reuse
    # guard silently disabled. C pins the one format the parsers expect.
    return subprocess.check_output(cmd, text=True,
                                   env={**os.environ, "LC_ALL": "C"})


def _memsize() -> int:
    """Total RAM in bytes, with no PATH dependence.

    The Swift app launches the sampler with a hardened PATH that omits
    /usr/sbin, where `sysctl` lives — so a bare-name subprocess returned 0
    and the panel read "34.8 / 0 GB · 0%". ctypes sysctlbyname needs no
    PATH at all; the absolute-path subprocess is the fallback."""
    if sys.platform == "darwin":
        try:
            import ctypes
            import ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            value = ctypes.c_uint64(0)
            size = ctypes.c_size_t(ctypes.sizeof(value))
            rc = libc.sysctlbyname(b"hw.memsize", ctypes.byref(value),
                                   ctypes.byref(size), None, 0)
            if rc == 0 and value.value:
                return int(value.value)
        except (OSError, AttributeError, ValueError):
            pass
    for sysctl in ("/usr/sbin/sysctl", "sysctl"):
        try:
            return int(_run([sysctl, "-n", "hw.memsize"]).strip())
        except (subprocess.SubprocessError, ValueError, OSError):
            continue
    return 0


# --- putting a sample together ---------------------------------------------

def _procs_from(prev_rows, cur_rows, wall: float) -> list:
    """Build sysmon.Proc objects from two ps snapshots, cpu% from the delta."""
    prev = {r["pid"]: r["cpu_seconds"] for r in prev_rows}
    cur = {r["pid"]: r["cpu_seconds"] for r in cur_rows}
    out = []
    for row in cur_rows:
        out.append(sysmon.Proc(
            pid=row["pid"], ppid=row["ppid"], uid=row["uid"],
            rss_kb=row["rss_kb"], elapsed=row["elapsed"],
            cpu=cpu_percent(row["pid"], prev, cur, wall),
            args=row["args"], start=row["start"]))
    return out


def _seam():
    """(procs, cores, mem, load) from the SMARTBAR_SYSMON_PS/_STATS files, or
    None when the seam is not set. The whole live pipeline collapses to a
    deterministic fixture read so the runner and CLI are testable anywhere."""
    ps_path = os.environ.get("SMARTBAR_SYSMON_PS")
    if not ps_path:
        return None
    with open(ps_path) as handle:
        rows = parse_ps(handle.read())
    procs = _procs_from(rows, rows, 1.0)   # one snapshot → every cpu% is 0
    cores, mem, load = [], {"totalBytes": 0, "usedBytes": 0, "pct": 0.0}, (0, 0, 0)
    stats_path = os.environ.get("SMARTBAR_SYSMON_STATS")
    if stats_path:
        with open(stats_path) as handle:
            stats = json.load(handle)
        cores = stats.get("cores", [])
        mem = stats.get("mem", mem)
        load = tuple(stats.get("load", load))
    return procs, cores, mem, load


def _read(path: str) -> str:
    with open(path) as handle:
        return handle.read()


def _linux_core_busy(before: str, after: str) -> list:
    """Per-core busy% from two /proc/stat reads."""
    def rows(text):
        out = {}
        for line in text.splitlines():
            if line.startswith("cpu") and line[3:4].isdigit():
                nums = [int(x) for x in line.split()[1:]]
                idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
                out[line.split()[0]] = (sum(nums), idle)
        return out
    a, b = rows(before), rows(after)
    busy = []
    for key in sorted(a, key=lambda k: int(k[3:])):
        if key not in b:
            continue
        total_d = b[key][0] - a[key][0]
        idle_d = b[key][1] - a[key][1]
        busy.append(round(100.0 * (total_d - idle_d) / total_d, 1)
                    if total_d > 0 else 0.0)
    return busy


def _linux_mem() -> dict:
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        name, _, value = line.partition(":")
        info[name.strip()] = int(value.strip().split()[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    used = total - available
    return {"totalBytes": total, "usedBytes": used,
            "compressedBytes": info.get("SwapCached", 0),
            "pct": round(100.0 * used / total, 1) if total else 0.0}


def sample(interval: float = 0.5, my_uid: int = None) -> tuple:
    """One (procs, cores, mem, load) sample for the current platform.

    Takes two `ps` reads `interval` apart so each process gets a real per-
    interval CPU%; per-core CPU comes from a matching tick delta. The seam
    short-circuits all of this to a fixture read (tests, --preview)."""
    seamed = _seam()
    if seamed is not None:
        return seamed
    if my_uid is None:
        my_uid = os.getuid() if hasattr(os, "getuid") else -1

    if sys.platform == "darwin":
        t0 = time.monotonic()
        rows1 = parse_ps(_run(_PS_ARGS))
        ticks1 = core_ticks()
        time.sleep(interval)
        rows2 = parse_ps(_run(_PS_ARGS))
        ticks2 = core_ticks()
        # Divide by the MEASURED wall time: the two ps scans themselves take
        # tens of milliseconds, and dividing by the nominal interval
        # inflated every cpu% by their share.
        wall = time.monotonic() - t0
        procs = _procs_from(rows1, rows2, wall or interval or 0.5)
        cores = core_busy(ticks1, ticks2) if ticks1 and ticks2 else []
        mem = parse_vm_stat(_run(["vm_stat"]), _memsize())
        load = os.getloadavg()
        return procs, cores, mem, load

    if sys.platform.startswith("linux"):
        t0 = time.monotonic()
        stat1 = _read("/proc/stat")
        rows1 = parse_ps(_run(_PS_ARGS))
        time.sleep(interval)
        stat2 = _read("/proc/stat")
        rows2 = parse_ps(_run(_PS_ARGS))
        wall = time.monotonic() - t0
        procs = _procs_from(rows1, rows2, wall or interval or 0.5)
        cores = _linux_core_busy(stat1, stat2)
        return procs, cores, _linux_mem(), os.getloadavg()

    # Windows / anything else: an honest downgrade — a process list with
    # memory but no per-core CPU and no per-process CPU delta (no cheap
    # cumulative-time column). The tab still lists and can kill leftovers.
    return _fallback_procs(my_uid), [], {"totalBytes": 0, "usedBytes": 0,
                                          "pct": 0.0}, (0, 0, 0)


def _fallback_procs(my_uid: int) -> list:
    """Best-effort process list where `ps` with our columns is unavailable."""
    try:
        text = _run(["ps", "-Axwwo", "pid=,ppid=,rss=,args="])
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return []
    out = []
    for line in text.splitlines():
        fields = line.split(None, 3)
        if len(fields) < 4:
            continue
        try:
            out.append(sysmon.Proc(int(fields[0]), int(fields[1]), my_uid,
                                   int(fields[2]), 0, 0.0, fields[3]))
        except ValueError:
            continue
    return out
