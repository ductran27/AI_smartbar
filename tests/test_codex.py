"""OpenAI/Codex provider semantics: claims, rate limits, registry, parity.

Everything reads fixture files under a tmp SMARTBAR_CODEX_HOME — the suite
never touches the real ~/.codex and never handles real tokens.
"""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartbar.core import codex


def _b64(obj) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(obj).encode())
    return raw.decode().rstrip("=")


def _jwt(claims) -> str:
    """A structurally valid JWT with a junk signature — claims only."""
    return f"{_b64({'alg': 'RS256'})}.{_b64(claims)}.junksig"


def _write_auth(home: Path, email="a@x.com", plan="pro", mode="chatgpt"):
    claims = {"email": email,
              "https://api.openai.com/auth": {"chatgpt_plan_type": plan}}
    body = {"auth_mode": mode, "tokens": {"id_token": _jwt(claims)}}
    (home / "auth.json").write_text(json.dumps(body))


class TestPlanLabel(unittest.TestCase):
    def test_known_plans_map_to_display_names(self):
        for raw, label in (("free", "Free"), ("plus", "Plus"), ("pro", "Pro"),
                           ("prolite", "Pro Lite"), ("team", "Team"),
                           ("enterprise", "Enterprise"), ("edu", "Edu"),
                           ("business", "Business")):
            self.assertEqual(codex.plan_label(raw), label)

    def test_unknown_plans_title_case_and_empty_means_no_badge(self):
        self.assertEqual(codex.plan_label("plusplus"), "Plusplus")
        self.assertEqual(codex.plan_label(""), "")
        self.assertEqual(codex.plan_label(None), "")


