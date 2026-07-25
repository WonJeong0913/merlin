"""Research metrics and gates for Merlin MVP."""

from __future__ import annotations

import random
from dataclasses import dataclass
from collections.abc import Iterable, Sequence
from collections.abc import Callable, Mapping
from typing import TypeVar

from .models import BehaviorDelta, InvocationRecord
from .traces import validate_agent_trace_evidence

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class InvocationObservation:
    """Paper-grade observation of skills actually invoked in one trajectory.

    ``invoked_skill_ids`` is the distinct invocation set ``I`` from
    *More Skills, Worse Agents?*: skills whose bodies were actually loaded or
    whose invocation action was otherwise observed. It must not contain skills
    that were merely provisioned, retrieved, ranked, or planned. The legacy
    ``InvocationRecord`` wrappers below still use ``selected_skill_ids`` as a
    compatibility proxy; new shadowing analyses should construct this type from
    invocation evidence instead.
    """

    task_id: str
    invoked_skill_ids: Sequence[str]
    oracle_skill_ids: Sequence[str]
    success: bool | None


@dataclass(frozen=True, slots=True)
class RateEstimate:
    """A rate with its numerator and denominator kept explicit."""

    numerator: int
    denominator: int
    value: float | None


@dataclass(frozen=True, slots=True)
class InvocationEventSummary:
    """Counts and pass rates for the paper's mutually exclusive n/m/o events."""

    total_observations: int
    eligible: int
    excluded_no_oracle: int
    counts: dict[str, int]
    event_probabilities: dict[str, RateEstimate]
    pass_counts: dict[str, int]
    observed_outcomes: dict[str, int]
    conditional_pass_rates: dict[str, RateEstimate]
    overall_pass_rate: RateEstimate


@dataclass(frozen=True, slots=True)
class MoreSkillsDecomposition:
    """Exact More Skills decomposition when all required rates are observed."""

    p_oracle: float | None
    p_library: float | None
    observed_drop: float | None
    delta_ctx: float | None
    delta_shd: float | None
    total: float | None
    invariant_error: float | None
    invariant_holds: bool | None
    unavailable_reason: str | None = None


def trace_to_invocation_observation(trace) -> InvocationObservation:
    """Convert one evidence-bearing trace into a paper-grade observation.

    This function intentionally rejects missing, incomplete, or hash-invalid
    actual invocation evidence.  It never substitutes the selector's decision
    for a skill-body load or provider-native invocation event.
    """

    evidence = validate_agent_trace_evidence(trace, verify_raw_trace=True)
    if not evidence.actual_invocation_evidence_complete:
        raise ValueError(
            "actual invocation evidence is incomplete; cannot derive a paper-grade InvocationObservation"
        )
    if trace.invocation is None:
        raise ValueError("agent trace has no normalized invocation record")
    invoked_skill_ids = tuple(dict.fromkeys(event.skill_id for event in evidence.invocation_events))
    return InvocationObservation(
        task_id=trace.task_id,
        invoked_skill_ids=invoked_skill_ids,
        oracle_skill_ids=tuple(trace.invocation.oracle_skill_ids),
        success=trace.invocation.success,
    )


def _rate_estimate(numerator: int, denominator: int) -> RateEstimate:
    return RateEstimate(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator else None,
    )


def _invocation_event(invoked_skill_ids: set[str], oracle_skill_ids: set[str]) -> str | None:
    """Return paper event n, m, or o; no-oracle tasks are ineligible."""

    if not oracle_skill_ids:
        return None
    if not invoked_skill_ids:
        return "n"
    if invoked_skill_ids.issubset(oracle_skill_ids):
        return "o"
    return "m"


