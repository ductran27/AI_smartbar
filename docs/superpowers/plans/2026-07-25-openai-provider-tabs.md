# OpenAI (Codex/ChatGPT) accounts + provider tabs — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenAI tab with account cards (`email · Pro Lite`, 5h/7d/per-model %-used bars) fed entirely from Codex CLI's local files; tab row appears only when both providers have accounts.

**Architecture:** All policy in new `smartbar/core/codex.py` (claims decode, window mapping, registry, payload). OpenAI accounts ride `Snapshot.openai` — a separate list so no Claude semantics can shift. Swift renders `ai-smartbar --openai --json` verbatim (PlanStatus pattern); Linux panel gets the tab row from the shared layout.

**Tech stack:** Python 3.9 stdlib, SwiftUI, unittest. Spec: `docs/superpowers/specs/2026-07-25-openai-provider-tabs-design.md`.

**Security invariants (every task):** never store/print/log token strings; only claims `email` + `chatgpt_plan_type` leave the JWT; registry holds labels + numbers only; no network; never write under `~/.codex`.

---

### Task 1: `core/codex.py` — seams, plan map, login reader

**Files:** Create `smartbar/core/codex.py`, `tests/test_codex.py`.

- [ ] Write failing tests: `TestPlanLabel` (free→Free, plus→Plus, pro→Pro, prolite→Pro Lite, team→Team, enterprise→Enterprise, edu→Edu, business→Business, ""→"", None→"", unknown "plusplus"→"Plusplus"); `TestLogin` builds a synthetic auth.json in tmp `SMARTBAR_CODEX_HOME` with `_jwt({"email": "a@x.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}})` (header/payload base64url, fake sig) → `login() == ("a@x.com", "Pro")`; apikey mode (no tokens key) → None; corrupt file → None; kill switch `SMARTBAR_OPENAI=off` → `enabled()` False.
- [ ] Implement:

```python
DEFAULT_CODEX_HOME = "~/.codex"
_PLANS = {"free": "Free", "plus": "Plus", "pro": "Pro", "prolite": "Pro Lite",
          "team": "Team", "enterprise": "Enterprise", "edu": "Edu",
          "business": "Business"}
_AUTH_CLAIM = "https://api.openai.com/auth"

def enabled():  # SMARTBAR_OPENAI=off hides the tab and skips every read
def codex_home():  # SMARTBAR_CODEX_HOME override, expanduser
def plan_label(plan_type):  # _PLANS.get(lower) or title(); "" for empty
def _claims(id_token):  # split(".")[1], base64.urlsafe_b64decode + pad, json
def login():
    # auth.json -> (email, plan_label) or None. Reads ONLY the two label
    # claims; token strings never leave this function. OSError/ValueError/
    # IndexError -> None.
```

- [ ] `python3 -m unittest tests.test_codex -v` → green. Commit `feat: codex login + plan-label reader`.

### Task 2: rollout tail parser + rate limits

**Files:** Modify `smartbar/core/codex.py`, `tests/test_codex.py`.

- [ ] Failing tests: fixture rollouts under `<home>/sessions/2026/07/25/rollout-*.jsonl`; events `{"timestamp": iso, "payload": {"type": "token_count", "rate_limits": {...}}}`. Cover: primary 300→metric key "5h", secondary 10080→"7d"; scoped `codex_bengalfox` → key `scoped:Bengalfox`, label "Bengalfox"; latest-per-window merge when later event omits secondary; corrupt lines skipped; file bigger than the tail window still parses its tail; expired `resets_at` (past epoch) → pct 0.0 and resets_at "" (window idle, budget back); epoch→ISO ends with "Z" and `reset_countdown_format.remaining_text` accepts it; `cutoff` param excludes older events; mtime cache (`_scan_cache`) invalidates on touch.
- [ ] Implement: `TAIL_BYTES = 262144`, `MAX_FILES = 16`, `RECENT_DAYS = 8`.

```python
def _tail_lines(path):        # seek to end-TAIL_BYTES, drop first partial line
def _iso(epoch):              # datetime.fromtimestamp(epoch, tz.utc) -> "...Z"
def _events(path):            # cached by (mtime, size): [(ts, rate_limits)]
def rate_limits(home=None, cutoff="", now=None):
    # newest-mtime-first over MAX_FILES recent files -> {window_key:
    #   {"label","short","pct","resets_at","measured"}}, latest per window;
    # limit_id "codex" -> 5h/7d; "codex_<x>" -> scoped:<X>; expired window ->
    # pct 0, no countdown. Returns ({}, "") when nothing readable.
```

