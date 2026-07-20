# AI smartbar

Your Claude usage limits, always visible in the bar — with one-click
account switching. A cross-platform companion for
[claude-swap](https://github.com/realiti4/claude-swap).

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

## Features

- **Twin-pill icon (~16 px).** First pill = the all-models limit (worst
  of the 5-hour and 7-day windows), one more pill per per-model weekly
  bucket (e.g. Fable). Pills drain downward as tokens are spent and step
  green → yellow → light red → dark red → gray (empty); hollow `?` when
  there is no data.
- **Every number is "% left".** Claude Code's `/usage` speaks in % used;
  the bar shows the complement — 26% used there ≡ 74% left here.
  Promo-boosted limits are already baked into the API's percentages, so
  both read the same scale.
- **Native macOS popover** (SwiftUI, macOS 13+). One card per account:
  draining bars for 5h / 7d / per-model with `% left · countdown`,
  countdowns ticking live from the absolute reset time, the active card
  outlined white with a green `ACTIVE` chip, and an "Updated" stamp that
  shows when the usage was actually measured at the API — not when the
  app last polled.
- **One-click switching.** `Make Active` flips the account instantly
  (optimistic UI; failures surface in the popover and the next fetch
  corrects the display). New Claude Code sessions use the new account;
  already-running sessions keep theirs (claude-swap semantics).
- **Near-live numbers.** Polls every 60 s and refreshes the moment you
  open the popover, wake the Mac, or switch accounts. Tracks `/usage`
  within claude-swap's per-account poll plan — ≤180 s while actively
  burning, ~70 s near the limit, ≤3 min idle — without ever exceeding
  the usage API's per-token request budget (see [Data
  freshness](#data-freshness)).
- **Switch alert.** At ≤10% left on any metric of the active account you
  get one desktop notification naming the best account to switch to; it
  re-arms when the window resets.
- **Auto-registration.** Sign in to Claude Code with a new account and
  within ≤60 s (instantly on popover open) the bar runs cswap's
  non-interactive `add` — the account appears with bars, switching and
  warmup coverage, zero setup. `SMARTBAR_AUTO_ADD=off` keeps
  registration manual.
- **Auto window-starter (opt-in).** Keeps every registered account's
  5-hour window running so budget resets come as early as possible — see
  [below](#auto-window-starter-warmup-opt-in).
- **Linux tray.** The same twin-pill badge and a click menu via
  AppIndicator + cairo, rendered from the same unit-tested core.
- **Hands off your credentials.** The bar reads `cswap list --json`,
  switches via `cswap switch`, registers via `cswap add`, and warms via
  `cswap run` + the official claude CLI. It never touches credentials or
  Anthropic endpoints itself.

## Requirements

- [claude-swap](https://github.com/realiti4/claude-swap) ≥ 0.22
  (`pipx install claude-swap`). Registration is automatic: sign in to
  Claude Code and the bar runs `cswap add` for you; manual `cswap add`
  still works.
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

Both macOS installers share the `com.ductran.ai-smartbar` LaunchAgent
label — installing one replaces the other at login (single instance).
Uninstall with `./install/linux.sh --uninstall` /
`./install/macos.sh --uninstall`.

> **Status:** the native macOS app is live-verified (2026-07-19/20:
> icon, popover, switching, freshness, auto-registration). The Linux
> tray is written to spec from the unit-tested core but has not yet been
> re-run on a Linux box since the v2 redesign.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SMARTBAR_INTERVAL` | `60` | Poll period in seconds (popover open / wake / switch also refresh) |
| `SMARTBAR_AUTO_ADD` | on | Auto-run `cswap add` when the current login isn't registered (`off` disables) |
| `SMARTBAR_YELLOW` | `50` | Yellow at or below this % **left** |
| `SMARTBAR_LOW` | `25` | Light red at or below this % **left** |
| `SMARTBAR_RED` | `10` | Dark red + notification at or below this % **left** |
| `SMARTBAR_TEST_THRESHOLD` | – | Sets all three thresholds (testing) |
| `SMARTBAR_CSWAP` | – | Path override for the cswap binary |
| `SMARTBAR_CSWAP_PYTHON` | auto | Interpreter for the store primer (auto-detected from the pipx cswap launcher) |
| `SMARTBAR_CLAUDE` | – | Path override for the claude CLI (warmup) |
| `SMARTBAR_WARMUP_DAILY_CAP` | `6` | Max warmup pings per account per day |
| `SMARTBAR_WARMUP_QUIET` | – | Warmup quiet hours, e.g. `23-05` (wraps) |

## Auto window-starter (warmup, opt-in)

The Claude 5-hour window starts at your **first message** — keeping one
running means the budget reset always comes as early as possible. The
warmup agent checks every 10 minutes and, for **every registered account**
whose 5h window is idle, sends one minimal ping (`claude -p "."`, one turn,
haiku, output discarded) via `cswap run <account>` — accounts added or
removed in cswap are picked up automatically, nothing to configure.

```bash
./install/macos-warmup.sh              # opt in (macOS LaunchAgent)
./install/macos-warmup.sh --uninstall  # opt out
# Linux: */10 * * * * ~/tools/AI_smartbar/bin/ai-smartbar --warmup-once
```

Gates before any ping: window actually idle (`resetsAt` absent/past),
usage data fresh (≤30 min), 30 min per-account cooldown, daily cap,
optional quiet hours. After a ping the runner re-fetches and verifies the
window started; failures raise a desktop notification. Every attempt is
logged to `~/.cache/ai-smartbar/warmup.log`. Trade-off: each ping spends a
sliver of the general budgets — that is what buys the earlier reset. Note
that starting a window affects the account everywhere, not just this
machine. No credentials are touched: account context comes from
`cswap run`, auth from the official claude CLI.

## Data freshness

`cswap list --json` serves claude-swap's usage store and only hits
Anthropic's usage API (`/api/oauth/usage`) on its adaptive per-account
plan — real fetches run every 1–10 min under a strict per-token budget
(~20/h against a measured ~28–30/h cap for non-first-party clients).

AI smartbar polls every 60 s and, before each read, *primes* the store
using claude-swap's own auto-engine collector mode: anything stale
**or** plan-due is fetched now — the sanctioned way past the 3-minute
serve TTL — while a fresh-and-not-yet-due account is never re-fetched,
so the budget holds by construction. Combined with the refresh-on-open /
wake / switch triggers, the display tracks `/usage` within the active
poll plan; the residual gap is the price of the API's per-token cap
(`/usage` itself is exempt as a first-party client). Design notes with
the full audit live in `docs/superpowers/specs/`.

## Notes

- **Auto-registration caveat:** every account you sign into on this
  machine gets captured into claude-swap's local backup. Set
  `SMARTBAR_AUTO_ADD=off` if you don't want that. Failed attempts
  (logged out, locked keychain) retry at most every 10 min.
- **Multi-machine:** usage numbers are server-side per account, so the
  bar shows an account draining even when it is being used on another
  computer. Registration, slot numbers, aliases, the active selection
  and warmup state are per machine.
- **Restart re-notify:** alert state is in-memory; restarting the app
  while a metric is ≤10% left fires that notification once more.
- **XFCE hover text** is a single line (StatusNotifier limitation); full
  details are in the menu.

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
python3 -m unittest discover -s tests -v   # 89 tests, no external deps
./tests/e2e-warmup.sh                      # warmup loop against stateful mocks
./tests/e2e-autoadd.sh                     # auto-registration against the built app
```

Layout: `smartbar/core/` holds all logic and formatting (unit-tested,
Python 3.9+); `smartbar/linux/tray.py`, `smartbar/macos/menubar.py` and
the Swift app (`macos-swift/`, a 1:1 mirror of core) only render what
core produces. Design docs live in `docs/superpowers/`.
