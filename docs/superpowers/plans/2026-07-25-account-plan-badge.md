# Account Plan Badge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each account's subscription plan (`20x` / `5x` / `Pro` / `Free`) on its card as `email · 20x (2)`, on both macOS and Linux, read from local label files only.

**Architecture:** Pure semantics in `smartbar/core/plan.py` (tier mapping + reader of cswap's per-slot config backups with a live `~/.claude.json` overlay). `Account.plan` stamped post-parse like `presence.apply_counts`; `model.account_label()` stays the single label composer for all Python UIs. macOS follows the check-update principle: Swift spawns `ai-smartbar --plans --json` (final display labels, Swift maps nothing) every 900s. Parity pinned by source-scrape tests.

**Tech Stack:** Python 3 stdlib (json/glob/re/os), unittest, SwiftUI (macOS 13+), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-25-account-plan-badge-design.md`

**Conventions:** commits are local-only, conventional prefixes (`feat:`/`docs:`/`test:`), no Co-Authored-By trailer. Run tests with `python3 -m unittest discover -s tests -p <file> -v` from the repo root.

---

### Task 1: Tier mapping — `core/plan.py` (pure function)

**Files:**
- Create: `smartbar/core/plan.py`
- Create: `tests/test_plan.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Plan badge semantics: mapping, reader, stamping, cross-language parity."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartbar.core import plan


class TestTierLabel(unittest.TestCase):
    def test_max_multipliers_come_from_the_tier_suffix(self):
        self.assertEqual(plan.tier_label("default_claude_max_20x"), "20x")
        self.assertEqual(plan.tier_label("default_claude_max_5x"), "5x")
        self.assertEqual(plan.tier_label("default_claude_max_1x"), "1x")

    def test_pro_free_team_come_from_tier_or_org_type(self):
        self.assertEqual(plan.tier_label("default_claude_pro"), "Pro")
        self.assertEqual(plan.tier_label(None, "claude_pro"), "Pro")
        self.assertEqual(plan.tier_label("", "claude_free"), "Free")
        self.assertEqual(plan.tier_label(None, "claude_enterprise"), "Team")
        self.assertEqual(plan.tier_label("team_something", None), "Team")

    def test_subscription_type_is_the_coarse_fallback(self):
        self.assertEqual(plan.tier_label(None, None, "max"), "Max")

    def test_unknown_means_no_badge(self):
        self.assertEqual(plan.tier_label(None, None, None), "")
        self.assertEqual(plan.tier_label("", "", ""), "")
        self.assertEqual(plan.tier_label("mystery_tier_x", "org?", ""), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s tests -p test_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartbar.core.plan'` (or ImportError).

- [ ] **Step 3: Minimal implementation**

```python
"""Subscription-plan badges: which plan each account is on (20x/5x/Pro/Free).

Tiers are read from LOCAL label fields only — cswap's per-slot config
backups plus the live ~/.claude.json for the active login. No keychain,
no network, no token fields ever. Design note:
docs/superpowers/specs/2026-07-25-account-plan-badge-design.md
"""
from __future__ import annotations

import glob
import json
import os
import re

DEFAULT_BACKUP_DIR = "~/.claude-swap-backup"
DEFAULT_CLAUDE_JSON = "~/.claude.json"

_MULT = re.compile(r"_(\d+)x$")


def enabled() -> bool:
    """False when SMARTBAR_PLANS=off — hides badges and skips all reads."""
    return os.environ.get("SMARTBAR_PLANS", "").strip().lower() != "off"


def backup_dir() -> str:
    return os.path.expanduser(
        os.environ.get("SMARTBAR_CSWAP_BACKUP_DIR", "") or DEFAULT_BACKUP_DIR)


def claude_json_path() -> str:
    return os.path.expanduser(
        os.environ.get("SMARTBAR_CLAUDE_JSON", "") or DEFAULT_CLAUDE_JSON)


def tier_label(rate_limit_tier=None, organization_type=None,
               subscription_type=None) -> str:
    """Anthropic tier strings -> short badge; "" means show nothing.

    "default_claude_max_20x" -> "20x" is the primary path; pro/free/team
    are recognised in either the tier or the org type; subscriptionType
    ("max"/"pro"/"free", from credential blobs) is the coarse fallback.
    """
    tier = (rate_limit_tier or "").strip().lower()
    match = _MULT.search(tier)
    if match:
        return f"{match.group(1)}x"
    org = (organization_type or "").strip().lower()
    for hay in (tier, org):
        if not hay:
            continue
        if "enterprise" in hay or "team" in hay:
            return "Team"
        if "pro" in hay:
            return "Pro"
        if "free" in hay:
            return "Free"
    sub = (subscription_type or "").strip()
    return sub.title() if sub else ""
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s tests -p test_plan.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add smartbar/core/plan.py tests/test_plan.py
git commit -m "feat: tier_label maps Anthropic plan tiers to card badges"
```

---

### Task 2: Reader — per-slot config backups + live overlay + mtime cache

**Files:**
- Modify: `smartbar/core/plan.py` (append)
- Modify: `tests/test_plan.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_plan.py`)

```python
def _write_config(directory: Path, n: int, email: str, tier: str) -> Path:
    path = directory / "configs" / f".claude-config-{n}-{email}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"oauthAccount": {
        "emailAddress": email,
        "organizationRateLimitTier": tier,
        "organizationType": "claude_max",
    }}))
    return path


class TestPlansByEmail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        # Reader must never fall back to the real live file in tests.
        self.live = self.dir / "claude.json"
        self.live.write_text("{}")
        plan._cache.clear()

    def plans(self):
        return plan.plans_by_email(str(self.dir), claude_json=str(self.live))

    def test_reads_every_slot_backup(self):
        _write_config(self.dir, 1, "a@x.com", "default_claude_max_20x")
        _write_config(self.dir, 2, "b@x.com", "default_claude_max_5x")
        self.assertEqual(self.plans(), {"a@x.com": "20x", "b@x.com": "5x"})

    def test_live_claude_json_wins_for_its_own_address(self):
        _write_config(self.dir, 1, "a@x.com", "default_claude_max_5x")
        self.live.write_text(json.dumps({"oauthAccount": {
            "emailAddress": "a@x.com",
            "organizationRateLimitTier": "default_claude_max_20x",
        }}))
        self.assertEqual(self.plans(), {"a@x.com": "20x"})

    def test_corrupt_or_tierless_files_are_skipped_silently(self):
        _write_config(self.dir, 1, "a@x.com", "default_claude_max_20x")
        bad = self.dir / "configs" / ".claude-config-2-b@x.com.json"
        bad.write_text("{not json")
        no_oauth = self.dir / "configs" / ".claude-config-3-c@x.com.json"
        no_oauth.write_text(json.dumps({"somethingElse": True}))
        self.assertEqual(self.plans(), {"a@x.com": "20x"})

    def test_kill_switch_returns_empty_and_reads_nothing(self):
        _write_config(self.dir, 1, "a@x.com", "default_claude_max_20x")
        with mock.patch.dict(os.environ, {"SMARTBAR_PLANS": "off"}):
            self.assertEqual(self.plans(), {})

    def test_mtime_bump_invalidates_the_cache(self):
        path = _write_config(self.dir, 1, "a@x.com", "default_claude_max_5x")
        self.assertEqual(self.plans()["a@x.com"], "5x")
        path.write_text(json.dumps({"oauthAccount": {
            "emailAddress": "a@x.com",
            "organizationRateLimitTier": "default_claude_max_20x",
        }}))
        os.utime(path, (os.stat(path).st_atime, os.stat(path).st_mtime + 5))
        self.assertEqual(self.plans()["a@x.com"], "20x")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s tests -p test_plan.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'plans_by_email'` (and `_cache`).

- [ ] **Step 3: Implement** (append to `smartbar/core/plan.py`)

```python
# path -> ((mtime, size), (email, label) | None). Config backups are a
# couple hundred KB each and polled every 60-180s on Linux; the cache
# makes the steady state a stat() per file.
_cache: dict = {}


def _labelled(path: str):
    """(email, label) from one config-backup/claude.json, None if unusable."""
    try:
        stat = os.stat(path)
        key = (stat.st_mtime, stat.st_size)
        hit = _cache.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        with open(path, encoding="utf-8") as handle:
            oauth = (json.load(handle) or {}).get("oauthAccount") or {}
        email = (oauth.get("emailAddress") or "").strip()
        label = tier_label(oauth.get("organizationRateLimitTier"),
                           oauth.get("organizationType"),
                           oauth.get("subscriptionType"))
        value = (email, label) if email and label else None
        _cache[path] = (key, value)
        return value
    except (OSError, ValueError):
        return None


def plans_by_email(directory=None, claude_json=None) -> dict:
    """email -> badge label for every account whose tier is readable."""
    if not enabled():
        return {}
    directory = directory or backup_dir()
    plans: dict = {}
    pattern = os.path.join(directory, "configs", ".claude-config-*.json")
    for path in sorted(glob.glob(pattern)):
        pair = _labelled(path)
        if pair:
            plans[pair[0]] = pair[1]
    pair = _labelled(claude_json or claude_json_path())
    if pair:  # the live login is fresher than its backup copy
        plans[pair[0]] = pair[1]
    return plans
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s tests -p test_plan.py -v`
Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add smartbar/core/plan.py tests/test_plan.py
git commit -m "feat: read plan tiers from cswap config backups with live overlay"
```

---

### Task 3: `Account.plan` + `apply_plans` + label composition

**Files:**
- Modify: `smartbar/core/model.py:110` (Account dataclass) and `:269-283` (`account_label`)
- Modify: `smartbar/core/plan.py` (append)
- Modify: `tests/test_plan.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_plan.py`)

```python
from smartbar.core import model


def _account(email="a@x.com", plan_label="", devices=0):
    acct = model.Account(number=1, email=email)
    acct.plan = plan_label
    acct.devices = devices
    return acct


class TestLabelComposition(unittest.TestCase):
    def test_email_only(self):
        self.assertEqual(model.account_label(_account()), "a@x.com")

    def test_plan_slots_between_email_and_devices(self):
        self.assertEqual(model.account_label(_account(plan_label="20x")),
                         "a@x.com · 20x")
        self.assertEqual(
            model.account_label(_account(plan_label="5x", devices=2)),
            "a@x.com · 5x (2)")

    def test_devices_without_plan_is_unchanged(self):
        self.assertEqual(model.account_label(_account(devices=3)),
                         "a@x.com (3)")


class TestApplyPlans(unittest.TestCase):
    def test_stamps_matching_accounts_and_blanks_the_rest(self):
        snap = mock.Mock(accounts=[_account("a@x.com"), _account("b@x.com")])
        plan.apply_plans(snap, {"a@x.com": "20x"})
        self.assertEqual(snap.accounts[0].plan, "20x")
        self.assertEqual(snap.accounts[1].plan, "")

    def test_none_snapshot_is_a_no_op(self):
        plan.apply_plans(None, {"a@x.com": "20x"})  # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s tests -p test_plan.py -v`
Expected: FAIL — composition tests get `"a@x.com"` without the badge; `apply_plans` missing.

- [ ] **Step 3: Implement.** In `smartbar/core/model.py`, add the field after `devices: int = 0` (line 110):

```python
    # Subscription plan badge ("20x", "5x", "Pro", "Free") — stamped by
    # core/plan.apply_plans from local label files; "" means unknown and
    # renders NO badge, same convention as devices == 0.
    plan: str = ""
```

Replace the last two lines of `account_label` (keep the docstring, extend its first line to `"a@b.com · 20x (2)"`):

```python
    label = account.email
    badge = getattr(account, "plan", "") or ""
    if badge:
        label = f"{label} · {badge}"
    count = getattr(account, "devices", 0) or 0
    return f"{label} ({count})" if count > 0 else label
```

Append to `smartbar/core/plan.py`:

```python
def apply_plans(snapshot, plans) -> None:
    """Stamp accounts with their plan badge, for model.account_label."""
    if snapshot is None:
        return
    for account in snapshot.accounts:
        account.plan = str((plans or {}).get(account.email, "") or "")
```

- [ ] **Step 4: Run plan + presence + layout suites** (layout guards the unchanged default)

Run: `python3 -m unittest discover -s tests -p test_plan.py -v && python3 -m unittest discover -s tests -p test_presence.py -v && python3 -m unittest discover -s tests -p test_popover_layout.py -v`
Expected: all PASS (plan="" keeps every existing label identical).

- [ ] **Step 5: Commit**

```bash
git add smartbar/core/model.py smartbar/core/plan.py tests/test_plan.py
git commit -m "feat: account_label composes the plan badge between email and device count"
```

---

### Task 4: Stamp plans in both Python UIs + layout badge test

**Files:**
- Modify: `smartbar/macos/menubar.py:57` (next to `presence.apply_counts`)
- Modify: `smartbar/linux/tray.py:402` (next to `presence.apply_counts`)
- Modify: `tests/test_popover_layout.py` (new badge case near line 131)

- [ ] **Step 1: Write the failing layout test** (append beside the existing `(2)` case at `tests/test_popover_layout.py:131`)

```python
    def test_plan_badge_rides_the_account_label(self):
        card = account(email="a@example.com")
        card.plan = "20x"
        card.devices = 2
        layout = build([card], fetched_at="")
        self.assertTrue(any("a@example.com · 20x (2)" in getattr(s, "text", "")
                            for s in layout.shapes))
```

(Adapt `build([card], fetched_at="")` to the exact helper this file already uses for the `(2)` assertion at line 133 — same call, one new account attribute.)

- [ ] **Step 2: Run to verify it fails, then make it pass**

Run: `python3 -m unittest discover -s tests -p test_popover_layout.py -v`
Expected: the new test FAILS only if `account_label` isn't wired (it is — Task 3), so it should PASS immediately; if it fails, the fixture helper differs — fix the test to use this file's real builder, not the code.

- [ ] **Step 3: Stamp plans at both call sites.** In `smartbar/macos/menubar.py` (imports: add `from smartbar.core import plan`), directly after line 57's `presence.apply_counts(snap, presence_client.counts())`:

```python
        plan.apply_plans(snap, plan.plans_by_email())
```

Same one-liner (plus the same import) in `smartbar/linux/tray.py` after line 402's `presence.apply_counts(...)`. Check neither file already binds the name `plan` locally (`grep -n "\bplan\b" smartbar/macos/menubar.py smartbar/linux/tray.py`); if it does, import as `from smartbar.core import plan as plan_badges` and call through that name in that file.

- [ ] **Step 4: Run the full test suite**

Run: `python3 -m unittest discover -s tests`
Expected: everything passes (the stamp is read-only label files; kill switch off-path returns `{}`).

- [ ] **Step 5: Commit**

```bash
git add smartbar/macos/menubar.py smartbar/linux/tray.py tests/test_popover_layout.py
git commit -m "feat: stamp plan badges in the rumps and Linux tray polls"
```

---

### Task 5: CLI verb — `ai-smartbar --plans --json`

**Files:**
- Modify: `bin/ai-smartbar` (argparse block ~line 131; dispatch block ~line 174)
- Modify: `tests/test_plan.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_plan.py`)

