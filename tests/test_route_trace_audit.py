from __future__ import annotations

import copy
import unittest

from experiments.mvp.route_trace_audit import (
    RouteTraceAuditError,
    audit_route_trace_bundle,
    sample_trace_bundle,
)


class RouteTraceAuditTests(unittest.TestCase):
    def test_sample_is_observe_only_and_isolates_repeated_failed_route(self) -> None:
        report = audit_route_trace_bundle(sample_trace_bundle())
        self.assertEqual(report["metrics"]["record_count"], 3)
        self.assertEqual(report["metrics"]["route_counts"]["wrong"], 1)
        self.assertEqual(report["metrics"]["route_counts"]["mixed"], 1)
        self.assertAlmostEqual(report["metrics"]["exposure_shadowing_rate"], 2 / 3)
        self.assertEqual(
            [item["skill_id"] for item in report["diagnosis"]["provisional_candidates"]],
            ["quick-summary"],
        )
        self.assertTrue(report["safety"]["observe_only"])
        self.assertFalse(report["safety"]["source_library_mutated"])
        self.assertFalse(report["safety"]["promotion_allowed"])
        self.assertFalse(report["evidence_boundary"]["provider_native_invocation_claimed"])

    def test_single_route_risk_does_not_cross_threshold(self) -> None:
        bundle = sample_trace_bundle()
        bundle["records"][1]["exposed_skill_ids"] = ["structured-summary"]
        bundle["records"][1]["verifier_passed"] = True
        report = audit_route_trace_bundle(bundle)
        self.assertEqual(report["diagnosis"]["candidate_count"], 0)

    def test_malformed_or_overclaimed_evidence_fails_closed(self) -> None:
        cases = []
        overclaimed = sample_trace_bundle()
        overclaimed["evidence_level"] = "provider_native_invocation"
        cases.append(overclaimed)
        duplicate = sample_trace_bundle()
        duplicate["records"][1]["trace_id"] = duplicate["records"][0]["trace_id"]
        cases.append(duplicate)
        malformed = copy.deepcopy(sample_trace_bundle())
        malformed["records"][0]["unexpected"] = True
        cases.append(malformed)
        for bundle in cases:
            with self.subTest(bundle=bundle):
                with self.assertRaises(RouteTraceAuditError):
                    audit_route_trace_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
