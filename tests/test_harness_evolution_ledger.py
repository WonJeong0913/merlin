from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.merlin_harness.harness_evolution_ledger import (
    HarnessEvolutionLedger,
    HarnessEvolutionLedgerError,
    HarnessEvolutionObservation,
    append_harness_evolution_observation,
    load_and_validate_harness_evolution_ledger,
    observations_from_aegis_campaign,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def observation(
    observation_id: str,
    *,
    governance_spend: float = 2,
    verified_savings: float = 4,
    dimension: str = "account-auth:gpt-5.6-terra:low",
    verifier_epoch: str = "verifier-v1",
) -> HarnessEvolutionObservation:
    return HarnessEvolutionObservation(
        observation_id=observation_id,
        campaign_id="campaign-v1",
        round_index=1,
        change_kind="harness_policy",
        verifier_epoch_id=verifier_epoch,
        verifier_suite_sha256=SHA_A,
        evidence_sha256=SHA_B,
        parent_state_sha256=SHA_C,
        resolved_state_sha256=SHA_D,
        candidate_count=2,
        promotion_count=1,
        rollback_count=0,
        regression_exposure_count=48,
        regression_count=0,
        resource_unit="provider_turns",
        resource_dimension_id=dimension,
        resource_window_id="quota-window-1",
        governance_spend=governance_spend,
        verified_direct_savings=verified_savings,
        savings_evidence_sha256=SHA_E if verified_savings else None,
    )


class HarnessEvolutionLedgerTests(unittest.TestCase):
    def test_summary_keeps_quality_metrics_and_gs_ratio_separate(self) -> None:
        ledger = HarnessEvolutionLedger(
            (
                observation("obs-1"),
                replace(
                    observation("obs-2"),
                    round_index=2,
                    candidate_count=1,
                    promotion_count=1,
                    rollback_count=1,
                    regression_count=1,
                ),
            )
        )
        summary = ledger.summarize()
        self.assertEqual(summary.promotion_rate, 2 / 3)
        self.assertEqual(summary.rollback_rate, 1 / 2)
        self.assertEqual(summary.regression_rate, 1 / 96)
        self.assertEqual(summary.governance_to_savings_ratio, 0.5)
        self.assertEqual(
            summary.ratio_reason,
            "G/S computed from one matched evidence dimension",
        )

    def test_zero_savings_and_mixed_dimensions_make_gs_unavailable(self) -> None:
        no_savings = HarnessEvolutionLedger(
            (observation("obs-1", governance_spend=0, verified_savings=0),)
        ).summarize()
        self.assertIsNone(no_savings.governance_to_savings_ratio)
        self.assertIn("no verified", no_savings.ratio_reason)

        mixed = HarnessEvolutionLedger(
            (
                observation("obs-1"),
                observation("obs-2", dimension="account-auth:other"),
            )
        ).summarize()
        self.assertIsNone(mixed.governance_to_savings_ratio)
        self.assertEqual(mixed.comparison_dimension_count, 2)
        self.assertIn("mixed verifier or resource", mixed.ratio_reason)

    def test_positive_savings_require_independent_evidence(self) -> None:
        with self.assertRaisesRegex(
            HarnessEvolutionLedgerError, "requires savings evidence"
        ):
            replace(observation("obs-1"), savings_evidence_sha256=None)

    def test_jsonl_chain_rejects_duplicates_and_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evolution.jsonl"
            append_harness_evolution_observation(path, observation("obs-1"))
            append_harness_evolution_observation(
                path,
                replace(observation("obs-2"), round_index=2),
            )
            records = load_and_validate_harness_evolution_ledger(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(
                records[1]["previous_record_sha256"],
                records[0]["record_sha256"],
            )
            with self.assertRaisesRegex(
                HarnessEvolutionLedgerError, "duplicate observation_id"
            ):
                append_harness_evolution_observation(path, observation("obs-1"))
            lines = path.read_text(encoding="utf-8").splitlines()
            altered = json.loads(lines[0])
            altered["observation"]["promotion_count"] = 0
            lines[0] = json.dumps(altered)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(HarnessEvolutionLedgerError, "hash chain"):
                load_and_validate_harness_evolution_ledger(path)

    def test_scripted_campaign_conversion_is_replayed_and_has_no_fake_gs(self) -> None:
        campaign = Path(
            "experiments/mvp/results/harnessx_aegis_multiround_scripted_v1"
        )
        observations = observations_from_aegis_campaign(
            campaign,
            campaign_id="scripted-multitarget-v1",
            verifier_epoch_id="live-policy-multitarget-50-v1",
            resource_unit="provider_turns",
            resource_dimension_id="model-free",
            resource_window_id="offline-v1",
        )
        self.assertEqual(len(observations), 3)
        self.assertEqual(
            [item.promotion_count for item in observations],
            [1, 1, 1],
        )
        self.assertEqual(sum(item.regression_count for item in observations), 0)
        summary = HarnessEvolutionLedger(observations).summarize()
        self.assertEqual(summary.promotion_rate, 1.0)
        self.assertEqual(summary.regression_rate, 0.0)
        self.assertIsNone(summary.governance_to_savings_ratio)


if __name__ == "__main__":
    unittest.main()
