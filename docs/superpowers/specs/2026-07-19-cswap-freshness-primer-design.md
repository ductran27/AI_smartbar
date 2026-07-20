# cswap freshness primer — design note (2026-07-19)

Goal: popover ≈ `/usage` even mid-burn. `/usage` is live because Claude Code
is first-party; claude-swap (0.22, PyPI latest) is budgeted by Anthropic's
usage endpoint: ~28–30 requests / rolling hour / token for non-first-party
clients (measured by claude-swap's own probes; see its `poll_policy.py`).

## Source-audit findings (claude-swap 0.22)

- `UsageStore.reserve(respect_plans=…)` has two eligibility modes:
  - on-demand (`cswap list/status/switch`): fetch only when **stale
    (>SERVE_TTL_S=180 s) AND plan-due** — can never harvest the 60 s
    "urgent" plans (a due entry inside the TTL fails the AND).
  - auto-engine (`respect_plans=False`): **stale OR plan-due** — the
    documented way "the bounded urgent cadence beats the TTL". Never
    re-fetches a fresh-and-not-yet-due entry, so the per-token budget
    (~20/h target) holds by construction.
- Public API `ClaudeAccountSwitcher.accounts_snapshot(fetch=<slot set>)`
  selects the auto-engine mode; no CLI flag exposes it.
- Poll plans: active 60 s (urgent: moving ≥ threshold−15 %) / 180 s
  (moving) / 300 s ceiling; alternates up to 600 s. Plans are persisted;
  every collector inherits them. Concurrency safe via atomic reserve/claims.

## Decision

1. **Primer**: before each smartbar refresh, run the pipx venv python with a
   tiny `-c` program (PRIMER_CODE in `smartbar/core/cswap.py`, mirrored in
   `CswapClient.swift`) that calls `accounts_snapshot(fetch=all slots)` —
   claude-swap's own collector convention, then read the just-updated store
   via unchanged `cswap list --json` (zero JSON coupling). Venv resolved
   from the cswap launcher's exec line (`SMARTBAR_CSWAP_PYTHON` overrides);
   any failure degrades silently to plain list.
2. **Poll 60 s** (was 180): harvests plans the moment they come due,
   including urgent mode near the limit. Store-governed → no added API
   traffic; cost ≈ one 0.3–0.6 s subprocess/min.

## Rejected

- Patching poll-policy constants in site-packages: flat 60 s ⇒ 60 req/h >
  cap ⇒ 429 oscillation ⇒ *worse* freshness; also wiped by pipx upgrades.
- Lowering the autoswitch `threshold` setting to widen the urgent band:
  changes real switch semantics; breaks urgent-mode's boundedness argument.
- First-party UA spoofing: rate-limit evasion — not acceptable.
- Smartbar fetching the API itself: violates its no-credentials principle.

## Verified live (single real account, active)

- fresh+not-due → primer no-op (fetchedAt unchanged), twice.
- **due-inside-TTL (age 173 s < 180, plan-due) → primer fetched in 0.59 s**
  — plain `cswap list` structurally could not. Plans re-persisted.
- Store after all runs: `last429At=None`, `lastError=None`.
- Popover ↔ live cswap same-second: 31 %↔69 %, 72↔28, 60↔40; countdown
  exact; "Updated" = measurement time.

Result: display tracks `/usage` within the account's plan — ≤180 s while
burning, ≤~70 s in the urgent band (≥75 % used), ≤3 min hard worst case.
That is the legitimate freshness ceiling for a non-first-party client.

Upstream idea (not filed): `cswap list --json --fresh` doing exactly the
fetch-set pass, so shells don't need the library trick.
