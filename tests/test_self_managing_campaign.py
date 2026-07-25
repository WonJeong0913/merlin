from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.merlin_harness.self_managing_campaign import (
    SELF_MANAGING_50_TASKS,
    SelfManagingCampaignError,
    evaluate_self_managing_50_tasks,
    run_self_managing_50_campaign,
    validate_self_managing_50_campaign,
)


class SelfManagingCampaignTests(unittest.TestCase):
    def test_frozen_suite_contains_50_tasks_across_six_governance_families(self) -> None:
        self.assertEqual(len(SELF_MANAGING_50_TASKS), 50)
        self.assertEqual(
            Counter(task.category for task in SELF_MANAGING_50_TASKS),
            {
                "skill_lifecycle_routing": 10,
                "controller_dispatch": 6,
                "verifier_upgrade_gate": 8,
                "account_resource_governance": 8,
                "exact_multitool_mediation": 10,
                "trace_to_harness_action": 8,
            },
        )
        self.assertEqual(
            len({task.task_id for task in SELF_MANAGING_50_TASKS}),
            50,
        )

    def test_all_frozen_tasks_pass_production_governance_functions(self) -> None:
        results = evaluate_self_managing_50_tasks()
        self.assertEqual(len(results), 50)
        self.assertTrue(all(result["passed"] for result in results))

    def test_campaign_is_hash_bound_and_exactly_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            report = run_self_managing_50_campaign(output)
            validation = validate_self_managing_50_campaign(output)
            self.assertEqual(report["pass_count"], 50)
            self.assertTrue(validation["valid"])
            self.assertFalse(
                report["evidence_boundary"]["low_cost_model_comparison_included"]
            )

    def test_report_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_self_managing_50_campaign(output)
            path = output / "campaign-report.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            report["pass_count"] = 49
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(
                SelfManagingCampaignError, "replay validation"
            ):
                validate_self_managing_50_campaign(output)


if __name__ == "__main__":
    unittest.main()