def oracle_invocation_event_summary(
    observations: Iterable[InvocationObservation],
) -> InvocationEventSummary:
    """Summarize paper-defined invocation events without hiding denominators.

    The eligible population contains only observations with a non-empty oracle
    set. Event probabilities use that shared denominator. Conditional pass
    rates use only observations whose ``success`` is not ``None`` within the
    corresponding event. Undefined rates have ``value=None`` while preserving
    their numerator and denominator.
    """

    items = list(observations)
    counts = {"n": 0, "m": 0, "o": 0}
    pass_counts = {"n": 0, "m": 0, "o": 0}
    observed_outcomes = {"n": 0, "m": 0, "o": 0}
    excluded_no_oracle = 0

    for observation in items:
        event = _invocation_event(
            set(observation.invoked_skill_ids),
            set(observation.oracle_skill_ids),
        )
        if event is None:
            excluded_no_oracle += 1
            continue
        counts[event] += 1
        if observation.success is not None:
            observed_outcomes[event] += 1
            if observation.success:
                pass_counts[event] += 1

    eligible = sum(counts.values())
    total_passes = sum(pass_counts.values())
    total_observed_outcomes = sum(observed_outcomes.values())
    return InvocationEventSummary(
        total_observations=len(items),
        eligible=eligible,
        excluded_no_oracle=excluded_no_oracle,
        counts=counts,
        event_probabilities={
            event: _rate_estimate(count, eligible)
            for event, count in counts.items()
        },
        pass_counts=pass_counts,
        observed_outcomes=observed_outcomes,
        conditional_pass_rates={
            event: _rate_estimate(pass_counts[event], observed_outcomes[event])
            for event in counts
        },
        overall_pass_rate=_rate_estimate(total_passes, total_observed_outcomes),
    )


def _weighted_value(weight: float, value: float | None, *, tolerance: float) -> float | None:
    if abs(weight) <= tolerance:
        return 0.0
    if value is None:
        return None
    return weight * value


def more_skills_decomposition(
    oracle_only: InvocationEventSummary,
    full_library: InvocationEventSummary,
    *,
    tolerance: float = 1e-12,
) -> MoreSkillsDecomposition:
    """Compute ``Delta_ctx`` and ``Delta_shd`` from matched event summaries.

    This implements Eq. 10 from *More Skills, Worse Agents?*:

    ``Delta_ctx = pi_n* (rho_n* - rho_n) + pi_o* (rho_o* - rho_o)``

    ``Delta_shd = (pi_n* - pi_n) rho_n + (pi_o* - pi_o) rho_o - pi_m rho_m``

    The oracle-only arm must contain no mixed/distractor event. Both arms must
    have a non-empty eligible population and complete binary outcomes; otherwise
    decomposition values are ``None`` with an explicit reason. ``total`` is
    checked against the directly observed pass-rate drop.
    """

    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")
    if oracle_only.counts["m"]:
        raise ValueError("oracle-only summary contains distractor invocations (event m)")

    p_oracle = oracle_only.overall_pass_rate.value
    p_library = full_library.overall_pass_rate.value
    observed_drop = (
        p_oracle - p_library
        if p_oracle is not None and p_library is not None
        else None
    )

    if oracle_only.eligible == 0 or full_library.eligible == 0:
        return MoreSkillsDecomposition(
            p_oracle=p_oracle,
            p_library=p_library,
            observed_drop=observed_drop,
            delta_ctx=None,
            delta_shd=None,
            total=None,
            invariant_error=None,
            invariant_holds=None,
            unavailable_reason="eligible oracle-task denominator is zero",
        )
    if (
        oracle_only.overall_pass_rate.denominator != oracle_only.eligible
        or full_library.overall_pass_rate.denominator != full_library.eligible
    ):
        return MoreSkillsDecomposition(
            p_oracle=p_oracle,
            p_library=p_library,
            observed_drop=observed_drop,
            delta_ctx=None,
            delta_shd=None,
            total=None,
            invariant_error=None,
            invariant_holds=None,
            unavailable_reason="one or more eligible trajectories have no observed success outcome",
        )

    pi_star = {event: oracle_only.event_probabilities[event].value for event in ("n", "o")}
    pi = {event: full_library.event_probabilities[event].value for event in ("n", "m", "o")}
    rho_star = {event: oracle_only.conditional_pass_rates[event].value for event in ("n", "o")}
    rho = {event: full_library.conditional_pass_rates[event].value for event in ("n", "m", "o")}

    # Eligible denominators above guarantee the event probabilities are defined.
    assert all(value is not None for value in [*pi_star.values(), *pi.values()])
    pi_n_star = float(pi_star["n"])
    pi_o_star = float(pi_star["o"])
    pi_n = float(pi["n"])
    pi_m = float(pi["m"])
    pi_o = float(pi["o"])

    ctx_n = _weighted_value(
        pi_n_star,
        None if rho_star["n"] is None or rho["n"] is None else rho_star["n"] - rho["n"],
        tolerance=tolerance,
    )
    ctx_o = _weighted_value(
        pi_o_star,
        None if rho_star["o"] is None or rho["o"] is None else rho_star["o"] - rho["o"],
        tolerance=tolerance,
    )
    shd_n = _weighted_value(pi_n_star - pi_n, rho["n"], tolerance=tolerance)
    shd_o = _weighted_value(pi_o_star - pi_o, rho["o"], tolerance=tolerance)
    shd_m = _weighted_value(-pi_m, rho["m"], tolerance=tolerance)

    if None in {ctx_n, ctx_o, shd_n, shd_o, shd_m}:
        return MoreSkillsDecomposition(
            p_oracle=p_oracle,
            p_library=p_library,
            observed_drop=observed_drop,
            delta_ctx=None,
            delta_shd=None,
            total=None,
            invariant_error=None,
            invariant_holds=None,
            unavailable_reason="a required invocation event has no conditional pass-rate estimate",
        )

    delta_ctx = float(ctx_n) + float(ctx_o)
    delta_shd = float(shd_n) + float(shd_o) + float(shd_m)
    total = delta_ctx + delta_shd
    invariant_error = None if observed_drop is None else total - observed_drop
    invariant_holds = None if invariant_error is None else abs(invariant_error) <= tolerance
    return MoreSkillsDecomposition(
        p_oracle=p_oracle,
        p_library=p_library,
        observed_drop=observed_drop,
        delta_ctx=delta_ctx,
        delta_shd=delta_shd,
        total=total,
        invariant_error=invariant_error,
        invariant_holds=invariant_holds,
    )