```python
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent


class TestPlansCli(unittest.TestCase):
    def test_plans_json_prints_labels_from_the_seamed_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_config(tmp_path, 1, "a@x.com", "default_claude_max_20x")
            live = tmp_path / "claude.json"
            live.write_text("{}")
            env = dict(os.environ,
                       SMARTBAR_CSWAP_BACKUP_DIR=str(tmp_path),
                       SMARTBAR_CLAUDE_JSON=str(live))
            proc = subprocess.run(
                [sys.executable, str(REPO / "bin" / "ai-smartbar"),
                 "--plans", "--json"],
                capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout),
                             {"plans": {"a@x.com": "20x"}})
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s tests -p test_plan.py -v`
Expected: FAIL — argparse errors on unknown `--plans` (non-zero exit).

- [ ] **Step 3: Implement.** In `bin/ai-smartbar`, add after the `--presence-status` argument (line ~131):

```python
    parser.add_argument("--plans", action="store_true",
                        help="print each account's plan badge as JSON "
                             "(the macOS app's data source; output is "
                             "always JSON, --json is accepted for symmetry)")
```

Add the dispatch immediately after the presence block (line ~177, before the platform UI launch):

```python
    if args.plans:
        import json as _json
        from smartbar.core import plan
        print(_json.dumps({"plans": plan.plans_by_email()}))
        return 0
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s tests -p test_plan.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/ai-smartbar tests/test_plan.py
git commit -m "feat: ai-smartbar --plans prints the plan badge map for the Swift app"
```

