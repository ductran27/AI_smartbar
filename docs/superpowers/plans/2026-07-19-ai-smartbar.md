# AI_smartbar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cross-platform Claude usage-limit indicator (XFCE tray / macOS menu bar) with one-click account switching, powered by claude-swap.

**Architecture:** Shared pure-Python core (`smartbar/core/`) that shells out to `cswap list --json` / `cswap switch`, computes worst-metric/color/alert state, and formats every user-visible string; two thin UIs render those strings (GTK3+AppIndicator on Linux, rumps on macOS). Entry point `bin/ai-smartbar` dispatches by OS and offers `--once` for headless testing.

**Tech Stack:** Python 3.10+ (3.14.4 on the Linux box), stdlib `unittest`, GTK3 + AyatanaAppIndicator3 + pycairo + libnotify (all preinstalled on Linux), rumps (macOS, installed by `install/macos.sh`). No other dependencies.

**Verified facts this plan relies on** (examined 2026-07-19, cswap 0.22.0):
- `cswap list --json` → `schemaVersion: 1`, `accounts[]` with `number`, `email`, `organizationName`, `active`, `usageStatus`, `usage.fiveHour|sevenDay: {pct, resetsAt, countdown, clock}`, `usage.scoped[]: {name, pct, resetsAt, countdown, clock}` (per-model limits, e.g. `"Fable"`).
- XFCE 4.20/X11; `xfce4-terminal` absent → use `x-terminal-emulator`. `notify-send` present; `gi` Notify 0.7 works.
- Test runner: `python3 -m unittest` (pytest not assumed).

---

### Task 1: Repo skeleton

**Files:**
- Create: `.gitignore`, `smartbar/__init__.py`, `smartbar/core/__init__.py`, `smartbar/linux/__init__.py`, `smartbar/macos/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Write files**

`.gitignore`:
```
__pycache__/
*.pyc
.DS_Store
```

`smartbar/__init__.py`:
```python
__version__ = "0.1.0"
```

The other four `__init__.py` files are empty.

- [ ] **Step 2: Commit**

```bash
git add -A && git commit -m "chore: package skeleton"
```

### Task 2: core/model.py (TDD)

**Files:**
- Test: `tests/test_model.py`
- Create: `smartbar/core/model.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for smartbar.core.model — pure logic, no I/O."""
import os
import unittest

from smartbar.core import model


def metric(key="5h", pct=10.0, **kw):
    defaults = dict(label=key, short=key, resets_at="r1", countdown="1h 2m", clock="Jul 20 00:39")
    defaults.update(kw)
    return model.Metric(key=key, pct=pct, **defaults)


def account(number=1, email="a@x.com", active=True, ok=True, metrics=None):
    return model.Account(number=number, email=email, org="", active=active,
                         ok=ok, metrics=metrics if metrics is not None else [])


class Env(unittest.TestCase):
    def setUp(self):
        for var in ("SMARTBAR_YELLOW", "SMARTBAR_RED", "SMARTBAR_TEST_THRESHOLD"):
            os.environ.pop(var, None)

    tearDown = setUp


class TestWorst(Env):
    def test_picks_highest_pct(self):
        a = account(metrics=[metric("5h", 24.0), metric("7d", 20.0),
                             metric("scoped:Fable", 28.0, short="F", label="Fable")])
        self.assertEqual(model.worst(a).pct, 28.0)

    def test_none_for_empty_or_missing(self):
        self.assertIsNone(model.worst(account(metrics=[])))
        self.assertIsNone(model.worst(None))


class TestColor(Env):
    def test_thresholds(self):
        self.assertEqual(model.color(69.9), "green")
        self.assertEqual(model.color(70.0), "yellow")
        self.assertEqual(model.color(89.9), "yellow")
        self.assertEqual(model.color(90.0), "red")

    def test_env_overrides(self):
        os.environ["SMARTBAR_YELLOW"] = "50"
        os.environ["SMARTBAR_RED"] = "60"
        self.assertEqual(model.color(55), "yellow")
        self.assertEqual(model.color(60), "red")

    def test_test_threshold_sets_both(self):
        os.environ["SMARTBAR_TEST_THRESHOLD"] = "10"
        self.assertEqual(model.color(10), "red")
        self.assertEqual(model.color(9.9), "green")


class TestBestSwitch(Env):
    def test_lowest_worst_among_others(self):
        snap = model.Snapshot(accounts=[
            account(1, "a@x.com", active=True, metrics=[metric("5h", 95)]),
            account(2, "b@x.com", active=False, metrics=[metric("5h", 62)]),
            account(3, "c@x.com", active=False, metrics=[metric("5h", 34)]),
        ])
        self.assertEqual(model.best_switch(snap).number, 3)

    def test_none_when_alone(self):
        snap = model.Snapshot(accounts=[account(1, active=True, metrics=[metric()])])
        self.assertIsNone(model.best_switch(snap))

    def test_skips_no_data_accounts(self):
        snap = model.Snapshot(accounts=[
            account(1, active=True, metrics=[metric()]),
            account(2, active=False, ok=False, metrics=[]),
        ])
        self.assertIsNone(model.best_switch(snap))


class TestFormatting(Env):
    def setUp(self):
        super().setUp()
        self.acct = account(1, "ios8build@gmail.com", metrics=[
            metric("5h", 24.0), metric("7d", 20.0),
            metric("scoped:Fable", 28.4, short="F", label="Fable"),
        ])

    def test_title_line(self):
        self.assertEqual(model.title_line(self.acct),
                         "ios8build@gmail.com — 5h 24% · 7d 20% · F 28%")

    def test_title_line_no_account(self):
        self.assertEqual(model.title_line(None), "AI smartbar — no active account")

    def test_menu_row_active_and_inactive(self):
        self.assertTrue(model.menu_row(self.acct).startswith("● 1 ios8build@gmail.com"))
        other = account(2, "b@x.com", active=False, metrics=[metric("5h", 62)])
        self.assertEqual(model.menu_row(other), "○ 2 b@x.com   5h 62%")

    def test_icon_text_worst_short_and_int_pct(self):
        self.assertEqual(model.icon_text(self.acct), "F28")
        self.assertEqual(model.icon_text(account(metrics=[])), "?")

    def test_macos_title(self):
        self.assertEqual(model.macos_title(self.acct), "🟢 F 28%")
        red = account(metrics=[metric("5h", 92)])
        self.assertEqual(model.macos_title(red), "🔴 5h 92%")
        self.assertEqual(model.macos_title(None), "⚪ –")

    def test_active_account_property(self):
        snap = model.Snapshot(accounts=[account(1, active=False), account(2, active=True)])
        self.assertEqual(snap.active_account.number, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/tools/AI_smartbar && python3 -m unittest tests.test_model -v 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'smartbar.core.model'`

- [ ] **Step 3: Implement `smartbar/core/model.py`**

```python
"""Data model and presentation logic shared by all AI_smartbar UIs.

