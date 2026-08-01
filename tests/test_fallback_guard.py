"""Static policy, safe mutation plans, and CLI runner for fallback guard.

All filesystem cases live under TemporaryDirectory.  Administrator execution
is represented by injected fakes; this suite never invokes osascript and never
reads or writes the real /Library policy.
"""
import json
import hashlib
import contextlib
import io
import os
import plistlib
import runpy
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from smartbar.core import fallback_guard as guard
from smartbar import fallback_guard_runner as runner


class GuardTree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ClaudeCode"
        self.dropins = self.root / guard.DROPIN_NAME
        self.dropins.mkdir(parents=True)
        self.root.chmod(0o755)
        self.dropins.chmod(0o755)
        self.uid, self.gid = os.getuid(), os.getgid()

    @property
    def target(self):
        return self.dropins / guard.POLICY_NAME

    def write_bytes(self, path, content, mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)
        return path

    def install(self, content=None, mode=0o644):
        return self.write_bytes(self.target, content or guard.POLICY_BYTES, mode)

    def inspect(self, **kwargs):
        options = dict(managed_root=self.root, mdm_paths=(),
                       remote_path=Path(self.temp.name) / "remote-settings.json",
                       required_uid=self.uid, required_gid=self.gid,
                       platform="darwin", claude_version="2.1.220")
        options.update(kwargs)
        return guard.inspect_guard(**options)


class TestPolicyContract(GuardTree):
    def test_fragment_has_exactly_the_two_documented_controls(self):
        self.assertEqual(guard.POLICY, {
            "switchModelsOnFlag": False,
            "fallbackModel": [],
        })
        self.assertEqual(json.loads(guard.POLICY_BYTES), guard.POLICY)
        self.assertTrue(guard.POLICY_BYTES.endswith(b"\n"))

    def test_missing_fragment_is_not_protected(self):
        report = self.inspect()
        self.assertEqual(report["state"], "not_protected")
        self.assertFalse(report["protected"])
        self.assertEqual(report["safetyAutoFallback"], "unknown")
        self.assertEqual(report["availabilityAutoFallback"], "unknown")

    def test_secure_exact_fragment_blocks_both_paths_but_needs_live_check(self):
        self.install()
        report = self.inspect()
        self.assertTrue(report["ok"])
        self.assertTrue(report["protected"])
        self.assertEqual(report["state"], "protected_inconclusive")
        self.assertEqual(report["safetyAutoFallback"], "blocked")
        self.assertEqual(report["availabilityAutoFallback"], "blocked")
        self.assertFalse(report["manualOpusRestrictedByGuard"])
        self.assertEqual(report["activeManagedSource"], "file")
        self.assertEqual(report["scope"],
                         "local Claude Code sessions on this Mac")

    def test_matching_current_live_pass_makes_state_protected(self):
        self.install()
        live = {"status": "passed", "checkedAt": "2026-07-31T12:00:00Z",
                "claudeVersion": "2.1.220", "totalCostUsd": 0.1,
                "budgetLimitUsd": 0.25, "probes": []}
        report = self.inspect(last_live_check=live)
        self.assertEqual(report["state"], "protected")
        self.assertIs(report["lastLiveCheck"], live)

    def test_live_pass_from_old_claude_version_is_inconclusive(self):
        self.install()
        live = {"status": "passed", "checkedAt": "2026-07-31T12:00:00Z",
                "claudeVersion": "2.1.219", "probes": []}
        self.assertEqual(self.inspect(last_live_check=live)["state"],
                         "protected_inconclusive")

    def test_live_pass_without_current_version_is_inconclusive(self):
        self.install()
        live = {"status": "passed", "checkedAt": "2026-07-31T12:00:00Z",
                "claudeVersion": "2.1.220", "probes": []}
        self.assertEqual(self.inspect(last_live_check=live,
                                      claude_version="")["state"],
                         "protected_inconclusive")

    def test_symlink_fragment_is_never_followed_or_trusted(self):
        outside = self.write_bytes(Path(self.temp.name) / "outside.json",
                                   guard.POLICY_BYTES)
        self.target.symlink_to(outside)
        report = self.inspect()
        self.assertFalse(report["protected"])
        self.assertEqual(report["state"], "action_needed")
        self.assertTrue(any("symlink" in line for line in report["details"]))

    def test_malformed_and_non_object_json_are_action_needed(self):
        for content in (b"{broken", b"[]"):
            with self.subTest(content=content):
                self.write_bytes(self.target, content)
                report = self.inspect()
                self.assertFalse(report["protected"])
                self.assertEqual(report["state"], "action_needed")

    def test_extra_key_breaks_minimal_app_policy(self):
        self.install(json.dumps({**guard.POLICY,
                                 "availableModels": ["fable"]}).encode())
        report = self.inspect()
        self.assertFalse(report["protected"])
        self.assertTrue(any("exactly" in line for line in report["details"]))

    def test_wrong_mode_and_owner_requirement_fail_closed(self):
        self.install(mode=0o600)
        self.assertFalse(self.inspect()["protected"])
        self.target.chmod(0o644)
        self.assertFalse(self.inspect(required_uid=self.uid + 1000)["protected"])

    def test_semantically_exact_reformat_is_effective_but_not_removable(self):
        pretty = json.dumps(guard.POLICY, indent=2).encode() + b"\n"
        self.install(pretty)
        report = self.inspect()
        self.assertTrue(report["protected"])
        allowed, reason = guard.removal_allowed(
            managed_root=self.root, required_uid=self.uid,
            required_gid=self.gid)
        self.assertFalse(allowed)
        self.assertIn("modified", reason)


