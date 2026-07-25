# OpenAI (Codex/ChatGPT) accounts + provider tabs — design note (2026-07-25)

Catch ChatGPT logins the way Claude logins are caught, and show them under a
second tab. Popover/panel grows a `Claude | OpenAI` tab row **only when both
providers have accounts** (a single-provider machine looks exactly like
today); each OpenAI account is a card `● duc.dut.wr@gmail.com · Pro Lite`
with the same 5h / 7d / per-model %-used bars. User-approved: hide-when-one,
remember signed-out accounts, passive freshness.

## Data sources (verified live on this Mac, 2026-07-25; all local, no tokens)

1. **Who is signed in + plan:** `~/.codex/auth.json` → the id_token's JWT
   *payload claims* `email` and `https://api.openai.com/auth.chatgpt_plan_type`
   (observed: duc.dut.wr@gmail.com / `prolite`). Decoded in-memory
   (base64url, unverified); ONLY those label claims are extracted. The token
   strings themselves are never returned, stored, logged or printed.
   `auth_mode: apikey` has no email → treated as "no ChatGPT login".
2. **Usage:** Codex session rollouts
   `~/.codex/sessions/Y/M/D/rollout-*.jsonl` carry `token_count` events with
   `rate_limits`: `limit_id "codex"` → primary `window_minutes: 300` (5h) +
   secondary `10080` (7d); scoped per-model ids like `codex_bengalfox`;
   each window `{used_percent, resets_at (epoch)}` — the exact v3 %-used
   semantics. Files reach 530 MB → **tail-read only** (last 256 KB), newest
   mtime first, per-file (mtime,size) cache; events sometimes omit the
   secondary window → keep latest-per-(limit, window).
3. **No switcher exists for Codex** (cswap is Claude-only) → OpenAI cards
   have no "Make Active" in v1; the live login gets the ACTIVE chip.

Rejected: OpenAI usage API (network + access-token handling — breaks the
"hands off your credentials" promise); `codex exec` probe (spends quota —
possible later opt-in, warmup-style); keychain (Codex keeps auth on disk).

## Semantics — `smartbar/core/codex.py` (pure, all policy lives here)

- `enabled()` — `SMARTBAR_OPENAI=off` kill switch: hides the tab AND skips
  every read. `codex_home()` — `SMARTBAR_CODEX_HOME` seam, default `~/.codex`.
- `plan_label(plan_type)` — `free→Free, plus→Plus, pro→Pro, prolite→Pro
  Lite, team→Team, enterprise→Enterprise, edu→Edu, business→Business`,
  unknown → `title()`, empty → "" (no badge).
- `login()` — (email, plan) of the live auth.json, or None (apikey/absent).
- `rate_limits()` — latest snapshot per (limit_id, window) from rollout
  tails. Window→metric: 300→`5h`, 10080→`7d`, other N→`{N//60}h`; scoped
  `codex_<name>` → `scoped:<Name>` label `<Name>.title()`. `resets_at`
  epoch → ISO-8601 UTC (what reset_countdown_format / TimeRemaining parse).
- **Registry** `<cache>/openai-accounts.json` (same cache-dir resolution as
  update-state.json, so `SMARTBAR_CACHE_DIR` isolates it): `{email:
  {plan, metrics, measuredAt, lastSeen, active}}` — labels + numbers ONLY.
  Attribution cutoff rule (rollouts carry no account id): when the login
  changes, the old account's numbers freeze as-is; only events with
  timestamp > the detection moment are credited to the new login. Cold
  start: all history → current login.
- `accounts()` → `[model.Account]`, provider="openai": live login first
  (active=True, status "ok"), then remembered logins (active=False,
  status `signed_out`). `payload()` → the `--openai --json` dict.

## Model & snapshot (no Claude semantics may shift)

- `Account.provider: str = "claude"` (new field, default keeps every
  existing constructor working).
- `Snapshot.openai: list = []` — a SEPARATE list, deliberately NOT merged
  into `snapshot.accounts`: duc.dut.wr@gmail.com is both a Claude and a
  ChatGPT account, so a merged list would let plan.apply_plans stamp "20x"
  onto the OpenAI card, presence counts leak across, and a second
  active=True break active_account / needs_registration / best_switch /
  alerts / warmup / the icon. Separation makes cross-contamination
  impossible by construction; those helpers keep their exact meaning.