Every user-visible string (icon text, hover title, menu rows, macOS
menu-bar title) is produced here so both platform UIs render identically.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_YELLOW = 70.0
DEFAULT_RED = 90.0

DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "gray": "⚪"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def yellow_threshold() -> float:
    if "SMARTBAR_TEST_THRESHOLD" in os.environ:
        return _env_float("SMARTBAR_TEST_THRESHOLD", DEFAULT_YELLOW)
    return _env_float("SMARTBAR_YELLOW", DEFAULT_YELLOW)


def red_threshold() -> float:
    if "SMARTBAR_TEST_THRESHOLD" in os.environ:
        return _env_float("SMARTBAR_TEST_THRESHOLD", DEFAULT_RED)
    return _env_float("SMARTBAR_RED", DEFAULT_RED)


@dataclass
class Metric:
    key: str            # "5h", "7d", or "scoped:<Name>"
    label: str          # "5h", "7d", "Fable"
    short: str          # "5h", "7d", "F"
    pct: float
    resets_at: str = ""
    countdown: str = ""  # preformatted by cswap, e.g. "4h 3m"
    clock: str = ""


@dataclass
class Account:
    number: int
    email: str
    org: str = ""
    active: bool = False
    ok: bool = True     # usageStatus == "ok" and usage data present
    metrics: list = field(default_factory=list)


@dataclass
class Snapshot:
    accounts: list = field(default_factory=list)
    fetched_at: str = ""
    schema_warning: str = ""

    @property
    def active_account(self):
        for acct in self.accounts:
            if acct.active:
                return acct
        return None


def worst(account):
    """The metric closest to its limit, or None without data."""
    if account is None or not account.metrics:
        return None
    return max(account.metrics, key=lambda m: m.pct)


def color(pct: float) -> str:
    if pct >= red_threshold():
        return "red"
    if pct >= yellow_threshold():
        return "yellow"
    return "green"


def best_switch(snapshot):
    """Among non-active accounts with data, the one with most headroom."""
    candidates = [a for a in snapshot.accounts if not a.active and a.ok and a.metrics]
    if not candidates:
        return None
    return min(candidates, key=lambda a: worst(a).pct)


def metrics_text(account) -> str:
    return " · ".join(f"{m.short} {round(m.pct)}%" for m in account.metrics)