class TestMergeAndManagedSources(GuardTree):
    def test_earlier_conflict_is_shadowed_by_99_policy(self):
        self.write_bytes(self.dropins / "10-old.json", json.dumps({
            "switchModelsOnFlag": True,
            "fallbackModel": ["claude-opus-4-8"],
        }).encode())
        self.install()
        report = self.inspect()
        self.assertTrue(report["protected"])
        self.assertEqual(report["safetyAutoFallback"], "blocked")
        self.assertTrue(any("is overridden by" in row for row in report["details"]))

    def test_later_dropin_can_override_both_values(self):
        self.install()
        self.write_bytes(self.dropins / "zz-override.json", json.dumps({
            "switchModelsOnFlag": True,
            "fallbackModel": ["claude-opus-4-8"],
        }).encode())
        report = self.inspect()
        self.assertFalse(report["protected"])
        self.assertEqual(report["state"], "action_needed")
        self.assertEqual(report["safetyAutoFallback"], "enabled")
        self.assertEqual(report["availabilityAutoFallback"], "enabled")
        self.assertTrue(any("overrides" in row for row in report["details"]))

    def test_unreadable_later_source_makes_values_unknown(self):
        self.install()
        self.write_bytes(self.dropins / "zz-broken.json", b"{")
        report = self.inspect()
        self.assertFalse(report["protected"])
        self.assertEqual(report["safetyAutoFallback"], "unknown")
        self.assertEqual(report["availabilityAutoFallback"], "unknown")

    def test_malformed_base_also_fails_closed_even_though_target_is_later(self):
        self.write_bytes(self.root / guard.BASE_NAME, b"{")
        self.install()
        report = self.inspect()
        self.assertFalse(report["protected"])
        self.assertEqual(report["safetyAutoFallback"], "unknown")

    def test_policy_helper_is_never_executed_and_fails_closed(self):
        self.write_bytes(self.root / guard.BASE_NAME,
                         json.dumps({"policyHelper": {"path": "/tmp/helper"}}).encode())
        self.install()
        report = self.inspect()
        self.assertFalse(report["protected"])
        self.assertEqual(report["activeManagedSource"], "policyHelper")
        self.assertTrue(any("not executed" in row for row in report["details"]))

    def test_policy_helper_in_dropin_is_also_a_higher_source_indicator(self):
        self.install()
        self.write_bytes(self.dropins / "20-helper.json",
                         json.dumps({"policyHelper": {"path": "/tmp/helper"}}).encode())
        report = self.inspect()
        self.assertFalse(report["protected"])
        self.assertEqual(report["activeManagedSource"], "policyHelper")

    def test_readable_plist_with_both_values_can_protect(self):
        self.install()
        plist_path = Path(self.temp.name) / "managed.plist"
        with plist_path.open("wb") as handle:
            plistlib.dump({"Settings": json.dumps(guard.POLICY)}, handle)
        report = self.inspect(mdm_paths=(plist_path,))
        self.assertTrue(report["protected"])
        self.assertEqual(report["activeManagedSource"], "plist")
        self.assertEqual(report["state"], "protected_inconclusive")

    def test_plist_missing_one_guard_value_replaces_file_tier(self):
        self.install()
        plist_path = Path(self.temp.name) / "managed.plist"
        with plist_path.open("wb") as handle:
            plistlib.dump({"Settings": json.dumps({
                "switchModelsOnFlag": False})}, handle)
        report = self.inspect(mdm_paths=(plist_path,))
        self.assertFalse(report["protected"])
        self.assertEqual(report["safetyAutoFallback"], "blocked")
        self.assertEqual(report["availabilityAutoFallback"], "unknown")

    def test_malformed_plist_is_unknown_not_green(self):
        self.install()
        plist_path = self.write_bytes(Path(self.temp.name) / "managed.plist", b"nope")
        report = self.inspect(mdm_paths=(plist_path,))
        self.assertFalse(report["protected"])
        self.assertEqual(report["state"], "action_needed")
        self.assertEqual(report["safetyAutoFallback"], "unknown")

    def test_insecure_plist_indicator_is_unknown_not_trusted(self):
        self.install()
        plist_path = Path(self.temp.name) / "managed.plist"
        with plist_path.open("wb") as handle:
            plistlib.dump({"Settings": json.dumps(guard.POLICY)}, handle)
        plist_path.chmod(0o666)
        report = self.inspect(mdm_paths=(plist_path,))
        self.assertFalse(report["protected"])
        self.assertTrue(any("not securely" in row for row in report["details"]))

    def test_nonempty_remote_settings_outrank_plist_and_file(self):
        self.install()
        remote = self.write_bytes(Path(self.temp.name) / "remote.json",
                                  json.dumps(guard.POLICY).encode())
        report = self.inspect(remote_path=remote)
        self.assertTrue(report["protected"])
        self.assertEqual(report["activeManagedSource"], "remote")

    def test_good_remote_does_not_hide_malformed_visible_file_sibling(self):
        self.install()
        self.write_bytes(self.root / guard.BASE_NAME, b"{")
        remote = self.write_bytes(Path(self.temp.name) / "remote.json",
                                  json.dumps(guard.POLICY).encode())
        report = self.inspect(remote_path=remote)
        self.assertFalse(report["protected"])
        self.assertEqual(report["state"], "action_needed")

    def test_empty_remote_object_is_not_a_source_indicator(self):
        self.install()
        remote = self.write_bytes(Path(self.temp.name) / "remote.json", b"{}")
        report = self.inspect(remote_path=remote)
        self.assertTrue(report["protected"])
        self.assertEqual(report["activeManagedSource"], "file")

    def test_malformed_remote_cache_fails_closed(self):
        self.install()
        remote = self.write_bytes(Path(self.temp.name) / "remote.json", b"{")
        report = self.inspect(remote_path=remote)
        self.assertFalse(report["protected"])
        self.assertEqual(report["activeManagedSource"], "remote")
        self.assertEqual(report["safetyAutoFallback"], "unknown")

    def test_corrupt_dormant_target_stays_action_needed_under_good_remote(self):
        self.install(b"{")
        remote = self.write_bytes(Path(self.temp.name) / "remote.json",
                                  json.dumps(guard.POLICY).encode())
        report = self.inspect(remote_path=remote)
        self.assertFalse(report["protected"])
        self.assertEqual(report["state"], "action_needed")

    def test_corrupt_dormant_target_stays_action_needed_under_good_plist(self):
        self.install(b"{")
        plist_path = Path(self.temp.name) / "managed.plist"
        with plist_path.open("wb") as handle:
            plistlib.dump({"Settings": json.dumps(guard.POLICY)}, handle)
        report = self.inspect(mdm_paths=(plist_path,))
        self.assertFalse(report["protected"])
        self.assertEqual(report["state"], "action_needed")


