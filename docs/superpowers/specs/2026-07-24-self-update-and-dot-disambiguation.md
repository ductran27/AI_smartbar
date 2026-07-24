# Self-update across devices + status-dot disambiguation

Date: 2026-07-24. 169 unit tests + 4 e2e suites green; Swift release build
clean. Live-verified on the Mac: update agent installed and its RunAtLoad
pass logged `channel=main … -> current`, app rebuilt to 0.2.0 in the bundle
Info.plist (was hardcoded 0.1.0), popover footer screenshotted.

## Problems reported

1. "Why did the first account get no green light?" — ios8build's dot was
   gray while the other two were green/red.
2. "Make sure all devices that install this repo get auto update, reset and
   update when a newest version is released."

## 1. The gray dot was correct, but overloaded

`Account.worstStatus` (Models.swift) returns the status of the max-pct
metric; `Thresholds.status(forUsedPct:)` maps `used >= 100` to `.gray`
(mirror: `model.color`). Live cswap at the time: ios8build 5h 0%, 7d 79%,
**Fable 100.0%**, `usageStatus: "ok"`. So the dot meant *a limit is
exhausted* — not an error, and not the v3 dead-credential case (that slot
had healed since v3; the older note saying it was left dead was stale).

The real defect found while confirming it: `worstStatus` also falls back to
`.gray` when there are no metrics at all, so "the Fable bucket is spent"
and "this slot's credential is dead" rendered as the same dot.

**Fix, in two steps.** First, `model.dot_style(account)` →
`"solid" | "hollow"` (hollow ⟺ `worst(account) is None`), mirrored as
`Account.dotHollow` and rendered by AccountCardView as a stroked ring
instead of a filled circle.

Then, on the user's call, the exhausted step got its own colour: a **sixth
status `full`, purple #8B5CF6**, returned by `color()`/`Thresholds.status`
for `used >= 100`. Note this could NOT be done by recolouring `gray` —
`worstStatus` falls back to `.gray` for a dataless account and the hollow
ring is drawn in that same colour, so the two had to be separated first.
Result: purple = spent, hollow gray = unknown, red = nearly there, and no
two states share a rendering any more. MetricBarRow's exhausted row also
came up from 0.45 → 0.68 white: a purple bar is a deliberate signal, and
45% text made the row read as disabled instead.

**Palette parity, made testable.** Renderers look colours up *by name*
(`COLORS[color_name]` in the cairo Linux badge, `DOT[...]` in macos_title),
so a status missing from a table is a crash on every poll — in the one
renderer with no hardware to test it on. The RGB table therefore moved into
core as `model.RGB`, tray.py uses it instead of a private copy, and
`TestPaletteParity` asserts every value `color()` can return has both a
glyph and an RGB triple. Swift's exhaustive `switch` gives the same
guarantee for free.

## 2. There was no update mechanism at all

Audit: no tags, no releases, no version check, no scheduled fetch, no
update script; three disagreeing version markers (`__version__` 0.2.0,
Info.plist 0.1.0, docs "v3"). And the four install shapes fail differently
— only the warmup agent picks up a `git pull` on its own (fresh process per
run); the Swift app runs a *copy* in `~/Applications`, so a pull does
nothing at all until a rebuild.

### Design

* **Decisions in `smartbar/core/update.py`** (pure, no subprocess): semver
  tag parsing/selection, channel policy, dirty/unpushed refusal,
  no-downgrade guard, day-bucketed failure brake, installer-target order.
  Follows the repo rule that all semantics are unit-tested in core.
* **Plumbing in `smartbar/update_git.py`**, orchestration in
  `smartbar/update_runner.py` (mirrors the `warmup.py` / `warmup_runner.py`
  split, keeps every file near the 200-line guideline).
* **Channels.** `release` (default) checks out the newest `vX.Y.Z` tag
  *detached* — a consumer device's HEAD then names its release. `main`
  fast-forwards `origin/main` and is what this development checkout uses.
