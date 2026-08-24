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

import re
import subprocess
import sys
import time


# --- pure parsers ----------------------------------------------------------

def cpu_seconds(text: str) -> float:
    """Cumulative CPU time from ps TIME ("[[hh:]mm:]ss.cs"); minutes and hours
    are NOT capped at 60 (ps prints "213:23.48" for 213 minutes)."""
    parts = text.split(":")
    seconds = float(parts[-1])
    if len(parts) >= 2:
        seconds += int(parts[-2]) * 60
    if len(parts) >= 3:
        seconds += int(parts[-3]) * 3600
    return seconds


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
    """Used memory from `vm_stat`: (active + wired + compressor) × page size.

    Matches Activity Monitor's "memory used" closely enough for a bar: it
    counts the pages that are actually holding something that cannot be freed
    on demand, and reports the compressor separately for the caption."""
    page_match = re.search(r"page size of (\d+) bytes", text)
    page = int(page_match.group(1)) if page_match else 4096
    pages = {}
    for line in text.splitlines()[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            pages[name.strip()] = int(value.strip().rstrip("."))
    used_pages = (pages.get("Pages active", 0)
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
    return subprocess.check_output(cmd, text=True)


def _memsize() -> int:
    try:
        return int(_run(["sysctl", "-n", "hw.memsize"]).strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0