class TestRemovalEligibility(GuardTree):
    def test_only_canonical_owned_fragment_is_removable(self):
        self.install()
        self.assertEqual(guard.removal_allowed(
            managed_root=self.root, required_uid=self.uid,
            required_gid=self.gid), (True, ""))

    def test_missing_is_idempotently_removable(self):
        self.assertEqual(guard.removal_allowed(
            managed_root=self.root, required_uid=self.uid,
            required_gid=self.gid), (True, ""))

    def test_symlink_is_refused_even_if_target_bytes_match(self):
        actual = self.write_bytes(Path(self.temp.name) / "actual", guard.POLICY_BYTES)
        self.target.symlink_to(actual)
        allowed, reason = guard.removal_allowed(
            managed_root=self.root, required_uid=self.uid,
            required_gid=self.gid)
        self.assertFalse(allowed)
        self.assertIn("non-symlink", reason)


class TestRootCommandPlans(unittest.TestCase):
    def test_enable_is_one_osascript_with_atomic_fixed_policy_install(self):
        plan = guard.enable_command()
        self.assertEqual(plan.argv[:2], ("/usr/bin/osascript", "-e"))
        self.assertNotIn("sudo", " ".join(plan.argv))
        self.assertIn("do shell script", plan.argv[2])
        self.assertIn("with administrator privileges", plan.argv[2])
        self.assertIn("/usr/bin/mktemp", plan.shell_script)
        self.assertIn("/bin/mv -f", plan.shell_script)
        self.assertIn("/usr/sbin/chown root:wheel", plan.shell_script)
        self.assertIn("/bin/chmod 0644", plan.shell_script)
        self.assertIn("/usr/bin/cmp -s", plan.shell_script)
        self.assertIn("refusing modified policy", plan.shell_script)
        self.assertIn(guard.POLICY_BYTES.decode().strip(), plan.shell_script)
        self.assertIn(str(guard.MANAGED_ROOT / guard.DROPIN_NAME / guard.POLICY_NAME),
                      plan.shell_script)

    def test_remove_revalidates_exact_file_and_has_native_confirmation(self):
        plan = guard.remove_command()
        self.assertEqual(plan.argv[0], "/usr/bin/osascript")
        self.assertIn("display dialog", plan.argv[2])
        self.assertIn("/usr/bin/stat", plan.shell_script)
        self.assertIn("/usr/bin/cmp -s", plan.shell_script)
        self.assertIn("[ ! -L", plan.shell_script)
        self.assertIn('[ ! -L "$root" ]', plan.shell_script)
        self.assertIn("/bin/rm -f \"$target\"", plan.shell_script)


