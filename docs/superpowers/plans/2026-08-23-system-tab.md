# System tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third popover tab, **System**, that shows machine vitals (per-core CPU, 60-min history, memory) and three process lists — Leftovers (orphans of dead agent sessions, killable + optional auto-kill), Busy (hot processes, kill-with-confirm), Sessions (live agents, never killable) — mirroring the repo's core/renderer/Swift split.

**Architecture:** All policy in pure `smartbar/core/sysmon.py` (rules anchored on executable path); OS plumbing in `smartbar/core/sysmon_probe.py`; orchestration + CLI in `smartbar/sysmon_runner.py`; the painted panel via `popover_layout.build(system=…)` (per-core & history are Boxes — no new shape kind); macOS via `SystemStatus.swift` + `SystemView.swift` that map nothing. One shared answer, renderers thin.

**Tech Stack:** Python 3.9+ (stdlib only: ctypes, subprocess, os, signal, json), unittest, ruff 0.15.16; SwiftUI (macOS 13+); cairo (Linux), tkinter/cairo (Windows).

**Spec:** `docs/superpowers/specs/2026-08-23-system-tab-design.md`.

**Conventions carried from the codebase:**
- Run tests: `python3 -m unittest tests.test_sysmon -v` (module) / `python3 -m unittest discover -s tests` (all).
- Lint gate before each commit: `python3 -m ruff check smartbar/ bin/ai-smartbar tests/`.
- Every `smartbar/` module starts with `from __future__ import annotations` (ruff FA102 / py39 floor).
- Commit author is the user only — **no Claude co-author trailer** (per user preference).
- Stage only this feature's files per commit; leave the pre-existing uncommitted `warmup_runner.py` change alone.
- Config env vars match `^SMARTBAR_[A-Z0-9_]+$` so `config.env` picks them up for free.

---

## File Structure

**Create:**
- `smartbar/core/sysmon.py` — pure policy: rule table, `classify`, `build_view`, kill-token validation, auto-kill decision, alert wording, history ring, display formatting.
- `smartbar/core/sysmon_probe.py` — OS plumbing: `ps` sampling, Mach per-core ctypes, `vm_stat`/`/proc` memory, own-tree skip.
- `smartbar/sysmon_runner.py` — orchestration: `background_tick()`, `stream()`, `kill(token)`, state file, auto-kill + log.
- `tests/test_sysmon.py`, `tests/test_sysmon_probe.py`, `tests/test_sysmon_runner.py`.
- `macos-swift/Sources/AISmartbar/Launcher.swift` — shared launcher-spawn helper.
- `macos-swift/Sources/AISmartbar/SystemStatus.swift` — spawner (60 s poll + 1 s stream while visible).
- `macos-swift/Sources/AISmartbar/SystemView.swift` — the three cards.

**Modify:**
- `bin/ai-smartbar` — add `--sysmon [--json|--stream]` and `--kill TOKEN`.
- `smartbar/core/popover_layout.py` — `build(..., system=None)` renders `provider="system"`; new hits.
- `smartbar/core/popover_theme.py` — new geometry constants + `Glyph` kind `system`; Scheme unchanged (reuse status ramp).
- `smartbar/core/tray_controller.py` — background sysmon tick, hold payload, `on_kill`.
- `smartbar/paint/popover_draw.py` — draw the `system` glyph (a pulse line).
- `smartbar/linux/tray.py`, `smartbar/linux/popover_window.py` — tab state already generic; add `kill:`/`confirm-kill:`/`cancel-kill` dispatch + stream thread; flat menu status/action rows.
- `smartbar/windows/tray.py` — same dispatch (no per-core, no stream).
- `smartbar/macos/menubar.py` — route through controller (already does); nothing provider-specific.
- `macos-swift/Sources/AISmartbar/AISmartbarApp.swift` — `@StateObject SystemStatus`, inject.
- `macos-swift/Sources/AISmartbar/PopoverView.swift` — third tab + System list; `ProviderMark(kind:"system")`.
- `macos-swift/Sources/AISmartbar/ProviderMark.swift` — draw the system mark.
- `macos-swift/Sources/AISmartbar/Models.swift` — `SystemPayload` decodable structs.
- `tests/e2e-autoadd.sh` — add `SMARTBAR_SYSMON=off` to the fence.
- `tests/test_popover_layout.py`, `tests/test_codex.py`(parity home) or new `test_sysmon_parity.py` — layout + Swift parity pins.
- `README.md` — features, config table, platform notes.