---

### Task 6: Swift — PlanStatus + card wiring + build

**Files:**
- Create: `macos-swift/Sources/AISmartbar/PlanStatus.swift`
- Modify: `macos-swift/Sources/AISmartbar/PresenceStatus.swift` (~line 190: drop `private` from `static func repoRoot()`)
- Modify: `macos-swift/Sources/AISmartbar/AISmartbarApp.swift` (inject)
- Modify: `macos-swift/Sources/AISmartbar/AccountCardView.swift` (header text)

- [ ] **Step 1: Create `PlanStatus.swift`**

```swift
// Plan badges: email -> "20x" / "5x" / "Pro" / "Free", computed by Python
// (`ai-smartbar --plans --json`). ONE SHARED ANSWER, NOT A SWIFT PORT:
// Swift renders the labels verbatim and maps nothing — the tier strings
// and the SMARTBAR_PLANS kill switch live entirely in core/plan.py (the
// helper prints {} when disabled, which blanks every badge here too).
import Foundation

@MainActor
final class PlanStatus: ObservableObject {
    @Published private(set) var plans: [String: String] = [:]

    /// Plans change ~never (a tier change requires a fresh login), so a
    /// slow cadence is deliberate. Pinned by tests/test_plan.py.
    static let refreshInterval: TimeInterval = 900

    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: Self.refreshInterval,
                                     repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        Task.detached(priority: .utility) {
            let fetched = Self.fetchPlans()
            await MainActor.run { [weak self] in
                guard let self, let fetched else { return }
                if fetched != self.plans { self.plans = fetched }
            }
        }
    }

    /// nil = helper unavailable (missing checkout, bad JSON); keep the
    /// last-good map rather than blanking every badge on a hiccup.
    nonisolated private static func fetchPlans() -> [String: String]? {
        guard let root = PresenceStatus.repoRoot() else { return nil }
        let launcher = root + "/bin/ai-smartbar"
        guard FileManager.default.isExecutableFile(atPath: launcher) else {
            return nil
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = ["--plans", "--json"]
        // launchd hands a GUI app a bare PATH, and the launcher's shebang
        // has to be able to find python3 — same treatment as presence.
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        environment["PATH"] = [home + "/.local/bin", "/opt/homebrew/bin",
                               "/usr/local/bin", "/usr/bin", "/bin"]
            .joined(separator: ":")
        process.environment = environment
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard
            let raw = try? JSONSerialization.jsonObject(with: data)
                as? [String: Any],
            let plans = raw["plans"] as? [String: String]
        else { return nil }
        return plans
    }
}
```

