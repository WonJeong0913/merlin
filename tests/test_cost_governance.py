from __future__ import annotations

import unittest

from src.merlin_harness.cost_governance import (
    CostGovernanceError,
    HarnessInvestmentLedger,
    HarnessReinvestmentPolicy,
    TaskEconomicsObservation,
    VerifierUpgradeEvidence,
    gate_verifier_upgrade,
)


CONTRACT = "a" * 64


def observation(
    observation_id: str,
    *,
    baseline_success: bool = True,
    managed_success: bool = True,
    baseline_cost: float = 1.0,
    managed_cost: float = 0.4,
    governance_cost: float = 0.1,
    avoided_value: float | None = None,
) -> TaskEconomicsObservation:
    return TaskEconomicsObservation(
        observation_id=observation_id,
        task_id=f"task-{observation_id}",
        evaluation_contract_sha256=CONTRACT,
        verifier_epoch_id="verifier-v1",
        baseline_success=baseline_success,
        managed_success=managed_success,
        baseline_execution_cost_usd=baseline_cost,
        managed_execution_cost_usd=managed_cost,
        governance_cost_usd=governance_cost,
        avoided_failure_value_usd=avoided_value,
    )


class TaskEconomicsObservationTest(unittest.TestCase):
    def test_verified_success_pair_produces_direct_savings(self) -> None:
        item = observation("one")
        self.assertAlmostEqual(item.verified_direct_savings_usd, 0.6)
        self.assertAlmostEqual(item.net_direct_value_usd, 0.5)

    def test_failed_arm_never_produces_spendable_savings(self) -> None:
        item = observation("one", baseline_success=False, avoided_value=4.0)
        self.assertEqual(item.verified_direct_savings_usd, 0.0)
        self.assertAlmostEqual(item.estimated_total_value_usd, 3.9)

    def test_invalid_contract_digest_is_rejected(self) -> None:
        with self.assertRaises(CostGovernanceError):
            TaskEconomicsObservation(
                observation_id="one",
                task_id="task",
                evaluation_contract_sha256="not-a-digest",
                verifier_epoch_id="v1",
                baseline_success=True,
                managed_success=True,
                baseline_execution_cost_usd=1.0,
                managed_execution_cost_usd=0.5,
                governance_cost_usd=0.1,
            )


class HarnessInvestmentLedgerTest(unittest.TestCase):
    def test_budget_comes_from_fraction_of_verified_savings_after_spend(self) -> None:
        ledger = HarnessInvestmentLedger(
            policy=HarnessReinvestmentPolicy(
                reinvestment_fraction=0.5,
                reserve_usd=0.05,
            )
        )
        ledger.append(observation("one"))
        decision = ledger.decide()
        self.assertAlmostEqual(decision.authorized_budget_usd, 0.15)
        self.assertEqual(decision.reason, "budget authorized from verified direct savings")

    def test_avoided_failure_value_is_reported_but_not_spendable(self) -> None:
        ledger = HarnessInvestmentLedger()
        ledger.append(
            observation(
                "one",
                baseline_success=False,
                managed_success=True,
                baseline_cost=1.0,
                managed_cost=0.5,
                governance_cost=0.1,
                avoided_value=10.0,
            )
        )
        decision = ledger.decide()
        self.assertEqual(decision.authorized_budget_usd, 0.0)
        self.assertEqual(decision.estimated_avoided_failure_value_usd, 10.0)

    def test_duplicate_observation_is_rejected(self) -> None:
        ledger = HarnessInvestmentLedger()
        ledger.append(observation("one"))
        with self.assertRaises(CostGovernanceError):
            ledger.append(observation("one"))

    def test_rolling_window_and_cap_are_enforced(self) -> None:
        ledger = HarnessInvestmentLedger(
            policy=HarnessReinvestmentPolicy(
                reinvestment_fraction=1.0,
                rolling_observations=1,
                per_decision_cap_usd=0.2,
            )
        )
        ledger.extend([observation("one"), observation("two")])
        decision = ledger.decide()
        self.assertEqual(decision.observation_count, 1)
        self.assertAlmostEqual(decision.authorized_budget_usd, 0.2)

    def test_mixed_verifier_epochs_cannot_fund_one_pooled_budget(self) -> None:
        ledger = HarnessInvestmentLedger()
        ledger.append(observation("one"))
        ledger.append(
            TaskEconomicsObservation(
                observation_id="two",
                task_id="task-two",
                evaluation_contract_sha256=CONTRACT,
                verifier_epoch_id="verifier-v2",
                baseline_success=True,
                managed_success=True,
                baseline_execution_cost_usd=1.0,
                managed_execution_cost_usd=0.4,
                governance_cost_usd=0.1,
            )
        )

        decision = ledger.decide()

        self.assertEqual(decision.authorized_budget_usd, 0.0)
        self.assertEqual(decision.verifier_epoch_count, 2)
        self.assertIn("mixed verifier epochs", decision.reason)


class VerifierUpgradeGateTest(unittest.TestCase):
    def test_complete_independent_evidence_promotes(self) -> None:
        decision = gate_verifier_upgrade(
            VerifierUpgradeEvidence(
                incumbent_epoch_id="v1",
                candidate_epoch_id="v2",
                replay_case_count=200,
                replay_regression_count=0,
                independent_oracle_passed=True,
                human_approved=True,
            ),
            minimum_replay_cases=100,
        )
        self.assertTrue(decision.promote)
        self.assertEqual(decision.reasons, ())

    def test_candidate_cannot_self_promote_without_independent_approval(self) -> None:
        decision = gate_verifier_upgrade(
            VerifierUpgradeEvidence(
                incumbent_epoch_id="v1",
                candidate_epoch_id="v2",
                replay_case_count=20,
                replay_regression_count=1,
                independent_oracle_passed=False,
                human_approved=False,
            ),
            minimum_replay_cases=100,
        )
        self.assertFalse(decision.promote)
        self.assertEqual(len(decision.reasons), 4)
