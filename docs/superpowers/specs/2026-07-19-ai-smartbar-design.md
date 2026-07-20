# AI_smartbar — Design Spec

**Date:** 2026-07-19 (approved by user in-session)
**Repo:** `ductran27/AI_smartbar` (private)
**Local path:** `~/tools/AI_smartbar`

## Overview

AI_smartbar is a cross-platform status-bar indicator showing remaining Claude
usage limits (5-hour window, 7-day window, per-model weekly limits such as
Fable) for every Claude account managed by
[claude-swap](https://github.com/realiti4/claude-swap), with one-click account
switching. It answers "which account should I use right now?" at a glance,
from the system tray (Linux/XFCE) or menu bar (macOS).

### Goals
- Always-visible worst-metric indicator with traffic-light coloring.
- Click menu: per-account usage rows, switch account, refresh, open TUI, quit.
- One desktop notification when the active account crosses 90% on any metric,
  naming the best account to switch to; re-arms after the window resets.
- Identical behavior and information on Linux and macOS from one shared core.

### Non-goals (v1)
- No OpenAI/Codex tracking (extension point only: core is provider-agnostic
  enough that a second data source could be added later).
- No reimplementation of claude-swap: accounts, credential vault, token
  refresh, Anthropic usage API calls, and switching all stay in cswap.
- No packaging/PyPI; install is via scripts in `install/`.

## Verified environment facts (examined 2026-07-19)

- Linux box: XFCE 4.20 panel on X11 (`XDG_CURRENT_DESKTOP=XFCE`); GNOME Shell
  installed but not the active session.
- Python 3.14.4; `python3-gi` with GTK3, `AyatanaAppIndicator3` 0.1
  (gir1.2-ayatanaappindicator3), libnotify (`gi` Notify 0.7), pycairo — all
  present; **no sudo needed**.
- `notify-send`, `x-terminal-emulator`, `exo-open` present;
  `xfce4-terminal` NOT installed.
- claude-swap 0.22.0 installed via `uv tool install` at `~/.local/bin/cswap`.
- `cswap list --json` returns schema v1:

```json
{
  "schemaVersion": 1,
  "activeAccountNumber": 1,
  "accounts": [
    {
      "number": 1,
      "email": "…",
      "organizationName": "…",
      "active": true,
      "usageStatus": "ok",
      "usage": {
        "fiveHour": {"pct": 24.0, "resetsAt": "…", "countdown": "4h 3m", "clock": "Jul 20 00:39"},
        "sevenDay": {"pct": 20.0, "resetsAt": "…", "countdown": "6d 15h", "clock": "Jul 26 11:59"},
        "scoped": [
          {"name": "Fable", "pct": 28.0, "resetsAt": "…", "countdown": "6d 15h", "clock": "…"}
        ]
      },
      "usageFetchedAt": "…",
      "usageAgeSeconds": 0.0
    }
  ]
}
```

Notes: per-model weekly limits arrive in `usage.scoped[]` (name varies by
plan: "Fable", "Opus", …). `countdown`/`clock` are preformatted — reuse them,
do not recompute. `usageStatus` observed as `"ok"`; treat any other value or
missing `usage` as "no data" for that account, never crash.

## Architecture

```
AI_smartbar/
├─ README.md                     # what it is, install per OS, screenshots
├─ smartbar/
│  ├─ __init__.py                # __version__
│  ├─ core/
│  │  ├─ __init__.py
│  │  ├─ cswap.py                # subprocess wrapper: list_accounts(), switch(n)
│  │  ├─ model.py                # dataclasses + worst-metric/color/format logic
│  │  └─ alerts.py               # 90% threshold arming / re-arm state machine
│  ├─ linux/
│  │  └─ tray.py                 # GTK3 + AyatanaAppIndicator3 + cairo icon
│  └─ macos/
│     └─ menubar.py              # rumps app (native status-bar text + menu)
├─ bin/ai-smartbar               # entry point: --once/--version, OS dispatch
├─ install/
│  ├─ linux.sh                   # ~/.local/bin link + autostart; --uninstall
│  └─ macos.sh                   # venv + rumps + LaunchAgent; --uninstall
├─ tests/                        # stdlib unittest; no external deps
└─ docs/superpowers/specs|plans/
```

claude-swap is the engine underneath (user decision): AI_smartbar shells out
to `cswap list --json` and `cswap switch <num>`; it never touches credentials
or Anthropic endpoints itself.

## Shared core contracts

### `core/cswap.py`
- `fetch() -> Snapshot` — runs `cswap list --json` (30 s timeout), validates
  `schemaVersion == 1` (tolerate unknown extra fields; if version != 1, parse
  best-effort and set `snapshot.schema_warning`), returns parsed `Snapshot`.
  Raises `CswapError` on nonzero exit, timeout, or JSON parse failure.
- `switch(number: int) -> None` — runs `cswap switch <num>`; raises
  `CswapError` on failure.
- `CSWAP` binary resolved from `$SMARTBAR_CSWAP` override, else `cswap` on
  PATH (with `~/.local/bin` appended as fallback — autostart environments may
  lack it).

### `core/model.py`
- `Metric(key, label, short, pct, resets_at, countdown, clock)` — `key` in
  `{"5h", "7d"}` or `scoped:<name>`; `label` e.g. "Fable"; `short` for the
  icon: `5h`, `7d`, or first letter of scoped name (`F`).
- `Account(number, email, org, active, metrics: list[Metric], ok: bool)`.
- `Snapshot(accounts, active_account, fetched_at)`.
- `worst(account) -> Metric | None` — max `pct` across metrics.
- `color(pct) -> "green" | "yellow" | "red"` — green < 70 ≤ yellow < 90 ≤ red.
  Thresholds overridable via `SMARTBAR_YELLOW` / `SMARTBAR_RED` env vars
  (float, percent); `SMARTBAR_TEST_THRESHOLD` sets BOTH (test hook).
- `best_switch(snapshot) -> Account | None` — among non-active accounts with
  data, the one with the lowest worst-pct.
- Formatting helpers produce every user-visible string (menu rows, title
  line, notification body) so both UIs render identical text:
  - title line: `ios8build — 5h 24% · 7d 20% · F 28%`
  - menu row:  `● 1 ios8build   5h 24% · 7d 20% · F 28%` (`●` active, `○` other)
  - icon text: `F28` (worst metric short + integer pct)

### `core/alerts.py`
- `AlertManager.check(snapshot) -> list[Alert]` — for each metric of the
  ACTIVE account with `pct >= red_threshold`: fire once, then hold until
  re-armed. Re-arm when that metric's `pct` drops below threshold OR its
  `resets_at` changes (window rolled over). State is in-memory only; after an
  app restart a still-red metric notifies once more (accepted, documented).
- `Alert` carries: metric label, pct, countdown, best-switch suggestion
  (from `best_switch`), preformatted body text.

## Linux UI (`smartbar/linux/tray.py`)

- `AyatanaAppIndicator3.Indicator` with `set_icon_theme_path` pointing at
  `~/.cache/ai-smartbar/icons/`; icon PNGs are cairo-rendered on every
  refresh, alternating filenames (`state-a`/`state-b`) to defeat icon caching.
- Icon: rounded rect, status color fill, white bold text (e.g. `F28`),
  rendered 48×24 @2x. XFCE's tray scales to panel height; if the wide icon
  renders poorly in practice, fallback (build-time decision, both code paths
  kept trivial): square icon with pct number only, letter moved to title.
- Hover text: XFCE shows the indicator **title** as a single line — set to
  the title-line format above. (No rich tooltips in StatusNotifier — known
  platform limit, communicated to user.)
- Menu (GTK):
  - one row per account (clicking a non-active row → `cswap switch <n>` in a
    worker thread → immediate refresh; active row is insensitive)
  - `⟳ Refresh now`
  - `⚙ Open cswap TUI` → `x-terminal-emulator -e cswap tui`
  - `⏻ Quit`
- Refresh: `GLib.timeout_add_seconds(interval)` (default 60, override
  `SMARTBAR_INTERVAL`); fetch runs in a `threading.Thread`; UI mutation only
  via `GLib.idle_add`. Immediate refresh at startup and after switch.
- Notifications: `gi` libnotify (`Notify`), fallback to `notify-send` if init
  fails.

## macOS UI (`smartbar/macos/menubar.py`)

- `rumps.App` with status-bar **text title** `🟢 F 28%` (colored dot emoji +
  worst metric; native text, no icon hack). Menu mirrors the Linux menu
  exactly (same core-formatted strings). `rumps.Timer` for refresh;
  `rumps.notification` for alerts; TUI item opens Terminal.app via
  `osascript`.
- Runtime: venv at `~/Library/Application Support/ai-smartbar/venv` with
  `rumps` installed by `install/macos.sh`.
- **Verification limit:** written to spec and unit-testable to the `--once`
  level on Linux; live menu-bar verification deferred until run on a Mac.

## Entry point (`bin/ai-smartbar`)

- `#!/usr/bin/env python3`; inserts repo root into `sys.path` from
  `__file__`; argparse: `--once` (print parsed snapshot + chosen icon state
  as text, exit 0/1 — headless test), `--version`.
- OS dispatch: `darwin` → macos.menubar, else linux.tray (errors clearly if
  gi unavailable).

## Error handling

- Fetch failure: keep last snapshot rendered; after 3 consecutive failures
  icon goes gray with `?`, title shows the error class; menu stays usable
  with stale data marked `(stale)`.
- Log: rotating-ish simple log (~200 KB cap, truncate-on-open when larger) at
  `~/.cache/ai-smartbar/tray.log` (Linux) /
  `~/Library/Logs/ai-smartbar.log` (macOS).
- All subprocess calls have timeouts; no call ever blocks the UI thread.

## Install / uninstall

- `install/linux.sh`: symlink `bin/ai-smartbar` → `~/.local/bin/ai-smartbar`;
  write `~/.config/autostart/ai-smartbar.desktop`; start it. `--uninstall`
  removes link, autostart entry, cache dir, and kills the running instance.
- `install/macos.sh`: create venv, `pip install rumps`, write LaunchAgent
  `~/Library/LaunchAgents/com.ductran.ai-smartbar.plist` (RunAtLoad),
  `launchctl load`. `--uninstall` reverses all of it.

## Testing

- `python3 -m unittest discover tests` — no external deps:
  - `test_model.py`: worst-metric selection, color thresholds (69.9/70/89.9/90),
    env overrides, formatting of title/menu/icon strings, scoped-name letters.
  - `test_alerts.py`: fire-once, hold, re-arm on pct drop, re-arm on
    resets_at change, multi-metric independence, best-switch suggestion.
  - `test_cswap.py`: parser against captured real fixture JSON (sanitized),
    schemaVersion tolerance, missing-usage tolerance, error paths.
- Live (Linux, this machine): `--once` output; tray launch; icon PNG exists
  and process survives ≥2 refresh cycles; menu switch (single-account no-op
  case); forced notification via `SMARTBAR_TEST_THRESHOLD=10`; autostart file
  present.
- Live (macOS): deferred to first run on the Mac.

## Risks & mitigations

- **Upstream schema drift** (cswap is fast-moving): `schemaVersion` check +
  tolerant parser + pinned known-good version documented in README.
- **Undocumented Anthropic endpoint**: entirely cswap's problem by design
  (user decision — cswap-as-engine).
- **XFCE wide-icon rendering**: fallback path specified above.
- **Switch semantics**: `cswap switch` affects new Claude Code sessions only;
  running sessions keep their token (documented in README).
