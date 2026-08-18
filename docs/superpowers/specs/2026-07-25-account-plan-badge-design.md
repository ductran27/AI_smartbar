# Account plan badge — design note (2026-07-25)

Show each account's subscription plan on its card: `● ios8build@gmail.com · 20x (2)`.
Labels: `20x` / `5x` / `Pro` / `Free` (+ `Team` for enterprise orgs). Dim gray, after
the email, before the device count. User-approved placement + wording.

## Data source (verified live on this Mac, 2026-07-25)

Tier is already on disk, per account, in **cswap's per-slot config backups**:

```
~/.claude-swap-backup/configs/.claude-config-<N>-<email>.json
  └── .oauthAccount.organizationRateLimitTier   e.g. "default_claude_max_20x"
      .oauthAccount.organizationType            e.g. "claude_max"
      .oauthAccount.subscriptionType (absent here; keychain blob has it)
```

Observed: ios8build=`default_claude_max_20x`, jsmith=`default_claude_max_5x`,
duc.dut.wr=`_20x`, duc.dut.wr2=`_20x`. Live `~/.claude.json` `.oauthAccount`
carries the same keys for the ACTIVE login (freshest right after a plan change +
/login; overlays the backup copy for that email).

**Read ONLY these label keys.** Never token fields, never the keychain, never
`credentials/` (empty on macOS — per-slot creds live in the `claude-swap`
keychain service; NOT touched). Never copy/import backups (grant-rotation war).

Rejected alternatives: per-slot keychain reads (ACL prompts, Linux divergence);
OAuth profile endpoint (network + token handling for data that changes ~never);
injecting into PRIMER/COMBINED (those strings are sync-pinned with Swift).

## Semantics — `smartbar/core/plan.py` (pure)

- `tier_label(rate_limit_tier, organization_type, subscription_type) -> str`
  1. tier suffix `_(\d+)x` → `"{N}x"` (`default_claude_max_20x` → `20x`)
  2. else org type contains `pro`→`Pro`, `free`→`Free`, `enterprise`|`team`→`Team`
  3. else subscription type title-cased (`max`→`Max` — coarse fallback)
  4. else `""` → NO badge (mirrors devices=0 → no badge; also covers managed
     API-key accounts whose backups lack `oauthAccount`)
- `plans_by_email(backup_dir, claude_json=...) -> dict[str, str]` — parses
  `configs/.claude-config-*.json`, overlays live claude.json for its
  `emailAddress`. mtime-cached per file. Corrupt/missing file → skip, never raise.
- `apply_plans(accounts, plans)` — stamps `Account.plan` (new field, default
  `""`), same shape as `presence.apply_counts`.

## Flow (mirrors device-badge seams)

- **One composition point:** `model.account_label()` becomes
  `email[ · plan][ (devices)]`. Python renderers (cairo panel, Linux tray menu
  rows, rumps fallback) inherit it.
- **Linux/tray:** poll path calls `plans_by_email` + `apply_plans` each poll
  (cheap local reads, mtime cache).
- **macOS (one shared answer, not a Swift port):** new
  `bin/ai-smartbar --plans --json` prints `{"plans": {email: label}}` — FINAL
  display labels; Swift maps nothing. Swift spawns it on launch + every 900s
  (no per-poll spawn — preserves the halved-spawns perf win), caches the map,
  and composes the label in the existing `PresenceStatus.label` path /
  `AccountCardView`. Badge text dim gray, email-sized.

## Config / kill switch

- `SMARTBAR_PLANS=off` — hides badges AND skips all reads (config.env-settable,
  same contract as `SMARTBAR_PRESENCE`). Default on.
- Backup dir override for tests: `plans_by_email(dir)` parameter +
  `SMARTBAR_CSWAP_BACKUP_DIR` env (default `~/.claude-swap-backup`), and
  `SMARTBAR_CLAUDE_JSON` for the live-overlay file (default `~/.claude.json`)
  so the CLI test never reads real state.

## Errors & edge cases

- Any read/parse failure → account keeps `plan=""` → no badge; poll never fails.
- Tier staleness: inactive account's tier is as of its last add/re-capture —
  acceptable; plan changes require a login, which refreshes the backup.
- Long email + badge: layout estimates text width, renderer measures — existing
  behavior; worst case cosmetic (same rule as device count).
- No new outside-world route (reader is local files only) → e2e-autoadd needs
  no new fence; unit tests use tmp fixture dirs via the seam.

## Testing

- `tests/test_plan.py`: mapping table (`_20x`, `_5x`, `_1x`, pro, free,
  enterprise, unknown, None, corrupt json, missing oauthAccount, kill switch,
  live-overlay precedence, mtime cache).
- Label composition: email-only / +plan / +devices / +both.
- Parity pin (TestMacAndLinuxAgree pattern): source-scrape Swift — badge
  composition matches python's; Swift contains NO `organizationRateLimitTier`
  or mapping logic.
- `tests/test_popover_layout.py`: header text updated expectations.
- Live verify on this Mac: `--plans --json` → 20x / 5x / 20x / 20x; popover
  screenshot; Linux path via the gi-stub trick.

## Non-goals

No keychain reads, no network, no pill/icon changes, no cswap changes, no
per-window-limit inference, no new state files.
