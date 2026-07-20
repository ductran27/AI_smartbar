# AI_smartbar v2 — compact twin-pill redesign — Design Spec

**Date:** 2026-07-19 (approved via rendered mocks, artifact
`smartbar-redesign-mocks`; supersedes the ring-gauge popover + text menu-bar
label of the 2026-07-19 SwiftUI spec)

## Decisions (locked by user)

1. **Menu bar = one tiny icon** (Variant B): twin vertical pills, fill
   anchored bottom, level = **% tokens left**, drains downward. Left pill =
   general limit (worst of 5h/7d), one more pill per scoped model (Fable).
   ~16 px wide vs ~95 px of the old two-segment text (−83%).
2. **All numbers are "% left"** — icon fill, popover bars, values, alerts,
   thresholds. One mental model: bar sinks = tokens sinking.
3. **5-step status ramp on % left:** green >50 · yellow 25–50 · light red
   10–25 · dark red 1–10 (fires the switch alert, ≡ old 90% used) · gray 0.
   Colors: #2EA652 / #D9A621 / #E4604B / #CC2F2F / #737373.
4. **Popover:** horizontal 6 pt bars replace ring gauges; **bold** metric
   labels (5h/7d/Fable); value column `62% · 3h 15m` (bold %, mono);
   active card wears a **white 1.5 pt outline + ACTIVE chip**; tightened
   spacing (outer 11, card 9/11, gaps 7); `Updated HH:MM` moves into the
   header right of the title; ⟳ and ⏻ share one equal-size box; stale ⚠
   joins the header; **footer removed**.
5. **Dark mode only.** Popover forces dark appearance; light-mode styling
   is out of scope.
6. **Refresh default 300 s** (was 60); `SMARTBAR_INTERVAL` still overrides;
   manual ⟳ and instant post-switch fetch unchanged.
7. **Same design on Linux:** the tray renders the identical twin-pill icon
   via cairo from the same core pill states. GTK menu stays text but flips
   to % left.

## Core contract changes (smartbar/core, unit-tested first)

- `Metric.left` = `max(0, 100 − pct)`.
- `color(pct_used)` → one of `green|yellow|low|critical|gray` with
  boundaries on left: green >50 ≥ yellow >25 ≥ low >10 ≥ critical >0 = gray.
- Env (remaining-based): `SMARTBAR_YELLOW=50`, `SMARTBAR_LOW=25`,
  `SMARTBAR_RED=10`; `SMARTBAR_TEST_THRESHOLD` sets all three.
- New `pill_states(account)` → `[(fraction_left, color)]`, general first
  then each scoped; `None`/no-data → `[]` (renderers draw hollow + "?").
- Display strings flip to remaining: `icon_text` "5h62", `metrics_text`
  "5h 62% · 7d 78% · F 69%", `icon_rows` text likewise; `DOT` gains
  low→🟠, critical→🔴 (text fallback UIs only).
- Alerts: fire when `left ≤ red`, once per (account, metric, resetsAt);
  copy: title "Claude: 5h — 8% left", body "Resets in 3h 15m." +
  "Best switch: #2 email (74% left)" / "No other account available."

## Renderers

- **Swift (primary):** `MenuBarIcon.swift` draws an NSImage
  (width 4+5n+2(n−1), pill 5×16 r 2.5, track gray-0.5 α 0.45, fill
  ≥2 pt when >0, hollow + centered "?" on no-data/≥3 failures,
  isTemplate=false); `MetricBarRow.swift` + `StatusPalette.swift` replace
  `RingGauge.swift`; `PopoverView`/`AccountCardView` per decisions 4–5;
  `UsageStore` default 300 s, publishes the icon, new alert copy.
- **Linux:** `render_icon(rows)` → `render_pills(states)` — same geometry
  at 6× (pill 30×96, gap 12, r 15) via cairo; unverified on this Mac
  (no GTK), mirrors the unit-tested core exactly like the Swift port did.
- **rumps fallback:** inherits remaining text automatically; unchanged
  otherwise.

## Non-goals

Light mode; icon animation (popover bars keep 0.6 s ease-out); Sparkle /
signing; changes to cswap interaction.
