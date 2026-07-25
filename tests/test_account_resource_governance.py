from __future__ import annotations

import unittest

from src.merlin_harness.account_resource_governance import (
    AccountAuthResourceObservation,
    AccountReinvestmentPolicy,
    AccountResourceGovernanceError,
    AccountResourceLedger,
)


CONTRACT = "b" * 64


def observation(
    observation_id: str,
    *,
    baseline_success: bool = True,
    managed_success: bool = True,
    baseline_turns: int = 6,
    managed_turns: int = 2,
    governance_turns: int = 1,
    verifier_epoch: str = "verifier-v1",
    quota_window: str = "subscription-window-1",
    model_id: str = "account-model",
) -> AccountAuthResourceObservation:
    return AccountAuthResourceObservation(
        observation_id=observation_id,
        task_id=f"task-{observation_id}",
        evaluation_contract_sha256=CONTRACT,
        verifier_epoch_id=verifier_epoch,
        quota_window_id=quota_window,
        provider_id="codex-cli",
        model_id=model_id,
        effort="high",
        baseline_success=baseline_success,
        managed_success=managed_success,
        baseline_execution_turns=baseline_turns,
        managed_execution_turns=managed_turns,
        governance_turns=governance_turns,
        baseline_total_tokens=1_000,
        managed_total_tokens=400,
        governance_total_tokens=100,
        baseline_latency_s=20.0,
        managed_latency_s=8.0,
        governance_latency_s=2.0,
    )


class AccountAuthResourceObservationTests(unittest.TestCase):
    def test_matched_success_produces_turn_token_and_latency_savings(self) -> None:
        item = observation("one")
        self.assertEqual(item.verified_turn_savings, 4)
        self.assertEqual(item.verified_token_savings, 600)
        self.assertEqual(item.latency_delta_s, 12.0)

    def test_cheaper_failure_is_not_spendable(self) -> None:
        item = observation("one", managed_success=False, managed_turns=0)
        self.assertEqual(item.verified_turn_savings, 0)
        self.assertIsNone(item.verified_token_savings)

    def test_invalid_count_and_contract_are_rejected(self) -> None:
        with self.assertRaises(AccountResourceGovernanceError):
            observation("one", baseline_turns=-1)
        with self.assertRaises(AccountResourceGovernanceError):
            AccountAuthResourceObservation(
                observation_id="one",
                task_id="task",
                evaluation_contract_sha256="bad",
                verifier_epoch_id="v1",
                quota_window_id="q1",
                provider_id="codex-cli",
                model_id="model",
                effort="high",
                baseline_success=True,
                managed_success=True,
                baseline_execution_turns=1,
                managed_execution_turns=1,
                governance_turns=0,
            )


class AccountResourceLedgerTests(unittest.TestCase):
    def test_budget_is_integer_turns_after_governance_and_reserve(self) -> None:
        ledger = AccountResourceLedger(
            policy=AccountReinvestmentPolicy(
                reinvestment_fraction=0.75,
                reserve_turns=1,
            )
        )
        ledger.extend([observation("one"), observation("two")])
        decision = ledger.decide()
        self.assertEqual(decision.verified_turn_savings, 8)
        self.assertEqual(decision.governance_turns_spent, 2)
        self.assertEqual(decision.authorized_provider_turns, 3)
        self.assertEqual(decision.verified_token_savings, 1_200)

    def test_model_or_quota_window_drift_blocks_pooling(self) -> None:
        ledger = AccountResourceLedger()
        ledger.append(observation("one"))
        ledger.append(observation("two", model_id="other-model"))
        decision = ledger.decide()
        self.assertEqual(decision.authorized_provider_turns, 0)
        self.assertEqual(decision.comparison_dimension_count, 2)
        self.assertIn("mixed verifier", decision.reason)

        other_window = AccountResourceLedger()
        other_window.append(observation("one"))
        other_window.append(observation("two", quota_window="window-2"))
        self.assertEqual(other_window.decide().authorized_provider_turns, 0)

    def test_empty_ledger_fails_closed(self) -> None:
        decision = AccountResourceLedger().decide()
        self.assertEqual(decision.authorized_provider_turns, 0)
        self.assertEqual(decision.reason, "no matched account-auth observations")

    def test_duplicate_observation_is_rejected(self) -> None:
        ledger = AccountResourceLedger()
        ledger.append(observation("one"))
        with self.assertRaises(AccountResourceGovernanceError):
            ledger.append(observation("one"))

    def test_rolling_window_and_cap_are_enforced(self) -> None:
        ledger = AccountResourceLedger(
            policy=AccountReinvestmentPolicy(
                reinvestment_fraction=1.0,
                rolling_observations=1,
                per_decision_cap_turns=2,
            )
        )
        ledger.extend([observation("one"), observation("two", governance_turns=0)])
        decision = ledger.decide()
        self.assertEqual(decision.observation_count, 1)
        self.assertEqual(decision.authorized_provider_turns, 2)
