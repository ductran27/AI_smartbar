# AI smartbar

Your Claude usage limits, always visible in the bar — with one-click
account switching. A cross-platform companion for
[claude-swap](https://github.com/realiti4/claude-swap).

```
Menu bar / tray:   … [▮▮] 🔋 📶 🔊 …          (two vertical pills, fill =
                                             % used, each its own color)

The panel — identical on macOS and Linux:

 AI smartbar  Updated 9:56 PM        ⟳ ⏻
 ┌──────────────────────────────────────┐
 │ ● ios8build@gmail.com   [Make Active]│
 │ 5h    ████──────────      45% · 2h43m│
 │ 7d    ██───────────       24% · 6d14h│
 │ Fable ███──────────       34% · 6d14h│
 └──────────────────────────────────────┘
 ╔═════════════════════════════[white]══╗
 ║ ● other@account              [ACTIVE]║
 ║ 5h    ██████████──        62% · 1h02m║
 ╚══════════════════════════════════════╝
   v0.3.1                [Update to 0.4.0]

 (numbers are % USED — the /usage scale; active card outlined white;
  dark-only UI)
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
- **Device count per account.** `syu3cs@virginia.edu (2)` means two of your
  devices have that account active right now — and are therefore spending
  the same 5-hour and weekly budget. No badge means nobody else is on it.
  See [Device presence](#device-presence-the-n-next-to-an-address).
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
  waiting, and **Update to vX.Y.Z** (popover on macOS, menu row on Linux)
  applies it immediately. Rolls itself back if a release fails to build, and
  never touches a checkout with uncommitted work — see
  [Updating](#updating-and-releases).
- **The same panel on Linux.** Cards, filling bars, the ACTIVE chip and the
  upgrade button — not a stripped-down menu. Geometry and wording come from
  one unit-tested layout in `smartbar/core/`, and the Linux side paints it
  with cairo rather than GTK widgets, so it looks the same on XFCE, GNOME
  and KDE instead of inheriting each distro's theme. `⟳ Open AI smartbar`
  in the tray menu (or middle-click the icon) opens it — see
  [Linux panel](#the-linux-panel).
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
git clone https://github.com/ductran27/AI_smartbar ~/tools/AI_smartbar
cd ~/tools/AI_smartbar

# Linux (installs ~/.local/bin/ai-smartbar + autostart, starts it):
./install/linux.sh

# macOS — native SwiftUI app (recommended; macOS 13+, needs Xcode CLT):
./install/macos-swift.sh

# macOS — Python/rumps fallback (older Macs, no Swift toolchain):
./install/macos.sh
```

The checkout stays live — it is what the app runs from and what the updater
fast-forwards, so put it somewhere permanent.

Both macOS installers share the `com.ductran.ai-smartbar` LaunchAgent
label — installing one replaces the other at login (single instance).
Uninstall with `./install/linux.sh --uninstall` /
`./install/macos.sh --uninstall`.

Every installer also turns on [self-updating](#updating-and-releases).
Add `--no-auto-update` to opt a device out, or `--channel main` to follow
`origin/main` instead of releases (what a development checkout wants).

> **Private-repo devices:** the updater runs from launchd/systemd where
> nobody can type a password, so each device needs a working
> non-interactive credential *before* it can update itself:
> `gh auth login && gh auth setup-git` (HTTPS, token → keychain) or an SSH
> key with an `git@github.com:` remote. The installer probes this and
> refuses loudly rather than letting updates fail silently forever.

> **Status:** the native macOS app is live-verified (2026-07-22: v3
> %-used UI, re-login card + blocked switch, re-capture, warmup agent
> with fixed PATH). The Linux tray is written to spec from the
> unit-tested core but has not been re-run on a Linux box since v2.

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
| `SMARTBAR_UPDATE` | on | `off` disables self-updating entirely on this device |
| `SMARTBAR_UPDATE_CHANNEL` | `release` | `release` tracks the newest `vX.Y.Z` tag; `main` follows `origin/main` fast-forward-only |
| `SMARTBAR_UPDATE_INTERVAL` | `21600` | Seconds between update checks. **macOS only, and read at install time** — it becomes the LaunchAgent's `StartInterval`, so it must be set in the installer's own environment (`SMARTBAR_UPDATE_INTERVAL=3600 ./install/macos-update.sh`), not in `config.env`. Linux's timer is fixed at 6 h. Use **⇅ Check for updates** for "right now" |
| `SMARTBAR_UPDATE_NOTIFY` | – | `off` silences the updated/failed notifications |
| `SMARTBAR_PRESENCE` | on | `off` stops this device publishing or counting devices |
| `SMARTBAR_PRESENCE_INTERVAL` | `300` | Seconds between presence heartbeats (floor 60) |
| `SMARTBAR_PRESENCE_TTL` | 3 × interval | How long a silent device still counts (floor 2 × interval) |
| `SMARTBAR_PRESENCE_LABEL` | hostname | Name this device shows in `--presence-status` |

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

## Device presence: the `(N)` next to an address

`syu3cs@virginia.edu (2)` means **two of your devices have that account
active right now**, so they are spending the same 5-hour and weekly budget.
That is usually the answer to "why is this window burning so fast?". No
badge means nobody else is on that account — the badge never shows `(0)`.

Exactly one account is active per device, so the badges across all cards
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
reached at all, the last good answer stands briefly and then every badge
disappears — the app will not claim `(1)` when it cannot see the others.

`SMARTBAR_PRESENCE=off` opts a device out completely: it publishes nothing
and shows no counts.

## The Linux panel

The Linux UI is the same panel as the macOS popover, not a reduced menu.
Open it with **⟳ Open AI smartbar** in the tray menu, or **middle-click**
the tray icon. Hovering the icon shows the same numbers as a tooltip.

**Or keep it up permanently.** `SMARTBAR_PANEL=always` turns the panel into a
desktop readout instead of a popover: shown at startup, never auto-hidden,
anchored to the top-right of the work area, on every workspace, and never
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
ai-smartbar --preview-popover out.png          # your real accounts
ai-smartbar --preview-popover out.png --demo   # every card state, no cswap needed
```

Needs `pycairo` only — it never imports GTK, so this works on macOS too and
is how the panel is reviewed during development.

Notes: cards are a solid dark fill rather than macOS's translucent material
(cairo has no portable blur); under Wayland the compositor places the window
because clients cannot position themselves, while on X11 it appears next to
the pointer. If the window cannot be created at all, the tray menu falls
back to the old text rows so nothing is lost.

## Updating and releases

Every device self-updates. A LaunchAgent (macOS) or systemd user timer
(Linux, cron fallback) runs `ai-smartbar --update` **at login and every 6
hours**; the popover's **Update to vX.Y.Z** button (Linux: the **⬆ Update
to vX.Y.Z** menu row) runs the same pass immediately.

**Asking now, on Linux.** Those buttons only appear once a check has already
*found* something, so without one a device could sit up to 6 hours behind with
no way to ask. **⇅ Check for updates**, directly under *⟳ Refresh now* in the
tray menu, asks the remote immediately. It only reports — applying stays the
separate, deliberate **⬆ Update to vX.Y.Z** row — and since clicking a row
closes the menu, the answer arrives as a desktop notification (the row also
shows it for 20 s, and the tray icon gains its red dot). The row is hidden on a
device with `SMARTBAR_UPDATE=off`, where it could not promise anything. If
another update run is already in progress the check says so rather than
reporting "up to date", which would be a plain lie at the one moment it matters.

**How a device tells you.** A waiting release badges the bar icon with a
small red dot — the icon gets slightly wider rather than the dot covering a
pill, since a pill's top is where "nearly at the limit" is read. A desktop
notification also fires once the update has been applied. Both UIs learn
this from `~/.cache/ai-smartbar/update-state.json`, written by the updater.

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

That bumps the one canonical version (`smartbar/__init__.py`), propagates
it into `Version.swift` and the app bundle's `Info.plist`, runs the unit
suite plus `tests/e2e-update.sh`, then commits, tags `vX.Y.Z` and pushes.
`--no-push` stops before pushing, `--full` also runs the slow e2e suites,
`--gh` additionally creates a GitHub release. Devices on the release
channel converge within 6 hours, or instantly from the popover button.

**Bootstrapping a device.** A device running code from before this feature
has no updater, so its first update is manual — `git pull` then re-run its
installer. After that it maintains itself. Registration, slot numbers,
aliases, the active account and warmup state stay per machine (see below);
only code is shared.

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
tail ~/.cache/ai-smartbar/warmup.log      # warmup attempts + skip reasons
tail ~/.cache/ai-smartbar/update.log      # update decisions, applies, rollbacks
```

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

```bash
python3 -m unittest discover -s tests -v   # 205 tests, no external deps
                                           # (8 painter tests need pycairo)
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

Layout: `smartbar/core/` holds all logic, formatting and now the popover
geometry (`popover_theme.py` + `popover_layout.py`, unit-tested, Python
3.9+); `smartbar/linux/` paints it with cairo (`popover_draw.py`,
`tray_icon.py` — both GTK-free and therefore renderable anywhere) and hosts
it in GTK (`popover_window.py`, `tray.py`); the Swift app (`macos-swift/`)
mirrors core 1:1. `smartbar/macos/menubar.py` is the legacy rumps fallback
for Macs that cannot build the Swift app — it keeps the simple text menu and
is not part of the panel work. Design docs live in `docs/superpowers/`.
