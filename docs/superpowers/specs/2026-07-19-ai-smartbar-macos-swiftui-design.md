# AI_smartbar native macOS app (Swift/SwiftUI) — Design Spec

**Date:** 2026-07-19 (follow-up to the approved cross-platform spec; user
requested the Swift/SwiftUI option with a very clean, beautiful macOS UI)

## Overview

A native macOS menu-bar app replacing the rumps UI as the **recommended**
macOS experience: same information and behavior as the Linux tray (general
limit + per-model bucket, account switching, 90% alerts), rendered as a
custom SwiftUI popover with animated ring gauges. The Python/rumps app stays
in-repo as a fallback for older Macs.

## Hard constraints (examined 2026-07-19)

- Development happens on Linux: **no Swift toolchain here** (`which swift`
  empty) and SwiftUI is Apple-only → the package ships compile-unverified;
  first build happens on the user's Mac. Risk is minimized by:
  - Swift Package Manager executable (hand-written `Package.swift`), no
    .xcodeproj. Builds with Xcode Command Line Tools only: `swift build`.
  - swift-tools-version 5.9 → language mode 5 (no strict-concurrency traps),
    no macros, no `@Observable` (macOS 14+) — `ObservableObject`/`@Published`.
  - Target **macOS 13+** for `MenuBarExtra(.window)`; older Macs use rumps.
  - Data layer (`Models.swift`, `CswapClient.swift`) imports Foundation only —
    UI-independent, matching the captured real cswap fixture in
    `tests/fixtures/cswap_list.json`.
- GUI apps launched by launchd get a bare PATH → cswap binary resolved from
  `$SMARTBAR_CSWAP`, `~/.local/bin/cswap`, `/opt/homebrew/bin/cswap`,
  `/usr/local/bin/cswap` (first executable wins).
- Notifications via `osascript -e 'display notification …'`:
  `UNUserNotificationCenter` needs bundle identity + permission flow, which a
  bare SPM binary lacks; osascript works from any process.
- No date math: cswap's preformatted `countdown` strings are displayed as-is.

## Architecture

```
macos-swift/
├─ Package.swift                  # SPM executable, platforms: [.macOS(.v13)]
└─ Sources/AISmartbar/
   ├─ AISmartbarApp.swift         # @main App, MenuBarExtra(.window), .accessory policy
   ├─ Models.swift                # Foundation-only: Codable wire structs + domain
   ├─ CswapClient.swift           # Foundation-only: Process wrapper, 30s timeout
   ├─ UsageStore.swift            # @MainActor ObservableObject: timer, alerts, switch
   ├─ RingGauge.swift             # animated ring + Status→Color palette
   ├─ AccountCardView.swift       # per-account card
   └─ PopoverView.swift           # window content: header/cards/footer
install/macos-swift.sh            # build → minimal .app bundle → LaunchAgent
```

Behavior contracts are ports of the unit-tested Python core (`smartbar/core/`):
same thresholds (70/90, env-overridable), same row logic (general worst above
scoped worst, independent colors), same fire-once/re-arm alert semantics keyed
on (account, metric) → resetsAt, same menu-bar segments string.

## Design language ("very clean, very beautiful")

- **Menu bar label:** `Text` segments identical to the Python title —
  `🟢 5h31 · 🟢 F30` — monospaced digits (emoji dots render in color in the
  menu bar; custom label views risk template flattening).
- **Popover (330 pt wide, window-style):**
  - Header: `AI smartbar` headline, borderless refresh (`arrow.clockwise`,
    disabled while refreshing) and quit (`power`) icon buttons.
  - One **card per account** on `.thinMaterial` with 12 pt continuous-corner
    radius and a hairline border (accent-tinted when active): status dot +
    email (middle-truncated), `ACTIVE` capsule chip or a bordered small
    `Make Active` button, then a row of **ring gauges** — one per metric
    (5h, 7d, Fable, …): 48 pt circle, 6 pt rounded-cap stroke on an 8%
    primary track, threshold color, integer % centered (rounded design,
    semibold, monospaced digits), metric label + countdown captions below,
    0.6 s ease-out animation on value change.
  - Footer: orange `stale` label with `wifi.slash` when the last fetch
    failed but old data is shown; `Updated HH:MM` in tertiary at right.
  - Loading state: small spinner + caption; error-without-data: orange
    triangle label with the error text.
  - Palette matches the Linux badge: green (0.18,0.65,0.32), yellow
    (0.85,0.65,0.13), red (0.80,0.16,0.16), gray (0.45,0.45,0.45); light and
    dark mode come free via materials/semantic styles.

## Install / lifecycle

`install/macos-swift.sh` (idempotent; `--uninstall` reverses):
1. Preflight: `swift` toolchain, cswap present.
2. `swift build -c release --package-path macos-swift`.
3. Wrap the binary into `~/Applications/AI_smartbar.app` (hand-written
   Info.plist: `CFBundleIdentifier com.ductran.ai-smartbar`,
   `LSUIElement true`, min system 13.0) — menu-bar-only, no Dock icon
   (belt-and-braces: the app also sets `.accessory` activation policy).
4. LaunchAgent `com.ductran.ai-smartbar` (same label as the rumps installer —
   deliberate takeover so only one variant runs at login), RunAtLoad, stderr
   to `~/Library/Logs/ai-smartbar.log`, `launchctl load`.

## Error handling

Mirror of Linux: keep last snapshot rendered on fetch failure; after 3
consecutive failures the menu-bar label degrades to `⚪ ?`; footer marks
`stale`; error text shown in the popover when no data exists yet. All
subprocess calls carry a 30 s kill timer; UI state mutates only on the main
actor.

## Verification limits (honesty section)

On Linux: bash syntax check of the installer, XML validation of both plists,
brace/paren balance scan of every Swift file, Python suite regression run.
**No Swift compilation happens before the user runs `./install/macos-swift.sh`
on the Mac** — first-build fix-ups are possible and expected to be minor;
the README says exactly that.

## Non-goals

- No Sparkle updates, no code signing/notarization (personal app run from
  source), no Login Items API (LaunchAgent is enough), no per-model rows
  beyond what cswap's `scoped[]` provides, no Codex/OpenAI data (unchanged).
