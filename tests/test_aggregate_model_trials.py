from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from experiments.skillsbench.aggregate_model_trials import aggregate_model_trials


PROMPT_SHA = "1" * 64
TASK_SHA = PROMPT_SHA
SKILL_SHA = "3" * 64
IMAGE_ID = "sha256:" + "4" * 64
MODEL_ID = "claude-sonnet-4-5"
PROMPT_CONTRACT = {
    "task_user_message": "task_md_body_only",
    "execution_contract_source": "provider_tool_schema",
    "prompt_equals_task_instruction": True,
}
ACCOUNT_AUTH = {
    "logged_in": True,
    "auth_method": "claude.ai",
    "api_provider": "firstParty",
    "subscription_type": "max",
}
PROVIDER_BUILTINS = ["batch", "claude-api", "doctor"]


def _command(argv: list[str], stdout: str) -> dict[str, Any]:
    return {
        "argv": argv,
        "exit_code": 0,
        "duration_sec": 0.1,
        "timed_out": False,
        "stdout_tail": stdout,
        "stderr_tail": "",
    }


def _record(trial_index: int, arm: str) -> dict[str, Any]:
    return {
        "task_id": "weighted-gdp-calc",
        "condition_id": "claude-sonnet",
        "arm": arm,
        "trial_index": trial_index,
        "harness_mode": "agentic_workspace",
        "backend_type": "B_cli",
        "backend": "claude",
        "model_id": MODEL_ID,
        "effort": "high",
        "runtime_effort": "high",
        "auth_mode": "user_owned_account",
        "credential_forwarded_to_container": False,
        "execution_bridge": "host_account_cli_to_mcp_bound_task_container",
        "status": "passed" if arm == "C1" else "reward_failed",
        "passed": arm == "C1",
        "reward": 1.0 if arm == "C1" else 0.0,
        "wall_time_sec": 10.0,
        "prompt_sha256": PROMPT_SHA,
        "task_instruction_sha256": TASK_SHA,
        "skill_delivery": (
            {
                "mode": "none",
                "file_count": 0,
                "total_bytes": 0,
                "sha256": None,
            }
            if arm == "C0"
            else {
                "mode": "complete_bundle_provider_native_and_container_read_only",
                "file_count": 2,
                "total_bytes": 100,
                "sha256": SKILL_SHA,
            }
        ),
        "configuration_audit": {
            "passed": True,
            "verifier_invocation_count": 1,
            "account_auth": copy.deepcopy(ACCOUNT_AUTH),
            "provider_builtin_skills": list(PROVIDER_BUILTINS),
            "expected_model_id": MODEL_ID,
            "resolved_assistant_models": [MODEL_ID],
            "resolved_model_matches_request": True,
        },
        "control_barrier": {
            "passed": True,
            "task_event": {"count": 1},
            "warmup_model_turn_count": 0,
        },
        "tool_trace": {"assistant_models": [MODEL_ID]},
        "container_exposure": {"passed": True},
        "commands": {
            "backend_version": _command(["claude", "--version"], "2.1.205"),
            "image_id": _command(
                ["docker", "image", "inspect", "-f", "{{.Id}}", "task:latest"],
                IMAGE_ID,
            ),
            "verifier": _command(["python", "/tmp/verifier.py"], "27 passed"),
        },
    }


def _summary(trial_index: int) -> dict[str, Any]:
    version = _command(["claude", "--version"], "2.1.205")
    return {
        "run_id": f"trial-{trial_index}",
        "harness_mode": "agentic_workspace",
        "prompt_contract": copy.deepcopy(PROMPT_CONTRACT),
        "benchmark_eligibility": {
            "skillsbench_paper_c0_c1": False,
            "paper_aligned_agentic_pilot": True,
            "reasons": ["temperature=0 is unavailable in the provider CLI"],
        },
        "backend_contract": {
            "type": "B_cli",
            "auth_mode": "user_owned_account",
            "api_keys_required": False,
            "credentials_forwarded_to_container": False,
        },
        "configuration_isolation": {
            "fresh_home_per_arm": True,
            "strict_explicit_mcp_config": True,
        },
        "image_contract": {
            "same_container_agent_and_verifier": True,
            "credentials_embedded": False,
        },
        "trial_control": {"trial_index": trial_index},
        "backend_versions": {"claude-sonnet": version},
        "records": [_record(trial_index, "C0"), _record(trial_index, "C1")],
    }


