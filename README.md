# AI smartbar

Your Claude usage limits, always visible in the bar — with one-click
account switching. A cross-platform companion for
[claude-swap](https://github.com/realiti4/claude-swap).

```
Menu bar / tray:   … [▮▮] 🔋 📶 🔊 …          (two vertical pills, fill =
                                             % used, each its own color)

macOS popover:                     Linux click menu:
 AI smartbar  Updated 9:56 PM ⟳⏻    ● 1 ios8build@gmail.com  5h 45% · 7d 24% · F 34%
 ┌─────────────────────[white]┐    ○ 2 other@account   5h 62% · 7d 40% · F 71%  ← switch
 │ ● ios8build@…      ACTIVE  │    ─────────────────────────
 │ 5h    █████─────  45%·2h43m │    ⟳ Refresh now
 │ 7d    ██────────  24%·6d14h │    ⚙ Open cswap TUI
 │ Fable ███───────  34%·6d14h │    ⏻ Quit
 └─────────────────────────────┘
 (numbers are % USED — the /usage scale; active card outlined white;
  dark-only UI)
```

## Features

- **Twin-pill icon (~16 px).** First pill = the all-models limit (worst
  of the 5-hour and 7-day windows), one more pill per per-model weekly
  bucket (e.g. Fable). Pills FILL upward as tokens are spent — a nearly
  full pill means nearly at the limit — and step green → yellow → light
  red → dark red → gray (exhausted); hollow `?` when there is no data.
- **Every number is "% used"** — exactly what Claude Code's `/usage`
  shows, no mental arithmetic. Promo-boosted limits are already baked
  into the API's percentages, so both read the same scale.
- **Native macOS popover** (SwiftUI, macOS 13+). One card per account:
  filling bars for 5h / 7d / per-model with `% used · countdown`,
  countdowns ticking live from the absolute reset time, the active card
  outlined white with a green `ACTIVE` chip, and an "Updated" stamp that
  shows when the usage was actually measured at the API — not when the
  app last polled.
- **One-click switching.** `Make Active` flips the account instantly
  (optimistic UI; failures surface in the popover and the next fetch
  corrects the display). New Claude Code sessions use the new account;
  already-running sessions keep theirs (claude-swap semantics). Accounts
  whose stored credential is dead are labeled and can't be switched to
  (see [Credential lifecycle](#credential-lifecycle-re-capture--healing)).
- **Credential re-capture & healing.** Anthropic rotates OAuth refresh
  tokens, which silently kills stored account backups over time ("my
  first account is gone"). The bar now re-captures the live login's
  credential every 15 min and heals a dead active slot immediately — see
  [below](#credential-lifecycle-re-capture--healing).
- **Near-live numbers, fewer processes.** Adaptive polling: 60 s while
  anything is near a limit or data is missing, 180 s when everything is
  calm (`SMARTBAR_INTERVAL` / `SMARTBAR_INTERVAL_IDLE`), plus refresh
  the moment you open the popover, wake the Mac, or switch accounts.
  Each poll is now a single combined prime+list process (halved from
  v2), and identical snapshots skip all UI work. Tracks `/usage` within
  claude-swap's per-account poll plan without ever exceeding the usage
  API's per-token request budget (see [Data freshness](#data-freshness)).
- **Switch alert.** At ≥90% used on any metric of the active account you
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
  switches via `cswap switch`, registers/re-captures via `cswap add`,
  and warms via `cswap run` + the official claude CLI. It never touches
  credentials or Anthropic endpoints itself.

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

> **Status:** the native macOS app is live-verified (2026-07-22: v3
> %-used UI, re-login card + blocked switch, re-capture, warmup agent
> with fixed PATH). The Linux tray is written to spec from the
> unit-tested core but has not been re-run on a Linux box since v2.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SMARTBAR_INTERVAL` | `60` | Poll period in seconds while near a limit / recovering |
| `SMARTBAR_INTERVAL_IDLE` | `180` | Relaxed poll period when nothing is near a limit (macOS Swift app) |
| `SMARTBAR_AUTO_ADD` | on | Auto-run `cswap add` when the current login isn't registered (`off` disables all adds) |
| `SMARTBAR_RECAPTURE` | on | Periodic re-capture + dead-slot healing via `cswap add` (`off` disables; registration still works) |
| `SMARTBAR_YELLOW` | `50` | Yellow at or above this % **used** |
| `SMARTBAR_LOW` | `75` | Light red at or above this % **used** |
| `SMARTBAR_RED` | `90` | Dark red + notification at or above this % **used** |
| `SMARTBAR_TEST_THRESHOLD` | – | Sets all three thresholds (testing) |
| `SMARTBAR_CSWAP` | – | Path override for the cswap binary |
| `SMARTBAR_CSWAP_PYTHON` | auto | Interpreter for the store primer (auto-detected from the pipx cswap launcher) |
| `SMARTBAR_CLAUDE` | – | Path override for the claude CLI (warmup) |
| `SMARTBAR_WARMUP_DAILY_CAP` | `6` | Max warmup pings per account per day |
| `SMARTBAR_WARMUP_QUIET` | – | Warmup quiet hours, e.g. `23-05` (wraps) |

## Credential lifecycle (re-capture & healing)

**The problem.** Anthropic rotates OAuth refresh tokens. Claude Code
keeps the LIVE login's grant current, but claude-swap's per-slot backup
only holds whatever was captured at `add` time. Once the live grant
rotates past the backup, the backup dies server-side (`invalid_grant`)
and the slot turns `relogin_required` — the popover used to show a bare
"No usage data" and switching to the slot would log Claude Code out.
This is also why numbers looked "out of sync" across machines: each
machine's dead slots differ.

**What the bar does now** (all through cswap's own `add`, which
re-captures an already-registered login in place and clears its
dead-token state):

- **Register** — a `/login` with an unregistered account is added within
  ≤60 s (as before).
- **Heal** — if the ACTIVE slot reports a dead stored credential while
  Claude Code itself is signed in, `cswap add` runs immediately and the
  slot comes back.
- **Refresh** — every 15 min the live login's credential is re-captured,
  so the backup always holds the newest rotation and switching away
  never strands it.

**What you may still have to do once:** an INACTIVE dead slot (like an
account you haven't used on this machine in a while) can only be healed
by its own credential — sign in to Claude Code as that account once
(`/login`), and the bar re-captures it automatically. Until then its
card says "Re-login required" and its switch button is disabled.

**Multi-machine:** usage numbers are server-side per account, so all
machines converge once each machine holds live credentials. Register
each account by signing in on each machine (auto-registration handles
the rest). Do NOT copy claude-swap backups between machines — two
machines refreshing the same grant rotate each other's tokens dead.
Registration, slot numbers, aliases, the active selection and warmup
state are deliberately per machine.

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
optional quiet hours, and a failure brake (3 failed pings in a row pause
the account until the next day). After a ping the runner re-fetches and
verifies the window started. Failures notify once per streak (first
failure + the giving-up notice), not every 10 minutes. Every attempt is
logged to `~/.cache/ai-smartbar/warmup.log`. Trade-off: each ping spends a
sliver of the general budgets — that is what buys the earlier reset. Note
that starting a window affects the account everywhere, not just this
machine. No credentials are touched: account context comes from
`cswap run`, auth from the official claude CLI.

> **launchd PATH note:** agents get a bare PATH, and cswap resolves the
> claude CLI via `which`. The runner hardens its subprocess PATH and the
> installer bakes the usual bin dirs into the LaunchAgent — re-run
> `./install/macos-warmup.sh` after updating to pick this up (this fixed
> warmup silently never firing in v2).

## Data freshness

`cswap list --json` serves claude-swap's usage store and only hits
Anthropic's usage API (`/api/oauth/usage`) on its adaptive per-account
plan — real fetches run every 1–10 min under a strict per-token budget
(~20/h against a measured ~28–30/h cap for non-first-party clients).

Each poll runs ONE combined venv-python process that first *primes* the
store using claude-swap's own auto-engine collector mode — anything stale
**or** plan-due is fetched now, the sanctioned way past the 3-minute
serve TTL — and then serves `cswap list --json` in the same interpreter
(v2 spawned two processes per poll). A fresh-and-not-yet-due account is
never re-fetched, so the budget holds by construction. Combined with the
refresh-on-open / wake / switch triggers, the display tracks `/usage`
within the active poll plan; the residual gap is the price of the API's
per-token cap (`/usage` itself is exempt as a first-party client).
Design notes with the full audit live in `docs/superpowers/specs/`.

## Notes

- **Auto-registration caveat:** every account you sign into on this
  machine gets captured into claude-swap's local backup. Set
  `SMARTBAR_AUTO_ADD=off` if you don't want that. Failed attempts
  (logged out, locked keychain) retry at most every 10 min.
- **Restart re-notify:** alert state is in-memory; restarting the app
  while a metric is ≥90% used fires that notification once more.
- **XFCE hover text** is a single line (StatusNotifier limitation); full
  details are in the menu.

## Troubleshooting

```bash
ai-smartbar --once     # headless: prints icon state, title, account rows
tail ~/.cache/ai-smartbar/tray.log        # Linux log
tail ~/Library/Logs/ai-smartbar.log       # macOS log
tail ~/.cache/ai-smartbar/warmup.log      # warmup attempts + skip reasons
```

- **"Re-login required" on an account card** — claude-swap's stored
  credential for that slot is dead (token rotation; see
  [Credential lifecycle](#credential-lifecycle-re-capture--healing)).
  Sign in to Claude Code as that account once; the bar re-captures it
  automatically. Its switch button stays disabled until then.
- After 3 consecutive cswap failures the pills go hollow with a gray `?`
  and the last data stays visible marked stale.

## Development

```bash
python3 -m unittest discover -s tests -v   # 122 tests, no external deps
./tests/e2e-warmup.sh                      # warmup loop against stateful mocks
./tests/e2e-autoadd.sh                     # auto-registration against the built app
```

Layout: `smartbar/core/` holds all logic and formatting (unit-tested,
Python 3.9+); `smartbar/linux/tray.py`, `smartbar/macos/menubar.py` and
the Swift app (`macos-swift/`, a 1:1 mirror of core) only render what
core produces. Design docs live in `docs/superpowers/`.