- `model.STATE_TEXT["signed_out"] = "Signed out — usage from its last
  session"`. `account_label()` already renders `email · Pro` (plan field).
- Icon pills, alerts, auto-add, warmup, presence: Claude-only, unchanged.

## Flow

- **Python UIs** (tray.py poll, menubar.py, --once): after apply_counts /
  apply_plans, stamp `snap.openai = codex.accounts()`.
- **Layout** (`popover_layout.build(snapshot, provider="claude", …)`): when
  both `snapshot.accounts` and `snapshot.openai` are non-empty, render a
  tab row under the header — two pill buttons with hit rects
  `tab:claude` / `tab:openai`; the selected pill is full strength and the
  other faded (user-picked over an accent fill); cards below are the
  selected provider's. Single provider → no tab row (today's layout,
  byte-identical for Claude-only snapshots). OpenAI cards: ACTIVE chip on
  the live login, no switch button, no registration banner (that text is
  Claude/cswap-specific). Linux window keeps the selection in-memory
  (default: claude if present); tray menu + rumps fallback get flat
  section header rows (dbusmenu carries labels only — no tabs possible).
- **macOS (one shared answer, not a Swift port):** new
  `ai-smartbar --openai --json` prints FINAL display data
  `{"accounts": [{email, plan, active, status, stateText, updatedAt,
  metrics: [{key, label, short, pct, resetsAt}]}]}`. Swift `OpenAIStatus`
  (PlanStatus pattern: repoRoot launcher spawn, PATH fix, last-good kept on
  failure) polls every **120 s** + on popover open. PopoverView shows the
  tab row when both lists are non-empty (`@AppStorage` remembers the tab);
  AccountCardView renders OpenAI cards with `account.plan` as the badge,
  no switch button, stateText verbatim. Swift maps nothing: no plan
  strings, no window minutes, no rollout/auth.json knowledge.

## Config / seams

- `SMARTBAR_OPENAI=off` — kill switch (config.env-settable). Default on.
- `SMARTBAR_CODEX_HOME` — codex dir override (tests; default `~/.codex`).
- Registry rides `SMARTBAR_CACHE_DIR`. e2e-autoadd fence: add
  `SMARTBAR_OPENAI=off` (the reader is local-only, but the fence rule is
  "any route past cswap gets fenced").

## Errors & edge cases

- Unreadable/absent auth.json, malformed JWT, corrupt rollout lines,
  unreadable registry → that piece is skipped, never raises; card shows
  what remains (bars absent → hollow dot + state text, same as Claude).
- Bars freeze between Codex uses — honest `updatedAt` (newest event ts)
  feeds the card's measured stamp; no fake freshness.
- limit with no 5h event yet → 7d-only card (rows render per metric found).
- Plan change mid-subscription: claim is fresher than old events'
  plan_type — the claim wins (badge from login(), never from rate_limits).

## Testing

- `tests/test_codex.py`: plan_label table; JWT decode from a synthetic
  token (header.payload.sig built in-test); auth_mode=apikey → None;
  rollout tail parser (fixtures incl. a file larger than the tail window,
  secondary-omitted events, corrupt lines); latest-per-window merge;
  registry upsert/freeze-on-login-change/cold-start; kill switch; ISO
  conversion; accounts() ordering; payload shape; CLI subprocess with
  SMARTBAR_CODEX_HOME + SMARTBAR_CACHE_DIR pointing at tmp fixtures.
- `tests/test_popover_layout.py`: tab row only when both providers
  present; hit rects tab:claude/tab:openai; selected filtering; OpenAI
  card has no switch hit; Claude-only snapshot layout unchanged.
- `TestOpenAIParity` (source-scrape, runs without Swift toolchain): no
  policy markers in any Swift file ("chatgpt_plan_type", "id_token",
  "window_minutes", "rollout", "prolite"); OpenAIStatus
  refreshInterval pinned to 120; both Python UIs stamp `codex.accounts`.
- Live verify: `--openai --json` on this Mac (expect duc.dut.wr / Pro
  Lite / 7d 25%), popover screenshot with both tabs, `--preview-popover`
  for the Linux panel.

## Non-goals (v1)

No OpenAI account switching, no active probes/network, no icon-pill
changes, no presence/warmup/auto-add for OpenAI, no cswap changes, no
reading `~/.codex` beyond auth.json + rollout tails, never writing to it.
