from __future__ import annotations

import unittest
from unittest.mock import patch

from src.merlin_harness import governance_view


class CampaignGovernanceTests(unittest.TestCase):
    def test_campaign_state_is_read_from_disk_and_revalidated(self) -> None:
        campaign = governance_view._campaign_governance()
        self.assertTrue(campaign["artifacts_present"])
        self.assertTrue(campaign["validated"], campaign["validation_error"])
        self.assertEqual(campaign["task_count"], 50)
        self.assertEqual(campaign["pair_count"], 100)
        self.assertEqual(len(campaign["manifest_sha256"]), 64)
        self.assertEqual(len(campaign["schedule_sha256"]), 64)

    def test_absent_campaign_reports_absence(self) -> None:
        with patch.object(governance_view, "CAMPAIGN_DIR", "does/not/exist"):
            campaign = governance_view._campaign_governance()
        self.assertFalse(campaign["artifacts_present"])
        self.assertFalse(campaign["validated"])
        self.assertNotIn("task_count", campaign)

    def test_no_ratio_is_claimed_without_verified_savings(self) -> None:
        campaign = governance_view._campaign_governance()
        if campaign["matched_observation_count"] == 0:
            self.assertIsNone(campaign["g_over_s"])
            self.assertFalse(campaign["level_7_achieved"])


class EvolutionGovernanceTests(unittest.TestCase):
    def test_absent_ledger_reports_absence_not_zeroes(self) -> None:
        with patch.object(governance_view, "EVOLUTION_LEDGER", "does/not/exist.jsonl"):
            evolution = governance_view._evolution_governance()
        self.assertFalse(evolution["ledger_present"])
        self.assertNotIn("observation_count", evolution)
        self.assertNotIn("promotion_count", evolution)


class LifecycleGovernanceTests(unittest.TestCase):
    def test_promotion_stays_blocked_until_provider_native_evidence_exists(self) -> None:
        campaign = governance_view._campaign_governance()
        invocation = governance_view._invocation_evidence_governance(campaign)
        operations = governance_view._lifecycle_governance(campaign, invocation)
        promote = next(item for item in operations if item["kind"] == "promote")
        self.assertFalse(promote["available"])
        if not invocation["provider_native_evidence_complete"]:
            self.assertEqual(
                invocation["blocking_reason"],
                "provider_native_skill_invocation_evidence_incomplete",
            )

    def test_provider_evidence_alone_cannot_unblock_promotion(self) -> None:
        campaign = {
            "artifacts_present": True,
            "validated": True,
            "matched_observation_count": 1,
            "lifecycle_action_kind_counts": {},
            "level_7_checks": {
                "actual_invocation_evidence_complete": True,
                "matched_baseline_required": True,
                "g_over_s_or_turn_equivalent_required": True,
            },
        }
        invocation = governance_view._invocation_evidence_governance(campaign)
        operations = governance_view._lifecycle_governance(campaign, invocation)
        promote = next(item for item in operations if item["kind"] == "promote")
        self.assertFalse(promote["available"])
        self.assertEqual(promote["reason"], "candidate-specific gate required")
        self.assertIsNone(invocation["blocking_reason"])

    def test_promotion_requires_validated_artifacts_and_a_matched_observation(self) -> None:
        campaign = {
            "artifacts_present": True,
            "validated": False,
            "matched_observation_count": 1,
            "lifecycle_action_kind_counts": {},
            "level_7_checks": {
                "actual_invocation_evidence_complete": True,
                "matched_baseline_required": True,
                "g_over_s_or_turn_equivalent_required": True,
            },
        }
        invocation = governance_view._invocation_evidence_governance(campaign)
        promote = next(
            item
            for item in governance_view._lifecycle_governance(campaign, invocation)
            if item["kind"] == "promote"
        )
        self.assertFalse(promote["available"])
        self.assertIn("revalidation", promote["reason"])

    def test_rollback_requires_a_recorded_promotion(self) -> None:
        campaign = {"lifecycle_action_kind_counts": {}, "level_7_checks": {}}
        invocation = governance_view._invocation_evidence_governance(campaign)
        operations = governance_view._lifecycle_governance(campaign, invocation)
        rollback = next(item for item in operations if item["kind"] == "rollback")
        self.assertFalse(rollback["available"])

    def test_rollback_unblocks_once_a_promotion_is_recorded(self) -> None:
        campaign = {
            "lifecycle_action_kind_counts": {"promote": 1},
            "level_7_checks": {},
        }
        invocation = governance_view._invocation_evidence_governance(campaign)
        operations = governance_view._lifecycle_governance(campaign, invocation)
        rollback = next(item for item in operations if item["kind"] == "rollback")
        self.assertTrue(rollback["available"])

    def test_batch_operations_are_never_offered_as_one_click_actions(self) -> None:
        campaign = governance_view._campaign_governance()
        invocation = governance_view._invocation_evidence_governance(campaign)
        operations = governance_view._lifecycle_governance(campaign, invocation)
        for kind in ("repair", "merge", "hide", "retire"):
            operation = next(item for item in operations if item["kind"] == kind)
            self.assertFalse(operation["available"], kind)
            self.assertTrue(operation["reason"], kind)


