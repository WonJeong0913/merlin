"""Evidence-bound economics and reinvestment controls for harness evolution.

The module deliberately separates three quantities that are easy to conflate:

* verified direct savings from a matched successful baseline,
* governance spend used to validate and maintain the harness, and
* estimated avoided-failure value.

Only the first quantity can fund automatic reinvestment. Avoided-failure value
is useful for research reporting, but remains non-spendable because it depends
on a counterfactual estimate.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CostGovernanceError(ValueError):
    """Raised when cost or verifier evidence violates the frozen contract."""


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CostGovernanceError(f"{name} must be a non-empty string")


def _require_cost(name: str, value: float | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CostGovernanceError(f"{name} must be a finite non-negative number")
    if not math.isfinite(value) or value < 0:
        raise CostGovernanceError(f"{name} must be a finite non-negative number")


@dataclass(frozen=True, slots=True)
class TaskEconomicsObservation:
    """One matched baseline/managed observation under a frozen evaluator."""

    observation_id: str
    task_id: str
    evaluation_contract_sha256: str
    verifier_epoch_id: str
    baseline_success: bool
    managed_success: bool
    baseline_execution_cost_usd: float
    managed_execution_cost_usd: float
    governance_cost_usd: float
    avoided_failure_value_usd: float | None = None

    def __post_init__(self) -> None:
        _require_non_empty("observation_id", self.observation_id)
        _require_non_empty("task_id", self.task_id)
        _require_non_empty("verifier_epoch_id", self.verifier_epoch_id)
        if not _SHA256_RE.fullmatch(self.evaluation_contract_sha256):
            raise CostGovernanceError(
                "evaluation_contract_sha256 must be a lowercase SHA-256 digest"
            )
        if not isinstance(self.baseline_success, bool) or not isinstance(
            self.managed_success, bool
        ):
            raise CostGovernanceError("success fields must be booleans")
        _require_cost("baseline_execution_cost_usd", self.baseline_execution_cost_usd)
        _require_cost("managed_execution_cost_usd", self.managed_execution_cost_usd)
        _require_cost("governance_cost_usd", self.governance_cost_usd)
        _require_cost("avoided_failure_value_usd", self.avoided_failure_value_usd)

    @property
    def verified_direct_savings_usd(self) -> float:
        """Return spendable savings only for matched successful outcomes."""

        if not (self.baseline_success and self.managed_success):
            return 0.0
        return max(
            0.0,
            self.baseline_execution_cost_usd - self.managed_execution_cost_usd,
        )

    @property
    def signed_execution_delta_usd(self) -> float:
        """Baseline minus managed execution cost, regardless of task outcome."""

        return self.baseline_execution_cost_usd - self.managed_execution_cost_usd

    @property
    def net_direct_value_usd(self) -> float:
        """Verified savings after governance cost, excluding counterfactual value."""

        return self.verified_direct_savings_usd - self.governance_cost_usd

    @property
    def estimated_total_value_usd(self) -> float:
        """Research value including explicitly supplied avoided-failure value."""

        return self.net_direct_value_usd + (self.avoided_failure_value_usd or 0.0)


@dataclass(frozen=True, slots=True)
class HarnessReinvestmentPolicy:
    """Budget rule for spending realized savings on future harness work."""

    reinvestment_fraction: float = 0.5
    rolling_observations: int = 100
    reserve_usd: float = 0.0
    per_decision_cap_usd: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.reinvestment_fraction, bool)
            or not math.isfinite(self.reinvestment_fraction)
            or not 0 <= self.reinvestment_fraction <= 1
        ):
            raise CostGovernanceError("reinvestment_fraction must be from 0 through 1")
        if (
            isinstance(self.rolling_observations, bool)
            or not isinstance(self.rolling_observations, int)
            or self.rolling_observations < 1
        ):
            raise CostGovernanceError("rolling_observations must be a positive integer")
        _require_cost("reserve_usd", self.reserve_usd)
        _require_cost("per_decision_cap_usd", self.per_decision_cap_usd)


@dataclass(frozen=True, slots=True)
class HarnessReinvestmentDecision:
    authorized_budget_usd: float
    verified_savings_usd: float
    governance_spend_usd: float
    estimated_avoided_failure_value_usd: float
    observation_count: int
    spendable_observation_count: int
    verifier_epoch_count: int
    reason: str


@dataclass(slots=True)
class HarnessInvestmentLedger:
    """Append-only in-memory ledger with duplicate-evidence rejection."""

    policy: HarnessReinvestmentPolicy = field(default_factory=HarnessReinvestmentPolicy)
    _observations: list[TaskEconomicsObservation] = field(default_factory=list)
    _observation_ids: set[str] = field(default_factory=set)

    @property
    def observations(self) -> tuple[TaskEconomicsObservation, ...]:
        return tuple(self._observations)

    def append(self, observation: TaskEconomicsObservation) -> None:
        if observation.observation_id in self._observation_ids:
            raise CostGovernanceError(
                f"duplicate observation_id: {observation.observation_id}"
            )
        self._observations.append(observation)
        self._observation_ids.add(observation.observation_id)

    def extend(self, observations: Iterable[TaskEconomicsObservation]) -> None:
        for observation in observations:
            self.append(observation)

    def decide(self) -> HarnessReinvestmentDecision:
        window = self._observations[-self.policy.rolling_observations :]
        verified_savings = sum(item.verified_direct_savings_usd for item in window)
        governance_spend = sum(item.governance_cost_usd for item in window)
        estimated_avoided = sum(
            item.avoided_failure_value_usd or 0.0 for item in window
        )
        verifier_epoch_count = len({item.verifier_epoch_id for item in window})
        available = max(
            0.0,
            verified_savings * self.policy.reinvestment_fraction
            - governance_spend
            - self.policy.reserve_usd,
        )
        if verifier_epoch_count > 1:
            available = 0.0
        if self.policy.per_decision_cap_usd is not None:
            available = min(available, self.policy.per_decision_cap_usd)

        spendable_observation_count = sum(
            item.verified_direct_savings_usd > 0 for item in window
        )
        if not window:
            reason = "no matched economics observations"
        elif verifier_epoch_count > 1:
            reason = "mixed verifier epochs cannot authorize a pooled budget"
        elif verified_savings <= 0:
            reason = "no verified direct savings"
        elif available <= 0:
            reason = "verified savings do not cover governance spend and reserve"
        else:
            reason = "budget authorized from verified direct savings"

        return HarnessReinvestmentDecision(
            authorized_budget_usd=available,
            verified_savings_usd=verified_savings,
            governance_spend_usd=governance_spend,
            estimated_avoided_failure_value_usd=estimated_avoided,
            observation_count=len(window),
            spendable_observation_count=spendable_observation_count,
            verifier_epoch_count=verifier_epoch_count,
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class VerifierUpgradeEvidence:
    """Evidence required to replace a verifier epoch."""

    incumbent_epoch_id: str
    candidate_epoch_id: str
    replay_case_count: int
    replay_regression_count: int
    independent_oracle_passed: bool
    human_approved: bool

    def __post_init__(self) -> None:
        _require_non_empty("incumbent_epoch_id", self.incumbent_epoch_id)
        _require_non_empty("candidate_epoch_id", self.candidate_epoch_id)
        if self.incumbent_epoch_id == self.candidate_epoch_id:
            raise CostGovernanceError("candidate verifier epoch must be new")
        for name in ("replay_case_count", "replay_regression_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CostGovernanceError(f"{name} must be a non-negative integer")
        if self.replay_regression_count > self.replay_case_count:
            raise CostGovernanceError(
                "replay_regression_count cannot exceed replay_case_count"
            )
        if not isinstance(self.independent_oracle_passed, bool) or not isinstance(
            self.human_approved, bool
        ):
            raise CostGovernanceError("verifier approval fields must be booleans")


@dataclass(frozen=True, slots=True)
class VerifierUpgradeDecision:
    promote: bool
    reasons: tuple[str, ...]


def gate_verifier_upgrade(
    evidence: VerifierUpgradeEvidence,
    *,
    minimum_replay_cases: int,
) -> VerifierUpgradeDecision:
    """Prevent a self-optimizing verifier from approving its own replacement."""

    if (
        isinstance(minimum_replay_cases, bool)
        or not isinstance(minimum_replay_cases, int)
        or minimum_replay_cases < 1
    ):
        raise CostGovernanceError("minimum_replay_cases must be a positive integer")

    reasons: list[str] = []
    if evidence.replay_case_count < minimum_replay_cases:
        reasons.append("insufficient frozen-corpus replay")
    if evidence.replay_regression_count:
        reasons.append("frozen-corpus regression detected")
    if not evidence.independent_oracle_passed:
        reasons.append("independent oracle did not pass")
    if not evidence.human_approved:
        reasons.append("high-risk human approval missing")
    return VerifierUpgradeDecision(promote=not reasons, reasons=tuple(reasons))
