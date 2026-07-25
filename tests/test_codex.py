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
    """Base: a tmp codex home wired through the env seam."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ,
                                {"SMARTBAR_CODEX_HOME": str(self.home)})
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
