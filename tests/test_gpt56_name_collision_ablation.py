from __future__ import annotations

import copy
import hashlib
import json
import unittest

from experiments.skillsbench.run_gpt56_name_collision_ablation import (
    CONDITIONS,
    TRIAL_INDICES,
    NameCollisionAblationError,
    _metrics,
    _name_collision_summary,
    build_plan,
    build_prompt,
    declared_skill_name,
    validate_report,
)
from src.merlin_harness.models import LifecycleStatus, SkillArtifact
from src.merlin_harness.skill_name_governance import build_name_unique_provisioning_view


class Gpt56NameCollisionAblationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = build_plan()

    def test_plan_holds_size_and_tasks_constant_while_removing_name_collisions(self) -> None:
        self.assertEqual([arm["arm_id"] for arm in self.plan["arms"]], list(CONDITIONS))
        raw, unique = self.plan["arms"]
        self.assertEqual(raw["library_size"], unique["library_size"])
        self.assertEqual(raw["library_size"], 56)
        self.assertGreater(raw["name_collision_summary"]["duplicate_name_count"], 0)
        self.assertEqual(unique["name_collision_summary"]["duplicate_name_count"], 0)
        oracle = {task["oracle_skill_id"] for task in self.plan["tasks"]}
        self.assertTrue(oracle.issubset(raw["skill_ids"]))
        self.assertTrue(oracle.issubset(unique["skill_ids"]))
        self.assertEqual(len(self.plan["plan_sha256"]), 64)

    def test_docx_collision_is_removed_by_oracle_independent_preference(self) -> None:
        raw, unique = self.plan["arms"]
        self.assertIn("docx", raw["name_collision_summary"]["collision_groups"])
        self.assertIn("docx", raw["skill_ids"])
        self.assertIn("docx@d3cfe519dca2", raw["skill_ids"])
        self.assertIn("docx", unique["skill_ids"])
        self.assertNotIn("docx@d3cfe519dca2", unique["skill_ids"])
        self.assertFalse(
            self.plan["provisioning_policy"][
                "uses_task_oracle_to_choose_between_same-name_variants"
            ]
        )

    def test_provider_prompt_contains_no_oracle_or_outcome_labels(self) -> None:
        prompt = build_prompt(self.plan, arm_id="raw-56", trial_index=TRIAL_INDICES[0])
        self.assertNotIn("oracle_skill_id", prompt)
        self.assertNotIn("outcome", prompt)
        self.assertNotIn("correct skill", prompt.lower())
        self.assertIn("CATALOG (56 skills", prompt)

    def test_name_collision_summary_counts_duplicate_variants(self) -> None:
        summary = _name_collision_summary(["docx", "docx@d3cfe519dca2"])
        self.assertEqual(summary["duplicate_name_count"], 1)
        self.assertEqual(summary["duplicate_variant_count"], 1)

    def test_frozen_ablation_and_runtime_use_the_same_canonical_preference(self) -> None:
        raw = self.plan["arms"][0]
        records = self.plan["skill_records"]
        library = [
            SkillArtifact(
                id=skill_id,
                name=declared_skill_name(skill_id),
                description=records[skill_id]["description"],
                trigger=records[skill_id]["description"],
                status=LifecycleStatus.ACTIVE,
            )
            for skill_id in raw["skill_ids"]
        ]
        view = build_name_unique_provisioning_view(library)
        canonical = {
            group.declared_name: group.canonical_skill_id
            for group in view.collision_groups
        }
        self.assertEqual(canonical, {"docx": "docx", "pdf": "pdf"})
        self.assertEqual(view.source_active_count, 56)
        self.assertEqual(view.provisionable_active_count, 53)

    def test_metrics_and_report_validation_reject_decision_drift(self) -> None:
        cells = []
        for arm in self.plan["arms"]:
            for trial in TRIAL_INDICES:
                decisions = []
                for task in self.plan["tasks"]:
                    decisions.append(
                        {
                            "task_id": task["task_id"],
                            "selected_skill_id": task["oracle_skill_id"],
                            "oracle_skill_id": task["oracle_skill_id"],
                            "outcome": "correct",
                            "oracle_declared_name": declared_skill_name(task["oracle_skill_id"]),
                            "selected_declared_name": declared_skill_name(task["oracle_skill_id"]),
                            "declared_name_match": True,
                        }
                    )
                cells.append(
                    {
                        "cell_id": f"{arm['arm_id']}__t{trial}",
                        "arm_id": arm["arm_id"],
                        "library_size": 56,
                        "trial_index": trial,
                        "membership_sha256": arm["membership_sha256"],
                        "presentation_sha256": next(
                            hashlib.sha256(
                                json.dumps(
                                    presentation["skill_ids"],
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode()
                            ).hexdigest()
                            for presentation in arm["presentations"]
                            if presentation["trial_index"] == trial
                        ),
                        "provider_tool_execution_observed": False,
                        "item_types": ["agent_message"],
                        "prompt_sha256": "1" * 64,
                        "schema_sha256": "2" * 64,
                        "raw_trace_sha256": "3" * 64,
                        "response_sha256": "4" * 64,
                        "decisions": decisions,
                    }
                )
        body = {
            "schema_version": 1,
            "ablation_id": self.plan["ablation_id"],
            "plan_sha256": self.plan["plan_sha256"],
            "cells": cells,
            "metrics": _metrics(cells),
        }
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        report = {**body, "report_sha256": hashlib.sha256(canonical.encode()).hexdigest()}
        self.assertTrue(validate_report(report, plan=self.plan)["passed"])
        tampered = copy.deepcopy(report)
        tampered["cells"][0]["decisions"][0]["declared_name_match"] = False
        with self.assertRaisesRegex(NameCollisionAblationError, "decision derivation drifted"):
            validate_report(tampered, plan=self.plan)


if __name__ == "__main__":
    unittest.main()
