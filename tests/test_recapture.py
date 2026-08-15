"""Tests for smartbar.core.recapture — `cswap add` pacing, pure logic."""
import os
import re
import unittest

import smartbar
from smartbar.core import model, recapture

SWIFT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__))),
    "macos-swift", "Sources", "AISmartbar", "UsageStore.swift")


def snap(active=True, status="ok", accounts_extra=None):
    accounts = []
    if active is not None:
        metrics = [model.Metric(key="5h", label="5h", short="5h", pct=10.0)] \
            if status == "ok" else []
        accounts.append(model.Account(number=1, email="a@x.com", active=active,
                                      ok=status == "ok", status=status,
                                      metrics=metrics))
    accounts.extend(accounts_extra or [])
    return model.Snapshot(accounts=accounts)


class Env(unittest.TestCase):
    def setUp(self):
        for var in ("SMARTBAR_AUTO_ADD", "SMARTBAR_RECAPTURE"):
            os.environ.pop(var, None)
        self.policy = recapture.RecapturePolicy()

    tearDown = setUp


class TestRegister(Env):
    def test_unregistered_login_registers_with_cooldown(self):
        no_active = snap(active=False)
        self.assertEqual(self.policy.action(no_active, 0.0), "register")
        self.assertIsNone(self.policy.action(no_active, 10.0))  # cooldown holds
        self.assertEqual(
            self.policy.action(no_active, recapture.REGISTER_COOLDOWN + 1.0),
            "register")

    def test_auto_add_off_disables_everything(self):
        os.environ["SMARTBAR_AUTO_ADD"] = "off"
        self.assertIsNone(self.policy.action(snap(active=False), 0.0))
        self.assertIsNone(self.policy.action(snap(), 0.0))


class TestHeal(Env):
    def test_dead_active_backup_heals_promptly(self):
        dead = snap(status="relogin_required")
        self.assertEqual(self.policy.action(dead, 0.0), "heal")
        self.assertIsNone(self.policy.action(dead, 30.0))  # heal cooldown
        self.assertEqual(
            self.policy.action(dead, recapture.HEAL_COOLDOWN + 1.0), "heal")

    def test_heal_counts_as_the_periodic_refresh(self):
        self.assertEqual(self.policy.action(snap(status="relogin_required"), 0.0),
                         "heal")
        # Healed: the next healthy snapshot must not refresh again right away.
        self.assertIsNone(self.policy.action(snap(), 1.0))


class TestRefresh(Env):
    def test_first_healthy_snapshot_only_baselines(self):
        healthy = snap()
        self.assertIsNone(self.policy.action(healthy, 0.0))  # baseline, no add
        self.assertIsNone(self.policy.action(healthy, 60.0))
        self.assertEqual(
            self.policy.action(healthy, recapture.RECAPTURE_INTERVAL), "refresh")
        self.assertIsNone(
            self.policy.action(healthy, recapture.RECAPTURE_INTERVAL + 60.0))
        self.assertEqual(
            self.policy.action(healthy, recapture.RECAPTURE_INTERVAL * 2), "refresh")

    def test_registration_not_chased_by_refresh(self):
        # /login gets registered; the very next healthy snapshot must NOT
        # immediately run a second add (this was caught by e2e-autoadd).
        self.assertEqual(self.policy.action(snap(active=False), 0.0), "register")
        self.assertIsNone(self.policy.action(snap(), 1.0))

    def test_recapture_off_keeps_registration_only(self):
        os.environ["SMARTBAR_RECAPTURE"] = "off"
        self.assertIsNone(self.policy.action(snap(), 0.0))
        self.assertIsNone(self.policy.action(snap(status="relogin_required"), 0.0))
        self.assertEqual(self.policy.action(snap(active=False), 0.0), "register")


class TestSwiftPacingParity(unittest.TestCase):
    """The three cooldowns exist twice, in two languages. Pin them together.

    UsageStore.swift re-declares this module's pacing by hand and says so —
    its own comment reads "`cswap add` pacing (mirror of
    smartbar/core/recapture.py)" — and nothing enforced it. Drift here is
    invisible and expensive in both directions: too short and the Mac hammers
    `cswap add` against a logged-out state, too long and a dead credential
    goes unhealed for longer there than anywhere else.

    Same source-scraping shape as tests/test_cswap_parity.py and
    tests/test_model_parity.py, for the same reason: it runs in the ordinary
    unit suite with no Swift toolchain.
    """

    #: `private static let registerCooldown: TimeInterval = 600`
    _CONSTANT = re.compile(
        r"static let (\w+): TimeInterval = ([\d.]+)")

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(SWIFT_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")

    def swift_pacing(self):
        with open(SWIFT_SOURCE, encoding="utf-8") as handle:
            return {name: float(value)
                    for name, value in self._CONSTANT.findall(handle.read())}

    def test_the_scraper_actually_found_them(self):
        """Without this, a rename on the Swift side would turn every
        assertion below into a lookup of None against None."""
        found = self.swift_pacing()
        self.assertEqual(sorted(found), ["healCooldown", "recaptureInterval",
                                         "registerCooldown"])

    def test_every_cooldown_matches_the_python_policy(self):
        found = self.swift_pacing()
        for swift_name, value in (
                ("registerCooldown", recapture.REGISTER_COOLDOWN),
                ("healCooldown", recapture.HEAL_COOLDOWN),
                ("recaptureInterval", recapture.RECAPTURE_INTERVAL)):
            with self.subTest(constant=swift_name):
                self.assertEqual(found.get(swift_name), value,
                                 "%s drifted from recapture.py — the Mac "
                                 "would pace `cswap add` differently from "
                                 "every other front-end" % swift_name)


if __name__ == "__main__":
    unittest.main()
