from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experiments.skill_authoring.run_authoring_policy_ablation import frozen_tasks
from experiments.skill_authoring.run_live_authoring_policy_ablation import (
    _aggregate,
    _body_exclusions,
    frozen_task_suites,
)
from src.merlin_harness.skill_authoring_policy import (
    CONTROL_ARM,
    POLICY_ARM,
    SkillAuthoringPolicyError,
    build_ablation_plan,
    build_authoring_prompt,
    load_authoring_policy,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_ROOT = (
    REPO_ROOT
    / "experiments"
    / "skill_authoring"
    / "policies"
    / "author-governed-skills"
)


class SkillAuthoringPolicyTests(unittest.TestCase):
    def test_canonical_policy_has_five_frozen_sources_and_portable_shape(self) -> None:
        policy = load_authoring_policy(POLICY_ROOT)

        self.assertEqual(policy.name, "author-governed-skills")
        self.assertEqual(len(policy.source_revisions), 5)
        self.assertEqual(len(set(policy.source_revisions)), 5)
        self.assertEqual(len(policy.policy_sha256), 64)
        self.assertLessEqual(len(policy.skill_markdown.splitlines()), 500)

    def test_control_and_policy_arms_bind_the_same_task_contract(self) -> None:
        policy = load_authoring_policy(POLICY_ROOT)
        task = frozen_tasks()[0]

        control = build_authoring_prompt(task, arm=CONTROL_ARM)
        treatment = build_authoring_prompt(task, arm=POLICY_ARM, policy=policy)

        self.assertEqual(control.task_contract_sha256, treatment.task_contract_sha256)
        self.assertNotEqual(control.prompt_sha256, treatment.prompt_sha256)
        self.assertIsNone(control.policy_sha256)
        self.assertEqual(treatment.policy_sha256, policy.policy_sha256)
        self.assertNotIn("<AUTHORING_POLICY", control.prompt)
        self.assertIn("<AUTHORING_POLICY", treatment.prompt)
        self.assertIn(task.candidate_skill_id, control.prompt)
        self.assertIn(task.candidate_skill_id, treatment.prompt)

    def test_ablation_plan_is_balanced_and_makes_no_provider_claim(self) -> None:
        policy = load_authoring_policy(POLICY_ROOT)

        report = build_ablation_plan(
            frozen_tasks(),
            policy=policy,
            repeats=2,
            model_id="gpt-5.6-terra",
            effort="high",
        )

        self.assertEqual(report["task_count"], 3)
        self.assertEqual(report["arm_count"], 2)
        self.assertEqual(report["expected_provider_calls"], 12)
        self.assertEqual(len(report["runs"]), 12)
        self.assertFalse(report["evidence_boundary"]["provider_calls_executed"])
        self.assertFalse(report["evidence_boundary"]["performance_comparison_available"])

    def test_policy_loader_rejects_unfrozen_or_extra_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "policy"
            (root / "agents").mkdir(parents=True)
            (root / "references").mkdir()
            (root / "SKILL.md").write_text(
                (POLICY_ROOT / "SKILL.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (root / "agents" / "openai.yaml").write_text(
                (POLICY_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (root / "references" / "source-contracts.md").write_text(
                "no frozen revisions\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(SkillAuthoringPolicyError, "five unique"):
                load_authoring_policy(root)
            (root / "README.md").write_text("extra\n", encoding="utf-8")
            with self.assertRaisesRegex(SkillAuthoringPolicyError, "files differ"):
                load_authoring_policy(root)

    def test_arm_contracts_fail_closed(self) -> None:
        policy = load_authoring_policy(POLICY_ROOT)
        task = frozen_tasks()[0]
        with self.assertRaises(SkillAuthoringPolicyError):
            build_authoring_prompt(task, arm=CONTROL_ARM, policy=policy)
        with self.assertRaises(SkillAuthoringPolicyError):
            build_authoring_prompt(task, arm=POLICY_ARM)

    def test_live_suite_matches_frozen_prompt_tasks_and_extracts_exclusions(self) -> None:
        self.assertEqual(
            {item.contract.candidate_skill_id for item in frozen_task_suites()},
            {item.candidate_skill_id for item in frozen_tasks()},
        )
        body = """# Example

## Do not use for

- general line counting
- arbitrary downloads

## Procedure

Run the script.
"""
        self.assertEqual(
            _body_exclusions(body),
            ("general line counting", "arbitrary downloads"),
        )

    def test_aggregate_reports_paired_policy_delta_without_inventing_missing_runs(self) -> None:
        control = {
            "repeat": 1,
            "task": "extract-todo-items",
            "arm": CONTROL_ARM,
            "metrics": {
                "promotion": False,
                "target_pass_rate": 1.0,
                "held_out_pass_rate": 1.0,
                "negative_route_accuracy": 0.5,
            },
        }
        treatment = {
            "repeat": 1,
            "task": "extract-todo-items",
            "arm": POLICY_ARM,
            "metrics": {
                "promotion": True,
                "target_pass_rate": 1.0,
                "held_out_pass_rate": 1.0,
                "negative_route_accuracy": 1.0,
            },
        }
        report = _aggregate([control, treatment])
        self.assertEqual(report["paired_deltas"][0]["promotion_delta"], 1)
        self.assertEqual(
            report["paired_deltas"][0]["negative_route_accuracy_delta"], 0.5
        )


if __name__ == "__main__":
    unittest.main()