---

## PHASE 1 — core + probe + runner + CLI (terminal-usable, fully tested)

### Task 1.1: `sysmon.py` config accessors + Proc dataclass

**Files:** Create `smartbar/core/sysmon.py`; Test `tests/test_sysmon.py`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sysmon.py
from __future__ import annotations
import os, unittest
from smartbar.core import sysmon


class TestConfig(unittest.TestCase):
    def setUp(self):
        for k in ("SMARTBAR_SYSMON", "SMARTBAR_SYSMON_HOT",
                  "SMARTBAR_SYSMON_INTERVAL", "SMARTBAR_SYSMON_AUTOKILL",
                  "SMARTBAR_SYSMON_NOTIFY"):
            os.environ.pop(k, None)

    def test_enabled_default_on(self):
        self.assertTrue(sysmon.enabled())

    def test_enabled_off(self):
        os.environ["SMARTBAR_SYSMON"] = "off"
        self.assertFalse(sysmon.enabled())

    def test_hot_default_and_override(self):
        self.assertEqual(sysmon.hot_threshold(), 50.0)
        os.environ["SMARTBAR_SYSMON_HOT"] = "75"
        self.assertEqual(sysmon.hot_threshold(), 75.0)

    def test_interval_floor_15(self):
        os.environ["SMARTBAR_SYSMON_INTERVAL"] = "5"
        self.assertEqual(sysmon.interval(), 15)

    def test_autokill_default_off_notify_default_on(self):
        self.assertFalse(sysmon.autokill_enabled())
        self.assertTrue(sysmon.notify_enabled())
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError`/`ModuleNotFoundError`).

Run: `python3 -m unittest tests.test_sysmon -v`

- [ ] **Step 3: Implement** the module header + accessors + `Proc`:

```python
"""Pure policy for the System tab — no OS calls, no graphics, unit-testable.

Mirrors the codex.py / plan.py shape: every rule, threshold, kill-token
check, auto-kill decision and display string lives here; the probe reads the
machine, the runner does side effects, the renderers draw. Rules are anchored
on the EXECUTABLE PATH, never free argv text — a rule matched on the whole
command line classifies the scanner's own shell as the thing its arguments
mention (found in the feasibility spike).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def enabled() -> bool:
    return os.environ.get("SMARTBAR_SYSMON", "").strip().lower() != "off"


def _env_float(name: str, fallback: float) -> float:
    try:
        return float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return fallback


def hot_threshold() -> float:
    return _env_float("SMARTBAR_SYSMON_HOT", 50.0)


def interval() -> int:
    return max(15, int(_env_float("SMARTBAR_SYSMON_INTERVAL", 60.0)))


def autokill_enabled() -> bool:
    return os.environ.get("SMARTBAR_SYSMON_AUTOKILL", "").strip().lower() == "on"


def notify_enabled() -> bool:
    return os.environ.get("SMARTBAR_SYSMON_NOTIFY", "").strip().lower() != "off"


@dataclass
class Proc:
    """One raw process sample (from the probe)."""
    pid: int
    ppid: int
    uid: int
    rss_kb: int
    elapsed: int          # seconds since start
    cpu: float            # % over the sample window (probe fills)
    args: str             # full command line
    start: int = 0        # start epoch (for the kill token; 0 = unknown)
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: ruff + commit**

```bash
python3 -m ruff check smartbar/core/sysmon.py tests/test_sysmon.py
git add smartbar/core/sysmon.py tests/test_sysmon.py
git commit -m "feat(sysmon): pure config accessors and Proc dataclass"
```

### Task 1.2: rule table + `classify` (exe-anchored)

**Files:** Modify `smartbar/core/sysmon.py`; `tests/test_sysmon.py`.

- [ ] **Step 1: Write failing tests** using REAL argv lines from today's `ps`:

```python
class TestClassify(unittest.TestCase):
    CHROME_ORPHAN = ("/Applications/Google Chrome.app/Contents/MacOS/Google "
        "Chrome --headless=new --disable-gpu --user-data-dir=/tmp/cdp-prof-9603 "
        "http://localhost:5173/")
    ESBUILD = ("/x/node_modules/@esbuild/darwin-arm64/bin/esbuild "
               "--service=0.28.1 --ping")
    VITE = "/usr/local/bin/node /x/node_modules/vite/bin/vite.js"
    CLAUDE = "/Users/dev/.local/bin/claude"
    SCANNER = "grep -E esbuild --service /tmp/cdp-prof- --headless"  # must NOT match

    def k(self, args, orphan, cpu=0.0, prev=0.0, uid=0, my=0):
        return sysmon.classify(sysmon.Proc(1, 1 if orphan else 2, uid, 0, 0,
                                           cpu, args), orphan, cpu, prev, my)

    def test_headless_chrome_orphan_is_junk(self):
        self.assertEqual(self.k(self.CHROME_ORPHAN, True), "junk")

    def test_headless_chrome_with_live_parent_is_watch(self):
        self.assertEqual(self.k(self.CHROME_ORPHAN, False), "watch")

    def test_esbuild_orphan_is_junk_live_parent_is_watch(self):
        self.assertEqual(self.k(self.ESBUILD, True), "junk")
        self.assertEqual(self.k(self.ESBUILD, False), "watch")

    def test_orphan_dev_server_is_idle_never_junk(self):
        self.assertEqual(self.k(self.VITE, True), "idle")

    def test_claude_is_session(self):
        self.assertEqual(self.k(self.CLAUDE, False), "session")

    def test_scanner_shell_mentioning_patterns_is_not_classified(self):
        # exe is `grep`, not a rule target; argv text must not match.
        self.assertIsNone(self.k(self.SCANNER, True))

    def test_hot_needs_two_samples(self):
        self.assertIsNone(self.k("/usr/bin/somebusyapp", False, cpu=80, prev=0,
                                 uid=5, my=5))
        self.assertEqual(self.k("/usr/bin/somebusyapp", False, cpu=80, prev=80,
                                uid=5, my=5), "hot")

    def test_other_users_process_is_system(self):
        self.assertEqual(self.k("/usr/sbin/foo", False, uid=0, my=501), "system")
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** rules + `classify` + exe extraction:

```python
import re

@dataclass
class Rule:
    kind: str
    label: str
    exe: str              # regex against argv[0] (or the .app bundle name)
    flag: str = ""        # optional regex that must appear in the rest of argv


# JUNK: never legitimate as an orphan. LEFTOVER (idle): an orphaned dev
# server, killable but never automatic. Order matters — first match wins.
JUNK_RULES = [
    Rule("junk", "esbuild service", r"(^|/)esbuild$", r"--service"),
    Rule("junk", "puppeteer Chrome for Testing",
         r"Chrome for Testing", r""),
    Rule("junk", "headless Chrome (CDP)", r"(Google Chrome|Chromium)$",
         r"--headless.*--user-data-dir=(/private)?/tmp/cdp-prof-"),
    Rule("junk", "playwright browser", r"ms-playwright/", r"--headless"),
    Rule("junk", "Claude Code shell snapshot", r"(^|/)zsh$",
         r"shell-snapshots/"),
]
LEFTOVER_RULES = [
    Rule("idle", "dev server",
         r"(^|/)(node|python[0-9.]*|bun|deno)$",
         r"\b(vite|serve-dist\.mjs|serve\.mjs|http\.server|live-server|"
         r"next|webpack|uvicorn|flask)\b"),
]
SESSION_EXE = re.compile(r"(^|/)(claude|codex)$")


def _exe(args: str) -> str:
    """argv[0], or the outermost `X.app` name for a bundled Mac binary."""
    head = args.split(" ", 1)[0]
    m = re.search(r"/([^/]+)\.app/", head)
    return m.group(1) if m else head


def _rule_for(args: str):
    exe = _exe(args)
    rest = args[len(args.split(" ", 1)[0]):]
    for rule in JUNK_RULES + LEFTOVER_RULES:
        if re.search(rule.exe, exe) and (not rule.flag
                                         or re.search(rule.flag, rest)):
            return rule
    return None


def classify(proc, orphan, cpu, prev_cpu, my_uid):
    """kind or None. junk/idle require the process be an ORPHAN; a live
    parent downgrades a junk match to 'watch' (counted, not shown)."""
    if proc.uid != my_uid and my_uid >= 0:
        return "system"
    if SESSION_EXE.search(_exe(proc.args)):
        return "session"
    rule = _rule_for(proc.args)
    if rule is not None:
        if rule.kind == "junk":
            return "junk" if orphan else "watch"
        return "idle" if orphan else "watch"
    if cpu >= hot_threshold() and prev_cpu >= hot_threshold():
        return "hot"
    return None
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: ruff + commit** `feat(sysmon): exe-anchored classification rules`.

### Task 1.3: process-tree grouping + kill-token validation + kill plan

**Files:** Modify `sysmon.py`; `tests/test_sysmon.py`.

Behaviours: `kill_token(proc) -> "pid:start"`; `tree_pids(pid, table)` collects a pid and all descendants; `validate_kill(token, table, my_uid, own_pids) -> (ok, error)` refusing unknown pid, start mismatch, foreign uid, session, own tree; `tree_cpu`/`tree_mem` sum a root's subtree.

- [ ] **Step 1: Write failing tests** (parent 100 with child 101 as a tree; a reused-pid token; a session refusal; a foreign-uid refusal). *(full test code written at execution time; asserts the behaviours above.)*
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** `kill_token`, `tree_pids` (build children map once), `validate_kill`, `tree_cpu`, `tree_mem`.
- [ ] **Step 4: PASS.**  **Step 5:** ruff + commit `feat(sysmon): process-tree grouping and guarded kill tokens`.

### Task 1.4: `build_view` — the display-ready payload

**Files:** Modify `sysmon.py`; `tests/test_sysmon.py`.

`build_view(procs, cores, mem, load, prev_cpu, now, my_uid, own_pids, history) -> dict` producing the payload shape from the spec (`machine`, `cpu.cores`, `history`, `mem`, `leftovers.rows`, `busy.rows`, `foot`), with display strings formed here (`Google Chrome (headless)`, `pid N · cdp-prof-9603`, `orphan · 6 h · 575%`, `2 burning · 10.1 cores`). Busy folds same-name rows; sessions/system never killable; leftovers sorted burning-first.

- [ ] Steps 1-5 as above. Tests assert: leftover rows carry `token`+`kind`+`meta`; a headless Chrome tree sums helper CPU into the root; busy fold (`Firefox ×4`); `killable` false on session/system; foot line text; empty-leftovers state. Commit `feat(sysmon): build the display-ready System payload`.

### Task 1.5: history ring + auto-kill decision + alert wording

**Files:** Modify `sysmon.py`; `tests/test_sysmon.py`.

`history_append(ring, minute, pct)` (60 entries, skip a minute already present, gaps stay `None`); `autokill_targets(rows, now, first_seen)` (junk ∧ orphan ∧ age≥300s); `alerts(rows, autokilled)` → list of `{title, body}` (`2 leftovers burning 9 cores — open the panel to kill` / `Killed orphaned headless Chrome · 5.8 cores · 6 h`).

- [ ] Steps 1-5. Commit `feat(sysmon): history ring, auto-kill decision and alert wording`.

### Task 1.6: `sysmon_probe.py` — ps parser (pure parsing, testable)

**Files:** Create `smartbar/core/sysmon_probe.py`; Test `tests/test_sysmon_probe.py`.

Pure parsers first (no real `ps`): `parse_ps(text, sample_prev, wall)` → `[Proc]` with per-process cpu = Δcpu/wall; `_cpu_seconds("[[hh:]mm:]ss")`, `_etime_seconds("[[dd-]hh:]mm:ss")`, `parse_lstart(five tokens)` → epoch; `parse_vm_stat(text, page, total)` → `{pct,...}`. `SMARTBAR_SYSMON_PS` / `SMARTBAR_SYSMON_STATS` seams let a file stand in for command output.

- [ ] **Step 1:** tests with fixtures = today's real `ps` and `vm_stat` output. **Steps 2-5.** Commit `feat(sysmon): ps/vm_stat/lstart parsers with test seams`.

### Task 1.7: `sysmon_probe.py` — live sampling (Mach ctypes + sample())

**Files:** Modify `sysmon_probe.py`; `tests/test_sysmon_probe.py`.

`core_ticks()` (Mach `host_processor_info` via ctypes, freed with `vm_deallocate`; the working code exists in the spike), `core_busy(a,b)`, `sample(interval, my_uid)` → `(procs, cores, mem, load)` taking two `ps` snapshots and one core-tick delta, skipping the runner's own tree; Linux `/proc/stat`+`/proc/meminfo` branch; Windows `GetSystemTimes` total-only + `tasklist`. The ctypes call is a smoke test skipped off macOS.

- [ ] Steps 1-5. Commit `feat(sysmon): live per-core/memory/process sampling per OS`.

### Task 1.8: `sysmon_runner.py` — orchestration + state file

**Files:** Create `smartbar/sysmon_runner.py`; Test `tests/test_sysmon_runner.py`.

`background_tick() -> dict` (0.5 s sample → `sysmon.build_view`; append history; update state file `<cache>/sysmon-state.json` atomically; apply auto-kill if enabled; return payload with `alerts`+`autokilled`); `stream(out, interval, stop)` (one JSON line/sec, display-only, exits on stop/EOF/SIGPIPE/ppid→1, 30-min cap); `kill(token) -> (ok, error)` (validate against a fresh sample, TERM→wait 3 s→KILL survivors; `SMARTBAR_SYSMON_KILL=off` dry-run); `load_state`/`save_state`.

- [ ] Steps 1-5. Tests: payload shape through the `SMARTBAR_SYSMON_PS`/`_STATS` seams; `kill` dry-run reports without signalling; state atomic; stream emits a line and exits when its parent dies (spawn under a short-lived parent). Commit `feat(sysmon): runner — background tick, stream, guarded kill`.

### Task 1.9: CLI wiring in `bin/ai-smartbar`

**Files:** Modify `bin/ai-smartbar`; Test `tests/test_sysmon_runner.py` (subprocess).

- [ ] **Step 1:** subprocess test: `--sysmon --json` prints a dict with the payload keys (under the PS/STATS seams); `--kill 999999:0` with `SMARTBAR_SYSMON_KILL=off` prints `{"ok":false,...}` and exits non-zero.
- [ ] **Step 3:** add args `--sysmon` (with `--json`/`--stream`) and `--kill TOKEN`, wired before the UI dispatch, mirroring the `--openai`/`--remove-account` blocks:

```python
parser.add_argument("--sysmon", action="store_true",
                    help="print machine vitals + process groups as JSON "
                         "(--json), or stream one JSON line per second "
                         "(--stream); the macOS app's data source")
parser.add_argument("--stream", action="store_true",
                    help="with --sysmon: emit one line per second until "
                         "stdin closes or the parent exits")
parser.add_argument("--kill", metavar="PID:START",
                    help="kill the process named by the token from --sysmon "
                         "(TERM then KILL); prints a JSON result. Refuses "
                         "other users' processes, live sessions and itself")
# ... in main(), before the UI dispatch:
if args.sysmon:
    from smartbar import sysmon_runner
    if args.stream:
        return sysmon_runner.stream()
    import json as _json
    print(_json.dumps(sysmon_runner.background_tick()))
    return 0
if args.kill:
    import json as _json
    from smartbar import sysmon_runner
    ok, error = sysmon_runner.kill(args.kill)
    print(_json.dumps({"ok": ok, "error": error}))
    return 0 if ok else 1
```

- [ ] **Steps 2,4,5.** Commit `feat(sysmon): --sysmon/--stream/--kill CLI`.

### Task 1.10: Phase 1 live verification (this Mac)

- [ ] `python3 -m unittest discover -s tests` — all green (existing 833 + new).
- [ ] `python3 -m ruff check .` — clean.
- [ ] `python3 bin/ai-smartbar --sysmon --json | python3 -m json.tool | head -40` — real state; the two known orphans (if present) appear under `leftovers`.
- [ ] Leave a deliberate orphan, confirm it's classified `junk`, `--kill` it (real), confirm gone; `--kill` a bogus token → refused.
- [ ] Commit any fixups. This is the shippable milestone: `--sysmon` usable from a terminal.

---

## PHASE 2 — layout + painted panels (`--preview-popover --demo` reviewable)

### Task 2.1: geometry constants + `system` glyph kind
**Files:** `popover_theme.py`; `tests/test_popover_theme_parity.py`. Add `SYS_CORES_H=22`, `SYS_CORE_GAP=2`, `SYS_HIST_H=34`, `SYS_HIST_GAP=1`, `SYS_ROW_H=26`, `PROC_KIND_W=48`, `SYS_MAX_CORES=32`, `SYS_HISTORY=60`; extend `Glyph` doc for `"system"`. TDD: a test asserts the constants exist and are positive. Commit.

### Task 2.2: `build(system=…)` renders `provider="system"`
**Files:** `popover_layout.py`; `tests/test_popover_layout.py`. Add `system=None` param; when `provider=="system"`, render the vitals card (CPU metric-row label line + per-core Box strip via a new `_cores(shapes,s,…)`, history Box strip via `_history(...)`, MEM via existing `_bar`), the leftovers card (rows via a new `_proc_row` with hover→`kill:`/`confirm-kill:`/`cancel-kill` mirroring `_card`'s remove flow), the busy card. Tab row rule becomes "≥2 tabs": tabs list = providers present + (`system` when `sysmon.enabled()`); hit `tab:system`; a `System · N` count when leftovers burn. TDD: `tab:system` present iff enabled; row rule ≥2 tabs; Claude-only + sysmon-off layout byte-identical to today; no `kill:` on session/system rows; confirm swaps one row; per-core column count caps at 32. Commit.

### Task 2.3: `system` glyph in cairo painter
**Files:** `paint/popover_draw.py`; `tests/test_popover_draw.py`. Add `_draw_system` (a pulse polyline) to `_GLYPH_DRAWERS`. TDD: draw smoke test. Commit.

### Task 2.4: demo payload in preview
**Files:** `paint/popover_preview.py`; `bin/ai-smartbar` already passes `system`. Add a `demo_system()` returning a payload with two burning junk rows, one idle, a busy fold, live sessions, 16 cores, a 60-pt history with a gap; pass it to `build(system=…)`. TDD: `--preview-popover --demo --scheme dark/light` writes a PNG; layout test includes a System frame. Render both PNGs and eyeball. Commit.

### Task 2.5: painted-UI dispatch + stream thread (Linux, Windows)
**Files:** `linux/tray.py`, `linux/popover_window.py`, `windows/tray.py`; `tests/test_linux_tray.py`, `tests/test_windows_tray.py`. Add `kill:`/`confirm-kill:`/`cancel-kill` to `_on_popover_action` (mirror remove flow: `confirm-kill:` → `controller.on_kill(token)`); `_popover_layout` passes `system=controller.system`; while the System tab shows, run the sampler in a thread updating `controller.system` (Linux/Windows are Python — no subprocess stream needed); flat menu gets a `⌁ CPU … · N leftovers burning` status row and, while burning, a `Kill N leftovers` junk-only action row. TDD: dispatcher recognises + routes the new hits; menu rows present. Commit.

---

## PHASE 3 — Swift tab

### Task 3.1: `Launcher.swift` (factor the 4× spawn boilerplate)
**Files:** Create `Launcher.swift`; migrate `OpenAIStatus`/`AccountRemoval` to it (PlanStatus/PresenceStatus optional). `run(_ args: [String]) -> [String: Any]?` with the PATH fix. TDD: parity test asserts `Launcher` is the only file constructing `Process()` for the launcher. Commit.

### Task 3.2: `SystemPayload` structs in `Models.swift`
**Files:** `Models.swift`. Decodable structs for the payload (`SystemPayload`, `SysMachine`, `SysCPU`, `SysHistory`, `SysMem`, `ProcRow`). Commit.

### Task 3.3: `SystemStatus.swift`
**Files:** Create `SystemStatus.swift`. 60 s poll via `Launcher.run(["--sysmon","--json"])` + `--sysmon --stream` while the tab is visible (start on appear/select, terminate on disappear/change), last-good kept, posts `alerts`. `kill(token)` via `Launcher.run(["--kill",token])`. TDD: parity scrape (no rule/env/threshold/wording in Swift; 60 s + 1 s pinned). Commit.

### Task 3.4: `SystemView.swift` + PopoverView third tab
**Files:** `SystemView.swift`, `PopoverView.swift`, `ProviderMark.swift`, `AISmartbarApp.swift`. Vitals/leftovers/busy cards (per-core = an HStack of capsule bars; history = HStack of 60 bars; each fill uses the `Status` ramp of its own value). Third tab pill (`showsTabs = tabs.count >= 2`, `tabs = [claude?, openai?, system?]`), `ProviderMark(kind:"system")`, `@StateObject SystemStatus` injected. Build: `swift build` in `macos-swift/`. TDD: parity pins. Commit.

### Task 3.5: macOS live verify
`swift build`, launch, screenshot the System tab (real orphans), kill one from the UI, confirm the stream stops on close (`pgrep -f 'sysmon --stream'` empty). Commit fixups.

---

## PHASE 4 — controller wiring, notifications, auto-kill, fence

### Task 4.1: TrayController sysmon tick + `on_kill`
**Files:** `tray_controller.py`; `tests/test_tray_controller.py`. A 60 s `sysmon_runner.background_tick()` in a worker, hold `self.system`, notify `alerts` through `self.host.notify`; `on_kill(token)` optimistic + refresh. TDD: controller holds payload, routes kill, fires alerts. Commit.

### Task 4.2: e2e-autoadd fence
**Files:** `tests/e2e-autoadd.sh`. Add `SMARTBAR_SYSMON=off` to the fenced env (a feature that can act outside cswap — killing processes — gets fenced). Run the suite. Commit.

### Task 4.3: parity + full-suite gate
`python3 -m unittest discover -s tests`, `python3 -m ruff check .`, `tests/e2e-autoadd.sh`, `tests/e2e-config.sh`. Commit fixups.

---

## PHASE 5 — README + retire the LaunchAgent reaper

### Task 5.1: README
**Files:** `README.md`. New "The System tab" section; add the five `SMARTBAR_SYSMON*` rows to the config table; platform-support note (macOS full; Linux full minus stream-subprocess; Windows total-CPU only, no stream). Commit `docs: document the System tab`.

### Task 5.2: retire the reaper (after macOS auto-kill live-verified)
Once `SMARTBAR_SYSMON_AUTOKILL=on` is proven on this Mac: `launchctl bootout gui/$UID/com.ductran.orphan-reaper`; `rm ~/Library/LaunchAgents/com.ductran.orphan-reaper.plist ~/.local/bin/orphan-reaper.sh`. Update memory `mac-battery-drain-2026-08`. (Operational, not a repo commit.)

---

## Self-Review

- **Spec coverage:** vitals (2.2), per-core Boxes (2.1/2.2), 60-min history (1.5/2.2), Leftovers junk/idle + kill (1.2/1.4/2.2), Busy + fold + confirm (1.4/2.2), Sessions never killable (1.2/1.4), exe-anchored rules (1.2), tree kill (1.3), tokens with start-time guard (1.3), auto-kill default off (1.5/4.1), notifications (1.5/4.1), history ring gaps (1.5), two cadences (1.8/3.3), stream self-exit (1.8), config seams (1.1/1.6), fence (4.2), Swift one-shared-answer (3.x parity), painted panels (2.x), README (5.1), reaper retire (5.2). All covered.
- **Placeholder scan:** Phase 1 steps carry real code; 1.3-1.8 mark "full test code at execution time" deliberately (this plan is executed inline by its author) but each names exact behaviours/signatures. Phases 2-5 are task-level with file lists + key code — acceptable for an inline execution where each task re-derives code from the read-in patterns.
- **Type consistency:** `Proc`, `Rule`, `kind` strings (`junk`/`idle`/`watch`/`session`/`system`/`hot`), payload keys, hit names (`tab:system`/`kill:`/`confirm-kill:`/`cancel-kill`) are used consistently across tasks and match the spec.
