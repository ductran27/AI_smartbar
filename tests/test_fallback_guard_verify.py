"""Deterministic tests for the paid Fable guard verifier.

No test invokes Claude.  Scripted ``CompletedProcess`` objects exercise the
same stream parser, model attribution, budget scheduler and atomic state writer
used by the live callable.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest import mock

from smartbar import fallback_guard_verify as guard


VERSION = "2.1.220"


def _line(event):
    return json.dumps(event, separators=(",", ":"))


def _stream(
    requested,
    served=None,
    *,
    cost=0.01,
    result_error=False,
    route=None,
    original=None,
    fallback=None,
    request_id="req_test",
    version=VERSION,
    result_text="DO_NOT_PERSIST_RESPONSE_TEXT",
    extra_lines=(),
    usage_models=None,
    session="session-test",
):
    events = [{
        "type": "system",
        "subtype": "init",
        "model": requested,
        "session_id": session,
        "claude_code_version": version,
    }]
    if route:
        event = {
            "type": "system",
            "subtype": route,
            "session_id": session,
            "request_id": request_id,
        }
        if original:
            event["original_model"] = original
        if fallback:
            event["fallback_model"] = fallback
        events.append(event)
    if served:
        events.append({
            "type": "assistant",
            "session_id": session,
            "request_id": request_id,
            "message": {
                "model": served,
                "stop_reason": "refusal" if served == "<synthetic>" else None,
                "content": [{"type": "text", "text": result_text}],
            },
        })
    events.extend(extra_lines)
    result = {
        "type": "result",
        "session_id": session,
        "is_error": result_error,
        "subtype": "success",
        "stop_reason": "refusal" if result_error else "end_turn",
        "result": result_text,
        "modelUsage": usage_models or {},
    }
    if cost is not None:
        result["total_cost_usd"] = cost
    events.append(result)
    return "\n".join(_line(event) for event in events) + "\n"


def _neutral(cost=0.01, version=VERSION):
    return _stream(
        guard.FABLE_MODEL,
        guard.FABLE_MODEL,
        cost=cost,
        version=version,
        result_text="CONTROL_OK",
        usage_models={
            guard.FABLE_MODEL: {"canonicalModel": guard.FABLE_MODEL},
            "claude-haiku-4-5-20251001": {
                "canonicalModel": "claude-haiku-4-5"
            },
        },
    )


def _positive(cost=0.005, version=VERSION):
    return _stream(
        guard.FABLE_MODEL,
        "<synthetic>",
        cost=cost,
        result_error=True,
        route=guard.SAFETY_NO_FALLBACK,
        original=guard.FABLE_MODEL,
        version=version,
        usage_models={
            guard.FABLE_MODEL: {"canonicalModel": guard.FABLE_MODEL},
            "claude-haiku-4-5-20251001": {
                "canonicalModel": "claude-haiku-4-5"
            },
        },
    )


def _opus(cost=0.02, version=VERSION):
    return _stream(
        guard.OPUS_MODEL,
        guard.OPUS_MODEL,
        cost=cost,
        version=version,
        result_text="OPUS_OK",
        usage_models={
            guard.OPUS_MODEL: {"canonicalModel": guard.OPUS_MODEL}
        },
    )


class ScriptedRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.cwd_snapshots = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        cwd = kwargs.get("cwd")
        if cwd:
            self.cwd_snapshots.append((
                cwd,
                os.path.isdir(cwd),
                tuple(os.listdir(cwd)) if os.path.isdir(cwd) else (),
            ))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestProbeContract(unittest.TestCase):
    def test_allocations_are_exactly_the_aggregate_limit(self):
        total = sum(
            (probe.max_budget_usd for probe in guard.DEFAULT_PROBES),
            Decimal("0"),
        )
        self.assertEqual(total, Decimal("0.25"))
        self.assertEqual(total, guard.BUDGET_LIMIT_USD)
        self.assertEqual(
            [probe.name for probe in guard.DEFAULT_PROBES],
            ["neutral_fable", "classifier_positive_fable", "manual_opus"],
        )

    def test_command_is_fresh_bounded_and_does_not_override_project_settings(self):
        spec = guard.DEFAULT_PROBES[0]
        command = guard.build_probe_command("/mock/claude", spec, "fresh-id")
        self.assertEqual(command[0], "/mock/claude")
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(command[command.index("--session-id") + 1], "fresh-id")
        self.assertEqual(command[command.index("--model") + 1], guard.FABLE_MODEL)
        self.assertEqual(command[command.index("--max-budget-usd") + 1], "0.05")
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--safe-mode", command)
        self.assertNotIn("--max-turns", command)
        self.assertNotIn("--settings", command)
        self.assertNotIn(spec.prompt, command)  # prompt travels through stdin

        opus = guard.build_probe_command(
            "/mock/claude", guard.DEFAULT_PROBES[2], "opus-session"
        )
        self.assertEqual(opus[opus.index("--model") + 1], "opus")

    def test_plan_is_non_paid_and_contains_no_prompt_text(self):
        plan = guard.verification_plan(claude_path="/mock/claude")
        encoded = json.dumps(plan)
        self.assertEqual(plan["budgetLimitUsd"], 0.25)
        self.assertEqual(len(plan["probes"]), 3)
        for spec in guard.DEFAULT_PROBES:
            self.assertNotIn(spec.prompt, encoded)
            self.assertNotIn(spec.system_prompt, encoded)


class TestStructuredParser(unittest.TestCase):
    def test_model_attribution_keeps_serving_and_auxiliary_models_distinct(self):
        parsed = guard.parse_stream_json(_neutral())
        self.assertEqual(parsed.protocol_errors, ())
        self.assertEqual(parsed.requested_model, guard.FABLE_MODEL)
        self.assertEqual(parsed.serving_models, (guard.FABLE_MODEL,))
        self.assertIn(guard.FABLE_MODEL, parsed.observed_models)
        self.assertIn("claude-haiku-4-5", parsed.observed_models)
        self.assertEqual(parsed.cost_usd, Decimal("0.01"))

    def test_malformed_line_fails_closed(self):
        parsed = guard.parse_stream_json(_neutral() + "not-json\n")
        self.assertIn("line_4_invalid_json", parsed.protocol_errors)
        evaluation = guard.evaluate_probe(guard.DEFAULT_PROBES[0], parsed, 0)
        self.assertEqual(evaluation.grade, guard.STATUS_INCONCLUSIVE)
        self.assertEqual(evaluation.outcome, "PROTOCOL_ERROR")

    def test_missing_and_duplicate_result_fail_closed(self):
        lines = _neutral().splitlines()
        missing = guard.parse_stream_json("\n".join(lines[:-1]) + "\n")
        self.assertIn("expected_one_result_got_0", missing.protocol_errors)
        duplicate = guard.parse_stream_json(
            _neutral() + lines[-1] + "\n"
        )
        self.assertIn("expected_one_result_got_2", duplicate.protocol_errors)

    def test_mixed_session_ids_fail_closed(self):
        rogue = {
            "type": "assistant",
            "session_id": "another-session",
            "message": {"model": guard.FABLE_MODEL, "content": []},
        }
        parsed = guard.parse_stream_json(
            _stream(guard.FABLE_MODEL, guard.FABLE_MODEL, extra_lines=(rogue,))
        )
        self.assertIn("mixed_session_ids", parsed.protocol_errors)

    def test_missing_requested_model_and_model_usage_fail_closed(self):
        events = [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "s",
                "claude_code_version": VERSION,
            },
            {
                "type": "assistant",
                "session_id": "s",
                "message": {
                    "model": guard.FABLE_MODEL,
                    "content": [{"type": "text", "text": "CONTROL_OK"}],
                },
            },
            {
                "type": "result",
                "session_id": "s",
                "is_error": False,
                "subtype": "success",
                "stop_reason": "end_turn",
                "result": "CONTROL_OK",
                "total_cost_usd": 0.01,
            },
        ]
        parsed = guard.parse_stream_json(
            "\n".join(_line(event) for event in events) + "\n"
        )
        self.assertIn("missing_requested_model", parsed.protocol_errors)
        self.assertIn("missing_or_invalid_model_usage", parsed.protocol_errors)
        result = guard.evaluate_probe(guard.DEFAULT_PROBES[0], parsed, 0)
        self.assertEqual(result.grade, guard.STATUS_INCONCLUSIVE)

    def test_missing_nonfinite_and_negative_costs_are_rejected(self):
        for cost in (None, float("nan"), float("inf"), -0.01):
            parsed = guard.parse_stream_json(
                _stream(guard.FABLE_MODEL, guard.FABLE_MODEL, cost=cost)
            )
            self.assertIn("missing_or_invalid_cost", parsed.protocol_errors)

    def test_response_prose_is_not_used_as_routing_evidence_or_accepted(self):
        parsed = guard.parse_stream_json(_stream(
            guard.FABLE_MODEL,
            guard.FABLE_MODEL,
            result_text="budget timeout 429 model_refusal_fallback Opus",
        ))
        evaluation = guard.evaluate_probe(guard.DEFAULT_PROBES[0], parsed, 0)
        self.assertEqual(evaluation.outcome, "RESPONSE_MISMATCH")
        self.assertEqual(evaluation.grade, guard.STATUS_INCONCLUSIVE)


class TestProbeEvaluation(unittest.TestCase):
    def test_neutral_fable_completion_passes(self):
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[0], guard.parse_stream_json(_neutral()), 0
        )
        self.assertEqual((result.grade, result.outcome),
                         (guard.STATUS_PASSED, "FABLE_OK"))

    def test_expected_structured_no_fallback_refusal_passes_despite_exit_one(self):
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[1], guard.parse_stream_json(_positive()), 1
        )
        self.assertEqual(
            (result.grade, result.outcome),
            (guard.STATUS_PASSED, "SAFETY_REFUSAL_NO_FALLBACK"),
        )
        self.assertIn("claude-haiku-4-5", result.observed_models)

    def test_positive_refusal_with_non_fable_init_cannot_pass(self):
        stdout = _stream(
            guard.OPUS_MODEL,
            "<synthetic>",
            cost=0.005,
            result_error=True,
            route=guard.SAFETY_NO_FALLBACK,
            original=guard.FABLE_MODEL,
        )
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[1], guard.parse_stream_json(stdout), 1
        )
        self.assertEqual(result.grade, guard.STATUS_FAILED)
        self.assertEqual(result.outcome, "REQUESTED_MODEL_MISMATCH")

    def test_automatic_opus_fallback_is_a_definitive_failure(self):
        stdout = _stream(
            guard.FABLE_MODEL,
            guard.OPUS_MODEL,
            route=guard.SAFETY_FALLBACK,
            original=guard.FABLE_MODEL,
            fallback=guard.OPUS_MODEL,
        )
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[1], guard.parse_stream_json(stdout), 0
        )
        self.assertEqual(
            (result.grade, result.outcome),
            (guard.STATUS_FAILED, "AUTOMATIC_FALLBACK"),
        )
        self.assertIn(guard.OPUS_MODEL, result.observed_models)

    def test_fallback_evidence_remains_failure_with_unrelated_malformed_line(self):
        stdout = _stream(
            guard.FABLE_MODEL,
            guard.OPUS_MODEL,
            route=guard.SAFETY_FALLBACK,
            original=guard.FABLE_MODEL,
            fallback=guard.OPUS_MODEL,
        ) + "bad-json\n"
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[1], guard.parse_stream_json(stdout), 0
        )
        self.assertEqual(result.grade, guard.STATUS_FAILED)

    def test_classifier_not_triggering_is_inconclusive_not_a_guard_failure(self):
        allowed = _stream(
            guard.FABLE_MODEL,
            guard.FABLE_MODEL,
            result_text="SPLIT_OK",
        )
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[1], guard.parse_stream_json(allowed), 0
        )
        self.assertEqual(
            (result.grade, result.outcome),
            (guard.STATUS_INCONCLUSIVE, "CLASSIFIER_NOT_TRIGGERED"),
        )

    def test_saved_availability_route_is_a_failure(self):
        stdout = _stream(
            guard.FABLE_MODEL,
            guard.FABLE_MODEL,
            route="model_fallback",
            original=guard.FABLE_MODEL,
            result_text="CONTROL_OK",
        )
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[0], guard.parse_stream_json(stdout), 0
        )
        self.assertEqual(result.grade, guard.STATUS_FAILED)
        self.assertEqual(result.outcome, "AVAILABILITY_FALLBACK")

    def test_usage_route_without_opus_evidence_is_inconclusive(self):
        stdout = _stream(
            guard.FABLE_MODEL,
            guard.FABLE_MODEL,
            route="model_consent_fallback",
            original=guard.FABLE_MODEL,
            result_text="CONTROL_OK",
        )
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[0], guard.parse_stream_json(stdout), 0
        )
        self.assertEqual(result.grade, guard.STATUS_INCONCLUSIVE)
        self.assertEqual(result.outcome, "USAGE_FALLBACK")

    def test_opus_in_model_usage_fails_a_fable_probe_even_if_fable_answered(self):
        stdout = _stream(
            guard.FABLE_MODEL,
            guard.FABLE_MODEL,
            result_text="CONTROL_OK",
            usage_models={
                guard.FABLE_MODEL: {"canonicalModel": guard.FABLE_MODEL},
                "claude-opus-5": {"canonicalModel": "claude-opus-5"},
            },
        )
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[0], guard.parse_stream_json(stdout), 0
        )
        self.assertEqual(result.grade, guard.STATUS_FAILED)
        self.assertEqual(result.outcome, "AUTOMATIC_FALLBACK")

    def test_sonnet_in_model_usage_also_fails_a_fable_probe(self):
        stdout = _stream(
            guard.FABLE_MODEL,
            guard.FABLE_MODEL,
            result_text="CONTROL_OK",
            usage_models={
                guard.FABLE_MODEL: {"canonicalModel": guard.FABLE_MODEL},
                "claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"},
            },
        )
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[0], guard.parse_stream_json(stdout), 0
        )
        self.assertEqual(result.grade, guard.STATUS_FAILED)
        self.assertEqual(result.outcome, "AUTOMATIC_FALLBACK")

    def test_manual_opus_completion_passes(self):
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[2], guard.parse_stream_json(_opus()), 0
        )
        self.assertEqual((result.grade, result.outcome),
                         (guard.STATUS_PASSED, "OPUS_OK"))

    def test_manual_opus_served_by_fable_fails(self):
        stdout = _stream(
            guard.OPUS_MODEL, guard.FABLE_MODEL, result_text="OPUS_OK"
        )
        result = guard.evaluate_probe(
            guard.DEFAULT_PROBES[2], guard.parse_stream_json(stdout), 0
        )
        self.assertEqual(result.grade, guard.STATUS_FAILED)
        self.assertEqual(result.outcome, "SERVED_MODEL_MISMATCH")


class TestLiveOrchestration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_path = os.path.join(self.temp.name, "cache", "state.json")
        self.when = datetime(2026, 7, 31, 12, 34, 56, tzinfo=timezone.utc)

    def runner(self, *probe_results):
        return ScriptedRunner(
            _completed("2.1.220 (Claude Code)\n"),
            *probe_results,
        )

    def test_three_passes_persist_exact_metadata_only(self):
        scripted = self.runner(
            _completed(_neutral()),
            _completed(_positive(), returncode=1),
            _completed(_opus()),
        )
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(state["status"], "passed")
        self.assertEqual(state["checkedAt"], "2026-07-31T12:34:56Z")
        self.assertEqual(state["claudeVersion"], "2.1.220 (Claude Code)")
        self.assertAlmostEqual(state["totalCostUsd"], 0.035)
        self.assertEqual(state["budgetLimitUsd"], 0.25)
        self.assertEqual(
            [probe["outcome"] for probe in state["probes"]],
            ["FABLE_OK", "SAFETY_REFUSAL_NO_FALLBACK", "OPUS_OK"],
        )

        self.assertEqual(len(scripted.calls), 4)  # version + three paid probes
        sessions = []
        for (command, kwargs), spec in zip(scripted.calls[1:], guard.DEFAULT_PROBES):
            self.assertEqual(kwargs["input"], spec.prompt)
            sessions.append(command[command.index("--session-id") + 1])
        self.assertEqual(len(set(sessions)), 3)
        self.assertEqual(len(scripted.cwd_snapshots), 3)
        self.assertEqual(len({item[0] for item in scripted.cwd_snapshots}), 3)
        for cwd, existed, entries in scripted.cwd_snapshots:
            self.assertTrue(existed)
            self.assertEqual(entries, ())
            self.assertNotEqual(cwd, os.path.abspath(self.temp.name))
            self.assertFalse(os.path.exists(cwd))  # cleaned after each probe

        with open(self.state_path, encoding="utf-8") as handle:
            raw_state = handle.read()
        self.assertEqual(json.loads(raw_state), state)
        self.assertEqual(
            set(state),
            {"status", "checkedAt", "claudeVersion", "totalCostUsd",
             "budgetLimitUsd", "probes"},
        )
        for probe in state["probes"]:
            self.assertTrue(
                set(probe).issubset(
                    {"name", "outcome", "requestedModel", "observedModels",
                     "costUsd", "requestId"}
                )
            )
        for spec in guard.DEFAULT_PROBES:
            self.assertNotIn(spec.prompt, raw_state)
            self.assertNotIn(spec.system_prompt, raw_state)
        self.assertNotIn("DO_NOT_PERSIST_RESPONSE_TEXT", raw_state)
        self.assertEqual(guard.load_last_check(self.state_path), state)

    def test_earlier_allocation_overshoot_stops_before_next_paid_probe(self):
        scripted = self.runner(_completed(_neutral(cost=0.06)))
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(len(scripted.calls), 2)  # version + neutral only
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(state["totalCostUsd"], 0.06)
        self.assertEqual(
            [probe["outcome"] for probe in state["probes"]],
            ["FABLE_OK", "NOT_RUN_BUDGET_GUARD", "NOT_RUN_BUDGET_GUARD"],
        )

    def test_missing_cost_stops_all_further_spending(self):
        no_cost = _stream(guard.FABLE_MODEL, guard.FABLE_MODEL, cost=None)
        scripted = self.runner(_completed(no_cost))
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(len(scripted.calls), 2)
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(
            [probe["outcome"] for probe in state["probes"]],
            ["COST_UNKNOWN", "NOT_RUN_COST_UNKNOWN", "NOT_RUN_COST_UNKNOWN"],
        )

    def test_timeout_stops_all_further_spending_and_persists_inconclusive(self):
        timeout = subprocess.TimeoutExpired(["claude"], 180)
        scripted = self.runner(timeout)
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(len(scripted.calls), 2)
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(state["probes"][0]["outcome"], "TIMEOUT")
        self.assertEqual(state["probes"][1]["outcome"], "NOT_RUN_COST_UNKNOWN")

    def test_definitive_fallback_marks_whole_check_failed(self):
        fallback = _stream(
            guard.FABLE_MODEL,
            guard.OPUS_MODEL,
            route=guard.SAFETY_FALLBACK,
            original=guard.FABLE_MODEL,
            fallback=guard.OPUS_MODEL,
            cost=0.005,
        )
        scripted = self.runner(
            _completed(_neutral()),
            _completed(fallback),
            _completed(_opus()),
        )
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["probes"][1]["outcome"], "AUTOMATIC_FALLBACK")
        self.assertIn(guard.OPUS_MODEL, state["probes"][1]["observedModels"])
        self.assertEqual(state["probes"][2]["outcome"], "NOT_RUN_AFTER_FAILURE")
        self.assertEqual(len(scripted.calls), 3)  # version + neutral + positive

    def test_neutral_fable_failure_stops_before_both_later_probes(self):
        fallback = _stream(
            guard.FABLE_MODEL,
            "claude-sonnet-5",
            route="model_fallback",
            original=guard.FABLE_MODEL,
            fallback="claude-sonnet-5",
            cost=0.01,
        )
        scripted = self.runner(_completed(fallback))
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(len(scripted.calls), 2)  # version + neutral only
        self.assertEqual(state["status"], "failed")
        self.assertEqual(
            [probe["outcome"] for probe in state["probes"]],
            ["AUTOMATIC_FALLBACK", "NOT_RUN_AFTER_FAILURE",
             "NOT_RUN_AFTER_FAILURE"],
        )

    def test_version_mismatch_prevents_a_pass(self):
        scripted = self.runner(
            _completed(_neutral(version="2.1.221")),
            _completed(_positive(version="2.1.221"), returncode=1),
            _completed(_opus(version="2.1.221")),
        )
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(state["claudeVersion"], "2.1.220 (Claude Code)")

    def test_version_prefix_is_not_mistaken_for_an_exact_match(self):
        scripted = self.runner(
            _completed(_neutral(version="2.1.22")),
            _completed(_positive(version="2.1.22"), returncode=1),
            _completed(_opus(version="2.1.22")),
        )
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(state["claudeVersion"], "2.1.220 (Claude Code)")

    def test_unknown_version_falls_back_to_consistent_init_version(self):
        scripted = ScriptedRunner(
            _completed(returncode=1),
            _completed(_neutral()),
            _completed(_positive(), returncode=1),
            _completed(_opus()),
        )
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: True,
            now=self.when,
        )
        self.assertEqual(state["status"], "passed")
        self.assertEqual(state["claudeVersion"], VERSION)

    def test_unprotected_static_guard_refuses_all_processes(self):
        scripted = ScriptedRunner()
        state = guard.run_verification(
            state_path=self.state_path,
            claude_path="/mock/claude",
            run_process=scripted,
            static_guard_check=lambda: False,
            now=self.when,
        )
        self.assertEqual(scripted.calls, [])
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(
            {probe["outcome"] for probe in state["probes"]},
            {"NOT_RUN_STATIC_GUARD_UNPROTECTED"},
        )
        self.assertTrue(os.path.exists(self.state_path))

    def test_default_static_preflight_uses_inspect_guard_protected_bit(self):
        with mock.patch(
            "smartbar.core.fallback_guard.inspect_guard",
            return_value={"protected": True},
        ) as inspect:
            self.assertTrue(guard._installed_guard_is_protected())
        inspect.assert_called_once_with()

        with mock.patch(
            "smartbar.core.fallback_guard.inspect_guard",
            return_value={"protected": False},
        ):
            self.assertFalse(guard._installed_guard_is_protected())


class TestStateLoading(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "state.json")

    def test_missing_corrupt_and_extra_metadata_are_not_trusted(self):
        self.assertEqual(guard.load_last_check(self.path), {})
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("not-json")
        self.assertEqual(guard.load_last_check(self.path), {})

        state = {
            "status": "passed",
            "checkedAt": "2026-07-31T12:00:00Z",
            "claudeVersion": VERSION,
            "totalCostUsd": 0.03,
            "budgetLimitUsd": 0.25,
            "probes": [
                {
                    "name": spec.name,
                    "outcome": "OK",
                    "requestedModel": spec.model,
                    "observedModels": [spec.model],
                    "costUsd": 0.01,
                }
                for spec in guard.DEFAULT_PROBES
            ],
            "responseText": "must never be accepted",
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        self.assertEqual(guard.load_last_check(self.path), {})


if __name__ == "__main__":
    unittest.main()