- [ ] **Step 2: Un-private the resolver.** In `PresenceStatus.swift` (~line 190), change the `repoRoot()` declaration from `private static func repoRoot()` to `static func repoRoot()` (keep `nonisolated` if present). Do not change its body.

- [ ] **Step 3: Inject in `AISmartbarApp.swift`** — add alongside the other `@StateObject`s and pass into the environment:

```swift
    @StateObject private var plans = PlanStatus()
```

```swift
                .environmentObject(plans)
```

(the second line goes with the existing `.environmentObject(...)` chain on `PopoverView()`).

- [ ] **Step 4: Compose in `AccountCardView.swift`.** Add the environment object and a header builder, and replace line 33's `Text(presence.label(for: account))` with `headerText`:

```swift
    @EnvironmentObject private var planStatus: PlanStatus
```

```swift
    /// "a@b.com · 20x (2)" — one string, three segments; the plan segment
    /// is dimmed. MUST stay in step with model.account_label (pinned by
    /// TestPlanParity in tests/test_plan.py).
    private var headerText: Text {
        var text = Text(account.email)
        let plan = planStatus.plans[account.email] ?? ""
        if !plan.isEmpty {
            text = text + Text(" \u{00B7} \(plan)").foregroundColor(.secondary)
        }
        if devices > 0 {
            text = text + Text(" (\(devices))")
        }
        return text
    }
```