def title_line(account) -> str:
    if account is None:
        return "AI smartbar — no active account"
    if not account.metrics:
        return f"{account.email} — no usage data"
    return f"{account.email} — {metrics_text(account)}"


def menu_row(account) -> str:
    dot = "●" if account.active else "○"
    body = metrics_text(account) if account.metrics else "no data"
    return f"{dot} {account.number} {account.email}   {body}"


def icon_text(account) -> str:
    m = worst(account)
    if m is None:
        return "?"
    return f"{m.short}{round(m.pct)}"


def macos_title(account) -> str:
    m = worst(account)
    if m is None:
        return "⚪ –"
    return f"{DOT[color(m.pct)]} {m.short} {round(m.pct)}%"
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest tests.test_model -v 2>&1 | tail -3`
Expected: `OK` (all tests pass)

- [ ] **Step 5: Commit**

```bash
git add smartbar/core/model.py tests/test_model.py
git commit -m "feat: core model — metrics, thresholds, formatting"
```

### Task 3: core/cswap.py (TDD, real fixture)

**Files:**
- Create: `tests/fixtures/cswap_list.json` (captured real output: `cswap list --json > tests/fixtures/cswap_list.json`)
- Test: `tests/test_cswap.py`
- Create: `smartbar/core/cswap.py`

- [ ] **Step 1: Capture fixture**

```bash
mkdir -p tests/fixtures && cswap list --json > tests/fixtures/cswap_list.json
```

- [ ] **Step 2: Write the failing tests**

```python
"""Tests for smartbar.core.cswap — parser + binary resolution (no network)."""
import json
import os
import unittest

from smartbar.core import cswap

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cswap_list.json")


class TestParse(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE) as f:
            self.raw = f.read()

    def test_parses_real_fixture(self):
        snap = cswap.parse_snapshot(self.raw)
        self.assertEqual(snap.schema_warning, "")
        self.assertGreaterEqual(len(snap.accounts), 1)
        acct = snap.active_account
        self.assertTrue(acct.ok)
        keys = [m.key for m in acct.metrics]
        self.assertIn("5h", keys)
        self.assertIn("7d", keys)
        self.assertTrue(any(k.startswith("scoped:") for k in keys))
        fable = [m for m in acct.metrics if m.key == "scoped:Fable"][0]
        self.assertEqual(fable.short, "F")
        self.assertEqual(fable.label, "Fable")
        self.assertTrue(fable.countdown)  # preformatted string carried through

    def test_unknown_schema_version_warns_but_parses(self):
        data = json.loads(self.raw)
        data["schemaVersion"] = 2
        snap = cswap.parse_snapshot(json.dumps(data))
        self.assertIn("schemaVersion", snap.schema_warning)
        self.assertGreaterEqual(len(snap.accounts), 1)

    def test_missing_usage_tolerated(self):
        data = json.loads(self.raw)
        del data["accounts"][0]["usage"]
        data["accounts"][0]["usageStatus"] = "error"
        snap = cswap.parse_snapshot(json.dumps(data))
        self.assertFalse(snap.accounts[0].ok)
        self.assertEqual(snap.accounts[0].metrics, [])

    def test_invalid_json_raises(self):
        with self.assertRaises(cswap.CswapError):
            cswap.parse_snapshot("not json {")


class TestBinary(unittest.TestCase):
    def test_env_override_wins(self):
        os.environ["SMARTBAR_CSWAP"] = "/nonexistent/cswap"
        try:
            self.assertEqual(cswap._binary(), "/nonexistent/cswap")
        finally:
            del os.environ["SMARTBAR_CSWAP"]


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failure**

Run: `python3 -m unittest tests.test_cswap -v 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'smartbar.core.cswap'`

- [ ] **Step 4: Implement `smartbar/core/cswap.py`**

