# System tab — machine vitals + leftover-process cleanup — design note (2026-08-23)

A third tab, **System**, that shows what the agent sessions on this machine
are costing it and lets the user kill what dead sessions left behind. Mini
htop/btop inside the existing panel: per-core CPU, a 60-minute CPU history,
memory, and three lists — **Leftovers** (orphans of dead Claude/Codex sessions,
killable, optionally auto-killed), **Busy** (anything hot, killable with
confirm), **Sessions** (live agents, never killable).

Why: twice in a week (2026-08-18, 2026-08-23) a dead Claude Code session left
CPU burners nobody could see — orphaned headless Chrome from an agent's CDP
script, `esbuild --service` from agent worktrees, puppeteer's Chrome for
Testing — at 400–600 % CPU each for hours, fans on, battery gone. The panel is
already the place the user looks to see what the agents are doing; the cost
side belongs next to the budget side. The framing is deliberately **"AI
footprint"**, not a general task manager: the lists are about sessions and
what they leave, the vitals are the context for reading them.

User-approved 2026-08-23: third tab pill (not a strip), default ON, named
"System", kill allowed on Busy rows (with confirm), auto-kill shipped in the
app and default OFF, 60-minute history from the background poll.

## UI (identical on all platforms)

**Tab row.** The rule becomes "a tab row exists when ≥ 2 tabs exist". With
`SMARTBAR_SYSMON` on (the default) the System tab is always available, so the
row is always shown; with it off, a single-provider machine looks exactly like
today. The pill follows the existing grammar — mark beside label,
faded / not-faded — and carries a count (`System · 2`) while leftovers are
BURNING; the count takes the critical colour, the pill itself never does. New
Glyph `system` (a pulse line) for the mark; `tab:system` hit; macOS remembers
the tab in the same `@AppStorage("providerTab")`.

**Vitals card** (`This Mac`):

- Header: dot in the ramp colour of the CPU total · `This Mac` · caption
  `16 cores · 32 GB · load 5.2 · 8.2 · 23.4` · `LIVE` chip (green, ACTIVE-chip
  styling) only while the 1 s stream is running.
- `CPU` row: the metric row's label line (`CPU` · caption
  `12 claude · 1 codex · 41 children · 1132 procs` · `13%`), and instead of one
  bar, one **column per core**: Boxes 22 pt tall on the bar track, 2 pt gaps,
  each filled to its own % in the used-ramp colour OF ITS OWN VALUE. Up to 32
  columns; more cores are averaged in adjacent pairs so a column never drops
  under ~6 pt.