def normalized_gain(p_skill: float, p_vanilla: float) -> float:
    """SkillsBench-style normalized gain.

    When the vanilla score is saturated the denominator is undefined, so the
    raw delta is returned instead: 0 when the skill also saturates, negative
    when the skill regresses a saturated baseline.
    """

    if p_vanilla >= 1.0:
        return p_skill - p_vanilla
    return (p_skill - p_vanilla) / (1.0 - p_vanilla)


def library_induced_drop(p_oracle: float, p_library: float) -> float:
    """More Skills-style library-induced drop."""

    return p_oracle - p_library


def self_harness_accept(delta_in: float, delta_held_out: float) -> bool:
    """Self-Harness non-regressive promotion rule."""

    return delta_in >= 0 and delta_held_out >= 0 and max(delta_in, delta_held_out) > 0


def clean_oracle_invocation_rate(records: Iterable[InvocationRecord]) -> float:
    """pi_o over tasks with a non-empty oracle set."""

    return oracle_invocation_event_rates(records)["oracle_only"]


def shadowing_rate(records: Iterable[InvocationRecord]) -> float:
    """pi_m over tasks with a non-empty oracle set: at least one distractor selected."""

    rates = oracle_invocation_event_rates(records)
    return rates["wrong"] + rates["mixed"]


def oracle_invocation_event_rates(records: Iterable[InvocationRecord]) -> dict[str, float]:
    """Legacy selected-skill proxy for mutually exclusive route-event rates.

    Paper-grade analyses should use :func:`oracle_invocation_event_summary`
    with :class:`InvocationObservation` built from actual invocation evidence.
    This compatibility API continues to interpret ``selected_skill_ids`` as the
    invocation set and returns historical zero values when its denominator is
    zero.

    Events:
    - oracle_only: selected != empty and selected subset oracle.
    - wrong: selected non-oracle skills only.
    - mixed: selected at least one oracle and at least one distractor.
    - empty: selected nothing.
    """

    counts = {"oracle_only": 0, "wrong": 0, "mixed": 0, "empty": 0}
    eligible = 0
    for record in records:
        oracle = set(record.oracle_skill_ids)
        if not oracle:
            continue
        eligible += 1
        selected = set(record.selected_skill_ids)
        if not selected:
            counts["empty"] += 1
        elif selected.issubset(oracle):
            counts["oracle_only"] += 1
        elif selected & oracle:
            counts["mixed"] += 1
        else:
            counts["wrong"] += 1
    if not eligible:
        return {"oracle_only": 0.0, "wrong": 0.0, "mixed": 0.0, "empty": 0.0, "eligible": 0.0}
    return {name: count / eligible for name, count in counts.items()} | {"eligible": float(eligible)}