- [ ] Green. Commit `feat: codex rate-limit tail reader`.

### Task 3: registry + accounts() + payload()

**Files:** Modify `smartbar/core/codex.py`, `tests/test_codex.py`.

- [ ] Failing tests: cold start (empty registry) attributes existing events to the live login; login change freezes the old account's metrics and sets a cutoff so pre-change events never bleed into the new login; signed-out account listed after the live one with `status "signed_out"`, `active False`; plans refresh from the claim (prolite claim beats old pro events); registry file holds NO key named token/access/refresh (scan the raw file text); kill switch → `accounts() == []`; `payload()` shape `{"accounts": [{email, plan, active, status, stateText, updatedAt, metrics: [...]}]}`; registry write skipped when content unchanged (mtime stable).
- [ ] Implement: registry at `os.path.join(os.environ.get("SMARTBAR_CACHE_DIR") or "~/.cache/ai-smartbar", "openai-accounts.json")` (same resolution as update_runner). Structure `{"active": email, "cutoff": iso, "accounts": {email: {plan, metrics, measuredAt, lastSeen}}}`. `accounts(now=None)` → `[model.Account]` provider="openai", live first (number 1, active=True, status "ok"), remembered by lastSeen desc; metrics as `model.Metric(key, label, short, pct, resets_at, countdown="")` ordered 5h, 7d, scoped. `payload()` serialises with `model.state_text` for stateText.
- [ ] Green. Commit `feat: openai account registry + accounts payload`.

### Task 4: model — provider field, Snapshot.openai, signed_out text

**Files:** Modify `smartbar/core/model.py`, `tests/test_model.py`.

- [ ] Failing tests: `Account(...).provider == "claude"`; `Snapshot().openai == []`; `STATE_TEXT["signed_out"]` present; `state_text` on a signed_out account returns it; active_account ignores `snapshot.openai` by construction (claude list only).
- [ ] Implement: `provider: str = "claude"` on Account (after `plan`); `openai: list = field(default_factory=list)` on Snapshot; `STATE_TEXT["signed_out"] = "Signed out — usage from its last session"`.
- [ ] Green. Commit `feat: provider-aware model`.

### Task 5: CLI `--openai --json`

**Files:** Modify `bin/ai-smartbar`, `tests/test_codex.py`.

- [ ] Failing subprocess test (pattern of TestPlansCli): env seams SMARTBAR_CODEX_HOME + SMARTBAR_CACHE_DIR at tmp fixtures → stdout JSON matches payload; `--once` prints an `openai:` section when accounts exist.
- [ ] Implement: argparse `--openai` ("print OpenAI/ChatGPT accounts as JSON — the macOS app's data source"); dispatch before UI launch mirroring `--plans`; `once()` prints `model.menu_row` for `snap.openai` after the Claude rows.
- [ ] Green. Commit `feat: --openai json helper`.

### Task 6: layout — tab row + OpenAI cards

**Files:** Modify `smartbar/core/popover_layout.py`, `smartbar/core/popover_theme.py`, `tests/test_popover_layout.py`.

- [ ] Failing tests: no tab row for Claude-only snapshot (layout identical to before — assert no `tab:` hits and unchanged height for an existing fixture); both providers → hits `tab:claude` + `tab:openai` and cards of the selected provider only; `provider="openai"` renders openai cards, no `switch:` hits, no registration banner; signed_out card shows the STATE_TEXT line; active openai card has the ACTIVE chip.
- [ ] Implement: theme `TAB_H = 20.0`; `build(..., provider="")` resolves `provider or ("claude" if snapshot.accounts else "openai")`; when both lists non-empty, emit two `_button`-style pills left-aligned at the cursor (selected: accent fill; unselected: normal), hits named `tab:<name>`, advance cursor by `TAB_H + SECTION_GAP`. Card loop iterates the selected list; `_card` skips the switch button when `getattr(account, "provider", "claude") != "claude"` (chip logic unchanged). Banner block only when provider == "claude".
- [ ] Green (incl. pycairo draw smoke via existing suite). Commit `feat: provider tabs in the shared layout`.

