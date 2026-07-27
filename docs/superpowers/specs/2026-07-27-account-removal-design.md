# Account removal (Claude + OpenAI) — design note

2026-07-27. User request: remove any account from either provider, from
the panel, carefully and without surprises.

## UI (identical on all platforms)

- Hovering a **non-active** card reveals a small dimmed ✕ in the card
  header, between the address and the control (`Make Active` / nothing).
- Clicking ✕ swaps ONLY the header row for
  `Remove <email>?  [Remove] [Keep]` — same card height, the bars stay,
  nothing reflows. `Remove` is the status ramp's critical red.
- Confirming drops the card optimistically, runs the removal in the
  background, then force-refreshes — the truth resurrects the card if the
  removal failed. Failures land in the popover's shared error line.
- The ACTIVE account is never removable, and the rule is enforced at
  every layer (layout renders no ✕; a stale confirm token naming a card
  that turned active is ignored; core refuses): the live login would be
  re-registered by auto-add / the codex sync within a minute, so removing
  it could only ever look like a silent failure.
- Removing the last OpenAI account makes the tab row disappear and the
  view falls back to Claude (pre-existing both-providers rule).

## Semantics (one shared answer, in core)

`smartbar/core/account_removal.py` — the single dispatcher both UIs use:

- `claude:<slot>` → `cswap.remove_account(number)`: checks the slot is
  not active against the store-served list, then runs `cswap remove N`
  with `y\n` piped to stdin (cswap's CLI has no `--yes`; its `[y/N]`
  prompt reads stdin). The slot NUMBER is always sent — `remove <email>`
  can hit cswap's interactive ambiguity prompt when one address fills two
  slots. This **permanently deletes the slot's stored credential
  backup**; signing in as that account re-registers it. cswap's own
  refusals (live `cswap run` session) surface as errors.
- `openai:<email>` → `codex.remove_account(email)`: deletes the entry
  from `~/.cache/ai-smartbar/openai-accounts.json` only. Nothing under
  the codex home is touched; signing in with Codex brings the card back.
  Registry saves are now atomic (`os.replace`) — a concurrent
  `--openai --json` sync can never read a half-written file, and the
  removal cannot be shredded by a crash. (Residual race: a sync that
  loaded the registry before the removal and writes after can resurrect
  the entry until the next poll; a lock was judged not worth it.)

Swift removes through `bin/ai-smartbar --remove-account provider:id`
(JSON `{ok, error}`) so no removal policy exists in Swift — pinned by
TestRemovalParity. The painted trays call core directly in-process.

## Hit names (shared layout)

- `card:<provider>:<id>` — whole-card hover region, never actioned;
  appended before the card's buttons so they win hit-testing. This is
  what lets the painted UIs know "the pointer is on this card".
- `remove:<provider>:<id>` — the ✕ (exists only while its card is
  hovered and not active).
- `confirm-remove:<provider>:<id>` / `cancel-remove` — the two confirm
  buttons. Claude ids are slot numbers; OpenAI ids are emails.

The Swift card keys its confirm state by a full
`provider:number:email` token, not a Bool — card views are recycled by
slot number and OpenAI numbers are re-enumerated positions, so a bare
flag could survive a data refresh and aim the question at a different
address. A token that no longer matches dismisses itself.

## Tests

`test_account_removal.py` (dispatcher, CLI end-to-end on a temp
registry, cross-language parity scrapes), `test_cswap.py::
TestRemoveAccount` (mocked — never touches real slots), `test_codex.py`
removal cases (live-login refusal, atomicity), `test_popover_layout.py::
TestRemoveAffordance` (hover/confirm geometry), `test_windows_tray.py`
(dispatcher recognises + routes the new hits; inline-thread remove
flow). 599 tests, all green under pycairo as well.