```swift
                headerText
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(devicesHelp)
```

- [ ] **Step 5: Build**

Run: `cd macos-swift && swift build 2>&1 | tail -5 && cd ..`
Expected: `Build complete!` (fix any compile error before proceeding).

- [ ] **Step 6: Commit**

```bash
git add macos-swift/Sources/AISmartbar/PlanStatus.swift macos-swift/Sources/AISmartbar/PresenceStatus.swift macos-swift/Sources/AISmartbar/AISmartbarApp.swift macos-swift/Sources/AISmartbar/AccountCardView.swift
git commit -m "feat(macos): plan badge on account cards via ai-smartbar --plans"
```

---

### Task 7: Parity + wiring pins — `TestPlanParity`

**Files:**
- Modify: `tests/test_plan.py` (append)

- [ ] **Step 1: Write the tests** (source-scraping, same technique as `TestMacAndLinuxAgree` in `tests/test_presence.py:518`)

```python
SWIFT_DIR = REPO / "macos-swift" / "Sources" / "AISmartbar"


class TestPlanParity(unittest.TestCase):
    """Pin the four decisions that exist in both languages (source-scrape,
    runs on Linux without a Swift toolchain — same trick as
    tests/test_presence.py::TestMacAndLinuxAgree)."""

    @classmethod
    def setUpClass(cls):
        cls.card = (SWIFT_DIR / "AccountCardView.swift").read_text()
        cls.status = (SWIFT_DIR / "PlanStatus.swift").read_text()
        cls.all_swift = "".join(
            p.read_text() for p in sorted(SWIFT_DIR.glob("*.swift")))

    def test_badge_composition_matches_python(self):
        # Swift renders exactly " · <plan>" between email and device count,
        # which is what model.account_label produces.
        self.assertIn('Text(" \\u{00B7} \\(plan)")', self.card)
        self.assertEqual(
            model.account_label(_account(plan_label="20x", devices=2)),
            "a@x.com · 20x (2)")

    def test_swift_maps_nothing(self):
        for marker in ("organizationRateLimitTier", "default_claude",
                       "subscriptionType", "SMARTBAR_PLANS"):
            self.assertNotIn(marker, self.all_swift,
                             f"tier policy leaked into Swift: {marker}")

    def test_swift_refresh_cadence_is_pinned(self):
        self.assertIn("refreshInterval: TimeInterval = 900", self.status)

    def test_both_python_uis_stamp_plans(self):
        for path in ("smartbar/macos/menubar.py", "smartbar/linux/tray.py"):
            self.assertIn("apply_plans", (REPO / path).read_text(), path)
```

