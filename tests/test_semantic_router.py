from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.models import LifecycleStatus
from src.merlin_harness.provisioning import make_single_step_skill
from src.merlin_harness.semantic_router import (
    CodexCliSemanticRouter,
    SemanticRouterError,
    SemanticRouterErrorCode,
    SemanticRouterResult,
    build_router_prompt,
    parse_router_result,
    safe_skill_catalog,
    validate_router_result,
)


def skill(skill_id="report", *, status=LifecycleStatus.ACTIVE):
    item = make_single_step_skill(
        skill_id=skill_id,
        name="Report writer",
        description="Create a concise report",
        trigger="write a report",
        step_description="SECRET FULL BODY PROCEDURE",
        status=status,
    )
    item.steps[0].inputs = ["source.txt"]
    item.steps[0].outputs = ["report.md"]
    item.expected_artifacts = ["report.md"]
    item.validators = ["file exists"]
    item.failure_modes = ["missing report"]
    return item


def router_jsonl(result, *, model="gpt-5.6-terra"):
    events = [
        {"type": "thread.started", "thread_id": "router-thread", "model": model},
        {"type": "turn.started", "turn_id": "router-turn"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(result)}},
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(item) for item in events) + "\n"


class SemanticRouterContractTests(unittest.TestCase):
    def test_catalog_and_prompt_exclude_full_body_and_mark_untrusted_data(self):
        item = skill()
        catalog = safe_skill_catalog([item])
        prompt = build_router_prompt(query="보고서를 작성해", skills=[item], exposure_budget=1)
        self.assertEqual(catalog[0]["id"], "report")
        self.assertNotIn("SECRET FULL BODY PROCEDURE", prompt)
        self.assertIn("UNTRUSTED_ROUTING_DATA", prompt)
        self.assertIn("보고서를 작성해", prompt)
        self.assertNotIn("steps", catalog[0])

    def test_strict_result_validates_abstain_unknown_duplicate_hidden_and_budget(self):
        active = skill()
        hidden = skill("hidden", status=LifecycleStatus.HIDDEN)
        valid = parse_router_result(
            json.dumps({"ranked_ids": [], "excluded_ids": ["report"], "abstain": True}),
            skills=[active], exposure_budget=1, requested_model_id="m", requested_effort="low",
        )
        self.assertTrue(valid.abstained)
        cases = (
            ({"ranked_ids": ["missing"], "excluded_ids": [], "abstain": False}, SemanticRouterErrorCode.UNKNOWN_SKILL_ID),
            ({"ranked_ids": ["report", "report"], "excluded_ids": [], "abstain": False}, SemanticRouterErrorCode.DUPLICATE_SKILL_ID),
            ({"ranked_ids": ["hidden"], "excluded_ids": [], "abstain": False}, SemanticRouterErrorCode.INACTIVE_SKILL_ID),
            ({"ranked_ids": ["report", "other"], "excluded_ids": [], "abstain": False}, SemanticRouterErrorCode.BUDGET_EXCEEDED),
        )
        other = skill("other")
        for payload, code in cases:
            with self.subTest(code=code), self.assertRaises(SemanticRouterError) as caught:
                parse_router_result(json.dumps(payload), skills=[active, hidden, other], exposure_budget=1, requested_model_id="m", requested_effort="low")
            self.assertEqual(caught.exception.code, code)

    def test_cli_is_ephemeral_read_only_stdin_only_and_preserves_raw_hash(self):
        calls = []
        raw = router_jsonl({"ranked_ids": ["report"], "excluded_ids": [], "abstain": False})
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, raw, "")
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            trace = workspace / "trace"
            trace.mkdir()
            router = CodexCliSemanticRouter(executable="/bin/echo", cli_version="test", workspace=workspace, trace_root=trace, runner=runner)
            result = router.route(query="보고서를 작성해", skills=[skill()], exposure_budget=1, turn_number=1)
            command, kwargs = calls[0]
            self.assertIn("--ephemeral", command)
            self.assertIn("read-only", command)
            self.assertNotIn("보고서를 작성해", command)
            self.assertIn("보고서를 작성해", kwargs["input"])
            raw_path = trace / result.raw_trace_pointer
            self.assertEqual(result.raw_trace_sha256, hashlib.sha256(raw_path.read_bytes()).hexdigest())

    def test_cli_timeout_and_provider_model_mismatch_fail_with_safe_codes(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 1, output=b'{"type":"turn.started"}\n', stderr=b"timeout")
        cases = (
            (timeout, SemanticRouterErrorCode.TIMEOUT),
            (lambda command, **kwargs: subprocess.CompletedProcess(command, 0, router_jsonl({"ranked_ids": ["report"], "excluded_ids": [], "abstain": False}, model="other"), ""), SemanticRouterErrorCode.PROVIDER_MODEL_MISMATCH),
        )
        for index, (runner, code) in enumerate(cases, 1):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                trace = workspace / "trace"
                trace.mkdir()
                router = CodexCliSemanticRouter(
                    executable="/bin/echo",
                    cli_version="test",
                    workspace=workspace,
                    trace_root=trace,
                    model_id="expected-model",
                    runner=runner,
                )
                with self.assertRaises(SemanticRouterError) as caught:
                    router.route(query="safe query", skills=[skill()], exposure_budget=1, turn_number=index)
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(list(trace.glob("router-turn-*.codex.jsonl")))

    def test_cli_malformed_and_oversize_outputs_fail_safely_after_raw_save(self):
        cases = (
            ("{broken\n", SemanticRouterErrorCode.MALFORMED_JSONL),
            ("x" * 1_000_001, SemanticRouterErrorCode.OVERSIZE),
        )
        for index, (stdout, code) in enumerate(cases, 10):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                trace = workspace / "trace"
                trace.mkdir()
                router = CodexCliSemanticRouter(
                    executable="/bin/echo", cli_version="test", workspace=workspace,
                    trace_root=trace,
                    runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout, ""),
                )
                with self.assertRaises(SemanticRouterError) as caught:
                    router.route(query="query", skills=[skill()], exposure_budget=1, turn_number=index)
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(list(trace.glob("router-turn-*.codex.jsonl")))

    def test_adapter_result_model_effort_and_raw_trace_contract_are_revalidated(self):
        base = SemanticRouterResult(("report",), (), False, "wrong-model", "high")
        with self.assertRaises(SemanticRouterError) as caught:
            validate_router_result(base, skills=[skill()], exposure_budget=1, expected_model_id="gpt-5.6-terra", expected_effort="low")
        self.assertEqual(caught.exception.code, SemanticRouterErrorCode.ROUTER_CONTRACT_MISMATCH)
        unsafe = SemanticRouterResult(("report",), (), False, "gpt-5.6-terra", "low", raw_trace_pointer="../../raw.jsonl", raw_trace_sha256="0" * 64)
        with self.assertRaises(SemanticRouterError) as caught:
            validate_router_result(unsafe, skills=[skill()], exposure_budget=1, expected_model_id="gpt-5.6-terra", expected_effort="low")
        self.assertEqual(caught.exception.code, SemanticRouterErrorCode.RAW_TRACE_CONTRACT)

    def test_cli_nonzero_and_missing_result_have_distinct_safe_codes(self):
        missing = "\n".join(json.dumps(item) for item in (
            {"type": "thread.started", "thread_id": "r", "model": "gpt-5.6-terra"},
            {"type": "turn.started", "turn_id": "t"},
            {"type": "turn.completed"},
        )) + "\n"
        cases = (
            (subprocess.CompletedProcess([], 2, "partial", "failure"), SemanticRouterErrorCode.SUBPROCESS),
            (subprocess.CompletedProcess([], 0, missing, ""), SemanticRouterErrorCode.MISSING_RESULT),
        )
        for index, (completed, code) in enumerate(cases, 20):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                trace = workspace / "trace"
                trace.mkdir()
                router = CodexCliSemanticRouter(executable="/bin/echo", cli_version="test", workspace=workspace, trace_root=trace, runner=lambda *args, _completed=completed, **kwargs: _completed)
                with self.assertRaises(SemanticRouterError) as caught:
                    router.route(query="query", skills=[skill()], exposure_budget=1, turn_number=index)
                self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
