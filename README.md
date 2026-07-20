# AI_smartbar

Your Claude usage limits, always visible in the bar — with one-click account
switching. A cross-platform companion for
[claude-swap](https://github.com/realiti4/claude-swap): a tiny **twin-pill
icon** (~16 px) shows what's **left** for the active account — first pill =
the all-models limit (worst of 5-hour / 7-day window), second pill = the
per-model weekly bucket (e.g. Fable). Pills drain downward as tokens are
spent and step green → yellow → light red → dark red → gray (empty).
Click for the details popover / menu with every account, so you always know
which one to use next.

```
Menu bar / tray:   … [▮▮] 🔋 📶 🔊 …          (two vertical pills, fill =
                                             % left, each its own color)

macOS popover:                     Linux click menu:
 AI smartbar  Updated 9:56 PM ⟳⏻    ● 1 ios8build@gmail.com  5h 55% · 7d 76% · F 66%
 ┌─────────────────────[white]┐    ○ 2 other@account   5h 38% · 7d 60% · F 29%  ← switch
 │ ● ios8build@…      ACTIVE  │    ─────────────────────────
 │ 5h    ███████──  55%·2h43m │    ⟳ Refresh now
 │ 7d    █████████─ 76%·6d14h │    ⚙ Open cswap TUI
 │ Fable ████████── 66%·6d14h │    ⏻ Quit
 └─────────────────────────────┘
 (numbers are % LEFT; active card outlined white; dark-only UI)
```

At ≤10% left on any metric of the active account you get one desktop
notification naming the best account to switch to; it re-arms when the
window resets.

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

The native app adds a designed popover: one card per account with draining
horizontal bars (5h / 7d / per-model, bold labels, `% left · countdown`),
a white-outlined `ACTIVE` card, `Make Active` switch buttons, stale/error
states. Dark-only UI. Both macOS installers share the
`com.ductran.ai-smartbar` LaunchAgent label — installing one replaces the
other at login (single instance).

> **Status:** the native macOS app is live-verified (2026-07-19). The
> Linux twin-pill cairo renderer is written to spec from the unit-tested
> core but has not yet been re-run on a Linux box since the v2 redesign.

Uninstall with `./install/linux.sh --uninstall` / `./install/macos.sh --uninstall`.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SMARTBAR_INTERVAL` | `300` | Refresh period in seconds |
| `SMARTBAR_YELLOW` | `50` | Yellow at or below this % **left** |
| `SMARTBAR_LOW` | `25` | Light red at or below this % **left** |
| `SMARTBAR_RED` | `10` | Dark red + notification at or below this % **left** |
| `SMARTBAR_TEST_THRESHOLD` | – | Sets all three thresholds (testing) |
| `SMARTBAR_CSWAP` | – | Path override for the cswap binary |

## Behavior notes

- **Every number is "% left"** (v2): pills/bars drain as tokens are spent;
  cswap reports % used and the core converts once, everywhere.
- **Switching** affects new Claude Code sessions; already-running sessions
  keep their current account (claude-swap semantics).
- **Restart re-notify:** alert state is in-memory; restarting the app while
  a metric is ≤10% left fires that notification once more.
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

After 3 consecutive cswap failures the pills go hollow with a gray `?` and
the last data stays visible marked stale.

## Development

```bash
python3 -m unittest discover -s tests -v   # 40 tests, no external deps
```

Layout: `smartbar/core/` (all logic + formatting, unit-tested) —
`smartbar/linux/tray.py` and `smartbar/macos/menubar.py` only render what
core produces. Design docs live in `docs/superpowers/`.
