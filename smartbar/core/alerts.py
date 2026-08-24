"""Fire-once threshold alerts with re-arm when the usage window resets.

State is in-memory only: after an app restart a still-red metric fires one
more notification. Accepted trade-off (documented in the spec).
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import best_switch, red_threshold, used_pct, worst
from .reset_countdown_format import parse_iso, remaining_text

# One usage window = one notification. The API re-stamps resets_at with
# fresh sub-second (sometimes ±1 s) values on every real fetch, so the raw
# string is NOT a stable window identity — keyed on it, a ≥90% account
# re-notified every fetch until the window closed. A 5-minute bucket of the
# parsed timestamp absorbs the observed drift (a 1-minute bucket still
# straddles 16:59:59 → 17:00:00); real windows are hours apart.
WINDOW_BUCKET_S = 300


def window_identity(resets_at: str):
    """A stable identity for the usage window `resets_at` names."""
    parsed = parse_iso(resets_at or "")
    if parsed is None:
        return resets_at            # unparseable → raw string, old behaviour
    return round(parsed.timestamp() / WINDOW_BUCKET_S)


@dataclass
class Alert:
    title: str
    body: str


class AlertManager:
    def __init__(self):
        self._fired = {}  # (account_number, metric_key) -> window identity

    def check(self, snapshot):
        alerts = []
        account = snapshot.active_account
        if account is None:
            return alerts
        threshold = red_threshold()
        for metric in account.metrics:
            key = (account.number, metric.key)
            if metric.pct >= threshold:
                window = window_identity(metric.resets_at)
                if self._fired.get(key) == window:
                    continue  # already fired for this window
                self._fired[key] = window
                alerts.append(self._build(snapshot, metric))
            else:
                self._fired.pop(key, None)  # re-arm after reset
        return alerts

    def _build(self, snapshot, metric):
        suggestion = best_switch(snapshot)
        title = f"Claude: {metric.label} — {used_pct(metric.pct)}% used"
        if suggestion is None:
            title += " — no accounts left"
        lines = []
        countdown = remaining_text(metric.resets_at) or metric.countdown
        if countdown:
            lines.append(f"Resets in {countdown}.")
        if suggestion is not None:
            w = worst(suggestion)
            lines.append(f"Best switch: #{suggestion.number} {suggestion.email} "
                         f"({used_pct(w.pct)}% used)")
        else:
            lines.append("No other account available — you're on your own until this resets.")
        return Alert(title=title, body="\n".join(lines))