```python
"""Thin subprocess wrapper around the claude-swap CLI (the data engine)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from .model import Account, Metric, Snapshot

TIMEOUT = 30


class CswapError(Exception):
    """Any failure talking to or parsing output from cswap."""


def _binary() -> str:
    override = os.environ.get("SMARTBAR_CSWAP")
    if override:
        return override
    found = shutil.which("cswap")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/cswap")
    if os.path.exists(fallback):
        return fallback
    raise CswapError("cswap binary not found (install claude-swap)")


def _run(args):
    try:
        proc = subprocess.run([_binary(), *args], capture_output=True,
                              text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise CswapError(f"cswap {' '.join(args)} timed out after {TIMEOUT}s") from exc
    except OSError as exc:
        raise CswapError(f"failed to run cswap: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        raise CswapError(f"cswap {' '.join(args)} failed (rc={proc.returncode}): {detail}")
    return proc.stdout


def _metric(key, label, short, raw) -> Metric:
    return Metric(key=key, label=label, short=short,
                  pct=float(raw.get("pct", 0.0)),
                  resets_at=raw.get("resetsAt", ""),
                  countdown=raw.get("countdown", ""),
                  clock=raw.get("clock", ""))


def parse_snapshot(text: str) -> Snapshot:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CswapError(f"cswap returned invalid JSON: {exc}") from exc
    snap = Snapshot()
    version = data.get("schemaVersion")
    if version != 1:
        snap.schema_warning = f"unexpected cswap schemaVersion {version!r}"
    for raw in data.get("accounts", []):
        usage = raw.get("usage")
        acct = Account(number=int(raw.get("number", 0)),
                       email=raw.get("email", "?"),
                       org=raw.get("organizationName", ""),
                       active=bool(raw.get("active", False)),
                       ok=raw.get("usageStatus") == "ok" and isinstance(usage, dict))
        if acct.ok:
            if "fiveHour" in usage:
                acct.metrics.append(_metric("5h", "5h", "5h", usage["fiveHour"]))
            if "sevenDay" in usage:
                acct.metrics.append(_metric("7d", "7d", "7d", usage["sevenDay"]))
            for scoped in usage.get("scoped", []):
                name = scoped.get("name") or "?"
                acct.metrics.append(_metric(f"scoped:{name}", name,
                                            name[:1].upper() or "?", scoped))
        snap.accounts.append(acct)
        if not snap.fetched_at and raw.get("usageFetchedAt"):
            snap.fetched_at = raw["usageFetchedAt"]
    return snap


def fetch() -> Snapshot:
    return parse_snapshot(_run(["list", "--json"]))


def switch(number: int) -> None:
    _run(["switch", str(number)])
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m unittest tests.test_cswap -v 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add smartbar/core/cswap.py tests/test_cswap.py tests/fixtures/cswap_list.json
git commit -m "feat: cswap wrapper — fetch/switch/tolerant parser with real fixture"
```

### Task 4: core/alerts.py (TDD)

**Files:**
- Test: `tests/test_alerts.py`
- Create: `smartbar/core/alerts.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for smartbar.core.alerts — fire-once / re-arm state machine."""
import os
import unittest

from smartbar.core import model
from smartbar.core.alerts import AlertManager


def snap(active_pct, resets="r1", other_pct=None):
    accounts = [model.Account(number=1, email="a@x.com", active=True, metrics=[
        model.Metric(key="5h", label="5h", short="5h", pct=active_pct,
                     resets_at=resets, countdown="1h 12m", clock="")])]
    if other_pct is not None:
        accounts.append(model.Account(number=2, email="b@x.com", active=False, metrics=[
            model.Metric(key="5h", label="5h", short="5h", pct=other_pct,
                         resets_at="rx", countdown="", clock="")]))
    return model.Snapshot(accounts=accounts)


class TestAlerts(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SMARTBAR_TEST_THRESHOLD", None)
        self.mgr = AlertManager()

    tearDown = setUp

    def test_fires_once_at_threshold(self):
        alerts = self.mgr.check(snap(92))
        self.assertEqual(len(alerts), 1)
        self.assertIn("5h", alerts[0].title)
        self.assertIn("92%", alerts[0].title)
        self.assertIn("Resets in 1h 12m", alerts[0].body)
        self.assertEqual(self.mgr.check(snap(93)), [])  # held, same window

    def test_rearm_on_drop_below(self):
        self.mgr.check(snap(92))
        self.mgr.check(snap(5))          # window reset -> pct low again
        self.assertEqual(len(self.mgr.check(snap(91))), 1)

    def test_rearm_on_resets_at_change(self):
        self.mgr.check(snap(92, resets="r1"))
        alerts = self.mgr.check(snap(95, resets="r2"))  # new window, still high
        self.assertEqual(len(alerts), 1)

    def test_below_threshold_never_fires(self):
        self.assertEqual(self.mgr.check(snap(89.9)), [])

    def test_suggestion_names_best_other_account(self):
        alerts = self.mgr.check(snap(92, other_pct=34))
        self.assertIn("#2 b@x.com", alerts[0].body)
        self.assertIn("34%", alerts[0].body)

    def test_no_other_account_message(self):
        alerts = self.mgr.check(snap(92))
        self.assertIn("No other account", alerts[0].body)

    def test_respects_test_threshold_env(self):
        os.environ["SMARTBAR_TEST_THRESHOLD"] = "10"
        self.assertEqual(len(self.mgr.check(snap(11))), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_alerts -v 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'smartbar.core.alerts'`

