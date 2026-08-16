"""Tests for smartbar.core.account_removal — the one removal entry point.

Both UIs remove accounts through this dispatcher (`ai-smartbar
--remove-account provider:id`), so the guard rules — the active account is
refused, unknown targets are named, nothing is ever half-done — are pinned
here once instead of per platform. The cswap side is mocked (tests never
touch real slots); the OpenAI side runs against a temp registry.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartbar.core import account_removal, codex, cswap

REPO = Path(__file__).resolve().parent.parent


class TestDispatcher(unittest.TestCase):
    def test_claude_spec_routes_to_cswap_by_slot_number(self):
        with mock.patch.object(cswap, "remove_account") as removed:
            ok, error = account_removal.remove("claude:2")
        self.assertEqual((ok, error), (True, ""))
        removed.assert_called_once_with(2)

    def test_openai_spec_routes_to_codex_by_email(self):
        with mock.patch.object(codex, "remove_account") as removed:
            ok, error = account_removal.remove("openai:a@x.com")
        self.assertEqual((ok, error), (True, ""))
        removed.assert_called_once_with("a@x.com")

    def test_unknown_provider_is_refused(self):
        ok, error = account_removal.remove("gemini:a@x.com")
        self.assertFalse(ok)
        self.assertIn("gemini", error)

    def test_claude_wants_a_slot_number_not_an_email(self):
        # Emails can hit cswap's interactive ambiguity prompt; the UIs
        # always send the slot number, and the dispatcher enforces it.
        ok, error = account_removal.remove("claude:a@x.com")
        self.assertFalse(ok)
        self.assertIn("slot number", error)

    def test_empty_openai_identifier_is_refused(self):
        ok, error = account_removal.remove("openai:")
        self.assertFalse(ok)

    def test_provider_errors_come_back_as_text_not_raises(self):
        with mock.patch.object(cswap, "remove_account",
                               side_effect=cswap.CswapError("slot in use")):
            ok, error = account_removal.remove("claude:2")
        self.assertEqual((ok, error), (False, "slot in use"))
        with mock.patch.object(codex, "remove_account",
                               side_effect=ValueError("no such account")):
            ok, error = account_removal.remove("openai:a@x.com")
        self.assertEqual((ok, error), (False, "no such account"))


class TestRemoveAccountCli(unittest.TestCase):
    """`--remove-account` end to end against a temp OpenAI registry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.env = dict(os.environ,
                        SMARTBAR_CACHE_DIR=str(self.cache),
                        SMARTBAR_CODEX_HOME=str(self.cache / "codex"))

    def run_cli(self, spec):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "ai-smartbar"),
             "--remove-account", spec],
            capture_output=True, text=True, env=self.env, timeout=30)

    def test_openai_removal_updates_the_registry_and_reports_ok(self):
        (self.cache / "openai-accounts.json").write_text(json.dumps({
            "active": "live@x.com",
            "accounts": {"live@x.com": {"lastSeen": "2026-07-27T00:00:00Z"},
                         "old@x.com": {"lastSeen": "2026-07-01T00:00:00Z"}}}))
        proc = self.run_cli("openai:old@x.com")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"ok": True, "error": ""})
        reg = json.loads((self.cache / "openai-accounts.json").read_text())
        self.assertNotIn("old@x.com", reg["accounts"])
        self.assertIn("live@x.com", reg["accounts"])

    def test_the_live_codex_login_is_refused_with_exit_1(self):
        (self.cache / "openai-accounts.json").write_text(json.dumps({
            "active": "live@x.com",
            "accounts": {"live@x.com": {"lastSeen": "2026-07-27T00:00:00Z"}}}))
        proc = self.run_cli("openai:live@x.com")
        self.assertEqual(proc.returncode, 1)
        body = json.loads(proc.stdout)
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])
        self.assertIn("live@x.com",
                      json.loads((self.cache / "openai-accounts.json")
                                 .read_text())["accounts"])

    def test_bad_spec_reports_the_usage_shape(self):
        proc = self.run_cli("nonsense")
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(json.loads(proc.stdout)["ok"])


SWIFT_DIR = REPO / "macos-swift" / "Sources" / "AISmartbar"


class TestRemovalParity(unittest.TestCase):
    """Pin the removal decisions that exist in more than one language
    (source-scrape, runs without a Swift toolchain — the TestPlanParity
    trick)."""

    @classmethod
    def setUpClass(cls):
        cls.card = (SWIFT_DIR / "AccountCardView.swift").read_text(
            encoding="utf-8")
        cls.all_swift = "".join(
            p.read_text(encoding="utf-8")
            for p in sorted(SWIFT_DIR.glob("*.swift")))

    def test_swift_confirm_question_matches_the_shared_layout(self):
        """Both languages must name the account in FULL when asking.

        This assertion used to pin the bare `account.email`, which is the
        BUG it now guards against: at 12pt semibold "Remove
        duc.dut.wr@gmail.com?" needs 189pt in the 182pt the one-row confirm
        header had, so an ordinary address was middle-truncated to
        "Remove duc.d…r@gmail.com?" — the identity elided at exactly the
        moment the user has to be certain what they are deleting. Both
        sides now ask with the full account label (address · plan), which
        is also what tells two similar addresses apart.
        """
        self.assertIn('Remove \\(accountLabel)?', self.card)
        # accountLabel must be the plain-string twin of model.account_label,
        # not a second opinion about what an account is called.
        self.assertIn("private var accountLabel: String", self.card)
        layout = (REPO / "smartbar/core/popover_layout.py").read_text(
            encoding="utf-8")
        self.assertIn('f"Remove {model.account_label(account)}?"', layout)

    def test_neither_language_asks_with_a_bare_email(self):
        """The regression above is one edit away in either language."""
        layout = (REPO / "smartbar/core/popover_layout.py").read_text(
            encoding="utf-8")
        self.assertNotIn('Remove \\(account.email)?', self.card)
        self.assertNotIn('f"Remove {account.email}?"', layout)

    def test_swift_removes_through_the_launcher_not_cswap(self):
        # ONE SHARED ANSWER: the active-account guard and both providers'
        # semantics live in core/account_removal.py; Swift only runs the
        # launcher flag and shows the answer.
        self.assertIn("--remove-account", self.all_swift)

    def test_both_python_trays_route_the_confirm_action(self):
        for path in ("smartbar/linux/tray.py", "smartbar/windows/tray.py"):
            text = (REPO / path).read_text(encoding="utf-8")
            self.assertIn("confirm-remove", text, path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