def wrong_skill_invocation_rate(records: Iterable[InvocationRecord]) -> float:
    """Rate of selecting only distractors when an oracle skill exists."""

    return oracle_invocation_event_rates(records)["wrong"]


def mixed_skill_invocation_rate(records: Iterable[InvocationRecord]) -> float:
    """Rate of selecting oracle and distractor skills together."""

    return oracle_invocation_event_rates(records)["mixed"]


def spurious_invocation_rate(records: Iterable[InvocationRecord]) -> float:
    """Fraction of no-oracle tasks where any skill was selected."""

    spurious = 0
    eligible = 0
    for record in records:
        if record.oracle_skill_ids:
            continue
        eligible += 1
        if record.selected_skill_ids:
            spurious += 1
    return spurious / eligible if eligible else 0.0


def no_skill_when_oracle_rate(records: Iterable[InvocationRecord]) -> float:
    return oracle_invocation_event_rates(records)["empty"]


def cost_no_gain_rate(deltas: Iterable[BehaviorDelta]) -> float:
    """Fraction of CTA-lite deltas that increase cost without success gain."""

    items = list(deltas)
    if not items:
        return 0.0
    flagged = 0
    for delta in items:
        if "cost_increase_without_gain" in delta.labels:
            flagged += 1
    return flagged / len(items)


def route_risk_components(
    records: Iterable[InvocationRecord],
    deltas: Iterable[BehaviorDelta] = (),
) -> dict[str, float]:
    """Mutually interpretable route-risk components.

    The oracle-task components share the oracle-task denominator. The spurious
    component uses the no-oracle denominator, so consumers should report the
    components alongside any weighted score.
    """

    records_list = list(records) if not isinstance(records, list) else records
    rates = oracle_invocation_event_rates(records_list)
    return {
        "wrong": rates["wrong"],
        "mixed": rates["mixed"],
        "empty": rates["empty"],
        "spurious": spurious_invocation_rate(records_list),
        "cost_no_gain": cost_no_gain_rate(deltas),
    }


def route_risk_score(
    records: Iterable[InvocationRecord],
    deltas: Iterable[BehaviorDelta] = (),
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Weighted R_route dashboard score.

    This is a reporting score, not a standalone claim metric. Missing weights
    default to 1.0 so the unweighted score is just the sum of components.
    """

    components = route_risk_components(records, deltas)
    if weights is None:
        weights = {name: 1.0 for name in components}
    return sum(float(weights.get(name, 0.0)) * value for name, value in components.items())


def paired_bootstrap_ci(
    items: Sequence[T],
    statistic: Callable[[Sequence[T]], float],
    *,
    iterations: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> dict[str, float]:
    """Paired bootstrap confidence interval for split-level experiment rows."""

    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if not items:
        return {"estimate": 0.0, "low": 0.0, "high": 0.0, "confidence": confidence, "iterations": 0.0}

    rng = random.Random(seed)
    n = len(items)
    estimate = float(statistic(items))
    samples: list[float] = []
    for _ in range(iterations):
        resample = [items[rng.randrange(n)] for _ in range(n)]
        samples.append(float(statistic(resample)))

    alpha = (1.0 - confidence) / 2.0
    samples.sort()
    return {
        "estimate": estimate,
        "low": _quantile_sorted(samples, alpha),
        "high": _quantile_sorted(samples, 1.0 - alpha),
        "confidence": confidence,
        "iterations": float(iterations),
    }


def _quantile_sorted(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("sorted_values must not be empty")
    if q <= 0.0:
        return sorted_values[0]
    if q >= 1.0:
        return sorted_values[-1]
    position = q * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def select_first_success_or_best_utility(candidates: Sequence[tuple[str, bool, float]]) -> str | None:
    """SkillRevise-style selector.

    Each candidate is `(skill_id, success, utility)`, ordered by evaluated version.
    """

    for skill_id, success, _utility in candidates:
        if success:
            return skill_id
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[2])[0]