- [ ] **Step 3: Implement `smartbar/core/alerts.py`**

```python
"""Fire-once threshold alerts with re-arm when the usage window resets.

State is in-memory only: after an app restart a still-red metric fires one
more notification. Accepted trade-off (documented in the spec).
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import best_switch, red_threshold, worst


@dataclass
class Alert:
    title: str
    body: str


class AlertManager:
    def __init__(self):
        self._fired = {}  # (account_number, metric_key) -> resets_at when fired

    def check(self, snapshot):
        alerts = []
        account = snapshot.active_account
        if account is None:
            return alerts
        threshold = red_threshold()
        for metric in account.metrics:
            key = (account.number, metric.key)
            if metric.pct >= threshold:
                if self._fired.get(key) == metric.resets_at:
                    continue  # already fired for this window
                self._fired[key] = metric.resets_at
                alerts.append(self._build(snapshot, metric))
            else:
                self._fired.pop(key, None)  # re-arm after reset
        return alerts

    def _build(self, snapshot, metric):
        title = f"Claude: {metric.label} window at {round(metric.pct)}%"
        lines = []
        if metric.countdown:
            lines.append(f"Resets in {metric.countdown}.")
        suggestion = best_switch(snapshot)
        if suggestion is not None:
            w = worst(suggestion)
            lines.append(f"Best switch: #{suggestion.number} {suggestion.email} "
                         f"({w.short} {round(w.pct)}%)")
        else:
            lines.append("No other account available.")
        return Alert(title=title, body="\n".join(lines))
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest tests.test_alerts -v 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add smartbar/core/alerts.py tests/test_alerts.py
git commit -m "feat: alert manager — fire once at 90%, re-arm on window reset"
```

### Task 5: bin/ai-smartbar entry point

**Files:**
- Create: `bin/ai-smartbar` (executable)

- [ ] **Step 1: Implement**