### Task 7: Python UIs — stamp + tab clicks + menu sections

**Files:** Modify `smartbar/linux/tray.py`, `smartbar/macos/menubar.py`.

- [ ] tray.py: import codex; `self.provider = ""`; `_apply_snapshot` stamps `snap.openai = codex.accounts()` after apply_plans; `_popover_layout` passes `provider=self.provider`; `_on_popover_action` handles `tab:` (set + refresh_layout, no fetch). `_build_menu`: when `snapshot.openai` non-empty, append separator + disabled header rows `— Claude —` before the claude rows and `— OpenAI —` before `model.menu_row(acct)` rows (all insensitive — nothing to switch).
- [ ] menubar.py: same stamp in `_fetch`; `_rebuild_menu` appends the OpenAI header + rows (no callbacks).
- [ ] Verify with the gi-stub trick (`sys.modules["gi"]` MagicMock, unbound `Tray._build_menu(fake)`) or via the parity scrape in Task 8; run full unit suite. Commit `feat: both python UIs carry openai accounts`.

### Task 8: Swift — OpenAIStatus + tabs + card branches + parity pin

**Files:** Create `macos-swift/Sources/AISmartbar/OpenAIStatus.swift`; modify `Models.swift`, `PopoverView.swift`, `AccountCardView.swift`, `AISmartbarApp.swift`; add `TestOpenAIParity` to `tests/test_codex.py`.

- [ ] Failing parity tests first: markers `chatgpt_plan_type`, `id_token`, `window_minutes`, `rollout`, `prolite`, `SMARTBAR_OPENAI` absent from all Swift sources; `refreshInterval: TimeInterval = 120` pinned in OpenAIStatus.swift; `codex.accounts` referenced by both `smartbar/linux/tray.py` and `smartbar/macos/menubar.py`; AccountCardView guards plan/devices lookups by provider (`account.provider == "openai"` appears in it).
- [ ] Models.swift: Account gains `provider: String = "claude"`, `plan: String = ""`, `stateTextOverride: String = ""`; `stateText` returns the override first. OpenAI parse decodes the helper JSON (pure decoding, zero mapping).
- [ ] OpenAIStatus.swift: PlanStatus skeleton — `@Published accounts: [Account]`, 120 s timer + `refresh()`, spawns `repoRoot()/bin/ai-smartbar --openai --json`, keeps last-good on failure. Comments must not name the env var or the on-disk sources (the scrape enforces it).
- [ ] PopoverView: `@EnvironmentObject openai`, `@AppStorage("providerTab") var tab = "claude"`; `showsTabs = (snapshot has accounts) && !openai.accounts.isEmpty`; capsule tab row under the header (selected `.borderedProminent`); OpenAI branch lists `AccountCardView(account:)` for `openai.accounts`; registration banner stays inside the Claude branch; `.onAppear` also `openai.refresh()`.
- [ ] AccountCardView: `plan = account.provider == "openai" ? account.plan : planStatus.plans[email]`; devices 0 for openai; switch button replaced by nothing for a non-active openai account; `.help` with updatedAt tooltip.
- [ ] AISmartbarApp: `@StateObject openai = OpenAIStatus()` + `.environmentObject`.
- [ ] `swift build -c release` in macos-swift + full unit suite green. Commit `feat: OpenAI tab in the Swift popover`.

### Task 9: docs, fences, full verify

**Files:** Modify `README.md`, `tests/e2e-autoadd.sh`.

- [ ] README: feature bullet ("OpenAI/ChatGPT tab…"), env rows `SMARTBAR_OPENAI`, `SMARTBAR_CODEX_HOME`, honest-freshness note; Development test counts refreshed.
- [ ] e2e-autoadd.sh: export `SMARTBAR_OPENAI=off` next to the presence fence (rule: every route past cswap is fenced).
- [ ] Full gate: unit suite, `./tests/e2e-autoadd.sh`, `swift build -c release`; live `ai-smartbar --openai --json` (expect dev@mail.example / Pro Lite / 7d ≈25%); restart app, screenshot popover with both tabs; `--preview-popover` PNG for the Linux panel path.
- [ ] Commit `docs: openai provider docs + e2e fence`. Ask the user about release.
