"""Fire-once threshold alerts with re-arm when the usage window resets.

State is in-memory only: after an app restart a still-red metric fires one
more notification. Accepted trade-off (documented in the spec).
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import best_switch, red_threshold, worst
from .reset_countdown_format import remaining_text


@dataclass
class Alert:
    title: str
    body: str


class AlertManager:
    def __init__(self):
        self._fired = {}  # (account_number, metric_key) -> resets_at when fired

    def check(self, snapshot):
        alerts = []
        account = snapshot.active_account
        if account is None:
            return alerts
        threshold = red_threshold()
        for metric in account.metrics:
            key = (account.number, metric.key)
            if metric.left <= threshold:
                if self._fired.get(key) == metric.resets_at:
                    continue  # already fired for this window
                self._fired[key] = metric.resets_at
                alerts.append(self._build(snapshot, metric))
            else:
                self._fired.pop(key, None)  # re-arm after reset
        return alerts

    def _build(self, snapshot, metric):
        title = f"Claude: {metric.label} — {round(metric.left)}% left"
        lines = []
        countdown = remaining_text(metric.resets_at) or metric.countdown
        if countdown:
            lines.append(f"Resets in {countdown}.")
        suggestion = best_switch(snapshot)
        if suggestion is not None:
            w = worst(suggestion)
            lines.append(f"Best switch: #{suggestion.number} {suggestion.email} "
                         f"({round(w.left)}% left)")
        else:
            lines.append("No other account available.")
        return Alert(title=title, body="\n".join(lines))