```python
#!/usr/bin/env python3
"""AI_smartbar launcher: OS dispatch, plus --once headless mode."""
import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from smartbar import __version__  # noqa: E402


def once() -> int:
    from smartbar.core import cswap, model
    try:
        snap = cswap.fetch()
    except cswap.CswapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if snap.schema_warning:
        print(f"WARNING: {snap.schema_warning}", file=sys.stderr)
    account = snap.active_account
    m = model.worst(account)
    icon = "?" if m is None else f"{model.icon_text(account)} ({model.color(m.pct)})"
    print(f"icon:  {icon}")
    print(f"title: {model.title_line(account)}")
    for acct in snap.accounts:
        print(model.menu_row(acct))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ai-smartbar",
                                     description="Claude usage limits in your bar")
    parser.add_argument("--once", action="store_true",
                        help="print current state to stdout and exit")
    parser.add_argument("--version", action="version",
                        version=f"ai-smartbar {__version__}")
    args = parser.parse_args()
    if args.once:
        return once()
    if sys.platform == "darwin":
        from smartbar.macos.menubar import main as ui_main
    else:
        from smartbar.linux.tray import main as ui_main
    ui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify live**

Run: `chmod +x bin/ai-smartbar && ./bin/ai-smartbar --version && ./bin/ai-smartbar --once`
Expected: version line; then `icon: F28 (green)`-style line, title line, one `●` account row (real values vary).

- [ ] **Step 3: Commit**

```bash
git add bin/ai-smartbar && git commit -m "feat: launcher with --once headless mode"
```

### Task 6: Linux tray UI

**Files:**
- Create: `smartbar/linux/tray.py`

No unit tests (GTK main loop); verified live in Task 8. Keep ALL logic in core — tray.py only renders.

- [ ] **Step 1: Implement `smartbar/linux/tray.py`**

```python
"""XFCE/Linux system-tray UI: AppIndicator + cairo-drawn badge icon."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

import logging
import os
import subprocess
import threading

import cairo
from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk

from smartbar import __version__
from smartbar.core import cswap, model
from smartbar.core.alerts import AlertManager

CACHE_DIR = os.path.expanduser("~/.cache/ai-smartbar")
ICON_DIR = os.path.join(CACHE_DIR, "icons")
LOG_FILE = os.path.join(CACHE_DIR, "tray.log")

COLORS = {"green": (0.18, 0.65, 0.32), "yellow": (0.85, 0.65, 0.13),
          "red": (0.80, 0.16, 0.16), "gray": (0.45, 0.45, 0.45)}

log = logging.getLogger("ai-smartbar")


def render_icon(text: str, color_name: str, path: str) -> None:
    """Rounded-rect badge (96x48, scaled down by the panel) with bold text."""
    w, h, r = 96, 48, 12
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surface)
    ctx.new_sub_path()
    ctx.arc(w - r, r, r, -1.5708, 0)
    ctx.arc(w - r, h - r, r, 0, 1.5708)
    ctx.arc(r, h - r, r, 1.5708, 3.1416)
    ctx.arc(r, r, r, 3.1416, 4.7124)
    ctx.close_path()
    ctx.set_source_rgb(*COLORS[color_name])
    ctx.fill()
    ctx.set_source_rgb(1, 1, 1)
    ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(30)
    ext = ctx.text_extents(text)
    ctx.move_to((w - ext.width) / 2 - ext.x_bearing,
                (h - ext.height) / 2 - ext.y_bearing)
    ctx.show_text(text)
    surface.write_to_png(path)


class Tray:
    def __init__(self):
        os.makedirs(ICON_DIR, exist_ok=True)
        self.interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self.alerts = AlertManager()
        self.snapshot = None
        self.failures = 0
        self.flip = False
        self.indicator = AppIndicator.Indicator.new(
            "ai-smartbar", "dialog-information",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_icon_theme_path(ICON_DIR)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._set_icon("...", "gray")
        self.indicator.set_menu(self._build_menu())
        self._init_notify()

    def _init_notify(self):
        self.notify = None
        try:
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify
            Notify.init("AI smartbar")
            self.notify = Notify
        except Exception:
            log.warning("libnotify unavailable; will fall back to notify-send")

    def _send_alert(self, alert):
        try:
            if self.notify is not None:
                self.notify.Notification.new(alert.title, alert.body,
                                             "dialog-warning").show()
            else:
                subprocess.run(["notify-send", "-u", "critical",
                                alert.title, alert.body], timeout=10, check=False)
        except Exception:
            log.exception("failed to send notification")

    def _set_icon(self, text, color_name):
        # Alternate two icon names: AppIndicator ignores a set_icon_full call
        # with the current name, so a single name would never repaint.
        self.flip = not self.flip
        name = f"state-{'a' if self.flip else 'b'}"
        render_icon(text, color_name, os.path.join(ICON_DIR, name + ".png"))
        self.indicator.set_icon_full(name, "AI smartbar usage")

    def _build_menu(self):
        menu = Gtk.Menu()
        if self.snapshot is None:
            label = "Loading…" if self.failures == 0 else "cswap error — see tray.log"
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(False)
            menu.append(item)
        else:
            stale = "  (stale)" if self.failures else ""
            for acct in self.snapshot.accounts:
                item = Gtk.MenuItem(label=model.menu_row(acct)
                                    + (stale if acct.active else ""))
                if acct.active:
                    item.set_sensitive(False)
                else:
                    item.connect("activate", self._on_switch, acct.number)
                menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        for label, callback in (("⟳ Refresh now", self._on_refresh),
                                ("⚙ Open cswap TUI", self._on_tui),
                                ("⏻ Quit", self._on_quit)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)
        menu.show_all()
        return menu

    def _on_switch(self, _item, number):
        def run():
            try:
                cswap.switch(number)
            except cswap.CswapError:
                log.exception("switch failed")
            self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    def _on_refresh(self, _item):
        self._start_fetch()

    def _on_tui(self, _item):
        try:
            subprocess.Popen(["x-terminal-emulator", "-e", "cswap", "tui"])
        except OSError:
            log.exception("could not open terminal")

    def _on_quit(self, _item):
        Gtk.main_quit()

    def _start_fetch(self):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            snap = cswap.fetch()
        except cswap.CswapError as exc:
            log.warning("fetch failed: %s", exc)
            GLib.idle_add(self._apply_error, str(exc))
            return
        GLib.idle_add(self._apply_snapshot, snap)

    def _apply_snapshot(self, snap):
        self.failures = 0
        self.snapshot = snap
        if snap.schema_warning:
            log.warning("%s", snap.schema_warning)
        account = snap.active_account
        m = model.worst(account)
        if m is None:
            self._set_icon("?", "gray")
        else:
            self._set_icon(model.icon_text(account), model.color(m.pct))
        self.indicator.set_title(model.title_line(account))
        self.indicator.set_menu(self._build_menu())
        for alert in self.alerts.check(snap):
            self._send_alert(alert)
        return False

    def _apply_error(self, message):
        self.failures += 1
        if self.failures >= 3:
            self._set_icon("?", "gray")
            self.indicator.set_title(f"AI smartbar — cswap error: {message[:80]}")
        self.indicator.set_menu(self._build_menu())
        return False

    def _tick(self):
        self._start_fetch()
        return True


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200_000:
            os.remove(LOG_FILE)
    except OSError:
        pass
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log.info("ai-smartbar %s starting (interval %ss)", __version__,
             os.environ.get("SMARTBAR_INTERVAL", "60"))
    tray = Tray()
    tray._start_fetch()
    GLib.timeout_add_seconds(tray.interval, tray._tick)
    Gtk.main()
```

- [ ] **Step 2: Syntax/import check (no main loop)**

Run: `python3 -c "import sys; sys.path.insert(0,'.'); import smartbar.linux.tray; print('import OK')"`
Expected: `import OK`

- [ ] **Step 3: Commit**

```bash
git add smartbar/linux/tray.py && git commit -m "feat: Linux tray UI (AppIndicator + cairo badge)"
```

### Task 7: macOS menu-bar UI

**Files:**
- Create: `smartbar/macos/menubar.py`

Cannot be live-verified on Linux (rumps requires macOS). Gate: `py_compile` only.

- [ ] **Step 1: Implement `smartbar/macos/menubar.py`**

```python
"""macOS menu-bar UI for AI_smartbar (rumps / native NSStatusBar).

