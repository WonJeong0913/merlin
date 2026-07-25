"""Apply an M2-K route-local plan, re-run it, and promote or roll it back.

The common management substrate in :mod:`src.merlin_harness.management` deliberately
stops at a read-only decision plan.  This research-only module closes the next
bounded loop without changing the frozen Build Week package:

``complete invocation evidence -> route guard plan -> copy-on-write policy
candidate -> same-contract re-evaluation -> promote or rollback``.

The active skill library is not globally hidden or mutated.  M2-K's current
action is route-local, so the candidate is an immutable task/skill guard policy
layer over the same frozen library.  Every provisional trace must bind itself
to that candidate hash and expose exactly the staged task-conditioned view.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Protocol, Sequence

from src.merlin_harness.agent_adapter import AgentContractError
from src.merlin_harness.management import (
    DecisionAction,
    DecisionScope,
    ManagementArm,
    ManagementContractError,
    ManagementRoundInput,
    ManagementRoundOutput,
    ManagementRoundReport,
    TaskExposure,
    build_management_round_report,
    content_sha256,
    management_report_to_dict,
    run_management_round,
)
from src.merlin_harness.models import TraceRecord
from src.merlin_harness.traces import validate_agent_trace_evidence


LINEAGE_METADATA_KEY = "management_policy_lineage"


@dataclass(frozen=True, slots=True)
class RouteGuard:
    task_id: str
    skill_id: str
    evidence_trace_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoutePolicyCandidate:
    """Copy-on-write route policy staged over one frozen library snapshot."""

    parent_library_snapshot_id: str
    parent_library_snapshot_sha256: str
    source_plan_output_sha256: str
    policy_snapshot_id: str
    policy_snapshot_sha256: str
    exposure_decisions: tuple[TaskExposure, ...]
    guards: tuple[RouteGuard, ...]
    applied: bool = True
    underlying_library_mutated: bool = False


@dataclass(frozen=True, slots=True)
class M2KReevaluationCriteria:
    min_pass_rate_delta: float = 0.0
    min_pi_o_delta: float = 0.0
    min_pi_m_reduction: float = 1e-12
    require_complete_actual_evidence: bool = True
    require_no_guarded_invocations: bool = True


@dataclass(frozen=True, slots=True)
class ReevaluationCheck:
    name: str
    passed: bool
    score: float | None
    evidence: str


@dataclass(frozen=True, slots=True)
class M2KLifecycleReevaluationResult:
    accepted: bool
    rollback_required: bool
    resolution: str
    reason: str
    source_plan: ManagementRoundOutput
    candidate: RoutePolicyCandidate
    baseline_report: ManagementRoundReport
    provisional_report: ManagementRoundReport
    criteria: M2KReevaluationCriteria
    checks: tuple[ReevaluationCheck, ...]
    resolved_policy_snapshot_id: str
    resolved_policy_snapshot_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "accepted": self.accepted,
            "rollback_required": self.rollback_required,
            "resolution": self.resolution,
            "reason": self.reason,
            "source_plan": management_report_to_dict(self.source_plan),
            "candidate": asdict(self.candidate),
            "baseline_report": management_report_to_dict(self.baseline_report),
            "provisional_report": management_report_to_dict(self.provisional_report),
            "criteria": asdict(self.criteria),
            "checks": [asdict(check) for check in self.checks],
            "resolved_policy_snapshot_id": self.resolved_policy_snapshot_id,
            "resolved_policy_snapshot_sha256": self.resolved_policy_snapshot_sha256,
            "evidence_boundary": {
                "actual_invocation_required": self.criteria.require_complete_actual_evidence,
                "route_policy_applied": self.candidate.applied,
                "underlying_library_mutated": self.candidate.underlying_library_mutated,
                "same_frozen_library": True,
                "same_agent_model_tools_verifier_budget_contract": True,
            },
        }


class M2KReevaluationExecutor(Protocol):
    """Execute the staged task-conditioned policy and return immutable traces."""

    def run(self, candidate: RoutePolicyCandidate) -> Sequence[TraceRecord]: ...


def _validate_criteria(criteria: M2KReevaluationCriteria) -> None:
    for name, value in (
        ("min_pass_rate_delta", criteria.min_pass_rate_delta),
        ("min_pi_o_delta", criteria.min_pi_o_delta),
        ("min_pi_m_reduction", criteria.min_pi_m_reduction),
    ):
        if isinstance(value, bool) or value < 0:
            raise ManagementContractError(f"{name} must be >= 0")


def _exposure_map(exposures: Sequence[TaskExposure]) -> dict[str, tuple[str, ...]]:
    mapping = {item.task_id: item.skill_ids for item in exposures}
    if len(mapping) != len(exposures):
        raise ManagementContractError("route policy exposure contains duplicate task IDs")
    return mapping


def stage_m2k_route_policy(
    round_input: ManagementRoundInput,
    source_plan: ManagementRoundOutput,
) -> RoutePolicyCandidate:
    """Convert a read-only M2-K plan into an immutable route-policy candidate."""

    if round_input.arm is not ManagementArm.M2_K or source_plan.arm is not ManagementArm.M2_K:
        raise ManagementContractError("route-policy staging requires an M2-K input and plan")
    if source_plan.input_sha256 != round_input.input_sha256:
        raise ManagementContractError("M2-K source plan does not belong to the supplied input")
    if source_plan.parent_snapshot != round_input.parent_snapshot:
        raise ManagementContractError("M2-K source plan parent snapshot drifted")
    if source_plan.library_mutated:
        raise ManagementContractError("M2-K source plan must remain read-only before staging")
    if not source_plan.lifecycle_decisions:
        raise ManagementContractError("M2-K source plan contains no eligible route guard")

    exposures = _exposure_map(source_plan.exposure_decisions)
    guards: list[RouteGuard] = []
    for decision in source_plan.lifecycle_decisions:
        if (
            decision.scope is not DecisionScope.ROUTE_LOCAL
            or decision.action is not DecisionAction.GUARD_ROUTE
            or decision.task_id is None
        ):
            raise ManagementContractError("M2-K staging accepts only task-bound route guards")
        if decision.task_id not in exposures:
            raise ManagementContractError("M2-K route guard references a task outside the exposure plan")
        if decision.target_skill_id not in exposures[decision.task_id]:
            raise ManagementContractError("M2-K route guard target was not exposed on its task")
        guards.append(
            RouteGuard(
                task_id=decision.task_id,
                skill_id=decision.target_skill_id,
                evidence_trace_ids=decision.evidence_trace_ids,
            )
        )

    guard_pairs = {(guard.task_id, guard.skill_id) for guard in guards}
    if len(guard_pairs) != len(guards):
        raise ManagementContractError("M2-K source plan contains duplicate route guards")
    staged_exposures = tuple(
        TaskExposure(
            task_id=exposure.task_id,
            skill_ids=tuple(
                skill_id
                for skill_id in exposure.skill_ids
                if (exposure.task_id, skill_id) not in guard_pairs
            ),
        )
        for exposure in source_plan.exposure_decisions
    )
    candidate_payload = {
        "parent_library_snapshot_id": source_plan.parent_snapshot.snapshot_id,
        "parent_library_snapshot_sha256": source_plan.parent_snapshot.snapshot_sha256,
        "source_plan_output_sha256": source_plan.output_sha256,
        "exposure_decisions": staged_exposures,
        "guards": tuple(guards),
        "underlying_library_mutated": False,
    }
    candidate_sha256 = content_sha256(candidate_payload)
    return RoutePolicyCandidate(
        parent_library_snapshot_id=source_plan.parent_snapshot.snapshot_id,
        parent_library_snapshot_sha256=source_plan.parent_snapshot.snapshot_sha256,
        source_plan_output_sha256=source_plan.output_sha256,
        policy_snapshot_id=(
            f"{source_plan.parent_snapshot.snapshot_id}:m2k-route-policy:{candidate_sha256[:12]}"
        ),
        policy_snapshot_sha256=candidate_sha256,
        exposure_decisions=staged_exposures,
        guards=tuple(guards),
    )


def policy_lineage_payload(candidate: RoutePolicyCandidate) -> dict[str, object]:
    """Exact trace metadata required from every provisional trajectory."""

    return {
        "schema_version": 1,
        "policy_snapshot_id": candidate.policy_snapshot_id,
        "policy_snapshot_sha256": candidate.policy_snapshot_sha256,
        "source_plan_output_sha256": candidate.source_plan_output_sha256,
        "parent_library_snapshot_id": candidate.parent_library_snapshot_id,
        "parent_library_snapshot_sha256": candidate.parent_library_snapshot_sha256,
    }


def _validate_provisional_traces(
    candidate: RoutePolicyCandidate,
    traces: Sequence[TraceRecord],
) -> tuple[int, int]:
    expected_lineage = policy_lineage_payload(candidate)
    exposures = _exposure_map(candidate.exposure_decisions)
    guard_pairs = {(guard.task_id, guard.skill_id) for guard in candidate.guards}
    lineage_mismatches = 0
    guarded_invocations = 0
    for trace in traces:
        if trace.metadata.get(LINEAGE_METADATA_KEY) != expected_lineage:
            lineage_mismatches += 1
        if trace.task_id not in exposures or trace.invocation is None:
            raise ManagementContractError("provisional trace is missing its staged task exposure")
        if tuple(trace.invocation.provisioned_skill_ids) != exposures[trace.task_id]:
            raise ManagementContractError(
                f"provisional trace {trace.id} did not execute the exact staged exposure"
            )
        provisioned = set(exposures[trace.task_id])
        selected = set(trace.invocation.selected_skill_ids)
        if not selected.issubset(provisioned):
            raise ManagementContractError(
                f"provisional trace {trace.id} selected a skill outside the staged exposure"
            )
        try:
            evidence = validate_agent_trace_evidence(trace, verify_raw_trace=True)
        except AgentContractError as exc:
            raise ManagementContractError(
                f"provisional trace {trace.id} failed immutable evidence validation: {exc}"
            ) from exc
        invoked = {event.skill_id for event in evidence.invocation_events}
        if not invoked.issubset(provisioned):
            raise ManagementContractError(
                f"provisional trace {trace.id} invoked a skill outside the staged exposure"
            )
        for event in evidence.invocation_events:
            if (trace.task_id, event.skill_id) in guard_pairs:
                guarded_invocations += 1
    return lineage_mismatches, guarded_invocations


def _rate_value(report: ManagementRoundReport, name: str) -> float | None:
    return getattr(report.metrics, name).value


def _delta(after: float | None, before: float | None) -> float | None:
    if after is None or before is None:
        return None
    return after - before


def run_m2k_lifecycle_reevaluation(
    *,
    round_input: ManagementRoundInput,
    baseline_traces: Sequence[TraceRecord],
    executor: M2KReevaluationExecutor,
    criteria: M2KReevaluationCriteria | None = None,
) -> M2KLifecycleReevaluationResult:
    """Execute the bounded M2-K plan and return a promotion/rollback decision."""

    active_criteria = criteria or M2KReevaluationCriteria()
    _validate_criteria(active_criteria)
    source_plan = run_management_round(round_input)
    candidate = stage_m2k_route_policy(round_input, source_plan)

    evidence_ids = tuple(item.trace.id for item in round_input.m2k_evidence)
    baseline_ids = tuple(trace.id for trace in baseline_traces)
    if len(set(baseline_ids)) != len(baseline_ids) or set(evidence_ids) != set(baseline_ids):
        raise ManagementContractError(
            "baseline re-evaluation traces must exactly match the M2-K decision evidence"
        )
    baseline_report = build_management_round_report(source_plan, baseline_traces)

    provisional_traces = tuple(executor.run(candidate))
    lineage_mismatches, guarded_invocations = _validate_provisional_traces(
        candidate, provisional_traces
    )
    provisional_output = replace(
        source_plan,
        input_sha256=content_sha256(candidate),
        resulting_snapshot_id=candidate.policy_snapshot_id,
        resulting_snapshot_sha256=candidate.policy_snapshot_sha256,
        exposure_decisions=candidate.exposure_decisions,
        lifecycle_decisions=(),
        resulting_snapshot_kind="applied_route_policy_candidate",
    )
    provisional_report = build_management_round_report(
        provisional_output, provisional_traces
    )

    total = baseline_report.metrics.total_trajectories
    provisional_total = provisional_report.metrics.total_trajectories
    baseline_observed = baseline_report.metrics.verifier_observed
    provisional_observed = provisional_report.metrics.verifier_observed
    baseline_pass_rate = (
        baseline_report.metrics.verifier_passed / baseline_observed
        if baseline_observed
        else None
    )
    provisional_pass_rate = (
        provisional_report.metrics.verifier_passed / provisional_observed
        if provisional_observed
        else None
    )
    pass_delta = _delta(provisional_pass_rate, baseline_pass_rate)
    pi_o_delta = _delta(
        _rate_value(provisional_report, "pi_o"),
        _rate_value(baseline_report, "pi_o"),
    )
    pi_m_reduction = _delta(
        _rate_value(baseline_report, "pi_m"),
        _rate_value(provisional_report, "pi_m"),
    )
    complete_denominator = (
        baseline_report.metrics.actual_evidence_incomplete == 0
        and provisional_report.metrics.actual_evidence_incomplete == 0
        and baseline_report.metrics.actual_metric_eligible
        == provisional_report.metrics.actual_metric_eligible
        and baseline_report.metrics.actual_metric_eligible > 0
    )
    checks = (
        ReevaluationCheck(
            "same_trajectory_coverage",
            total == provisional_total,
            float(provisional_total - total),
            f"baseline={total}; provisional={provisional_total}",
        ),
        ReevaluationCheck(
            "policy_lineage_bound",
            lineage_mismatches == 0,
            float(lineage_mismatches),
            f"lineage_mismatches={lineage_mismatches}",
        ),
        ReevaluationCheck(
            "verifier_outcomes_complete",
            baseline_observed == total and provisional_observed == provisional_total,
            float((total - baseline_observed) + (provisional_total - provisional_observed)),
            (
                f"baseline_observed={baseline_observed}/{total}; "
                f"provisional_observed={provisional_observed}/{provisional_total}"
            ),
        ),
        ReevaluationCheck(
            "complete_actual_invocation_denominator",
            (not active_criteria.require_complete_actual_evidence) or complete_denominator,
            float(provisional_report.metrics.actual_metric_eligible),
            (
                "baseline_eligible="
                f"{baseline_report.metrics.actual_metric_eligible}; provisional_eligible="
                f"{provisional_report.metrics.actual_metric_eligible}; incomplete="
                f"{baseline_report.metrics.actual_evidence_incomplete}+"
                f"{provisional_report.metrics.actual_evidence_incomplete}"
            ),
        ),
        ReevaluationCheck(
            "guarded_routes_not_invoked",
            (not active_criteria.require_no_guarded_invocations)
            or guarded_invocations == 0,
            float(guarded_invocations),
            f"guarded_invocations={guarded_invocations}",
        ),
        ReevaluationCheck(
            "pass_rate_non_regression",
            pass_delta is not None
            and pass_delta >= active_criteria.min_pass_rate_delta,
            pass_delta,
            f"observed={pass_delta}; required>={active_criteria.min_pass_rate_delta}",
        ),
        ReevaluationCheck(
            "clean_oracle_non_regression",
            pi_o_delta is not None and pi_o_delta >= active_criteria.min_pi_o_delta,
            pi_o_delta,
            f"observed={pi_o_delta}; required>={active_criteria.min_pi_o_delta}",
        ),
        ReevaluationCheck(
            "shadowing_reduction",
            pi_m_reduction is not None
            and pi_m_reduction >= active_criteria.min_pi_m_reduction,
            pi_m_reduction,
            f"observed={pi_m_reduction}; required>={active_criteria.min_pi_m_reduction}",
        ),
    )
    accepted = all(check.passed for check in checks)
    parent_policy_payload = {
        "parent_library_snapshot_id": candidate.parent_library_snapshot_id,
        "parent_library_snapshot_sha256": candidate.parent_library_snapshot_sha256,
        "guards": (),
    }
    parent_policy_sha256 = content_sha256(parent_policy_payload)
    if accepted:
        resolved_id = candidate.policy_snapshot_id
        resolved_sha256 = candidate.policy_snapshot_sha256
        resolution = "provisional_route_policy_promoted"
        reason = "M2-K route policy passed the same-contract re-evaluation gate"
    else:
        resolved_id = f"{candidate.parent_library_snapshot_id}:m2k-route-policy:none"
        resolved_sha256 = parent_policy_sha256
        resolution = "provisional_route_policy_rolled_back"
        failed = ", ".join(check.name for check in checks if not check.passed)
        reason = f"M2-K route policy rejected by: {failed}"
    return M2KLifecycleReevaluationResult(
        accepted=accepted,
        rollback_required=not accepted,
        resolution=resolution,
        reason=reason,
        source_plan=source_plan,
        candidate=candidate,
        baseline_report=baseline_report,
        provisional_report=provisional_report,
        criteria=active_criteria,
        checks=checks,
        resolved_policy_snapshot_id=resolved_id,
        resolved_policy_snapshot_sha256=resolved_sha256,
    )
