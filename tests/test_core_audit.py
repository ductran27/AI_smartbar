"""Audit-driven core pins (2026-08-24, batch B6): cswap venv discovery,
codex windows, rounding parity, clock detection."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from smartbar.core import codex, cswap, model, reset_countdown_format


class TestVenvPythonFindsPlainShebangs(unittest.TestCase):
    """distlib writes `#!/path/bin/python` when the path has no space; the
    quoted-exec regex alone made every standard Linux install silently skip
    the primed near-live data path."""

    def _binary_with(self, first_line):
        tmp = tempfile.mkdtemp()
        launcher = os.path.join(tmp, "cswap")
        python = os.path.join(tmp, "venvs", "claude-swap", "bin", "python")
        os.makedirs(os.path.dirname(python))
        with open(python, "w") as fh:
            fh.write("")
        with open(launcher, "w") as fh:
            fh.write(first_line.replace("PYPATH", python) + "\nrest\n")
        return launcher, python

    def test_plain_shebang_is_recognised(self):
        launcher, python = self._binary_with("#!PYPATH")
        with mock.patch.object(cswap, "_binary", return_value=launcher):
            self.assertEqual(cswap.venv_python(), python)

    def test_quoted_exec_still_works(self):
        launcher, python = self._binary_with(
            "#!/bin/sh\n'''exec' 'PYPATH' \"$0\" \"$@\"")
        with mock.patch.object(cswap, "_binary", return_value=launcher):
            self.assertEqual(cswap.venv_python(), python)

    def test_windows_probes_current_pipx_and_uv_layouts(self):
        probed = []
        env = {"LOCALAPPDATA": r"C:\Users\d\AppData\Local",
               "APPDATA": r"C:\Users\d\AppData\Roaming"}
        with mock.patch.object(cswap.sys, "platform", "win32"), \
             mock.patch.dict(os.environ, env), \
             mock.patch.object(cswap.os.path, "exists",
                               lambda p: probed.append(p) or False):
            cswap.venv_python()
        joined = "\n".join(probed).replace("\\", "/")
        self.assertIn("pipx/pipx/venvs/claude-swap", joined)
        self.assertIn("uv/data/tools/claude-swap", joined)


class TestCodexWindows(unittest.TestCase):
    def test_window_key_rounds_real_codex_minutes(self):
        # Real rollouts report 299 and 10079 — flooring called them 4h/6d.
        self.assertEqual(codex._window_key(299), "5h")
        self.assertEqual(codex._window_key(300), "5h")
        self.assertEqual(codex._window_key(10079), "7d")
        self.assertEqual(codex._window_key(10080), "7d")
        self.assertEqual(codex._window_key(90), "2h")
        self.assertEqual(codex._window_key(30), "1h")

    def _limits(self, **window):
        base = {"used_percent": 42.0, "window_minutes": 300}
        base.update(window)
        return {"limit_id": "codex", "primary": base, "secondary": None}

    def test_resets_in_seconds_only_payloads_are_not_zeroed(self):
        # 886 of the local rollouts carry only resets_in_seconds; treating
        # them as already-reset showed 0% while the user sat at 42%.
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        events = [("2026-08-24T11:59:00Z",
                   self._limits(resets_in_seconds=3600))]
        with mock.patch.object(codex, "_recent_rollouts",
                               return_value=["r.jsonl"]), \
             mock.patch.object(codex, "_events", lambda path: events):
            metrics, _ = codex.rate_limits(home="/x", now=now)
        self.assertEqual(metrics["5h"]["pct"], 42.0)
        self.assertNotEqual(metrics["5h"]["resets_at"], "")

    def test_scoped_label_prefers_the_model_name_codex_supplies(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        limits = {"limit_id": "codex_bengalfox",
                  "limit_name": "GPT-5.3-Codex-Spark",
                  "primary": {"used_percent": 10.0, "window_minutes": 10080,
                              "resets_at": now.timestamp() + 3600},
                  "secondary": None}
        with mock.patch.object(codex, "_recent_rollouts",
                               return_value=["r.jsonl"]), \
             mock.patch.object(codex, "_events",
                               lambda path: [("2026-08-24T11:59:00Z", limits)]):
            metrics, _ = codex.rate_limits(home="/x", now=now)
        scoped = [k for k in metrics if k.startswith("scoped:")]
        self.assertEqual(metrics[scoped[0]]["label"], "GPT-5.3-Codex-Spark")

    def test_measured_only_advances_on_windowed_events(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        events = [
            ("2026-08-20T10:00:00Z", self._limits(
                resets_at=now.timestamp() + 3600)),
            ("2026-08-23T10:00:00Z", {"limit_id": "premium",
                                      "primary": None, "secondary": None}),
        ]
        with mock.patch.object(codex, "_recent_rollouts",
                               return_value=["r.jsonl"]), \
             mock.patch.object(codex, "_events", lambda path: events):
            _, measured = codex.rate_limits(home="/x", now=now)
        self.assertEqual(measured, "2026-08-20T10:00:00Z")

    def test_active_login_zeroes_a_window_whose_reset_passed(self):
        # README: "a window whose reset time passes while idle reads 0%".
        now = datetime.now(timezone.utc)
        entry = {"metrics": {"7d": {
            "label": "7d", "short": "7d", "pct": 61.0,
            "resets_at": (now - timedelta(days=2)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")}}}
        rows = codex._rows(entry, active=True, now=now)
        self.assertEqual(rows[0].pct, 0.0)
        self.assertEqual(rows[0].resets_at, "")

    def test_registry_temp_names_are_unique_per_write(self):
        seen = []
        cache = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"SMARTBAR_CACHE_DIR": cache}), \
             mock.patch.object(codex.os, "replace",
                               lambda tmp, path: seen.append(tmp)):
            codex._save_registry({"a": 1})
            codex._save_registry({"a": 2})
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1],
                            "pid-keyed temp names collide across threads")
        for tmp in seen:
            try:
                os.unlink(tmp)
            except OSError:
                pass


class TestRoundingParity(unittest.TestCase):
    def test_used_pct_rounds_half_away_like_swift(self):
        self.assertEqual(model.used_pct(12.5), 13)
        self.assertEqual(model.used_pct(0.5), 1)
        self.assertEqual(model.used_pct(2.5), 3)
        self.assertEqual(model.used_pct(-3.0), 0)

    def test_display_strings_use_it(self):
        acct = model.Account(number=1, email="a@x.com", active=True, metrics=[
            model.Metric(key="5h", label="5h", short="5h", pct=12.5,
                         resets_at="", countdown="")])
        self.assertIn("13", model.icon_text(acct))
        self.assertIn("13%", model.metrics_text(acct))


class TestClockDetection(unittest.TestCase):
    def test_windows_reads_locale_itime_not_idate(self):
        source = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "smartbar", "core", "reset_countdown_format.py")).read()
        self.assertIn("0x00000023", source)      # LOCALE_ITIME
        self.assertNotIn("0x00000021, buf", source)

    def test_glibc_en_us_percent_r_counts_as_12_hour(self):
        # glibc en_US t_fmt is the alias "%r" — no literal %I in it, so the
        # substring test called the US 24-hour.
        with mock.patch.object(reset_countdown_format, "_posix_time_format",
                               return_value="%r"):
            self.assertFalse(reset_countdown_format.prefers_24_hour_clock())

    def test_explicit_24_hour_format_still_wins(self):
        with mock.patch.object(reset_countdown_format, "_posix_time_format",
                               return_value="%H:%M:%S"):
            self.assertTrue(reset_countdown_format.prefers_24_hour_clock())

    def test_override_env_still_first(self):
        with mock.patch.dict(os.environ, {"SMARTBAR_CLOCK": "12"}):
            self.assertFalse(reset_countdown_format.prefers_24_hour_clock())


if __name__ == "__main__":
    unittest.main()