class CorroborationTierTests(unittest.TestCase):
    def _tiers(self) -> dict[str, dict]:
        campaign = governance_view._campaign_governance()
        invocation = governance_view._invocation_evidence_governance(campaign)
        return {tier["tier"]: tier for tier in invocation["corroboration_tiers"]}

    def test_tiers_are_ranked_by_who_wrote_the_artifact(self) -> None:
        tiers = self._tiers()
        self.assertTrue(tiers["harness_signed"]["self_attested"])
        self.assertFalse(tiers["provider_cli_rollout"]["self_attested"])
        self.assertFalse(tiers["provider_server_attested"]["self_attested"])

    def test_every_tier_states_its_own_limit(self) -> None:
        for tier in self._tiers().values():
            self.assertTrue(tier["limit"], tier["tier"])
            self.assertTrue(tier["establishes"], tier["tier"])

    def test_server_attestation_is_reported_unavailable(self) -> None:
        # Nothing on the observed Codex CLI surface carries it. Claiming
        # otherwise would be the exact overclaim this project refuses.
        self.assertFalse(self._tiers()["provider_server_attested"]["available"])

    def test_tier_availability_does_not_flip_the_promotion_gate(self) -> None:
        # A mechanism being available is not an observation being corroborated.
        campaign = {
            "level_7_checks": {"actual_invocation_evidence_complete": False},
            "lifecycle_action_kind_counts": {},
        }
        invocation = governance_view._invocation_evidence_governance(campaign)
        available = [
            tier["tier"]
            for tier in invocation["corroboration_tiers"]
            if tier["available"]
        ]
        self.assertIn("harness_signed", available)
        self.assertFalse(invocation["provider_native_evidence_complete"])
        operations = governance_view._lifecycle_governance(campaign, invocation)
        promote = next(item for item in operations if item["kind"] == "promote")
        self.assertFalse(promote["available"])


class SummaryTests(unittest.TestCase):
    def test_summary_carries_every_section(self) -> None:
        summary = governance_view.harness_governance_summary()
        self.assertEqual(
            set(summary),
            {
                "campaign",
                "evolution",
                "invocation_evidence",
                "lifecycle_operations",
                "evidence_boundary",
            },
        )

    def test_evidence_boundary_is_stated_not_implied(self) -> None:
        boundary = governance_view.harness_governance_summary()["evidence_boundary"]
        self.assertTrue(boundary["prompt_exposure_is_not_invocation_evidence"])
        self.assertTrue(boundary["failed_or_unverifiable_arms_create_no_savings"])
        self.assertTrue(boundary["legacy_evidence_is_not_relabeled_as_merlin_evidence"])


if __name__ == "__main__":
    unittest.main()