class StrictBCliAggregationTests(unittest.TestCase):
    def _write(self, root: Path, summaries: list[dict[str, Any]]) -> list[Path]:
        paths: list[Path] = []
        for index, summary in enumerate(summaries, start=1):
            run_root = root / f"run-{index}"
            run_root.mkdir()
            path = run_root / "summary.json"
            path.write_text(json.dumps(summary), encoding="utf-8")
            paths.append(path)
        return paths

    def _aggregate(self, summaries: list[dict[str, Any]]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            return aggregate_model_trials(
                self._write(Path(tmp), summaries),
                expected_trials=3,
            )

    def test_accepts_three_complete_consistent_pairs(self) -> None:
        result = self._aggregate([_summary(1), _summary(2), _summary(3)])

        self.assertTrue(result["complete"])
        self.assertTrue(result["data_contract_complete"])
        self.assertFalse(result["paper_eligible"])
        self.assertEqual(
            result["benchmark_eligibility"],
            {
                "data_contract_complete": True,
                "paper_eligible": False,
                "reasons": ["temperature=0 is unavailable in the provider CLI"],
            },
        )
        self.assertEqual(result["paired_reward_delta_observed"], 3)
        self.assertEqual(result["paired_mean_reward_delta_c1_minus_c0"], 1.0)

    def test_paper_eligibility_requires_every_source_to_opt_in(self) -> None:
        summaries = [_summary(1), _summary(2), _summary(3)]
        for summary in summaries:
            summary["benchmark_eligibility"] = {
                "skillsbench_paper_c0_c1": True,
                "reasons": [],
            }
        result = self._aggregate(summaries)
        self.assertTrue(result["data_contract_complete"])
        self.assertTrue(result["paper_eligible"])
        self.assertEqual(result["benchmark_eligibility"]["reasons"], [])

        summaries[1]["benchmark_eligibility"] = {
            "skillsbench_paper_c0_c1": False,
            "reasons": ["one source used a distinct evaluation cell"],
        }
        result = self._aggregate(summaries)
        self.assertFalse(result["paper_eligible"])
        self.assertEqual(
            result["benchmark_eligibility"]["reasons"],
            ["one source used a distinct evaluation cell"],
        )

    def test_rejects_control_barrier_contract_failures(self) -> None:
        mutations = {
            "barrier failed": lambda record: record["control_barrier"].update(passed=False),
            "task event count": lambda record: record["control_barrier"]["task_event"].update(
                count=2
            ),
            "warmup model turn": lambda record: record["control_barrier"].update(
                warmup_model_turn_count=1
            ),
        }
        for case, mutate in mutations.items():
            with self.subTest(case=case):
                summaries = [_summary(1), _summary(2), _summary(3)]
                mutate(summaries[1]["records"][0])
                with self.assertRaisesRegex(ValueError, "control barrier/task-event/warmup"):
                    self._aggregate(summaries)

    def test_rejects_resolved_model_mismatch_or_ambiguous_models(self) -> None:
        mutations = {
            "wrong model": lambda record: record["tool_trace"].update(
                assistant_models=["claude-opus-4-1"]
            ),
            "multiple models": lambda record: record["tool_trace"].update(
                assistant_models=[MODEL_ID, "claude-opus-4-1"]
            ),
            "audit mismatch": lambda record: record["configuration_audit"].update(
                resolved_assistant_models=["claude-opus-4-1"],
                resolved_model_matches_request=False,
            ),
        }
        for case, mutate in mutations.items():
            with self.subTest(case=case):
                summaries = [_summary(1), _summary(2), _summary(3)]
                mutate(summaries[1]["records"][0])
                with self.assertRaisesRegex(
                    ValueError, "resolved assistant model must exactly match requested model"
                ):
                    self._aggregate(summaries)

    def test_rejects_builtin_skill_or_account_auth_drift(self) -> None:
        cases = {
            "builtin drift": (
                lambda record: record["configuration_audit"].update(
                    provider_builtin_skills=["batch", "doctor"]
                ),
                "provider_builtin_skills exact set mismatch",
            ),
            "unsafe auth": (
                lambda record: record["configuration_audit"]["account_auth"].update(
                    auth_method="api_key"
                ),
                "unsafe or incomplete account_auth evidence",
            ),
            "auth drift": (
                lambda record: record["configuration_audit"]["account_auth"].update(
                    subscription_type="pro"
                ),
                "account_auth mismatch",
            ),
        }
        for case, (mutate, message) in cases.items():
            with self.subTest(case=case):
                summaries = [_summary(1), _summary(2), _summary(3)]
                mutate(summaries[1]["records"][0])
                with self.assertRaisesRegex(ValueError, message):
                    self._aggregate(summaries)

    def test_rejects_failed_verifier_or_inconsistent_terminal_state(self) -> None:
        cases = {
            "nonzero verifier": (
                lambda record: record["commands"]["verifier"].update(exit_code=1),
                "verifier must exit 0 without timeout",
            ),
            "timed out verifier": (
                lambda record: record["commands"]["verifier"].update(timed_out=True),
                "verifier must exit 0 without timeout",
            ),
            "contract inconsistent status": (
                lambda record: record.update(status="verifier_contract_inconsistent"),
                "reward/passed/status contract is inconsistent",
            ),
            "passed flag disagrees": (
                lambda record: record.update(passed=True),
                "reward/passed/status contract is inconsistent",
            ),
        }
        for case, (mutate, message) in cases.items():
            with self.subTest(case=case):
                summaries = [_summary(1), _summary(2), _summary(3)]
                mutate(summaries[1]["records"][0])
                with self.assertRaisesRegex(ValueError, message):
                    self._aggregate(summaries)

    def test_accepts_consistent_partial_reward_terminal_state(self) -> None:
        summaries = [_summary(1), _summary(2), _summary(3)]
        summaries[1]["records"][0].update(
            reward=0.25,
            passed=False,
            status="reward_partial",
        )
        result = self._aggregate(summaries)
        self.assertTrue(result["data_contract_complete"])

    def test_rejects_non_body_only_prompt_or_run_prompt_contract(self) -> None:
        summaries = [_summary(1), _summary(2), _summary(3)]
        summaries[1]["records"][0]["prompt_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            ValueError, "body-only prompt_sha256 must equal task_instruction_sha256"
        ):
            self._aggregate(summaries)

        summaries = [_summary(1), _summary(2), _summary(3)]
        summaries[1]["prompt_contract"]["extra_wrapper"] = True
        with self.assertRaisesRegex(ValueError, "prompt_contract must exactly match"):
            self._aggregate(summaries)

    def test_rejects_unscored_or_configuration_invalid_record(self) -> None:
        for case in ("reward", "configuration"):
            with self.subTest(case=case):
                summaries = [_summary(1), _summary(2), _summary(3)]
                if case == "reward":
                    summaries[1]["records"][0]["reward"] = None
                else:
                    summaries[1]["records"][0]["configuration_audit"]["passed"] = False
                with self.assertRaisesRegex(ValueError, "invalid or unscored B_cli record"):
                    self._aggregate(summaries)

    def test_rejects_prompt_task_or_skill_hash_mismatch(self) -> None:
        mutations = {
            "prompt_sha256 mismatch": lambda summaries: summaries[1]["records"][0].update(
                prompt_sha256="a" * 64
            ),
            "task_instruction_sha256 mismatch": lambda summaries: summaries[1]["records"][0].update(
                task_instruction_sha256="b" * 64
            ),
            "skill manifest sha256 mismatch": lambda summaries: summaries[1]["records"][1][
                "skill_delivery"
            ].update(sha256="c" * 64),
        }
        for message, mutate in mutations.items():
            with self.subTest(message=message):
                summaries = [_summary(1), _summary(2), _summary(3)]
                mutate(summaries)
                with self.assertRaisesRegex(ValueError, message):
                    self._aggregate(summaries)

    def test_rejects_backend_harness_version_or_image_mismatch(self) -> None:
        def mutate_backend(summaries: list[dict[str, Any]]) -> None:
            summaries[1]["records"][0]["backend"] = "codex"

        def mutate_harness(summaries: list[dict[str, Any]]) -> None:
            summaries[1]["harness_mode"] = "different_harness"
            for record in summaries[1]["records"]:
                record["harness_mode"] = "different_harness"

        def mutate_version(summaries: list[dict[str, Any]]) -> None:
            version = _command(["claude", "--version"], "2.1.999")
            summaries[1]["backend_versions"]["claude-sonnet"] = copy.deepcopy(version)
            for record in summaries[1]["records"]:
                record["commands"]["backend_version"] = copy.deepcopy(version)

        def mutate_image(summaries: list[dict[str, Any]]) -> None:
            for record in summaries[1]["records"]:
                record["commands"]["image_id"]["stdout_tail"] = "sha256:" + "9" * 64

        mutations = {
            "backend/model/effort/auth mismatch": mutate_backend,
            "run-level harness_mode mismatch": mutate_harness,
            "backend version mismatch": mutate_version,
            "image identity mismatch": mutate_image,
        }
        for message, mutate in mutations.items():
            with self.subTest(message=message):
                summaries = [_summary(1), _summary(2), _summary(3)]
                mutate(summaries)
                with self.assertRaisesRegex(ValueError, message):
                    self._aggregate(summaries)

    def test_rejects_missing_trial_and_invalidated_source(self) -> None:
        summaries = [_summary(1), _summary(2), _summary(3)]
        summaries[2]["records"] = [summaries[2]["records"][0]]
        with self.assertRaisesRegex(ValueError, "trial set"):
            self._aggregate(summaries)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write(root, [_summary(1), _summary(2), _summary(3)])
            (paths[1].parent / "INVALIDATED.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "refusing invalidated run summary"):
                aggregate_model_trials(paths, expected_trials=3)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write(root, [_summary(1), _summary(2), _summary(3)])
            (paths[1].parent / "NONFINAL.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "refusing nonfinal run summary"):
                aggregate_model_trials(paths, expected_trials=3)

    def test_legacy_non_b_cli_aggregate_remains_compatible(self) -> None:
        legacy = {
            "run_id": "legacy-trial-1",
            "harness_mode": "agentic_workspace",
            "records": [
                {
                    "task_id": "legacy-task",
                    "condition_id": "legacy-condition",
                    "arm": arm,
                    "trial_index": 1,
                    "harness_mode": "agentic_workspace",
                    "backend": "legacy",
                    "model_id": "legacy-model",
                    "effort": "high",
                    "status": "passed" if arm == "C1" else "reward_failed",
                    "passed": arm == "C1",
                    "reward": 1.0 if arm == "C1" else 0.0,
                    "wall_time_sec": 1.0,
                }
                for arm in ("C0", "C1")
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._write(Path(tmp), [legacy])
            result = aggregate_model_trials(paths, expected_trials=1)

        self.assertTrue(result["complete"])
        self.assertTrue(result["data_contract_complete"])
        self.assertFalse(result["paper_eligible"])
        self.assertIn("missing benchmark_eligibility", result["benchmark_eligibility"]["reasons"][0])


if __name__ == "__main__":
    unittest.main()
