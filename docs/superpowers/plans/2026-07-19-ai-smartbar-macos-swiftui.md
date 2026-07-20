# AI_smartbar native macOS (SwiftUI) Implementation Plan

> Executed inline by the same session that wrote the spec
> (`../specs/2026-07-19-ai-smartbar-macos-swiftui-design.md`). Deviation from
> the usual plan format, disclosed: complete code lives in the repo files
> committed alongside this plan rather than duplicated here — the spec pins
> all contracts (schema, thresholds, alert semantics, palette, layout).

**Goal:** Native menu-bar app with SwiftUI popover (ring gauges, account
cards, switching) as the recommended macOS UI; rumps app kept as fallback.

**Tech:** SPM executable, Swift 5.9 tools, macOS 13+, MenuBarExtra(.window),
ObservableObject store, Process-based cswap client, osascript notifications.

## Tasks

- [x] T1 Spec + this plan committed.
- [ ] T2 Data layer: `macos-swift/Package.swift`,
      `Sources/AISmartbar/Models.swift` (Codable wire structs matching
      `tests/fixtures/cswap_list.json`; domain Metric/Account/Snapshot;
      Thresholds with SMARTBAR_YELLOW/RED/TEST_THRESHOLD; rows +
      menuBarTitle + bestSwitch ports), `CswapClient.swift` (binary
      resolution order per spec; 30 s kill timer; CswapError). Foundation
      imports only.
- [ ] T3 Store + UI: `UsageStore.swift` (@MainActor; SMARTBAR_INTERVAL timer,
      default 60 s; fire-once/re-arm alerts keyed (account,metric)→resetsAt;
      switchTo; ⚪ ? after 3 failures), `RingGauge.swift` (+ Status→Color
      palette), `AccountCardView.swift`, `PopoverView.swift`,
      `AISmartbarApp.swift` (MenuBarExtra window style, .accessory policy).
- [ ] T4 `install/macos-swift.sh` (preflight → swift build -c release →
      ~/Applications/AI_smartbar.app bundle with LSUIElement Info.plist →
      LaunchAgent com.ductran.ai-smartbar, takeover of rumps label) +
      README macOS section rewrite (native recommended, rumps fallback,
      both marked unverified-on-Mac).
- [ ] T5 Linux-side verification gate (all must pass before push):
      - `bash -n install/macos-swift.sh`
      - XML-validate Info.plist and LaunchAgent plist contents
      - brace/paren/bracket balance scan across all `.swift` files
      - `python3 -m unittest discover -s tests` still green (no regression)
      - `git status` clean after commits
- [ ] T6 Commit series + push; update session project memory (Swift app
      added, compile-unverified until first Mac build).

## Mac-side acceptance (deferred, user-run)

`git pull && ./install/macos-swift.sh` → menu bar shows `🟢 5h·· · 🟢 F··`;
popover shows account card with three animated rings; Make Active appears
once a second account is registered; forced-alert check:
`SMARTBAR_TEST_THRESHOLD=10 ~/Applications/AI_smartbar.app/Contents/MacOS/AISmartbar`.
