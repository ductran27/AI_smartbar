"""Render the popover to a PNG without a desktop session.

`ai-smartbar --preview-popover` exists because this panel is hand-painted
for a platform that is easy to develop for and hard to look at: it renders
the exact same layout and painter the tray uses, so the result can be
reviewed (and diffed after a change) from any machine with pycairo. `--demo`
covers every card state at once and needs no claude-swap install.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from smartbar import __version__
from smartbar.core import model, paths, popover_layout

CACHE_DIR = paths.cache_dir()
DEFAULT_PATH = os.path.join(CACHE_DIR, "popover-preview.png")


def demo_snapshot():
    """One of every card state: spent, active, near-limit, dead credential.

    The active card also carries a device count, since "(2)" next to an
    address is only reviewable if the preview actually draws one.
    """
    # Same reason the device count is here: the pace caret is only
    # reviewable if the preview draws one, and it refuses to guess without
    # a resets_at (model.pace_fraction). These are stamped RELATIVE to now
    # so the caret lands in the same place whenever the preview is run,
    # rather than drifting to an edge as a hardcoded date goes stale.
    now = datetime.now(timezone.utc)

    def resets_in(**delta) -> str:
        return (now + timedelta(**delta)).isoformat()

    def rows(five, seven, fable):
        # Fable keeps its countdown but gets NO resets_at-backed caret: a
        # scoped bucket states no window length, so there is nothing to
        # measure "how far through" against — the deliberate gap
        # model.window_seconds documents.
        return [model.Metric(key="5h", label="5h", short="5h", pct=five,
                             countdown="1h 38m",
                             resets_at=resets_in(hours=1, minutes=38)),
                model.Metric(key="7d", label="7d", short="7d", pct=seven,
                             countdown="1d 23h",
                             resets_at=resets_in(days=1, hours=23)),
                model.Metric(key="scoped:Fable", label="Fable", short="F",
                             pct=fable, countdown="1d 23h")]
    snapshot = model.Snapshot(accounts=[
        model.Account(number=1, email="ios8build@gmail.com",
                      metrics=rows(0.0, 79.0, 100.0)),
        model.Account(number=2, email="jsmith@campus.example", active=True,
                      metrics=rows(49.0, 10.0, 0.0), devices=2),
        model.Account(number=3, email="duc.dut.wr@gmail.com",
                      metrics=rows(3.0, 93.0, 89.0)),
        model.Account(number=4, email="an.old.account@gmail.com", ok=False,
                      status="relogin_required", metrics=[]),
    ], fetched_at="")
    # A ChatGPT login + a remembered one, so the provider tab row and the
    # read-only OpenAI card states are reviewable without a Codex install.
    snapshot.openai = [
        model.Account(number=1, email="you@openai.example", active=True,
                      provider="openai", plan="Pro", metrics=[
                          model.Metric(key="5h", label="5h", short="5h",
                                       pct=12.0, countdown="2h 4m"),
                          model.Metric(key="7d", label="7d", short="7d",
                                       pct=25.0, countdown="3d 2h")]),
        model.Account(number=2, email="former@openai.example", ok=False,
                      provider="openai", status="signed_out", metrics=[]),
    ]
    return snapshot


def _pending() -> tuple:
    """(pending_version, blocked_reason) from the updater — best effort."""
    try:
        from smartbar import update_runner
        from smartbar.core import update
        state = update_runner.load_state()
        return (update.pending_version(state),
                state.get("reason", "") if state.get("action") == "blocked"
                else "")
    except Exception:
        return "", ""


def render(path: str = "", *, scale: float = 2.0, demo: bool = False,
           scheme: str = "dark") -> str:
    from smartbar.core import popover_theme
    from smartbar.paint import popover_draw

    appearance = popover_theme.scheme_for(scheme)

    error = ""
    if demo:
        snapshot = demo_snapshot()
    else:
        from smartbar.core import cswap
        try:
            snapshot = cswap.fetch()
        except Exception as exc:          # no cswap here? still show the UI
            snapshot, error = demo_snapshot(), f"demo data ({exc})"
        else:
            try:
                from smartbar.core import codex
                snapshot.openai = codex.accounts()
            except Exception:             # the preview must never crash
                pass
    pending, blocked = _pending()
    layout = popover_layout.build(snapshot, version=__version__,
                                  pending_version=pending,
                                  blocked_reason=blocked,
                                  fetched_at=snapshot.fetched_at,
                                  scheme=appearance)
    path = path or DEFAULT_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    # No background argument: the layout carries the scheme's own window
    # colour, so the painter cannot be handed a ground the cards were not
    # drawn against.
    popover_draw.render_png(layout, path, scale=scale)
    if error:
        print(error)
    return path