NOT yet live-verified on a Mac — written to spec; run install/macos.sh on
the Mac, then check the menu bar. Logic lives in smartbar.core (unit-tested).
"""
import os
import subprocess
import threading

import rumps

from smartbar.core import cswap, model
from smartbar.core.alerts import AlertManager


class SmartBarApp(rumps.App):
    def __init__(self):
        super().__init__("⚪ …", quit_button=None)
        self.alerts = AlertManager()
        self.snapshot = None
        self.failures = 0
        interval = int(os.environ.get("SMARTBAR_INTERVAL", "60"))
        self._rebuild_menu()
        self.timer = rumps.Timer(self._tick, interval)
        self.timer.start()
        self._tick(None)

    def _tick(self, _sender):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            snap = cswap.fetch()
        except cswap.CswapError:
            self.failures += 1
            if self.failures >= 3:
                self.title = "⚪ ?"
            return
        self.failures = 0
        self.snapshot = snap
        self.title = model.macos_title(snap.active_account)
        self._rebuild_menu()
        for alert in self.alerts.check(snap):
            rumps.notification("AI smartbar", alert.title, alert.body)

    def _rebuild_menu(self):
        self.menu.clear()
        items = []
        if self.snapshot is None:
            items.append(rumps.MenuItem("Loading…"))
        else:
            for acct in self.snapshot.accounts:
                callback = None if acct.active else self._make_switch(acct.number)
                items.append(rumps.MenuItem(model.menu_row(acct), callback=callback))
        items.append(None)  # separator
        items.append(rumps.MenuItem("⟳ Refresh now", callback=self._tick))
        items.append(rumps.MenuItem("⚙ Open cswap TUI", callback=self._open_tui))
        items.append(rumps.MenuItem("⏻ Quit", callback=lambda _s: rumps.quit_application()))
        self.menu = items

    def _make_switch(self, number):
        def callback(_sender):
            def run():
                try:
                    cswap.switch(number)
                except cswap.CswapError:
                    pass
                self._tick(None)
            threading.Thread(target=run, daemon=True).start()
        return callback

    def _open_tui(self, _sender):
        subprocess.Popen(["osascript", "-e",
                          'tell application "Terminal" to do script "cswap tui"'])


def main():
    SmartBarApp().run()
```

- [ ] **Step 2: Compile check**

Run: `python3 -m py_compile smartbar/macos/menubar.py && echo compiled`
Expected: `compiled`

- [ ] **Step 3: Commit**

```bash
git add smartbar/macos/menubar.py && git commit -m "feat: macOS menu-bar UI (rumps, pending live Mac verification)"
```

### Task 8: Install scripts + live Linux verification

**Files:**
- Create: `install/linux.sh`, `install/macos.sh` (both executable)

- [ ] **Step 1: Write `install/linux.sh`**

```bash
#!/usr/bin/env bash
# Install (default) or --uninstall ai-smartbar on Linux. No sudo needed.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HOME/.local/bin/ai-smartbar"
AUTOSTART="$HOME/.config/autostart/ai-smartbar.desktop"
CACHE="$HOME/.cache/ai-smartbar"

if [[ "${1:-}" == "--uninstall" ]]; then
  pkill -f "bin/ai-smartbar" 2>/dev/null || true
  rm -f "$BIN" "$AUTOSTART"
  rm -rf "$CACHE"
  echo "ai-smartbar uninstalled."
  exit 0
fi

mkdir -p "$(dirname "$BIN")" "$(dirname "$AUTOSTART")"
ln -sf "$REPO/bin/ai-smartbar" "$BIN"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=AI smartbar
Comment=Claude usage limits in the system tray
Exec=$BIN
Icon=dialog-information
X-GNOME-Autostart-enabled=true
EOF
echo "Installed $BIN and autostart entry. Starting..."
nohup "$BIN" >/dev/null 2>&1 &
sleep 2
pgrep -f "bin/ai-smartbar" >/dev/null && echo "ai-smartbar is running." \
  || { echo "FAILED to start — check $CACHE/tray.log"; exit 1; }
```

- [ ] **Step 2: Write `install/macos.sh`**

```bash
#!/usr/bin/env bash
# Install (default) or --uninstall ai-smartbar on macOS.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUPPORT="$HOME/Library/Application Support/ai-smartbar"
VENV="$SUPPORT/venv"
PLIST="$HOME/Library/LaunchAgents/com.ductran.ai-smartbar.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  rm -rf "$SUPPORT"
  echo "ai-smartbar uninstalled."
  exit 0
fi

command -v cswap >/dev/null || { echo "Install claude-swap first (pipx install claude-swap)"; exit 1; }
mkdir -p "$SUPPORT"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip rumps
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ductran.ai-smartbar</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python3</string>
    <string>$REPO/bin/ai-smartbar</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/ai-smartbar.log</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "ai-smartbar installed — check your menu bar."
```

- [ ] **Step 3: Run installer, verify process + icon + autostart**

```bash
chmod +x install/linux.sh install/macos.sh
./install/linux.sh
sleep 5
ls ~/.cache/ai-smartbar/icons/           # expect state-a.png and/or state-b.png
test -f ~/.config/autostart/ai-smartbar.desktop && echo autostart-ok
pgrep -af "bin/ai-smartbar"
```
Expected: process running, at least one `state-*.png` rendered, `autostart-ok`.
**User visual check:** badge (e.g. green `F28`) visible in the XFCE tray; menu opens with account row + Refresh/TUI/Quit.
Fallback (spec): if the wide badge scales illegibly, switch `render_icon` to a 48×48 square with pct only and move the metric letter into the title.

- [ ] **Step 4: Forced-notification test**

```bash
pkill -f "bin/ai-smartbar"; SMARTBAR_TEST_THRESHOLD=10 nohup ~/.local/bin/ai-smartbar >/dev/null 2>&1 &
sleep 8   # expect a desktop notification naming the worst metric
pkill -f "bin/ai-smartbar"; nohup ~/.local/bin/ai-smartbar >/dev/null 2>&1 &
```
Expected: one critical notification appears during the test run; normal instance restarted after.

- [ ] **Step 5: Commit**

```bash
git add install/ && git commit -m "feat: install scripts (Linux autostart, macOS LaunchAgent)"
```

### Task 9: README + push + memory

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README** — sections: what it is (one paragraph + the two bar mock lines), requirements (claude-swap ≥0.22 configured with accounts), install Linux (`./install/linux.sh`), install macOS (`./install/macos.sh`, marked "pending live verification"), usage (menu, env vars `SMARTBAR_INTERVAL`, `SMARTBAR_YELLOW/RED`, `SMARTBAR_TEST_THRESHOLD`, `SMARTBAR_CSWAP`), behavior notes (switch affects new sessions only; restart may re-notify once; XFCE title = single-line hover), troubleshooting (`--once`, `tray.log`), uninstall.

- [ ] **Step 2: Full test suite + headless check**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -3 && ./bin/ai-smartbar --once`
Expected: `OK`, then live state lines.

- [ ] **Step 3: Commit + push**

```bash
git add README.md && git commit -m "docs: README" && git push origin main
```

- [ ] **Step 4: Save project memory** (auto-memory dir + claude-mem observation): repo URL, local path, install state, macOS-pending status.

## Self-review (done at write time)

- **Spec coverage:** model/cswap/alerts contracts → Tasks 2–4; entry point/--once → Task 5; Linux UI incl. icon-flip, title, menu, threading, notify fallback → Task 6; macOS UI → Task 7; install/autostart/uninstall → Task 8; README/docs → Task 9; error handling (3-strike gray `?`, stale menu marker, log cap) → Tasks 6 code; wide-icon fallback → Task 8 Step 3.
- **Placeholder scan:** none — all steps carry full code/commands. README content itemized (prose file, written at execution).
- **Type consistency:** `Metric(key,label,short,pct,resets_at,countdown,clock)`, `Account(number,email,org,active,ok,metrics)`, `Snapshot(accounts,fetched_at,schema_warning)` used identically across Tasks 2–7; `CswapError`, `fetch()`, `switch()`, `AlertManager.check()` consistent.