class _CodexHome(unittest.TestCase):
    """Base: tmp codex home + tmp cache (registry), wired through the seams."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "codex"
        self.cache = Path(self.tmp.name) / "cache"
        self.home.mkdir()
        patch = mock.patch.dict(os.environ, {
            "SMARTBAR_CODEX_HOME": str(self.home),
            "SMARTBAR_CACHE_DIR": str(self.cache)})
        patch.start()
        self.addCleanup(patch.stop)


class TestLogin(_CodexHome):
    def test_reads_email_and_plan_from_the_claims(self):
        _write_auth(self.home, "duc@x.com", "prolite")
        self.assertEqual(codex.login(), ("duc@x.com", "Pro Lite"))

    def test_apikey_mode_has_no_chatgpt_login(self):
        (self.home / "auth.json").write_text(json.dumps(
            {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test-not-real"}))
        self.assertIsNone(codex.login())

    def test_missing_or_corrupt_auth_is_none(self):
        self.assertIsNone(codex.login())
        (self.home / "auth.json").write_text("{not json")
        self.assertIsNone(codex.login())
        (self.home / "auth.json").write_text(json.dumps(
            {"tokens": {"id_token": "only.two"}}))
        self.assertIsNone(codex.login())

    def test_kill_switch(self):
        _write_auth(self.home)
        with mock.patch.dict(os.environ, {"SMARTBAR_OPENAI": "off"}):
            self.assertFalse(codex.enabled())
            self.assertEqual(codex.accounts(), [])
        self.assertTrue(codex.enabled())


FUTURE = 4102444800      # 2100-01-01: a reset that has not happened yet
PAST = 1000000           # 1970: a window long since reset


def _win(minutes, pct, resets=FUTURE):
    return {"window_minutes": minutes, "used_percent": pct,
            "resets_at": resets}


def _rl(lid="codex", primary=None, secondary=None):
    return {"limit_id": lid, "primary": primary, "secondary": secondary}


def _write_rollout(home: Path, name, events, pad=0):
    path = home / "sessions" / "2026" / "07" / "25" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"junk": "x" * pad})] if pad else []
    for ts, rl in events:
        lines.append(json.dumps({"timestamp": ts, "payload": {
            "type": "token_count", "rate_limits": rl}}))
    path.write_text("\n".join(lines) + "\n")
    return path


class TestRateLimits(_CodexHome):
    def limits(self, cutoff=""):
        return codex.rate_limits(str(self.home), cutoff=cutoff)

    def test_general_windows_map_to_5h_and_7d(self):
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00.000Z",
             _rl(primary=_win(300, 12.5), secondary=_win(10080, 30.0)))])
        metrics, measured = self.limits()
        self.assertEqual(metrics["5h"]["pct"], 12.5)
        self.assertEqual(metrics["7d"]["pct"], 30.0)
        self.assertEqual(metrics["7d"]["label"], "7d")
        self.assertEqual(measured, "2026-07-25T10:00:00.000Z")

    def test_scoped_limit_prefers_its_weekly_window(self):
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z",
             _rl("codex_bengalfox", _win(300, 50.0), _win(10080, 75.0)))])
        metrics, _ = self.limits()
        self.assertEqual(metrics["scoped:Bengalfox"]["pct"], 75.0)
        self.assertEqual(metrics["scoped:Bengalfox"]["label"], "Bengalfox")
        self.assertNotIn("5h", metrics)

    def test_latest_per_window_survives_secondary_omission(self):
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z",
             _rl(primary=_win(300, 10.0), secondary=_win(10080, 20.0))),
            ("2026-07-25T11:00:00Z", _rl(primary=_win(10080, 25.0)))])
        metrics, measured = self.limits()
        self.assertEqual(metrics["5h"]["pct"], 10.0)   # kept from the older event
        self.assertEqual(metrics["7d"]["pct"], 25.0)   # newest wins
        self.assertEqual(measured, "2026-07-25T11:00:00Z")

    def test_expired_window_reads_as_idle_zero(self):
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 85.0, PAST)))])
        metrics, _ = self.limits()
        self.assertEqual(metrics["5h"]["pct"], 0.0)
        self.assertEqual(metrics["5h"]["resets_at"], "")

    def test_live_resets_at_round_trips_into_a_countdown(self):
        from smartbar.core.reset_countdown_format import remaining_text
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 40.0)))])
        metrics, _ = self.limits()
        resets = metrics["5h"]["resets_at"]
        self.assertTrue(resets.endswith("Z"))
        self.assertNotEqual(remaining_text(resets), "")

    def test_tail_of_a_file_bigger_than_the_window_still_parses(self):
        _write_rollout(self.home, "rollout-big.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 33.0)))],
            pad=codex.TAIL_BYTES + 50_000)
        metrics, _ = self.limits()
        self.assertEqual(metrics["5h"]["pct"], 33.0)

    def test_corrupt_lines_and_limitless_events_are_skipped(self):
        path = _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 21.0)))])
        with path.open("a") as handle:
            handle.write("{broken rate_limit json\n")
            handle.write(json.dumps({"timestamp": "2026-07-25T11:00:00Z",
                                     "payload": {"type": "token_count",
                                                 "rate_limits": _rl()}}) + "\n")
        metrics, _ = self.limits()
        self.assertEqual(metrics["5h"]["pct"], 21.0)

    def test_cutoff_excludes_older_events(self):
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 90.0))),
            ("2026-07-25T12:00:00Z", _rl(primary=_win(10080, 15.0)))])
        metrics, measured = self.limits(cutoff="2026-07-25T11:00:00Z")
        self.assertNotIn("5h", metrics)      # earlier login's traffic
        self.assertEqual(metrics["7d"]["pct"], 15.0)
        self.assertEqual(measured, "2026-07-25T12:00:00Z")

    def test_mtime_bump_invalidates_the_scan_cache(self):
        path = _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 10.0)))])
        self.assertEqual(self.limits()[0]["5h"]["pct"], 10.0)
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:30:00Z", _rl(primary=_win(300, 60.0)))])
        os.utime(path, (os.stat(path).st_atime, os.stat(path).st_mtime + 5))
        self.assertEqual(self.limits()[0]["5h"]["pct"], 60.0)


class TestAccounts(_CodexHome):
    def test_cold_start_attributes_existing_history_to_the_live_login(self):
        _write_auth(self.home, "a@x.com", "pro")
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 42.0)))])
        accts = codex.accounts()
        self.assertEqual(len(accts), 1)
        acct = accts[0]
        self.assertEqual((acct.email, acct.plan, acct.active, acct.status,
                          acct.provider),
                         ("a@x.com", "Pro", True, "ok", "openai"))
        self.assertEqual([(m.key, m.pct) for m in acct.metrics],
                         [("5h", 42.0)])

    def test_login_change_freezes_the_predecessor(self):
        _write_auth(self.home, "a@x.com", "pro")
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z",
             _rl(primary=_win(300, 42.0, PAST), secondary=_win(10080, 61.0)))])
        codex.accounts()
        _write_auth(self.home, "b@x.com", "plus")
        accts = codex.accounts()
        self.assertEqual([a.email for a in accts], ["b@x.com", "a@x.com"])
        new, old = accts
        self.assertTrue(new.active)
        # Nothing after the cutoff yet: the new login has no bars.
        self.assertEqual(new.metrics, [])
        self.assertFalse(old.active)
        self.assertEqual(old.status, "signed_out")
        # The frozen card keeps only windows that have not reset since:
        # the expired 5h row is dropped, the still-running 7d row stays.
        self.assertEqual([(m.key, m.pct) for m in old.metrics],
                         [("7d", 61.0)])

    def test_plan_badge_refreshes_from_the_claim(self):
        _write_auth(self.home, "a@x.com", "pro")
        codex.accounts()
        _write_auth(self.home, "a@x.com", "prolite")
        self.assertEqual(codex.accounts()[0].plan, "Pro Lite")

    def test_registry_holds_labels_and_numbers_only(self):
        _write_auth(self.home, "a@x.com", "pro")
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 42.0)))])
        codex.accounts()
        raw = (self.cache / "openai-accounts.json").read_text().lower()
        for needle in ("token", "refresh_", "access_", "sk-", "key"):
            self.assertNotIn(needle, raw, needle)

    def test_unchanged_state_skips_the_registry_write(self):
        _write_auth(self.home, "a@x.com", "pro")
        codex.accounts()
        path = self.cache / "openai-accounts.json"
        stamp = (path.stat().st_mtime_ns, path.read_text())
        codex.accounts()
        self.assertEqual((path.stat().st_mtime_ns, path.read_text()), stamp)

    def test_payload_is_display_ready(self):
        _write_auth(self.home, "a@x.com", "prolite")
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 42.0)))])
        body = codex.payload()
        self.assertEqual(len(body["accounts"]), 1)
        acct = body["accounts"][0]
        self.assertEqual(acct["email"], "a@x.com")
        self.assertEqual(acct["plan"], "Pro Lite")
        self.assertTrue(acct["active"])
        self.assertEqual(acct["status"], "ok")
        self.assertIn("stateText", acct)
        self.assertEqual(acct["metrics"][0]["key"], "5h")
        self.assertEqual(acct["metrics"][0]["pct"], 42.0)
        self.assertTrue(acct["metrics"][0]["resetsAt"].endswith("Z"))
        self.assertEqual(acct["updatedAt"], "2026-07-25T10:00:00Z")


import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent


class TestOpenAICli(_CodexHome):
    def test_openai_json_prints_the_display_ready_payload(self):
        _write_auth(self.home, "a@x.com", "prolite")
        _write_rollout(self.home, "rollout-a.jsonl", [
            ("2026-07-25T10:00:00Z", _rl(primary=_win(300, 42.0)))])
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "ai-smartbar"),
             "--openai", "--json"],
            capture_output=True, text=True, env=dict(os.environ), timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertEqual(body["accounts"][0]["email"], "a@x.com")
        self.assertEqual(body["accounts"][0]["plan"], "Pro Lite")
        self.assertEqual(body["accounts"][0]["metrics"][0]["pct"], 42.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
