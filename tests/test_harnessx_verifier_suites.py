from __future__ import annotations

import unittest

from src.merlin_harness.harnessx_policy_evolution import (
    evaluate_live_tool_policy_variant,
    make_live_tool_policy_parent,
)
from src.merlin_harness.harnessx_verifier_suites import (
    FROZEN_50_TOOL_POLICY_VERIFIER_SUITE,
    HarnessXVerifierSuiteError,
    MULTITARGET_50_TOOL_POLICY_VERIFIER_SUITE,
    ToolPolicyVerifierSuite,
    get_tool_policy_verifier_suite,
)


class HarnessXVerifierSuiteTests(unittest.TestCase):
    def test_frozen_50_is_unique_balanced_and_hash_stable(self) -> None:
        suite = FROZEN_50_TOOL_POLICY_VERIFIER_SUITE
        expected_hash = "d5473a4320104c12fa9cf005f015181abdbd9ab5d8bc2affaeabe630f2dbe8e8"

        self.assertEqual(len(suite.cases), 50)
        self.assertEqual(len({case.case_id for case in suite.cases}), 50)
        self.assertEqual(sum(case.expected_decision == "allow" for case in suite.cases), 3)
        self.assertEqual(sum(case.expected_decision == "deny" for case in suite.cases), 47)
        self.assertEqual(sum(suite.category_counts.values()), 50)
        self.assertEqual(suite.sha256, expected_hash)
        self.assertIs(get_tool_policy_verifier_suite(suite.suite_id), suite)

    def test_parent_has_one_actionable_failure_across_frozen_50(self) -> None:
        results = evaluate_live_tool_policy_variant(
            make_live_tool_policy_parent(),
            FROZEN_50_TOOL_POLICY_VERIFIER_SUITE.cases,
        )
        failures = [record["case_id"] for record in results if not record["passed"]]
        self.assertEqual(failures, ["directory-list-read"])

    def test_multitarget_suite_has_three_independent_read_failures(self) -> None:
        suite = MULTITARGET_50_TOOL_POLICY_VERIFIER_SUITE
        results = evaluate_live_tool_policy_variant(
            make_live_tool_policy_parent(),
            suite.cases,
        )
        failures = [record["case_id"] for record in results if not record["passed"]]
        self.assertEqual(
            failures,
            ["directory-list-read", "exact-absolute-list", "git-status-read"],
        )
        self.assertEqual(suite.category_counts["target_allow"], 3)
        self.assertEqual(
            suite.sha256,
            "3de80f97f6e573a71db9f197fe83ca05b72e81fb00aab3f0da2d1086e1580e53",
        )

    def test_suite_rejects_duplicate_or_incomplete_category_contract(self) -> None:
        case = FROZEN_50_TOOL_POLICY_VERIFIER_SUITE.cases[0]
        with self.assertRaises(HarnessXVerifierSuiteError):
            ToolPolicyVerifierSuite(
                suite_id="duplicate",
                cases=(case, case),
                case_categories=((case.case_id, "prior_allow"),),
            )
        with self.assertRaises(HarnessXVerifierSuiteError):
            ToolPolicyVerifierSuite(
                suite_id="missing-category",
                cases=(case,),
                case_categories=(),
            )


if __name__ == "__main__":
    unittest.main()
