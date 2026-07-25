"""Account-auth resource accounting for harness reinvestment.

Subscription-backed CLIs do not expose a meaningful per-request USD price.
This module therefore keeps account-auth evidence separate from API billing
and uses provider turns as the conservative spendable unit. Token and latency
observations are retained as diagnostics when the provider reports them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AccountResourceGovernanceError(ValueError):
    """Raised when account-auth resource evidence cannot be pooled safely."""


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AccountResourceGovernanceError(f"{name} must be a non-empty string")


def _require_count(name: str, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AccountResourceGovernanceError(
            f"{name} must be a non-negative integer"
        )


def _require_duration(name: str, value: float | None) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise AccountResourceGovernanceError(
            f"{name} must be a finite non-negative number"
        )


@dataclass(frozen=True, slots=True)
class AccountAuthResourceObservation:
    """One matched account-auth baseline/managed task observation."""

    observation_id: str
    task_id: str
    evaluation_contract_sha256: str
    verifier_epoch_id: str
    quota_window_id: str
    provider_id: str
    model_id: str
    effort: str
    baseline_success: bool
    managed_success: bool
    baseline_execution_turns: int
    managed_execution_turns: int
    governance_turns: int
    baseline_total_tokens: int | None = None
    managed_total_tokens: int | None = None
    governance_total_tokens: int | None = None
    baseline_latency_s: float | None = None
    managed_latency_s: float | None = None
    governance_latency_s: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "observation_id",
            "task_id",
            "verifier_epoch_id",
            "quota_window_id",
            "provider_id",
            "model_id",
            "effort",
        ):
            _require_non_empty(name, getattr(self, name))
        if not _SHA256_RE.fullmatch(self.evaluation_contract_sha256):
            raise AccountResourceGovernanceError(
                "evaluation_contract_sha256 must be a lowercase SHA-256 digest"
            )
        if not isinstance(self.baseline_success, bool) or not isinstance(
            self.managed_success, bool
        ):
            raise AccountResourceGovernanceError("success fields must be booleans")
        for name in (
            "baseline_execution_turns",
            "managed_execution_turns",
            "governance_turns",
            "baseline_total_tokens",
            "managed_total_tokens",
            "governance_total_tokens",
        ):
            _require_count(name, getattr(self, name))
        for name in (
            "baseline_latency_s",
            "managed_latency_s",
            "governance_latency_s",
        ):
            _require_duration(name, getattr(self, name))

    @property
    def verified_turn_savings(self) -> int:
        if not (self.baseline_success and self.managed_success):
            return 0
        return max(0, self.baseline_execution_turns - self.managed_execution_turns)

    @property
    def verified_token_savings(self) -> int | None:
        if (
            not (self.baseline_success and self.managed_success)
            or self.baseline_total_tokens is None
            or self.managed_total_tokens is None
        ):
            return None
        return max(0, self.baseline_total_tokens - self.managed_total_tokens)

    @property
    def latency_delta_s(self) -> float | None:
        if self.baseline_latency_s is None or self.managed_latency_s is None:
            return None
        return self.baseline_latency_s - self.managed_latency_s


@dataclass(frozen=True, slots=True)
class AccountReinvestmentPolicy:
    reinvestment_fraction: float = 0.5
    rolling_observations: int = 100
    reserve_turns: int = 0
    per_decision_cap_turns: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.reinvestment_fraction, bool)
            or not isinstance(self.reinvestment_fraction, (int, float))
            or not math.isfinite(self.reinvestment_fraction)
            or not 0 <= self.reinvestment_fraction <= 1
        ):
            raise AccountResourceGovernanceError(
                "reinvestment_fraction must be from 0 through 1"
            )
        if (
            isinstance(self.rolling_observations, bool)
            or not isinstance(self.rolling_observations, int)
            or self.rolling_observations < 1
        ):
            raise AccountResourceGovernanceError(
                "rolling_observations must be a positive integer"
            )
        _require_count("reserve_turns", self.reserve_turns)
        _require_count("per_decision_cap_turns", self.per_decision_cap_turns)


@dataclass(frozen=True, slots=True)
class AccountReinvestmentDecision:
    authorized_provider_turns: int
    verified_turn_savings: int
    governance_turns_spent: int
    verified_token_savings: int | None
    token_pair_count: int
    latency_delta_s: float | None
    latency_pair_count: int
    observation_count: int
    spendable_observation_count: int
    comparison_dimension_count: int
    reason: str


@dataclass(slots=True)
class AccountResourceLedger:
    """Append-only account-auth ledger with fail-closed pooling."""

    policy: AccountReinvestmentPolicy = field(default_factory=AccountReinvestmentPolicy)
    _observations: list[AccountAuthResourceObservation] = field(default_factory=list)
    _observation_ids: set[str] = field(default_factory=set)

    @property
    def observations(self) -> tuple[AccountAuthResourceObservation, ...]:
        return tuple(self._observations)

    def append(self, observation: AccountAuthResourceObservation) -> None:
        if observation.observation_id in self._observation_ids:
            raise AccountResourceGovernanceError(
                f"duplicate observation_id: {observation.observation_id}"
            )
        self._observations.append(observation)
        self._observation_ids.add(observation.observation_id)

    def extend(self, observations: Iterable[AccountAuthResourceObservation]) -> None:
        for observation in observations:
            self.append(observation)

    def decide(self) -> AccountReinvestmentDecision:
        window = self._observations[-self.policy.rolling_observations :]
        dimensions = {
            (
                item.verifier_epoch_id,
                item.quota_window_id,
                item.provider_id,
                item.model_id,
                item.effort,
            )
            for item in window
        }
        turn_savings = sum(item.verified_turn_savings for item in window)
        governance_turns = sum(item.governance_turns for item in window)
        token_values = [
            value
            for item in window
            if (value := item.verified_token_savings) is not None
        ]
        latency_values = [
            value
            for item in window
            if (value := item.latency_delta_s) is not None
        ]
        authorized = max(
            0,
            math.floor(turn_savings * self.policy.reinvestment_fraction)
            - governance_turns
            - self.policy.reserve_turns,
        )
        if len(dimensions) > 1:
            authorized = 0
        if self.policy.per_decision_cap_turns is not None:
            authorized = min(authorized, self.policy.per_decision_cap_turns)

        if not window:
            reason = "no matched account-auth observations"
        elif len(dimensions) > 1:
            reason = (
                "mixed verifier, quota-window, provider, model, or effort "
                "dimensions cannot authorize a pooled budget"
            )
        elif turn_savings <= 0:
            reason = "no verified provider-turn savings"
        elif authorized <= 0:
            reason = "verified turn savings do not cover governance spend and reserve"
        else:
            reason = "provider-turn budget authorized from matched account-auth savings"

        return AccountReinvestmentDecision(
            authorized_provider_turns=authorized,
            verified_turn_savings=turn_savings,
            governance_turns_spent=governance_turns,
            verified_token_savings=sum(token_values) if token_values else None,
            token_pair_count=len(token_values),
            latency_delta_s=sum(latency_values) if latency_values else None,
            latency_pair_count=len(latency_values),
            observation_count=len(window),
            spendable_observation_count=sum(
                item.verified_turn_savings > 0 for item in window
            ),
            comparison_dimension_count=len(dimensions),
            reason=reason,
        )
