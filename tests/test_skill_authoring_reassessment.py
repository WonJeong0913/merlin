from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "experiments/skill_authoring/results/authoring-policy-ablation-live-v1.json"
REASSESSED = ROOT / "experiments/skill_authoring/results/authoring-policy-ablation-live-v1-reassessed-v2.json"


class SkillAuthoringReassessmentEvidenceTests(unittest.TestCase):
    def test_reassessment_is_bound_to_the_frozen_twelve_run_report(self) -> None:
        original_bytes = ORIGINAL.read_bytes()
        report = json.loads(REASSESSED.read_text(encoding="utf-8"))

        self.assertEqual(report["original_report_sha256"], hashlib.sha256(original_bytes).hexdigest())
        self.assertEqual(report["original_recorded_runs"], 12)
        self.assertEqual(report["new_provider_calls"], 0)
        self.assertEqual(len(report["runs"]), 12)
        self.assertTrue(report["evidence_boundary"]["candidate_and_workspace_hashes_reverified"])
        self.assertFalse(report["evidence_boundary"]["candidate_regenerated"])
        self.assertFalse(report["evidence_boundary"]["candidate_reexecuted"])

    def test_primary_comparison_excludes_only_under_specified_markdown_contracts(self) -> None:
        report = json.loads(REASSESSED.read_text(encoding="utf-8"))
        excluded = [run for run in report["runs"] if not run["contract_eligible"]]

        self.assertEqual(len(excluded), 4)
        self.assertEqual({run["task"] for run in excluded}, {"extract-markdown-links"})
        self.assertTrue(all(run["metrics"]["safety_gate"] for run in report["runs"]))

        arms = report["aggregate"]["by_arm"]
        control = arms["target-contract-only"]
        governed = arms["governed-authoring-policy"]
        self.assertEqual(control["mean_target_pass_rate"], 1.0)
        self.assertEqual(governed["mean_target_pass_rate"], 1.0)
        self.assertEqual(control["mean_held_out_pass_rate"], 1.0)
        self.assertEqual(governed["mean_held_out_pass_rate"], 1.0)
        self.assertEqual(control["format_pass_rate"], 0.0)
        self.assertAlmostEqual(governed["format_pass_rate"], 5 / 6)
        self.assertEqual(control["promotion_rate"], 0.0)
        self.assertEqual(governed["promotion_rate"], 0.25)

    def test_model_identity_claim_stays_at_requested_contract_level(self) -> None:
        report = json.loads(REASSESSED.read_text(encoding="utf-8"))

        self.assertEqual({run["generation"]["requested_model_id"] for run in report["runs"]}, {"gpt-5.6-terra"})
        self.assertEqual(
            {run["generation"]["model_evidence_level"] for run in report["runs"]},
            {"requested_cli_contract_only"},
        )
        self.assertTrue(all(not run["generation"]["provider_reported_model_ids"] for run in report["runs"]))


if __name__ == "__main__":
    unittest.main()