- `60 min` row: label line (`60 min` · caption `peak 91% · 20:38` · the last
  minute's %), then 60 Boxes 34 pt tall, 1 pt gap, one per minute, oldest
  left, ramp colour per value; a minute with no sample (sleep, app not
  running) draws track only. The newest column carries a hairline outline.
- `MEM` row: the existing metric row unchanged — `MEM` · caption
  `17.6 / 32 GB · 3.1 GB compressed` · `55%` over a full-width bar.

CPU and MEM are literally "% used", the panel's own scale, which is why the
ramp applies to them without inventing a second colour meaning. Load and
counts are text.

**Leftovers card** (`Leftovers` · caption `orphans of dead sessions`):

- Header chip: `2 burning · 10.1 cores` in the danger tint while anything
  burns; `1 idle` neutral when only idle leftovers remain; no chip when empty.
- Rows, 26 pt: kind chip (`junk` danger-tinted / `idle` neutral) · display
  name + sub (`Google Chrome (headless)` `pid 22493 · cdp-prof-9603`;
  `node serve-dist.mjs` `pid 41210 · :5299`) · meta in mono
  (`orphan · 6 h · 575%` / `orphan · 3 d · idle`). Sorted burning-first, then
  CPU; at most 8 rows, then a `+N more` state line.
- A row is a process TREE rooted at the orphan: a headless Chrome's 575 % is
  its GPU helper, not the browser process; CPU and memory are summed over the
  tree and the kill targets the root (helpers exit with it — verified today).
- Hover reveals a dimmed ✕ (account-removal affordance, `REMOVE_HIT` square).
  Click swaps ONLY that row for `Kill Google Chrome (headless), pid 22493?
  [Kill] [Keep]` — same height, danger-red Kill, nothing reflows. Confirming
  drops the row optimistically, runs the kill in the background, and the next
  sample is the truth; failures land in the panel's shared error line.
- Empty state (STATE_ROW_H): `Nothing left behind — every orphan is gone.`
- Card foot, tertiary caption: `Auto-kill off · last sweep 21:24 · junk
  rules: 5` (or `Auto-kill on · killed 2 today · last sweep …`).

**Busy card** (`Busy` · caption `≥ 50% CPU over two samples`): top 5 rows,
same-name processes FOLDED into one (`Firefox ×4`, `claude ×12 sessions`),
kind chip `hot` / `session` / `system`, meta `49% · 1.2 GB`. The ✕ + confirm
exists only on user-owned rows that are not sessions; `session` (claude,
codex and their children) and `system` (other users' processes) rows have no
kill affordance at any layer. Killing a folded row kills every member.

**Everything else unchanged.** The menu-bar icon's pills stay strictly about
budget; the icon TOOLTIP may append `· 2 leftovers burning 9 cores`. Panel
height with three cards ≈ 480 pt, inside the existing 505 pt list budget; the
System view scrolls like a long account list would.

## Semantics — `smartbar/core/sysmon.py` (pure, all policy lives here)

- `enabled()` — `SMARTBAR_SYSMON=off` kill switch: hides the tab and skips
  every sample, stream, kill and notification.
- **Rules are anchored on the executable path**, never on free argv text: the
  spike's free-text rule matched the scanner's own shell (its command line
  contained the pattern) — any `grep`/`pgrep`/editor whose argv mentions a
  pattern would be classified as the thing it mentions. A rule is
  `(kind, label, exe_regex, flag_regex|None)`; `exe` is argv[0] (for `.app`
  bundles, the outermost `X.app` name), the optional flag must appear in the
  rest of argv.
  - JUNK (never legitimate as an orphan): `…/bin/esbuild` + `--service`;
    `~/.cache/puppeteer/chrome/…/Google Chrome for Testing`; Google Chrome /
    Chromium + `--headless` + `--user-data-dir=/tmp/cdp-prof-`;
    `…/ms-playwright/…` browsers + `--headless`; `zsh` + `shell-snapshots/`.
  - LEFTOVER (an orphaned dev server; killable, never automatic — an
    intentionally detached server looks identical): `node`/`python`/`bun`
    whose script basename is one of `vite`, `serve-dist.mjs`, `serve.mjs`,
    `http.server`, `live-server`, `next dev`, `webpack`, `uvicorn`, `flask`.
  - SESSION: basename ∈ {`claude`, `codex`} — and every DESCENDANT of a
    session is a session too (MCP servers, shells, helpers), which is what
    keeps them out of Busy and off the kill path without a fuzzy "mcp" rule.
  - SYSTEM: uid ≠ the current user. Never listed with a kill affordance.
- `classify(proc, orphan, cpu)`: junk if JUNK rule ∧ orphan; watch if JUNK
  rule ∧ ¬orphan (counted, not shown — a live esbuild is fine); idle if
  LEFTOVER rule ∧ orphan; session; system; hot if cpu ≥ `SMARTBAR_SYSMON_HOT`
  (50) in THIS sample AND the previous one (the previous poll's per-process
  cpu is kept in the state file, so the one-shot path has hysteresis too).
- `orphan` := ppid == 1 on macOS/Linux (reparented to launchd/init). Alone it
  means nothing on macOS — every GUI app has ppid 1 — which is exactly why it
  is only ever a signal in COMBINATION with a rule. Windows: parent pid
  absent, or the parent started after the child (pid reuse).
- `burning` := junk ∧ tree cpu ≥ 50 %; `cores` = cpu / 100.
- `fold(rows)` for Busy: key = display name (headless Chrome folds separately
  from the user's real Chrome because `(headless)` is part of the name).
- Kill token = `<pid>:<start-epoch>`. `validate_kill(token, table)` refuses:
  unknown pid, start time mismatch (pid reuse), uid ≠ mine, session, the
  runner's own tree, the bar itself. `kill_plan(root)` = TERM the root, wait
  3 s, KILL any survivor in the tree (the esbuild trio ignored TERM).
- `autokill_decision(row, now)`: `SMARTBAR_SYSMON_AUTOKILL=on` ∧ kind == junk
  ∧ orphan for ≥ 300 s. Only junk, only orphans, only after the grace period;
  idle/hot rows are never automatic. Every automatic kill is logged (`<cache>/
  sysmon.log`) and notified: `Killed orphaned headless Chrome · 5.8 cores ·
  6 h`.
- Alerts (fire-once per token, re-armed when the token disappears — the
  AlertManager shape): `2 leftovers burning 9 cores — open the panel to kill`
  when auto-kill is off; the "Killed …" line when it is on.
  `SMARTBAR_SYSMON_NOTIFY=off` silences both.
- History ring: 60 entries of `(minute, cpu%)`; the runner appends one point
  per background poll, skipping a minute already present; gaps stay gaps.
- Display formatting lives here: names (`Google Chrome (headless)`,
  `esbuild --service`, `node serve-dist.mjs`), subs (`pid N · cdp-prof-9603`,
  `pid N · :5299`), ages (`6 h`, `3 d`), captions, chip texts, the foot line.
  Swift and the painters print these strings verbatim.

## Plumbing — `smartbar/core/sysmon_probe.py`

- macOS: `ps -Axwwo pid=,ppid=,uid=,rss=,time=,lstart=,args=` (lstart = five
  tokens, then argv), two samples, per-process cpu = Δ cumulative time / Δ
  wall; per-core ticks via Mach `host_processor_info(PROCESSOR_CPU_LOAD_INFO)`
  through ctypes (freed with `vm_deallocate`); memory from `vm_stat`
  (active + wired + compressor pages × page size) over `sysctl hw.memsize`;
  load from `os.getloadavg()`. Verified on this Mac: 16 cores, 1132 processes,
  `ps` 45 ms, a full 1 s tick 0.17 s CPU, no dependencies.
- Linux: `/proc/stat` per-core, `/proc/meminfo`, the same `ps` columns.
- Windows (honest downgrade, scoped like the hotkey spec): total CPU via
  `GetSystemTimes` (no per-core columns — the row draws one bar), process
  table via `Get-Process` once per background poll, no live stream.
- The probe skips the runner's own process tree and always returns partial
  results rather than raising (a failed ctypes call → `cores: []` → the row
  draws track only).

## Orchestration — `smartbar/sysmon_runner.py` + CLI

- `ai-smartbar --sysmon --json`: one-shot (0.5 s sample) → display-ready
  payload. This is the ONE place policy side effects run: append the history
  point, update the state file (last per-process cpu, fired alerts), apply
  auto-kill if enabled, and return `alerts` + `autokilled` in the payload for
  the host to notify.
- `ai-smartbar --sysmon --stream`: one JSON line per second while the System
  tab is visible. Display only — no side effects; the background poll keeps
  policy. Exits on stdin EOF, SIGTERM, SIGPIPE, or when `os.getppid()` becomes
  1 — the sampler must never become the next orphan.
- `ai-smartbar --kill <pid:start>`: `{ok, error}`; the same dispatcher the
  painted trays call in-process. Confirm tokens are validated against the
  live table at kill time, never against the snapshot the row was drawn from.
- State: `<cache>/sysmon-state.json` (history ring, last cpu map, fired
  alerts, kill log tail), atomic `os.replace`, rides `SMARTBAR_CACHE_DIR`.
- Payload (all strings final): `{sampledAt, live, machine: {cores, memText,
  loadText}, cpu: {pct, cores: [..], caption}, history: {pct: [60|null],
  peakText, lastPct}, mem: {pct, caption}, leftovers: {chip, rows: [{token,
  kind, name, sub, meta, burning, cores}], more, foot}, busy: {caption, rows:
  [{token, kind, name, sub, meta, killable}]}, alerts: [{title, body}],
  autokilled: [..]}`.

## Flow per host

- **macOS (one shared answer, not a Swift port):** `SystemStatus.swift`
  spawns `--sysmon --json` every 60 s and on popover open (last-good kept on a
  hiccup, PlanStatus/OpenAIStatus pattern), posts `alerts` through the
  updater's existing notifier, and while the System tab is visible runs
  `--sysmon --stream` (start on appear/tab select, terminate on disappear/tab
  change), decoding one line at a time. `SystemView.swift` renders the three
  cards; kills go through the launcher (`--kill`). Swift maps nothing: no
  rule, no threshold, no env-var name, no wording. Targeted improvement while
  here: the launcher-spawn boilerplate now exists four times
  (OpenAIStatus, PlanStatus, PresenceStatus, AccountRemoval) — factor one
  `Launcher.swift` (`run(args) -> [String: Any]?`) and use it for the new
  calls; existing callers may migrate in the same PR or later.
- **Python UIs:** `TrayController` runs `sysmon_runner.background_tick()`
  every 60 s in-process and holds the payload beside the snapshot (a SEPARATE
  object, never merged into `Snapshot` — no Claude semantics may shift);
  `popover_layout.build(…, system=payload)` renders the System view for
  `provider="system"`; Linux `popover_window` / Windows popover run the
  sampler in a thread while the panel shows System; hits `row:<token>`
  (hover region, appended before the row's buttons so they win),
  `kill:<token>`, `confirm-kill:<token>`, `cancel-kill`, `tab:system`. The
  flat tray menus (dbusmenu / rumps / pystray carry labels only) get one
  status row `⌁ CPU 13% · 2 leftovers burning` and, while anything burns, one
  action row `Kill 2 leftovers (10 cores)` — junk rows only, no confirm
  possible in a menu; accepted asymmetry, stated in the README.

## Config / seams

| Variable | Default | Meaning |
|---|---|---|
| `SMARTBAR_SYSMON` | on | `off` hides the tab and skips every sample, stream, kill and notification |
| `SMARTBAR_SYSMON_INTERVAL` | 60 | background sample period in seconds, floor 15 |
| `SMARTBAR_SYSMON_HOT` | 50 | % CPU over two consecutive samples that puts a process in Busy |
| `SMARTBAR_SYSMON_AUTOKILL` | off | `on` kills junk orphans older than 5 min automatically, logs and notifies |
| `SMARTBAR_SYSMON_NOTIFY` | on | `off` silences the leftover / killed notifications |

All five are `^SMARTBAR_[A-Z0-9_]+$` and therefore config.env-settable by
construction. Test seams: `SMARTBAR_SYSMON_PS` (a file whose contents stand in
for the `ps` output), `SMARTBAR_SYSMON_STATS` (fake core ticks / memory /
load), `SMARTBAR_SYSMON_KILL=off` (dry-run: validate and report, signal
nothing), `SMARTBAR_CACHE_DIR` for the state file. **e2e-autoadd fence gains
`SMARTBAR_SYSMON=off`** — the rule is "any feature that can act outside cswap
gets fenced", and killing processes qualifies.

## Errors & edge cases

- `ps` fails → previous payload kept, error line `System: could not read
  processes`; stream line malformed → skipped.
- Kill refused (EPERM, pid reuse, session) → the row comes back on the next
  sample, reason in the shared error line.
- Host dies mid-stream → sampler self-exits (ppid 1 / SIGPIPE); a stream is
  also capped at 30 min of lifetime as a backstop.
- Sleep / app not running → history gaps drawn as empty minutes, never
  smeared; the `peak` caption ignores gaps.
- A process that is junk-by-rule but whose parent is alive is `watch`: not
  listed, but counted in the tooltip-less foot line `junk rules: 5 · 1 watched`
  so a future orphan is not a surprise.
- More than 32 cores → pairs averaged; fewer than 2 → single column.
- Windows: no per-core, no stream, orphan by absent/younger parent; the
  README's platform table says so.

## Testing

- `tests/test_sysmon.py`: rule table on FIXTURES TAKEN FROM TODAY'S REAL `ps`
  LINES (the two headless Chromes, their helpers, esbuild with a live parent,
  the zsh snapshots, 12 claude sessions and their MCP children); the anchoring
  test (a shell whose argv contains every pattern is classified as nothing);
  orphan × rule matrix; hot hysteresis; fold; tree sums; display strings;
  kill-token validation incl. pid-reuse; kill plan; auto-kill decision (kind
  gate, orphan gate, age gate, disabled); alert fire-once/re-arm; history
  ring with gaps.
- `tests/test_sysmon_probe.py`: `ps` time / etime / lstart parsers (macOS and
  Linux fixtures), `vm_stat` parser, delta maths, own-tree skip; the ctypes
  call is a smoke test skipped off macOS.
- `tests/test_sysmon_runner.py`: `--sysmon --json` payload shape through the
  seams; stream emits lines and exits on stdin EOF and when its parent dies
  (spawned under a short-lived parent); `--kill` dry-run; state file
  atomicity.
- `tests/test_popover_layout.py`: `tab:system` present iff enabled, row rule
  ≥ 2 tabs, Claude-only + sysmon-off layout byte-identical to today; System
  view hits; no `kill:` on session/system rows; confirm swaps one row in
  place; card heights; `--preview-popover --demo` includes a System frame.
- Parity (source-scrape, runs without a Swift toolchain): no rule regex, env
  name, threshold or wording in any Swift file; 60 s poll and 1 s stream
  pinned; `Launcher.swift` is the only place that spawns the launcher; every
  Python UI routes through `TrayController`.
- Live verify on this Mac: `--sysmon --json` lists real state; leave a
  deliberate orphan (`node -e "setInterval(()=>{},1e3)"` started detached
  with a junk-shaped profile dir) → it appears, ✕ → Kill removes it; enable
  auto-kill → it is killed within 6 min with a notification; stream stops
  when the popover closes (`pgrep -f 'sysmon --stream'` empty).

## Non-goals (v1)

No GPU / network / disk graphs, no per-process history, no tree view, no
renice or suspend, no rule editor (a user rules file is a v2 candidate), no
per-core on Windows, no icon-pill changes, no killing sessions, no reading or
writing anything under another user's home.

## Rollout

1. Core + probe + CLI (`--sysmon --json` usable from a terminal, tests).
2. Layout + painted panels (`--preview-popover --demo`), tray menu rows.
3. Swift tab (`Launcher.swift`, `SystemStatus`, `SystemView`, parity pins).
4. Notifications + auto-kill + e2e fence.
5. README (features, config table, platform notes) and, once auto-kill is
   live-verified on this Mac, retire the LaunchAgent
   `com.ductran.orphan-reaper` (`launchctl bootout gui/$UID/…`, rm plist +
   `~/.local/bin/orphan-reaper.sh`) — the app now owns the same allowlist,
   visibly.
