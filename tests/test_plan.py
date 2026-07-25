"""Plan badge semantics: mapping, reader, stamping, cross-language parity."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartbar.core import model, plan


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
