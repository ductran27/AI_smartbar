"""Rolling per-metric usage history for the hover-reveal trend sparkline.

Unlike the System tab — whose history core samples directly (see
sysmon.history_append/history_series) — the macOS app polls cswap and Codex
ITSELF (CswapClient / OpenAIStatus), so the front-ends record each poll's
per-metric % into a ring and this module owns only the SHAPE rules: how long a
sample is kept (SPAN_MINUTES) and when a hole in sampling breaks the drawn line
instead of connecting a segment across dead time (GAP_MINUTES). Swift's
UsageHistory.swift mirrors these 1:1 (pinned by test_usage_history_parity.py),
the same way UsageStore/Models port the rest of core's semantics natively.

The ring is a time-bounded, gap-aware version of sysmon's fixed 1-minute grid:
the sample stream here is irregular (60-180s polls, and nothing at all while
the app is closed), so a fixed grid would be mostly holes. Instead the ring
stores only the minutes actually polled, and `series` inserts a break only
where the real time between two samples exceeds GAP_MINUTES.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

# Retention: a full 7-day window, so the weekly and per-model rows show their
# whole window's rhythm and the 5h row shows its recent history. Minute buckets.
SPAN_MINUTES = 7 * 24 * 60          # 10080

# A hole larger than this — the app closed, the Mac asleep — draws as a break
# in the line rather than a straight segment across time nothing was sampled.
# Comfortably above the 60-180s poll cadence so ordinary polling never fakes a
# break (see test_gap_boundary_is_inclusive).
GAP_MINUTES = 15

Ring = List[Tuple[int, int]]


def record(ring, minute: int, pct, now_minute: int = None,
           span: int = SPAN_MINUTES) -> Ring:
    """Append (minute, pct) to `ring`, or replace the last entry when it names
    the same minute (a poll fires more than once a minute). Samples at or
    before `now_minute - span` (now_minute defaults to `minute`) are dropped.
    `pct` is clamped to 0..100 and rounded to an int. Returns a NEW list — the
    caller persists it; the input is left untouched."""
    now_minute = minute if now_minute is None else now_minute
    pct = max(0, min(100, int(round(pct))))
    out = [(m, p) for (m, p) in ring if m > now_minute - span]
    if out and out[-1][0] == minute:
        out[-1] = (minute, pct)
    else:
        out.append((minute, pct))
    return out


def series(ring, gap: int = GAP_MINUTES) -> List[Optional[int]]:
    """The ring's pcts in order as a [int | None] list for the trend chart,
    with a single None inserted wherever two consecutive samples are more than
    `gap` minutes apart — an honest break for time the app was not running,
    never a line drawn across it. A None counts for nothing downstream, exactly
    as a missing minute does in the System-tab history."""
    out: List[Optional[int]] = []
    prev: Optional[int] = None
    for minute, pct in ring:
        if prev is not None and minute - prev > gap:
            out.append(None)
        out.append(pct)
        prev = minute
    return out


def summary(ring) -> dict:
    """Peak and latest reading over the retained ring, for the hover caption.
    Gaps carry no number, so this reads the stored samples directly. peak/last
    are 0 on an empty ring."""
    pcts = [p for _, p in ring]
    return {"peak": max(pcts) if pcts else 0,
            "last": pcts[-1] if pcts else 0,
            "points": len(pcts)}
