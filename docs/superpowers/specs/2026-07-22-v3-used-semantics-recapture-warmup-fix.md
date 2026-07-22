# v3: %-used semantics, credential re-capture, warmup fix, perf pass

Date: 2026-07-22. Live-verified on the Mac same day (popover screenshot,
cswap add smoke, warmup agent log). 122 unit tests + 2 e2e suites green.

## Problems reported

1. First account (ios8build) "gone" after logging into other accounts —
   popover showed bare "No usage data".
2. Cross-machine numbers "not in sync".
3. Wanted % USED shown (the /usage scale), not % left.
4. Performance still heavy.
5. "Did the 5-hr auto trigger work?"

## Root causes (evidence)

- **Dead slot**: `cswap list --json` showed `usageStatus:
  "relogin_required"` for slot 1. Anthropic rotates OAuth refresh
  tokens; Claude Code rotates the live grant, cswap's backup stays at
  capture-time rotation → first backup refresh hits `invalid_grant`
  (claude-swap switcher.py:2801) → permanent until re-capture. Smartbar
  only ran `cswap add` when NO slot was active, so registered backups
  rotted. Same mechanism per machine explains "sync" divergence
  (server-side usage is identical; dead slots differ per machine).
- **Warmup NEVER fired in production**: every ping since install errored
  `'claude' was not found on PATH` — launchd agents get a bare PATH and
  cswap's SessionManager.run does `shutil.which("claude")`
  (session.py:330) regardless of what follows `--`. Second latent bug:
  runner passed the claude BINARY PATH as the first post-`--` token, but
  cswap treats post-`--` as claude ARGUMENTS. E2E mock exec'd argv[4:]
  directly and masked both. Failed attempts also consumed the daily cap
  (syu3cs: "daily cap (6) reached" = 6 failures) and notified every 30
  min.
- **Perf**: two Python process boots per 60s poll (primer + list),
  snapshot + NSImage republished every poll even when unchanged,
  @Published writes on every cycle.

## Changes

### Core (python, mirrored 1:1 in Swift)

- `model.py` v3: all display = % used; thresholds used-based
  (`SMARTBAR_YELLOW/LOW/RED` defaults 50/75/90 — same default colors as
  v2, only env-override interpretation flips); pills/bars FILL as spent;
  gray at ≥100. New: `Account.status` (raw usageStatus), `state_text`
  (card copy per status), `switch_blocked`
  (relogin_required/no_credentials), `needs_recapture` (active slot
  dead). `best_switch` skips dead slots.
- `recapture.py` (new): `RecapturePolicy.action(snapshot, monotonic) ->
  register|heal|refresh|None`. Register: no active slot, 600s cooldown
  (unchanged auto-add). Heal: active slot dead → `cswap add` now, 120s
  cooldown. Refresh: every 900s re-capture live login (first healthy
  snapshot only baselines — no add-chasing after registration; caught by
  e2e-autoadd). `cswap add` on existing slot = local re-capture +
  clear_dead_token (verified: "Updated credentials for Account 3").
  Gates: SMARTBAR_AUTO_ADD=off kills all; SMARTBAR_RECAPTURE=off keeps
  registration only.
- `cswap.py`: parse carries usageStatus; COMBINED_CODE primes AND runs
  `cli.main(["list","--json"])` in ONE venv-python boot; exit 97 =
  version drift → per-process latch → old primer+list fallback; plain
  list fallback on any failure.
- `warmup.py`: precise skip reasons ("re-login required (stored
  credential dead)" / "no usage data (<status>)" / "no 5h usage data" /
  "window running"); failure streak state (`record_failure/
  record_success/consecutive_failures`) — 3 consecutive failures pause
  the account until next day.
- `warmup_runner.py`: `env_with_claude_on_path` prepends resolved-claude
  dir + ~/.local/bin + /opt/homebrew/bin + /usr/local/bin to subprocess
  PATH; `ping_argv` post-`--` = claude ARGS ONLY; notify on streak==1
  and streak==3 only.

### Swift app

- Models/StatusPalette/MetricBarRow/MenuBarIcon/AccountCardView: v3
  flip; dead card shows state text + disabled Make Active (+ switchTo
  guard in store).
- UsageStore: adaptive timer (60s when failing/no data/worst≥80% used,
  else SMARTBAR_INTERVAL_IDLE=180); equal-snapshot skip (no icon
  rebuild/re-render); lastRefresh un-published; guarded @Published
  writes; RecapturePolicy port; alerts at used ≥ RED with "% used" copy.
- CswapClient: combined fetch + NSLock'd latch + fallbacks.

### Install / e2e

- macos-warmup.sh bakes PATH into the LaunchAgent (belt+suspenders with
  the runner env). Reinstalled on the Mac.
- mock-cswap-warmup now mirrors real cswap: resolves `claude` from PATH,
  rejects path-like first post-`--` token (regression-pins both bugs).
  e2e-warmup exposes the mock as a file literally named `claude`.

## Answer to "did the 5-hr auto trigger work?"

No — zero successful pings ever (log: every attempt failed on PATH since
2026-07-20). Fixed as above; agent reinstalled 2026-07-22 17:42, now
logs correct per-account skip reasons and will ping the next genuinely
idle window. Note: today's "daily cap reached" entries were failures,
not warms.

## Verification

- 122 unit tests, e2e-warmup (3 scenarios), e2e-autoadd (2 scenarios) —
  all pass; swift release build clean.
- Live: popover screenshot shows %-used bars, dead-slot card + disabled
  switch, ACTIVE outline; `cswap add` smoke → "Updated credentials for
  Account 3"; warmup agent running with new reasons; plist has PATH.

## Unresolved

- ios8build slot stays "Re-login required" until user runs `/login`
  as it once on this machine (then auto-heals). Cannot be automated —
  needs the account's own credential.
- Linux tray still not re-run on a Linux box (mirrors core; unit-tested
  only).
- Whether Anthropic also caps concurrent grants per account (would kill
  oldest machine's backup on Nth login) — unobservable client-side;
  re-capture keeps backups fresh either way.
