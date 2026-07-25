from __future__ import annotations

import copy
import unittest

from experiments.mvp.run_skill_merge_demo import (
    build_skill_merge_demo_report,
    validate_skill_merge_demo_report,
)


class SkillMergeDemoTests(unittest.TestCase):
    def test_controlled_report_is_valid_and_claim_bounded(self) -> None:
        report = build_skill_merge_demo_report()
        validate_skill_merge_demo_report(report)
        self.assertEqual(report["summary"]["gates_passed"], 9)
        self.assertFalse(report["claim_boundary"]["model_execution"])
        self.assertFalse(report["claim_boundary"]["actual_provider_trace_evidence"])

    def test_rehashed_claim_or_denominator_inflation_fails(self) -> None:
        report = build_skill_merge_demo_report()
        tampered = copy.deepcopy(report)
        tampered["summary"]["gates_total"] = 8
        with self.assertRaisesRegex(ValueError, "summary"):
            validate_skill_merge_demo_report(tampered)

        tampered = copy.deepcopy(report)
        tampered["claim_boundary"]["actual_provider_trace_evidence"] = True
        with self.assertRaisesRegex(ValueError, "claim boundary"):
            validate_skill_merge_demo_report(tampered)


if __name__ == "__main__":
    unittest.main()
