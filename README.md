# AI_smartbar

Your Claude usage limits, always visible in the bar — with one-click account
switching. A cross-platform companion for
[claude-swap](https://github.com/realiti4/claude-swap): the XFCE/Linux system
tray or macOS menu bar shows the metric closest to its limit for the active
account (5-hour window, 7-day window, or a per-model weekly limit like
Fable), colored green → yellow → red, and the click menu shows every account
so you always know which one to use next.

```
Linux tray:    … [🔊] [📶] [5h31] [🔋] …     (stacked badge: general row over
                          [F30 ]             the per-model row, each with its
                                             own green/yellow/red color)
macOS bar:     … 🟢 5h31 · 🟢 F30  🔋 📶 🔊 …  (same two segments, dotted)

Click menu:
 ● 1 ios8build@gmail.com   5h 28% · 7d 20% · F 29%
 ○ 2 other@account         5h 62% · 7d 40% · F 71%   ← click to switch
 ─────────────────────────
 ⟳ Refresh now
 ⚙ Open cswap TUI
 ⏻ Quit
```

At 90% on any metric of the active account you get one desktop notification
naming the best account to switch to; it re-arms when the window resets.

## Requirements

- [claude-swap](https://github.com/realiti4/claude-swap) ≥ 0.22 with your
  accounts registered (`pipx install claude-swap`, then `cswap add` per
  account). AI_smartbar reads `cswap list --json` and switches via
  `cswap switch` — it never touches credentials or Anthropic endpoints
  itself.
- Linux: Python 3 with GTK3 bindings (`python3-gi`), AyatanaAppIndicator3
  (`gir1.2-ayatanaappindicator3-0.1`), pycairo — preinstalled on most
  XFCE/GNOME distros. X11 or Wayland with a StatusNotifier-capable tray.
- macOS: Python 3; `install/macos.sh` creates a venv with
  [rumps](https://github.com/jaredks/rumps).

## Install

```bash
git clone git@github.com:ductran27/AI_smartbar.git ~/tools/AI_smartbar
cd ~/tools/AI_smartbar

# Linux (installs ~/.local/bin/ai-smartbar + autostart, starts it):
./install/linux.sh

# macOS — native SwiftUI app (recommended; macOS 13+, needs Xcode CLT):
./install/macos-swift.sh

# macOS — Python/rumps fallback (older Macs, no Swift toolchain):
./install/macos.sh
```

The native app adds a designed popover: one card per account with animated
ring gauges (5h / 7d / per-model), an `ACTIVE` chip, `Make Active` switch
buttons, stale/error states, light+dark mode. Both macOS installers share
the `com.ductran.ai-smartbar` LaunchAgent label — installing one replaces
the other at login (single instance).

> **macOS status:** both macOS variants are written to spec but not yet
> live-verified on a Mac (this project is developed on Linux, where SwiftUI
> cannot compile). The behavior logic is a 1:1 port of the unit-tested
> Python core; expect at most minor first-build fixes and report anything odd.

Uninstall with `./install/linux.sh --uninstall` / `./install/macos.sh --uninstall`.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SMARTBAR_INTERVAL` | `60` | Refresh period in seconds |
| `SMARTBAR_YELLOW` | `70` | Yellow threshold (%) |
| `SMARTBAR_RED` | `90` | Red + notification threshold (%) |
| `SMARTBAR_TEST_THRESHOLD` | – | Sets both thresholds (testing) |
| `SMARTBAR_CSWAP` | – | Path override for the cswap binary |

## Behavior notes

- **Switching** affects new Claude Code sessions; already-running sessions
  keep their current account (claude-swap semantics).
- **Restart re-notify:** alert state is in-memory; restarting the app while
  a metric is ≥90% fires that notification once more.
- **XFCE hover text** is a single line (StatusNotifier limitation); full
  details are in the menu.
- cswap polls Anthropic's usage API adaptively with caching — the 60s
  refresh here does not hammer anything.

## Troubleshooting

```bash
ai-smartbar --once     # headless: prints icon state, title, account rows
tail ~/.cache/ai-smartbar/tray.log        # Linux log
tail ~/Library/Logs/ai-smartbar.log       # macOS log
```

After 3 consecutive cswap failures the badge turns gray `?` and keeps the
last data marked `(stale)` in the menu.

## Development

```bash
python3 -m unittest discover -s tests -v   # 33 tests, no external deps
```

Layout: `smartbar/core/` (all logic + formatting, unit-tested) —
`smartbar/linux/tray.py` and `smartbar/macos/menubar.py` only render what
core produces. Design docs live in `docs/superpowers/`.
