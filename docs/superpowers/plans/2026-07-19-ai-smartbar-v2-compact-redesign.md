# AI_smartbar v2 Compact Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the text menu-bar label with a twin-pill "% left" icon, the ring popover with tight horizontal bars (dark-only), flip every number to remaining semantics, refresh every 5 min — same design on macOS (NSImage) and Linux (cairo).

**Architecture:** All semantics live in `smartbar/core` (unit-tested first); platform renderers (`linux/tray.py`, Swift app) only draw core-produced pill states / strings. Swift mirrors core 1:1 as before.

**Tech Stack:** Python 3 stdlib + unittest; cairo (Linux draw); Swift 5.9 SwiftUI `MenuBarExtra` + AppKit `NSImage`.

**Spec:** `docs/superpowers/specs/2026-07-19-ai-smartbar-v2-compact-redesign.md`

---

### Task 1: Core v2 semantics (TDD)

**Files:**
- Modify: `tests/test_model.py` (rewrite color/rows/formatting expectations, add left + pill_states)
- Modify: `tests/test_alerts.py` (left-based firing + new copy)
- Modify: `smartbar/core/model.py`
- Modify: `smartbar/core/alerts.py`

- [ ] **Step 1: Rewrite tests to v2 expectations** — boundaries (used%→status): 49.9 green · 50 yellow · 75 low · 90 critical · 100 gray; env `SMARTBAR_YELLOW/LOW/RED` remaining-based; `SMARTBAR_TEST_THRESHOLD` sets all three; `Metric(pct=38).left == 62`; `pill_states` for (5h 29, 7d 21, F 95) == `[(0.71,"green"),(0.05,"critical")]`, `[]` for no data; `icon_rows` → `[("5h71","green"),("F5","critical")]`; `metrics_text` → `"5h 76% · 7d 80% · F 72%"`; `macos_title` → `"🟢 5h76 · 🟢 F72"`, none → `"⚪ ?"`; alerts fire at `left<=10` with title `"Claude: 5h — 8% left"`, suggestion `"(66% left)"`, TEST_THRESHOLD=30 fires at pct 75.
- [ ] **Step 2: Run suite, verify new tests FAIL** — `python3 -m unittest discover -s tests -v`
- [ ] **Step 3: Implement model.py** — `DEFAULT_YELLOW_LEFT/LOW_LEFT/RED_LEFT = 50/25/10`; `low_threshold()`; `DOT` gains `low:🟠, critical:🔴`; `Metric.left` property `max(0,100-pct)`; `color(pct)` judges on left (gray ≤0 < critical ≤10 < low ≤25 < yellow ≤50 < green); `pill_states(account)` = general first + each scoped, `[]` when none; remaining text in `icon_rows`/`icon_text`/`metrics_text`.
- [ ] **Step 4: Implement alerts.py** — fire when `metric.left <= red_threshold()`; title `f"Claude: {label} — {round(left)}% left"`; suggestion `f"({round(worst(s).left)}% left)"`.
- [ ] **Step 5: Suite green** — `python3 -m unittest discover -s tests` → OK.
- [ ] **Step 6: Commit** `feat(core): v2 remaining-based 5-step semantics + pill states`

### Task 2: Linux tray pills + rumps interval

**Files:**
- Modify: `smartbar/linux/tray.py`
- Modify: `smartbar/macos/menubar.py` (interval 300 only; text inherits core)

- [ ] **Step 1: tray.py** — `COLORS` gains `low (0.894,0.376,0.294)`, `critical (0.80,0.184,0.184)`, drop `red`; add `_rounded_rect(ctx,x,y,w,h,r)` arc helper; replace `render_icon(rows,path)` with `render_pills(states,path)`: 6× geometry (pill 30×96, gap 12, margin 12, r 15), track gray 0.5 α 0.45, fill height `max(12, 96·frac)` bottom-anchored, hollow stroked pills + centered bold "?" when `states == []`; `_set_icon(model.pill_states(account))`, loading/error → `_set_icon([])`; interval default `"300"`.
- [ ] **Step 2: rumps** — interval default `"300"`.
- [ ] **Step 3: Syntax check** — `python3 -m py_compile smartbar/linux/tray.py smartbar/macos/menubar.py` (GTK run not possible on macOS; note in README already covers written-to-spec).
- [ ] **Step 4: Commit** `feat(linux,rumps): twin-pill cairo badge, 5-min default refresh`

### Task 3: Swift v2

**Files:**
- Modify: `macos-swift/Sources/AISmartbar/Models.swift` — Status 5-case; left-based `Thresholds` (yellow 50 / low 25 / red 10, TEST override); `Metric.left/leftPct/status`; `Account.pillStates`, `worstStatus`, `worstLeftPct`, `summary`; drop `rows`, `Snapshot.menuBarTitle`, `Status.dot`.
- Create: `macos-swift/Sources/AISmartbar/StatusPalette.swift` — `Status.nsColor` (#2EA652/#D9A621/#E4604B/#CC2F2F/#737373) + SwiftUI `color`.
- Create: `macos-swift/Sources/AISmartbar/MenuBarIcon.swift` — NSImage renderer: width `2·2+5n+2(n−1)`, pill 5×16 r 2.5, track white 0.5 α 0.45, fill `max(2,16·frac)` clamped radius, hollow+"?" for `[]`, `isTemplate=false`.
- Create: `macos-swift/Sources/AISmartbar/MetricBarRow.swift` — bold 11 pt label w 40, 6 pt capsule (track white 9%), fill `status.color` ≥6 pt wide when >0, value `62% · 3h 15m` mono 10.5 bold-%, w 104 trailing, `.easeOut(0.6)`.
- Modify: `AccountCardView.swift` — bars instead of rings; spacing 7, padding 9/11; active = white 0.92 border 1.5 + chip; dot = `worstStatus.color`.
- Modify: `PopoverView.swift` — header `[title · Updated · wifi.slash? · Spacer · ⟳ ⏻ @12.5 semibold in 22×22]`; footer deleted; outer padding 11, spacing 8; `ScrollView` maxHeight 440 when >4 accounts; `.preferredColorScheme(.dark)`.
- Modify: `UsageStore.swift` — default interval 300; `@Published var icon: NSImage` rebuilt on apply/failure; `accessibilitySummary`; alert check `metric.left <= Thresholds.red`, copy `"Claude: 5h — 8% left"` / `"(66% left)"`.
- Modify: `AISmartbarApp.swift` — label `Image(nsImage: store.icon).accessibilityLabel(...)`.
- Delete: `macos-swift/Sources/AISmartbar/RingGauge.swift`.

- [ ] **Step 1: Apply all file changes above**
- [ ] **Step 2: Build** — `swift build -c release --package-path macos-swift` → `Build complete!`
- [ ] **Step 3: Commit** `feat(macos): twin-pill icon + compact dark bar popover`

### Task 4: Install, live verify, docs

- [ ] **Step 1:** `./install/macos-swift.sh` (rebuilds, relaunches LaunchAgent)
- [ ] **Step 2:** Visual check — screencapture of menu bar (pill icon present, correct levels/colors); click icon (Finder frontmost) + screencapture popover: bars, bold labels, white outline, header Updated, equal glyphs.
- [ ] **Step 3:** README — Requirements/ASCII art, config table (`SMARTBAR_INTERVAL` 300, `SMARTBAR_YELLOW/LOW/RED` remaining), behavior notes ("% left" semantics), test count.
- [ ] **Step 4:** Commit `docs: README for v2 remaining-based UI`
