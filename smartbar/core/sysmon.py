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