class TestRunner(GuardTree):
    def _run(self, action, fake):
        state = str(Path(self.temp.name) / "state.json")
        remote = str(Path(self.temp.name) / "remote-settings.json")
        user_settings = str(Path(self.temp.name) / "settings.json")
        with mock.patch.object(runner.warmup_runner, "claude_binary",
                               return_value=None):
            return runner.run(
                action, managed_root=self.root, mdm_paths=(), remote_path=remote,
                required_uid=self.uid, required_gid=self.gid,
                platform="darwin", state_path=state,
                user_settings_path=user_settings, run_process=fake)

    def test_status_uses_exit_10_for_successful_unprotected_inspection(self):
        report, code = self._run("status", mock.Mock())
        self.assertTrue(report["ok"])
        self.assertEqual(code, runner.EXIT_NOT_PROTECTED)

    def test_enable_executes_exactly_one_admin_process_then_reinspects(self):
        calls = []

        def fake(argv, **kwargs):
            calls.append(argv)
            self.install()
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        report, code = self._run("enable", fake)
        self.assertEqual(code, 0)
        self.assertTrue(report["protected"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/usr/bin/osascript")

    def test_remove_never_calls_admin_for_modified_fragment(self):
        self.install(b'{"switchModelsOnFlag":false,"fallbackModel":[],"x":1}\n')
        fake = mock.Mock()
        report, code = self._run("remove", fake)
        self.assertEqual(code, runner.EXIT_INCONCLUSIVE)
        self.assertFalse(report["ok"])
        fake.assert_not_called()

    def test_enable_never_calls_admin_for_modified_fragment(self):
        self.install(b'{"switchModelsOnFlag":false,"fallbackModel":[],"x":1}\n')
        fake = mock.Mock()
        report, code = self._run("enable", fake)
        self.assertEqual(code, runner.EXIT_INCONCLUSIVE)
        self.assertFalse(report["ok"])
        fake.assert_not_called()

    def test_already_effective_enable_is_admin_free_clears_state_and_only_touches(self):
        self.install()
        state = Path(self.temp.name) / "state.json"
        state.write_text("stale")
        settings = Path(self.temp.name) / "settings.json"
        settings.write_bytes(b'{"theme":"dark"}\n')
        digest = hashlib.sha256(settings.read_bytes()).hexdigest()
        fake = mock.Mock()
        with mock.patch.object(runner.warmup_runner, "claude_binary",
                               return_value=None):
            report, code = runner.run(
                "enable", managed_root=self.root, mdm_paths=(),
                remote_path=Path(self.temp.name) / "remote.json",
                required_uid=self.uid, required_gid=self.gid,
                platform="darwin", state_path=str(state),
                user_settings_path=settings, run_process=fake)
        self.assertEqual(code, 0)
        self.assertTrue(report["protected"])
        fake.assert_not_called()
        self.assertFalse(state.exists())
        self.assertEqual(hashlib.sha256(settings.read_bytes()).hexdigest(), digest)

    def test_absent_remove_is_admin_free_and_successful(self):
        fake = mock.Mock()
        report, code = self._run("remove", fake)
        self.assertEqual(code, 0)
        self.assertFalse(report["protected"])
        fake.assert_not_called()

    def test_admin_cancel_maps_to_exit_2_and_json_report(self):
        def cancelled(argv, **kwargs):
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="execution error: User canceled. (-128)")

        report, code = self._run("enable", cancelled)
        self.assertEqual(code, runner.EXIT_INCONCLUSIVE)
        self.assertEqual(report["state"], "action_needed")
        self.assertIn("cancelled", report["details"][0])

    def test_previous_live_failure_does_not_block_verify_retry(self):
        self.install()
        failed = {"status": "failed", "checkedAt": "2026-07-31T12:00:00Z",
                  "claudeVersion": "2.1.220", "totalCostUsd": 0.1,
                  "budgetLimitUsd": 0.25, "probes": []}

        def verify_again(**kwargs):
            self.assertTrue(kwargs["static_guard_check"]())
            return {"status": "inconclusive"}

        with mock.patch.object(runner.fallback_guard_verify, "load_last_check",
                               return_value=failed), \
                mock.patch.object(runner.fallback_guard_verify,
                                  "run_verification",
                                  side_effect=verify_again) as invoked:
            report, code = self._run("verify", mock.Mock())
        invoked.assert_called_once()
        self.assertEqual(code, runner.EXIT_INCONCLUSIVE)
        self.assertEqual(report["state"], "action_needed")


class TestCliSource(unittest.TestCase):
    def test_launcher_exposes_exact_four_action_contract(self):
        text = (Path(__file__).resolve().parent.parent / "bin" / "ai-smartbar").read_text()
        self.assertIn('parser.add_argument("--fallback-guard"', text)
        self.assertIn('(\"status\", \"enable\", \"verify\", \"remove\")', text)
        self.assertIn("fallback_guard_runner.run(args.fallback_guard)", text)

    def test_unexpected_runner_exception_still_prints_full_json(self):
        launcher = Path(__file__).resolve().parent.parent / "bin" / "ai-smartbar"
        stdout = io.StringIO()
        argv = ["ai-smartbar", "--fallback-guard", "status"]
        with mock.patch("sys.argv", argv), \
                mock.patch.object(runner, "run", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(launcher), run_name="__main__")
        self.assertEqual(stopped.exception.code, 1)
        body = json.loads(stdout.getvalue())
        self.assertEqual(set(body), {
            "ok", "state", "protected", "safetyAutoFallback",
            "availabilityAutoFallback", "manualOpusRestrictedByGuard",
            "scope", "claudeVersion", "activeManagedSource", "policyPath",
            "details", "lastLiveCheck",
        })
        self.assertEqual(body["state"], "error")

    def test_no_pwd_module_still_builds_unsupported_report(self):
        with mock.patch.object(guard, "pwd", None):
            self.assertEqual(len(guard.default_mdm_paths()), 1)
            report = guard.inspect_guard(platform="win32")
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "unsupported")
        self.assertEqual(set(report), {
            "ok", "state", "protected", "safetyAutoFallback",
            "availabilityAutoFallback", "manualOpusRestrictedByGuard",
            "scope", "claudeVersion", "activeManagedSource", "policyPath",
            "details", "lastLiveCheck",
        })


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
