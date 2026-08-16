# AI smartbar

Your Claude — and ChatGPT/Codex — usage limits, always visible in the bar,
with one-click Claude account switching. A cross-platform companion for
[claude-swap](https://github.com/realiti4/claude-swap).

```
Menu bar / tray:   … [▮▮] 🔋 📶 🔊 …          (two vertical pills, fill =
                                             % used, each its own color)

The panel — the same layout on macOS, Linux and Windows:

 AI smartbar  Updated 9:56 PM              ⟳ ⏻
 [✳ Claude]  ❁ OpenAI
  ┌──────────────────────────────────────────┐
  │ ● ios8build@gmail.com  20x [Make Active] │
  │ 5h   resets in 2h 43m                45% │
  │ ██████████████████────────────────────── │
  │                        ╷                 │
  │ 7d   resets in 6d 14h                24% │
  │ ██████████────────────────────────────── │
  │                       ╷                  │
  │ Fable  resets in 6d 14h              34% │
  │ ██████████████────────────────────────── │
  └──────────────────────────────────────────┘
 ▌┌──────────────────────────────────────────┐
 ▌│ ● other@account           5x    [ACTIVE] │
 ▌│ 5h   resets in 1h 02m                62% │
 ▌│ █████████████████████████─────────────── │
 ▌│                   ╷                      │
 ▌└──────────────────────────────────────────┘
   v1.0.0                    [Update to 1.1.0]

 (numbers are % USED — the /usage scale; each limit is a label line over a
  full-width bar; ▌ = the rail marking the ACTIVE account, alongside its
  [ACTIVE] chip; "resets in …" sits beside the window it belongs to, so
  the only number over the bar is the bar's own; ╷ = the pace tick under
  the bar, how far through that window you are — past the fill means you
  are under budget, before it means you are burning faster; one chip
  carries the plan badge (hover a macOS card for its device count); [✳
  Claude] ❁ OpenAI = provider
  tabs, each with its provider's mark, the selected pill bright and the
  other faded, row only
  present when both providers have accounts; follows the system's
  light/dark appearance)
```

## Features

- **Twin-pill icon (~16 px).** First pill = the all-models limit (worst
  of the 5-hour and 7-day windows), one more pill per per-model weekly
  bucket (e.g. Fable). Pills FILL upward as tokens are spent — a nearly
  full pill means nearly at the limit — and step green → yellow → light
  red → dark red → **purple** (100% used, spent); hollow `?` when there is
  no data. A small red dot joins the icon when a new release is waiting.
- **Every number is "% used"** — exactly what Claude Code's `/usage`
  shows, no mental arithmetic. Promo-boosted limits are already baked
  into the API's percentages, so both read the same scale.
- **Native macOS popover** (SwiftUI, macOS 13+). One card per account,
  one row per window (5h / 7d / per-model): a label line
  (`5h   resets in 2h 43m   45%`) over a bar that gets the card's full
  width, countdowns ticking live from the absolute reset time, the active card
  marked by a leading rail and a green `ACTIVE` chip, and an "Updated"
  stamp that shows when the usage was actually measured at the API — not
  when the app last polled. It is the ACTIVE account's measurement time,
  the same account `/usage` describes; cswap refreshes each slot on its
  own plan, so the others in the same payload can be much older. The row
  stays this dense on purpose: a taller three-line variant read better in
  isolation and turned a glanceable panel into one you scroll.
- **Follows your appearance.** Light and dark are both first-class: the
  panel reads the system setting and paints from the matching palette,
  including a status ramp retuned for a light ground (the dark green and
  amber were picked against a near-black panel and wash out on white).
  Only colour changes — every position, size and hit target is identical
  in both, so the two can never drift into different layouts. The Linux
  and Windows panels stay dark for now; the appearance is a parameter they
  simply don't pass yet.
- **Nothing about time touches the bar.** The bar answers one question —
  how much of this window you have spent — so the countdown reads
  `resets in 2h 43m` beside the window's own name, and the percentage is
  the only value over the bar. Both used to sit at the right, which made
  the bar look like it was counting down to the time next to it.
- **The pace tick.** A hairline hanging under each bar marks how far
  through that window you currently are, so the row answers a second
  question: the fill is how much you have spent, the tick is whether that
  is ahead of schedule. Tick behind the fill means you are burning faster
  than the clock; ahead of it means you are under budget for the window.
  It used to be a notch cut THROUGH the bar, which made a 79% bar look
  like it ended at 72% — the fill's end is the one thing here that has to
  be unambiguous, so the mark moved out from under it. Rows that state no
  window length — a per-model bucket like Fable — get no tick rather than
  a guessed one.
- **One-click switching.** `Make Active` flips the account instantly
  (optimistic UI; failures surface in the popover and the next fetch
  corrects the display). New Claude Code sessions use the new account;
  already-running sessions keep theirs (claude-swap semantics). Accounts
  whose stored credential is dead are labeled and can't be switched to
  (see [Credential lifecycle](#credential-lifecycle-re-capture--healing)).
- **Remove an account.** Hover a card and a small ✕ appears; click it
  and the card's header asks `Remove <email>?  [Remove] [Keep]` in
  place — no dialog, nothing reflows. Claude removal runs
  `cswap remove` (the slot's stored credential backup is deleted;
  signing in as that account re-registers it), OpenAI removal just
  forgets the remembered card. The ACTIVE account is never removable:
  auto-registration would re-add the live login within a minute, so
  offering it would be a lie. Same affordance on the painted
  Linux/Windows panel.
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
- **Device count on hover (macOS).** Point at a card's header and a
  tooltip says how many of your devices have that account active right
  now — and are therefore spending the same 5-hour and weekly budget.
  Nothing shows for an account only one device is on. Cross-platform
  detail, including `--presence-status` on every OS, lives in
  [Device presence](#device-presence-how-many-devices-share-an-account).
- **Tray-icon hover summary.** Point at the menu-bar/tray icon itself —
  before opening anything — and a tooltip gives the one-line active
  account and usage summary. Not the card-header tooltip above (that one
  lives inside the open popover and shows a device count); this is the
  icon in the bar. Linux and Windows already show this natively
  (`AppIndicator`/`pystray` tooltips); macOS now does too.
- **Open-panel hotkey.** macOS: **⌃⌥A** opens the panel from anywhere,
  without clicking the menu-bar icon (needs Accessibility permission —
  see [Requirements](#requirements)). Windows: **Ctrl+Alt+A**, same
  mnemonic. Linux has no portable system-wide hotkey API to hook without
  a new dependency, so `ai-smartbar --open-panel` is the building block
  instead — bind it to a shortcut in your own desktop environment's
  keyboard settings; see [The Linux panel](#the-linux-panel). Design
  tradeoffs and what's verified vs. best-effort per platform:
  [`docs/superpowers/specs/2026-08-16-open-panel-hotkey-design.md`](docs/superpowers/specs/2026-08-16-open-panel-hotkey-design.md).
- **Plan badge per account.** A small chip beside the address names which
  subscription the account is on (`20x` / `5x` / `Pro` / `Team` /
  `Enterprise` / `Free`), read from claude-swap's local per-slot config
  backups; no network, no credential fields touched. Unknown plans show
  no badge. Disable with `SMARTBAR_PLANS=off`.
- **OpenAI/ChatGPT tab.** Sign in to Codex CLI (or the ChatGPT desktop
  app) and within ~2 min an **OpenAI** tab appears next to Claude — one
  card per ChatGPT account (`you@…` with a `Pro` chip, same 5h / 7d /
  per-model %-used bars), read entirely from Codex's own local files.
  Each pill carries its provider's mark to the left of its label, so the
  row is readable both by shape and by name. The tab row only exists when
  both providers have accounts, so a single-provider machine looks
  exactly like before — see
  [The OpenAI tab](#the-openai-tab-chatgptcodex-accounts).
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
- **Self-updating (on by default).** Every device checks for a new release
  at login and every 6 hours, then rebuilds, re-installs its agents and
  restarts itself. A **red dot appears on the bar icon** the moment one is
  waiting, and **Update to vX.Y.Z** (popover on macOS, menu row on
  Linux and Windows — a short commit sha on `--channel main`, where the
  target is a commit) applies it immediately. Rolls itself back if a
  release fails to build, and never touches a checkout with
  uncommitted work — see [Updating](#updating-and-releases).
- **The same panel on Linux and Windows.** Cards, filling bars, the ACTIVE
  chip and the upgrade button — not a stripped-down menu. Geometry and
  wording come from one unit-tested layout in `smartbar/core/`, and both
  platforms paint it with the same GTK-free cairo module
  (`smartbar/paint/`) instead of native widgets, so it looks the same
  everywhere rather than inheriting a theme. `⟳ Open AI smartbar` in the
  tray menu (or middle-click the icon) opens it on Linux; a left-click
  opens it on Windows — see [Linux panel](#the-linux-panel) and
  [`docs/windows-bring-up.md`](docs/windows-bring-up.md) (Windows is
  unverified on real hardware — see [Status](#install) below).
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
- macOS (native Swift app only): **Accessibility permission** for the
  **⌃⌥A** open-panel hotkey — System Settings > Privacy & Security >
  Accessibility, add AI smartbar (some macOS versions surface the same
  grant under Input Monitoring instead). Not required for anything else
  the app does; without it, ⌃⌥A silently does nothing (logged, not a
  crash) and every other feature works exactly the same.
- Windows: Python 3.9+ (pycairo ships `win_amd64` wheels for
  cp39–cp313, so there is no version ceiling here) and Git for
  Windows. `install/windows.ps1` creates a venv in the checkout and
  installs `pystray`, `pillow` and `pycairo` into it.

## Install

```bash
git clone https://github.com/ductran27/AI_smartbar ~/tools/AI_smartbar
cd ~/tools/AI_smartbar

# Linux (installs ~/.local/bin/ai-smartbar + autostart, starts it):
./install/linux.sh

# macOS — native SwiftUI app (recommended; macOS 13+, needs Xcode CLT):
./install/macos-swift.sh

# macOS — Python/rumps fallback (older Macs, no Swift toolchain):
./install/macos.sh

# Windows (installs a venv in the checkout, Startup shortcut + Scheduled Task):
.\install\windows.ps1
```

The checkout stays live — it is what the app runs from and what the updater
fast-forwards, so put it somewhere permanent.

Both macOS installers share the `com.ductran.ai-smartbar` LaunchAgent
label — installing one replaces the other at login (single instance).
Uninstall with `./install/linux.sh --uninstall`,
`./install/macos.sh --uninstall`, or `.\install\windows.ps1 -Uninstall`.

Every installer also turns on [self-updating](#updating-and-releases).
Add `--no-auto-update` to opt a device out, or `--channel main` to follow
`origin/main` instead of releases (what a development checkout wants).
On Windows the same flags are PowerShell-style: `-NoAutoUpdate` and
`-Channel main`.

> **Private-repo devices:** the updater runs from launchd/systemd/Task
> Scheduler where nobody can type a password, so each device needs a
> working non-interactive credential *before* it can update itself:
> `gh auth login && gh auth setup-git` (HTTPS, token → keychain) or an SSH
> key with an `git@github.com:` remote. The installer probes this and
> refuses loudly rather than letting updates fail silently forever.

> **Status:** the native macOS app is live-verified (2026-08-15:
> light/dark appearances are UNRELEASED — reviewed through
> `--preview-popover` in both schemes and the Swift builds clean, but the
> light one has not yet been seen on the native popover. The three-line
> metric sections and stacked provider tabs that shipped alongside them
> were reverted the same day, on the live app: the rows were ~2x taller
> and the panel stopped being glanceable; 2026-08-15: v1.0.0 builds,
> installs and runs; 2026-07-25: v0.8.0 OpenAI tab with faded tab pills;
> 2026-07-22: v3 %-used UI, re-login card + blocked switch,
> re-capture, warmup agent with fixed PATH). The Linux tray is written to
> spec from the unit-tested core but has not been re-run on a Linux box
> since v2. The Windows tray is further behind than that: it has never
> been run on a Windows machine at all.
> Every line of `smartbar/windows/` was written to spec and unit-tested
> with the GUI stubbed (pystray, tkinter and pycairo are faked in
> tests) — real DPI scaling, focus-out dismissal, hover tracking and
> the Task Scheduler apply path are all unverified. See
> [`docs/windows-bring-up.md`](docs/windows-bring-up.md) for the manual
> checklist and the known-unresolved issues before relying on it.

## Configuration (environment variables)

Everything below is read from the app's environment. To set any of it
**durably**, put it in `~/.config/ai-smartbar/config.env` — see
[Settings that survive an update](#settings-that-survive-an-update). Editing a
LaunchAgent or systemd unit by hand does not last: the installers rewrite those
from scratch, and applying an update *is* re-running them.

| Variable | Default | Meaning |
|---|---|---|
| `SMARTBAR_INTERVAL` | `60` | Poll period in seconds while near a limit / recovering |
| `SMARTBAR_INTERVAL_IDLE` | `180` | Relaxed poll period when nothing is near a limit (macOS Swift app) |
| `SMARTBAR_PANEL` | popover | `always` keeps the Linux panel on screen permanently instead of opening on demand |
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
| `SMARTBAR_WARMUP_NOTIFY` | – | `off` silences the warmup failure notifications |
| `SMARTBAR_UPDATE` | on | `off` disables self-updating entirely on this device |
| `SMARTBAR_UPDATE_CHANNEL` | `release` | `release` tracks the newest `vX.Y.Z` tag; `main` follows `origin/main` fast-forward-only |
| `SMARTBAR_UPDATE_INTERVAL` | `21600` | Seconds between scheduled update checks, floor 300 — see [Changing the cadence](#updating-and-releases) |
| `SMARTBAR_UPDATE_NOTIFY` | – | `off` silences the updated/failed notifications |
| `SMARTBAR_PRESENCE` | on | `off` stops this device publishing or counting devices |
| `SMARTBAR_PRESENCE_INTERVAL` | `300` | Seconds between presence heartbeats (floor 60) |
| `SMARTBAR_PRESENCE_TTL` | 3 × interval | How long a silent device still counts (floor 2 × interval) |
| `SMARTBAR_PRESENCE_LABEL` | hostname | Name this device shows in `--presence-status` |
| `SMARTBAR_PLANS` | on | `off` hides the plan badges (`· 20x`) and skips the local tier reads |
| `SMARTBAR_OPENAI` | on | `off` hides the OpenAI tab and skips every Codex read |
| `SMARTBAR_CODEX_HOME` | `~/.codex` | Where Codex CLI keeps its files (tests, relocated installs) |

## Settings that survive an update

```bash
mkdir -p ~/.config/ai-smartbar
cat > ~/.config/ai-smartbar/config.env <<'EOF'
# one setting per line; # comments and blank lines are ignored
SMARTBAR_INTERVAL=90
SMARTBAR_WARMUP_DAILY_CAP=3
SMARTBAR_PRESENCE=off
EOF
./install/macos-swift.sh          # or ./install/linux.sh — re-apply now
```

**Why a file rather than the agent.** Nothing reads your shell profile: a
macOS GUI app started by launchd gets no shell environment at all, and every
agent — the app's LaunchAgent, the warmup and update agents, the Linux
autostart entry and systemd units — is written *from scratch* by the
installers. Since applying an update means re-running those installers, a
variable added to an agent by hand survives exactly until the next release.
`config.env` sits outside the checkout and outside every generated unit, and
the installers fold it into each one they write — so it is re-applied by every
update instead of by anyone remembering to.

Settings take effect when the agent is next started (re-run the installer, or
just wait for the next update or login) — environment variables are read once
at process start either way.

Two rules, both deliberate:

- **Only `SMARTBAR_*` keys.** These end up in a launchd agent's environment, so
  a config file that could set `PATH` or `DYLD_INSERT_LIBRARIES` would be a
  privilege problem rather than a feature. Anything else is reported and ignored.
- **`SMARTBAR_UPDATE_CHANNEL` and `SMARTBAR_REPO_ROOT` are not settable here.**
  Both already have their own mechanism (`--channel` plus the installers'
  read-back of the installed unit; the checkout the installer built from). Two
  sources for one key is how the two halves of a system come to disagree.

Anything unusable is named at install time rather than silently dropped:

```
warning: ~/.config/ai-smartbar/config.env: line 3: 'PATH' is not a SMARTBAR_* setting
```

## Device presence: how many devices share an account

Hovering a card's header on macOS says **how many of your devices have
that account active right now**, so they are spending the same 5-hour and
weekly budget — usually the answer to "why is this window burning so
fast?". Nothing shows for an account only one device is on. The Linux and
Windows panels don't surface this in the card itself; `--presence-status`
below works on every platform.

Exactly one account is active per device, so the counts across all cards
add up to your number of live devices. That is the quickest way to check
the number is right.

`ai-smartbar --presence-status` names them:

```
device    13ceb7630470 "mac-ducs-mbp"
remote    readable
publish   ok
cadence   beat 300s, counted for 900s after the last beat

counts    syu3cs@virginia.edu (2)

live devices:
  mac-ducs-mbp             syu3cs@virginia.edu                this device
  linux-thinkpad           syu3cs@virginia.edu                42s ago
```

Each name is `<platform>-<hostname>`, so you can tell *which* machine a count
came from — a hostname alone does not distinguish a Mac from a Linux box.
`SMARTBAR_PRESENCE_LABEL` replaces the whole name, prefix included.

**How devices find each other.** They share no server, and a count that
only works on one network would be wrong exactly when it matters (a laptop
elsewhere). The one authenticated channel every device already has is this
repo, so each one parks a single ref under `refs/smartbar/` every 5
minutes:

```
refs/smartbar/p1/<device>/<label>/<epoch>/<sha256 of the address>
```

The platform lives in that `<label>` rather than in a component of its own, on
purpose: the label is display-only, so every existing device reads a prefixed
one without changes. Adding a component would change the ref *shape*, and an
older device's decoder rejects a shape it does not know — it would quietly stop
counting every upgraded device until it upgraded too, which is the exact
undercount this feature exists to prevent.

It points at a commit the remote already has, so **nothing is ever
committed and no objects are transferred** — no branch, no tag, no
history, invisible in GitHub's UI, and untouched by clones, fetches and
`install/release.sh`. Addresses never leave the machine; only a hash does.

**What it can and cannot see.** Only devices running AI smartbar. A machine
using the same Claude account without this app is invisible, and always
will be — there is no Anthropic API that reports sessions.

**Accuracy.** A device stops counting 15 minutes after its last heartbeat,
or immediately when you quit the app. Clock disagreement between machines
cannot hide a device: a beacon that looks expired still counts while we
have watched its ref change on our own clock. If the remote cannot be
reached at all, the last good answer stands briefly and then every count
disappears — the app will not claim `1` when it cannot see the others.

`SMARTBAR_PRESENCE=off` opts a device out completely: it publishes nothing
and shows no counts.

## The OpenAI tab (ChatGPT/Codex accounts)

Signing in with `codex login` (or the ChatGPT desktop app) is caught the
same way a Claude `/login` is: the card appears by itself, labeled with the
account's plan (`Free` / `Plus` / `Pro` / `Pro Lite` / `Team`). Everything
is read from Codex's **own local files** — the login's label fields for
who/which plan, and the rate-limit snapshots Codex records while it works
for the 5h / 7d / per-model bars. No network requests, no credential
fields, nothing ever written under `~/.codex`.

Three honest differences from the Claude tab, all consequences of what
exists to read:

- **Numbers move while Codex is being used** and hold still between uses
  (hover a card for when they were measured). There is no polling a usage
  API here without handling OAuth tokens, which this app never does. A
  window whose reset time passes while idle reads 0% — the budget is back.
- **No "Make Active".** claude-swap has no Codex equivalent, so ChatGPT
  accounts are shown, not switched. The live login wears the ACTIVE chip.
- **Signed-out accounts are remembered** (labels + last numbers only, in
  `~/.cache/ai-smartbar/openai-accounts.json`) and shown as read-only
  cards — "Signed out — usage from its last session" — with rows whose
  windows have since reset dropped rather than displayed stale. Hover a
  remembered card and click its ✕ to forget it (nothing under `~/.codex`
  is touched; signing in with Codex brings it back).

The menu-bar pills stay Claude-only; the tab is the OpenAI surface.
`SMARTBAR_OPENAI=off` removes all of it.

## The Linux panel

The Linux UI is the same panel as the macOS popover, not a reduced menu.
Open it with **⟳ Open AI smartbar** in the tray menu, or **middle-click**
the tray icon. Hovering the icon shows the same numbers as a tooltip.

**A hotkey, via your own desktop environment.** GNOME, KDE, XFCE and every
other DE bind keyboard shortcuts their own, mutually incompatible way, and
none expose a portable API this repo can hook without a new dependency —
see the design doc linked from the Features list above for what was
considered and why. What ships instead is the building block:
`ai-smartbar --open-panel` signals an already-running tray (over SIGUSR1,
by PID — see `smartbar/linux/tray.py`) to show its panel, exiting non-zero
with a clear message if no tray is running. Bind it yourself:

```bash
# GNOME: Settings → Keyboard → Keyboard Shortcuts → View and Customize
# Shortcuts → Custom Shortcuts → +
#   Name:    Open AI smartbar
#   Command: /path/to/AI_smartbar/bin/ai-smartbar --open-panel
#   Shortcut: whatever you like — ⌃⌥A mirrors macOS/Windows if you want
#             the same muscle memory across machines

# KDE: System Settings → Shortcuts → Custom Shortcuts → New → Global
# Shortcut → Command/URL, same command as above.
```

Windows hosts the identical panel in a borderless tkinter window instead
of GTK — see [`docs/windows-bring-up.md`](docs/windows-bring-up.md) for
how it opens there and for what is still unverified. Everything below
this point is Linux-specific plumbing (StatusNotifier, DBus, xfwm4) and
does not apply to Windows.

**Where it opens, and moving it.** The panel opens in the top-right corner
of the work area, and any part of it — cards and buttons included — is a
drag handle: a press that travels moves the panel, a press that stays put
is the click it always was. The spot a drag ends on is remembered
(`~/.cache/ai-smartbar/panel-position.json`) and reused until the monitor
it was on goes away, at which point the panel re-parks in its corner.

On a Wayland desktop the tray prefers X11 via XWayland (it sets
`GDK_BACKEND=x11,wayland` at startup; an explicit `GDK_BACKEND` wins),
because a native Wayland client can neither choose where its window opens —
GNOME just centers it — nor move itself, which is exactly the placement and
the drag above. A session with no X server at all falls back to native
Wayland: the compositor places the panel, and a drag hands the gesture to
the compositor instead of moving the window by hand.

**Or keep it up permanently.** `SMARTBAR_PANEL=always` turns the panel into a
desktop readout instead of a popover: shown at startup, never auto-hidden,
anchored to the top-right of the work area (or wherever you last dragged
it), on every workspace, and never
taking keyboard focus so it cannot steal typing from the app underneath. The
tray icon and its menu stay exactly as they are. This is the zero-gesture
option for hosts where a click cannot reach the app at all (see below).

A pin is a `DOCK` window, and it anchors to the roomiest monitor rather than
the "primary" one. Both are deliberate. Desktops that must keep a GPU output
alive often run a small headless dummy plug, and that dummy usually wins
`primary` while sitting stacked on top of the display you actually use — so
"top-right of primary" lands mid-screen, and xfwm4 additionally clamps an
ordinary window to that dummy's width (measured: a move to x=2218 landed at
1891). `DOCK` is exempt from that clamp. `pin_origin()` in
`smartbar/core/popover_layout.py` is the pure, unit-tested geometry.

**Why a window and not a nicer menu.** An AppIndicator menu is serialised to
the panel process over DBus as *dbusmenu*, which carries labels, icons,
checkmarks and separators — nothing else. `Gtk.MenuItem.add(widget)` works
in-process and is silently dropped in transit, so cards, filled bars and the
ACTIVE chip cannot exist inside a tray menu at any level of effort. Hence a
real window.

**Why left-click cannot open it.** StatusNotifier gives no left-click
callback, and on some hosts there is nothing to hook even in principle:
xfce4-panel 4.20's systray plugin implements exactly `AttentionIconName,
IconThemePath, ItemIsMenu, OverlayIconName, Scroll, SecondaryActivate,
Status, Title, ToolTip` — no `Activate` and no `ContextMenu` anywhere in the
library. Left-click there always opens the dbusmenu, and no amount of
app-side work changes that, including hand-writing the StatusNotifierItem
instead of using libayatana-appindicator: the host simply never sends the
event. `SecondaryActivate` *is* implemented, which is why middle-click works,
and `SMARTBAR_PANEL=always` is the answer when even one gesture is too many.

**Why it is painted, not built from widgets.** Every pixel comes from cairo,
driven by `smartbar/core/popover_layout.py` — the same geometry the SwiftUI
popover uses. Nothing is themed, so it looks identical on XFCE, GNOME and
KDE, and the layout (including where each click lands) is a pure function
with unit tests. The trade-off is no native keyboard navigation or
screen-reader support in the panel; the tray menu remains fully native.

**Look at it without a Linux box:**

```bash
ai-smartbar --preview-popover out.png                # your real accounts
ai-smartbar --preview-popover out.png --demo          # every card state, no cswap needed
ai-smartbar --preview-popover out.png --scheme light  # the light appearance
```

Needs `pycairo` only — it never imports GTK, so this works on macOS too and
is how the panel is reviewed during development. `--scheme` exists because
the panel follows the SYSTEM appearance at run time, which means the one you
are not currently in is the one that rots unnoticed.

Notes: under Wayland the compositor places the window
because clients cannot position themselves, while on X11 it appears next to
the pointer. If the window cannot be created at all, the tray menu falls
back to the old text rows so nothing is lost.

## Updating and releases

Every device self-updates. A LaunchAgent (macOS) or systemd user timer
(Linux, cron fallback) runs `ai-smartbar --update` **at login and every 6
hours**; the popover's **Update to vX.Y.Z** button (Linux: the **⬆ Update
to vX.Y.Z** menu row) runs the same pass immediately.

**Asking now.** Those buttons only appear once a check has already *found*
something, so without one a device could sit up to 6 hours behind with no way to
ask. **Check for Updates** asks the remote immediately — in the macOS
popover's **More options** menu, and as a **⇅ Check for updates** row directly
under *⟳ Refresh now* in the Linux and Windows tray menus.

It only reports; applying stays the separate, deliberate **Update to vX.Y.Z**
button. The answer appears in place, and also as a desktop notification —
necessary on Linux, where clicking a tray row closes the menu and a changed
label would be invisible until you reopened it. The tray icon gains its red dot
if something was found. Hidden on a device with `SMARTBAR_UPDATE=off`, where it
could not promise anything.

Both UIs call the same `ai-smartbar --check-update --json` and display what it
returns; neither decides what the result means. That is deliberate. `--update`
exits 0 both when a device is genuinely current **and** when another update run
already holds the lock, so "up to date" cannot be inferred from an exit code —
the check proves it actually looked before claiming it, and says *"an update run
is already in progress"* otherwise. One copy of that rule is hard enough to keep
right; two, in two languages, is how this app previously came to disagree with
itself about a different window.

**Changing the cadence.** `SMARTBAR_UPDATE_INTERVAL=3600` in
[`config.env`](#settings-that-survive-an-update) checks hourly instead
(floor 300 s). It becomes the LaunchAgent's `StartInterval`, the systemd timer's
period, or — where cron is the fallback — the closest crontab spacing cron can
express, since cron has minute resolution and no notion of an interval. It is
resolved at **install** time rather than read per run, so a change takes effect
when the installer next runs — immediately if you re-run it, otherwise at the
next update.

**How a device tells you.** A waiting release badges the bar icon with a
small red dot — the icon gets slightly wider rather than the dot covering a
pill, since a pill's top is where "nearly at the limit" is read. A desktop
notification also fires once the update has been applied, with a short
excerpt of what changed: on `release` the tagged GitHub Release's notes,
fetched over the network and silently omitted if that fetch fails; on
`main`, which has no Release object to ask, a local `git log --oneline` of
the commits just pulled in instead. Both UIs learn the version change from
`~/.cache/ai-smartbar/update-state.json`, written by the updater.

On macOS that notification arrives wearing **Script Editor's** name and
icon, and that is not a bug you can report away. macOS credits a
notification to the *bundle* of the process that posted it; the updater is
a launchd Python agent with no bundle, so its only mechanism is
`osascript`, which is not an app either — the system credits Script Editor
on its behalf. The app bundle cannot lend out its identity to fix this:
`UNUserNotificationCenter` refuses an ad-hoc signature outright
("Notifications are not allowed for this application", no prompt, measured
on macOS 26.5), and the deprecated `NSUserNotificationCenter` accepts the
call and delivers nothing. A correct sender is bought with a Developer ID
signature, not with code. Linux and Windows have no such rule and do carry
the real name and logo.

**What a pass does.** Fetch tags → pick the target for this device's
channel → check it out → **re-run whichever installers this device has** →
verify → notify. Re-running the real installers *is* the apply step, so a
device rebuilds the Swift binary, rewrites its LaunchAgent/systemd units
and restarts itself with one code path. (This is also why the warmup agent
no longer needs a manual re-install after an update: its plist gets
rewritten too.)

**Channels.**

| channel | target | checkout | for |
|---|---|---|---|
| `release` (default) | newest `vX.Y.Z` tag | detached at the tag | ordinary devices — HEAD names the release it runs |
| `main` | `origin/main` | fast-forward only | a development checkout |

On `main` the offer names a **commit**, not a release. The version only moves
when `install/release.sh` cuts a tag, so a development checkout can sit many
commits ahead of the number it prints — the button reads **Update to da43ea0**
there. macOS **About** names both, `v1.0.1 (da43ea0)`: the release it was built
for, and the commit it was built from. The sha is stamped into the app bundle's
`Info.plist` at build time, so it identifies the **bundle**, not the checkout —
which is the pair that can disagree, since a `git pull` moves the checkout and
only a rebuild moves the app.

```bash
ai-smartbar --check-update     # report only; exit 10 when a release is waiting
ai-smartbar --update           # apply it now
ai-smartbar --update --force   # re-apply the current target (repair a botched install)
ai-smartbar --update --reset   # discard local drift and re-install from scratch
./install/macos-update.sh --channel main   # change channel / re-probe credentials
./install/macos-update.sh --uninstall      # this device stops self-updating
```

> **A manual `--update` does not inherit this device's channel.** The channel
> lives in the update agent's LaunchAgent/systemd environment, so it applies
> to the agent's own runs. Running `ai-smartbar --update` by hand from a
> terminal starts with a clean environment and falls back to `release`, which
> checks a **development** checkout out *detached* at the tag — and
> `install/release.sh` then refuses to cut a release, because it requires
> `main`. On a development checkout, say which channel you mean:
> `SMARTBAR_UPDATE_CHANNEL=main ai-smartbar --update`. Recover a detached
> checkout with `git checkout main` (the tag and `main` are the same commit
> right after a release, so nothing is lost).

**It will not eat your work.** An update is refused (and the popover shows
a pause marker with the reason) when the checkout has uncommitted changes
to tracked files or unpushed commits. `--reset` is the only flag that
overrides that, and even then local changes are parked in a rescue ref
first — recover with `git stash apply refs/smartbar-rescue/<stamp>`. A
device is never walked backwards to an older release either.

**A bad release cannot brick a device.** The app bundle is backed up before
the rebuild; if the build, the install or the post-install verification
fails, the checkout and the binary are restored and the old app is
restarted, and the failure is counted — after 3 failed attempts against the
same ref in a day the device stops retrying it (`--force` overrides). Every
pass is logged to `~/.cache/ai-smartbar/update.log`.

**Cutting a release** (from the development checkout, on `main`, clean and
synced):

```bash
./install/release.sh patch      # or minor / major / 1.2.3
```

That first requires green GitHub Actions for the current exact `main` SHA,
bumps the one canonical version (`smartbar/__init__.py`), propagates it into
`Version.swift` and the app bundle's `Info.plist`, and runs the local unit/e2e
gates. It then pushes the version commit, waits for the cross-platform Actions
matrix on that exact SHA, and only after success creates and pushes `vX.Y.Z`.
A failed or missing CI run therefore leaves an untagged candidate that cannot
reach release-channel devices. `--no-push` also leaves an untagged local
candidate; resume it with the printed explicit-version command. `--full` adds
the slow e2e suites, and `--gh` additionally creates a GitHub release. Devices
on the release channel converge within 6 hours, or instantly from the popover
button.

**Bootstrapping a device.** A device running code from before this feature
has no updater, so its first update is manual — `git pull` then re-run its
installer. After that it maintains itself. Only code is shared between
devices — everything else stays per machine, listed under
[Credential lifecycle](#credential-lifecycle-re-capture--healing).

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
> installer bakes the usual bin dirs into the LaunchAgent (this fixed warmup
> silently never firing in v2). Since v0.3.0 the self-updater re-runs this
> installer for you, so plist changes like that one propagate on their own.

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
ai-smartbar --once          # headless: prints icon state, title, account rows
ai-smartbar --check-update  # is a newer release waiting?
tail ~/.cache/ai-smartbar/tray.log        # Linux log
tail ~/Library/Logs/ai-smartbar.log       # macOS log
type %LOCALAPPDATA%\ai-smartbar\tray.log  # Windows log (PowerShell: Get-Content -Tail)
tail ~/.cache/ai-smartbar/warmup.log      # warmup attempts + skip reasons
tail ~/.cache/ai-smartbar/update.log      # update decisions, applies, rollbacks
```

- **Windows: nothing appears and `tray.log` is empty.** The Startup shortcut
  runs `pythonw.exe`, which has no console — so a failure before logging is
  configured leaves no trace anywhere. Run it by hand with the console-attached
  interpreter instead: `venv\Scripts\python.exe bin\ai-smartbar`. Full
  procedure and the known-unresolved issues are in
  [`docs/windows-bring-up.md`](docs/windows-bring-up.md).
- **Purple means spent** — a metric at 100% used is past "critical", so it
  gets its own step in the ramp rather than looking switched off.
- **A dot is hollow** when there is no measurement behind it (no data yet,
  or a dead credential — the text under it says which). Gray therefore only
  ever means "unknown", never "exhausted".
- **The popover shows a pause marker next to the version** when an update
  was found but held back; hover it for the reason (usually uncommitted
  changes or unpushed commits in the checkout).

- **"Re-login required" on an account card** — claude-swap's stored
  credential for that slot is dead (token rotation; see
  [Credential lifecycle](#credential-lifecycle-re-capture--healing)).
  Sign in to Claude Code as that account once; the bar re-captures it
  automatically. Its switch button stays disabled until then.
- After 3 consecutive cswap failures the pills go hollow with a gray `?`
  and the last data stays visible marked stale.

## Development

**The logo** is one committed asset, `assets/ai-smartbar.png`, drawn by
`smartbar/paint/app_icon.py` — the menu-bar mark at icon scale, using the
same pill proportions and the same colour ramp, so the thing in your Dock
is recognisably the thing in your menu bar. It is a *generated* artifact
like `Version.swift`: edit the painter, never the PNG, and regenerate with

```bash
python3 -m smartbar.paint.app_icon assets/ai-smartbar.png
```

`tests/test_branding.py` fails if the two drift apart, and pins the three
installers that consume it (a macOS `.icns` built with `iconutil`, a
freedesktop icon-theme entry, a Windows `.ico`) to the same filename.
Committing it is what lets those installers place an icon before any of
this project's Python dependencies exist — macOS never `pip install`s
anything, and Windows needs the icon while it is still building the venv.

```bash
python3 -m unittest discover -s tests -v   # whole suite, no external deps
                                           # (the painter and Linux front-end
                                           #  tests skip without pycairo)
./tests/e2e-warmup.sh                      # warmup loop against stateful mocks
./tests/e2e-autoadd.sh                     # auto-registration against the built app
./tests/e2e-update.sh                      # self-update against a throwaway origin
./tests/e2e-presence.sh                    # two real clones against a bare origin
./tests/e2e-config.sh                      # config.env -> real LaunchAgents/units
```

`tests/e2e-config.sh` runs the real installers with `launchctl`, `systemctl`,
`crontab`, `pkill`, `setsid` and `nohup` shadowed by no-op stubs and `HOME`
pointed at a temporary directory, then reads back the agent files they wrote
and lints them. It is contained by `PATH` order rather than by trust, so it
cannot disturb real agents, your crontab or running processes.

`tests/e2e-update.sh` builds a fake origin out of the current working tree
and drives a fake device through check → apply → no-op → blocked → reset →
rollback → brake. It never touches the real repo, the real LaunchAgents or
the real `$HOME`, which is what makes it the safety net for devices that
cannot be tested by hand.

Layout: `smartbar/core/` holds all logic, formatting and the popover
geometry (`popover_theme.py` + `popover_layout.py`, unit-tested, Python
3.9+). `tray_controller.py` is the toolkit-free state machine the Linux,
Windows and rumps front-ends all drive — fetch, apply, alert, re-capture,
check-update — so a fix to any of that lands once instead of in three
copies that drift. `smartbar/paint/` paints the panel with cairo
(`popover_draw.py`, `tray_icon.py` — both GTK-free and therefore
renderable anywhere, which is what lets one painter serve Linux, Windows
and the preview CLI without drifting). `smartbar/linux/` hosts it in GTK
(`popover_window.py`, `tray.py`); `smartbar/windows/` hosts the same
painter in tkinter and pystray (`popover_window.py`, `tray.py`,
unverified on real hardware — see [Status](#install)); the Swift app
(`macos-swift/`) mirrors core 1:1 natively instead of using the shared
painter. `smartbar/macos/menubar.py` is the legacy rumps fallback for Macs
that cannot build the Swift app — it keeps the simple text menu and is not
part of the panel work. Design docs live in `docs/superpowers/`.