- [ ] **Step 2: Run to verify pass**

Run: `python3 -m unittest discover -s tests -p test_plan.py -v`
Expected: all PASS (these pin what Tasks 4-6 built; a failure means a wiring step was missed — fix the wiring, not the test).

- [ ] **Step 3: Commit**

```bash
git add tests/test_plan.py
git commit -m "test: pin plan badge parity between core and the Swift app"
```

---

### Task 8: Docs — README feature bullet + setting row

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the feature bullet.** Locate the `- **Device count per account.**` bullet in the Features section and insert directly after it:

```markdown
- **Plan badge per account.** `ios8build@gmail.com · 20x` — which
  subscription each account is on (`20x` / `5x` / `Pro` / `Free`), read
  from claude-swap's local per-slot config backups; no network, no
  credential fields touched. Unknown plans show no badge. Disable with
  `SMARTBAR_PLANS=off`.
```

- [ ] **Step 2: Add the setting.** Find the settings/environment table or list that documents `SMARTBAR_PRESENCE` (`grep -n "SMARTBAR_PRESENCE" README.md`) and add an adjacent entry in the same format:

```markdown
`SMARTBAR_PLANS` — set `off` to hide plan badges and skip the local reads (default: on).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the account plan badge and SMARTBAR_PLANS"
```

---

### Task 9: Full verification + live check

- [ ] **Step 1: Full unit suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK` (205 pre-existing + ~16 new; 8 skips without pycairo are normal).

- [ ] **Step 2: Release build**

Run: `cd macos-swift && swift build -c release 2>&1 | tail -3 && cd ..`
Expected: `Build complete!`

- [ ] **Step 3: Live data check (this Mac)**

Run: `bin/ai-smartbar --plans --json`
Expected: `{"plans": {"duc.dut.wr2@gmail.com": "20x", "duc.dut.wr@gmail.com": "20x", "ios8build@gmail.com": "20x", "jsmith@campus.example": "5x"}}` (key order may differ).

- [ ] **Step 4: Live UI check (optional but recommended)**

Run: `install/macos-swift.sh` (rebuilds the bundle and restarts the app; channel read-back is safe since e2e scenario G). Open the popover — each card shows `email · 20x/5x (n)`; jsmith shows `5x`.

- [ ] **Step 5: Do NOT push.** Commits stay local until the user says otherwise; cutting v0.6.4 via `install/release.sh` is a separate, user-approved step.

---

## Notes for the implementer

- **Never read token fields.** The reader touches `oauthAccount.emailAddress`, `organizationRateLimitTier`, `organizationType`, `subscriptionType` — nothing else, and never `~/.claude-swap-backup/credentials/` or any keychain.
- **The Linux cairo panel badge is same-color** (single Label run — the painter draws `account_label()` as one shape). Only SwiftUI dims the plan segment. Accepted cosmetic asymmetry, like the panel's missing footer check button.
- **No new e2e fence needed:** the reader has no route to the outside world (local files only). Unit tests use the `SMARTBAR_CSWAP_BACKUP_DIR`/`SMARTBAR_CLAUDE_JSON` seams so they never touch real state.
- **Modularization rule:** `core/plan.py` stays well under 200 lines; do not fold it into model.py.