* **Apply = re-run the device's own installers.** They already rebuild,
  rewrite units and restart, and they are idempotent. No duplicated logic,
  and agent-body changes (v3's baked warmup PATH) finally propagate without
  the manual re-install the README used to demand.
* **Scheduling.** macOS LaunchAgent `com.ductran.ai-smartbar.update`
  (StartInterval 21600 + RunAtLoad, PATH baked, channel baked). Linux
  systemd user timer with `KillMode=process` — without it systemd tears
  down the cgroup and takes the freshly restarted tray with it — cron as
  fallback. Installed **by default** by every installer (updating costs
  nothing, unlike warmup which spends quota); `--no-auto-update` opts out.
* **One-click upgrade.** `UpdateStatus.swift` reads
  `~/.cache/ai-smartbar/update-state.json` (written by the runner, includes
  `repoRoot`) and the popover footer shows the version plus an *Update to
  vX.Y.Z* button. The button `launchctl kickstart`s the **update job**
  rather than spawning a child: applying an update restarts the very app
  that would be the parent. Linux/rumps get an equivalent *⬆ Update to
  vX.Y.Z* menu row, spawned with `start_new_session=True` for the same
  reason.
* **Announcing it without being opened.** A waiting release badges the bar
  icon itself (`MenuBarIcon.badged`, cairo dot in `render_pills`). The dot
  goes in a *widened* frame, never over a pill — a pill's top is the
  "nearly at the limit" region and covering it would trade usage
  information for a notification. Colour is system red #FF3B30, chosen
  brighter than both usage reds (#E4604B / #CC2F2F) so it cannot be misread
  as a usage alarm; one constant changes it if it ever does.
  `UpdateStatus` polls the state file every 300 s (plus on activation) so
  the badge appears with nothing opened.
* **`install/release.sh`** bumps the one canonical version, propagates it to
  `Version.swift` + Info.plist, runs the tests, commits, tags, pushes.
  Without this nothing exists to update *to*.

### Safety, and why each rail is there

| Rail | Failure it prevents |
|---|---|
| Refuse on dirty tree / unpushed commits | A scheduled update eating this dev checkout's work |
| `--reset` parks work in `refs/smartbar-rescue/<stamp>` first | "Repair" silently destroying local changes |
| Forced git identity in `stash create` | On a device with no global git identity the stash fails, turning `--reset` into a silent discard |
| Bundle backup + checkout restore + old-app restart | A release that fails to build bricking the menu bar on every device at once |
| 3-failures-per-day-per-ref brake | A poisoned tag retried forever |
| No-downgrade guard | The dev box between a version bump and its tag being dragged back |
| `GIT_TERMINAL_PROMPT=0` + install-time headless fetch probe | The repo is PRIVATE; a credential prompt in launchd would wedge or silently never update — exactly v2's warmup failure mode |
| `SMARTBAR_UPDATE_APPLY=1` skips the update agent's own unload | The updater killing itself while rewriting its own plist |
| `SMARTBAR_UPDATE_TARGETS` override | `tests/e2e-update.sh` reaching the tester's real LaunchAgents |

### Tests

`tests/test_update.py` (39 tests) covers the pure policy. `tests/e2e-update.sh`
builds a fake origin from the current working tree, cuts four releases plus
junk tags (`nightly`, `v0.0.10-rc1` — a lexicographic selector would pick
the latter), and drives a fake device through: check (exit 10) → apply →
no-op → blocked-on-dirty → `--reset` with a verified-applicable rescue ref
→ failed install rolled back to the previous tag → brake engaging. Isolated
via `HOME`, `SMARTBAR_CACHE_DIR` and `SMARTBAR_UPDATE_TARGETS`.

## Known limits

* The **first** update on any existing device is manual (`git pull` + re-run
  its installer): today's deployed code has no updater to invoke.
* Only the macOS Swift path is live-verified. The Linux timer/tray restart
  is written to spec and covered by mocks; no Linux box has run it (the
  tray itself has not been re-run on Linux since v2).
* The dev checkout on `main` will report *blocked — N unpushed commits*
  whenever work is pending. That is the intended trade-off, surfaced in the
  popover footer rather than hidden.
* A hollow dot could not be verified visually on this machine: all three
  slots currently report usage. It is covered by core tests and the Swift
  mirror is one branch.
