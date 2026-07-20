# AI_smartbar auto window-starter ("warmup") — Design Spec

**Date:** 2026-07-19. Approved in chat: state-triggered keep-alive, ALL
registered accounts, membership derived live from cswap (auto add/remove,
never asks), security-first, notify on failure only.

## Concept

The Claude 5-hour window starts at the first message. Keeping a window
running at all times means the budget reset is always as early as possible.
Policy: **whenever an account has no 5h window running, send one minimal
ping**. Structurally self-capped at ~5 warmups/day/account (a warmup starts
a 5 h window; the gate blocks while one runs). Trade-off (documented): each
ping spends a sliver of the general budgets.

## Gate (pure logic, smartbar/core/warmup.py, unit-tested)

Warm account A iff ALL of:
1. `A.ok` and A has a 5h metric.
2. The 5h metric's `resets_at` is empty **or ≤ now** (an ended window =
   idle; reported pct describes the ended window and is ignored).
   Unparseable `resets_at` → skip (conservative).
3. Snapshot `fetched_at` age ≤ 30 min (stale data is not trusted).
4. Cooldown: ≥ 30 min since A's last warmup attempt (verification lag guard).
5. Daily cap: < `SMARTBAR_WARMUP_DAILY_CAP` (default 6) attempts today.
6. Not inside quiet hours `SMARTBAR_WARMUP_QUIET` ("23-05" wraps midnight;
   empty/default = none).

## Runner (smartbar/warmup_runner.py)

Every run (launchd StartInterval 600 → `ai-smartbar --warmup-once`):
flock lock (skip if another run is live) → `cswap list --json` → for each
eligible account: `cswap run <n> -- <claude> --model haiku -p . --max-turns 1`
(haiku ping spares scoped weekly buckets; nonzero exit → one retry without
`--model`), 120 s timeout, output discarded → refetch → verify the account's
5h `resets_at` is now in the future → log line per attempt
(`~/.cache/ai-smartbar/warmup.log`), desktop notification on failure only
(`SMARTBAR_WARMUP_NOTIFY=off` disables; used by tests). State
(`~/.cache/ai-smartbar/warmup-state.json`, atomic tmp+rename): per-email
daily attempt counts + last-attempt ts, pruned to registered emails and
7 days.

## Security model

- No credentials touched: `cswap run` provides account context, the
  official `claude` CLI owns auth. AI_smartbar still never talks to
  Anthropic endpoints itself.
- Fixed one-char prompt "." — no injection surface; `--max-turns 1`;
  stdout/stderr discarded.
- Enabled = the warmup LaunchAgent is installed (`install/macos-warmup.sh`,
  `--uninstall` reverses). Nothing runs without that explicit step.
- Every attempt logged locally; structural cap + daily cap + cooldown mean
  a misbehaving upstream cannot cause a ping storm.
- Binary resolution: `SMARTBAR_CLAUDE` env → `which claude` →
  `~/.local/bin/claude` → `/opt/homebrew/bin/claude` (cswap resolved as in
  core.cswap).

## Platforms

macOS: LaunchAgent `com.ductran.ai-smartbar.warmup`, StartInterval 600.
Linux: documented crontab line (`*/10 * * * * ~/tools/AI_smartbar/bin/ai-smartbar --warmup-once`).

## Non-goals

UI surface in the popover (v2 candidate); per-account opt-out lists;
warming the 7-day/weekly buckets (impossible — they are always running).
