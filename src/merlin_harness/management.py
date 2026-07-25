"""Frozen management-policy rounds and comparable actual-invocation reports.

This module is deliberately narrower than a lifecycle runtime.  It records
what a policy *would* expose or change from a frozen library, but never mutates
the live library.  The policy input is capability-separated from the common
post-run report so that an M2-H policy cannot inspect outcomes or actual skill
invocation evidence merely because those values are later reported.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .agent_adapter import AgentContractError
from .metrics import RateEstimate
from .models import TraceRecord
from .traces import validate_agent_trace_evidence


class ManagementContractError(ValueError):
    """Raised when a management arm is not a fair or auditable comparison."""


class ManagementArm(str, Enum):
    M0 = "M0"
    M1 = "M1"
    M2_H = "M2-H"
    M2_K = "M2-K"


class DecisionScope(str, Enum):
    EXPOSURE = "exposure"
    SKILL_LOCAL = "skill_local"
    ROUTE_LOCAL = "route_local"


class DecisionAction(str, Enum):
    HIDE_SKILL = "hide_skill"
    GUARD_ROUTE = "guard_route"


def _canonical(value: Any) -> Any:
    """Convert supported values into deterministic JSON-compatible data."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _canonical(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, set):
        return [_canonical(item) for item in sorted(value)]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_sha256(value: str, *, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ManagementContractError(f"{label} must be a lowercase SHA-256")


def _require_nonempty(value: str, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ManagementContractError(f"{label} must be non-empty")


@dataclass(frozen=True, slots=True)
class LibrarySnapshotIdentity:
    """Content-addressed, frozen active library shared by every arm."""

    snapshot_id: str
    snapshot_sha256: str
    active_skill_ids: tuple[str, ...]
    active_library_capacity: int


@dataclass(frozen=True, slots=True)
class ManagementRunContract:
    """All conditions that must be equal before policy reports may be compared."""

    library_snapshot: LibrarySnapshotIdentity
    split_id: str
    task_ids: tuple[str, ...]
    base_agent_id: str
    base_agent_version: str
    backend: str
    model_id: str
    effort: str | None
    tools: tuple[str, ...]
    verifier_ids_by_task: tuple[tuple[str, str], ...]
    budget_id: str
    repeats: int
    schema_version: int = 1

    @property
    def comparison_sha256(self) -> str:
        return content_sha256(self)


@dataclass(frozen=True, slots=True)
class TaskExposure:
    """A task-conditioned, predeclared skill exposure list."""

    task_id: str
    skill_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ManagementThresholds:
    """Pre-registered deterministic thresholds; no outcome tuning is allowed."""

    m2h_max_usage: int = 0
    m2h_min_recency_rank: int = 1
    m2k_min_shadowing_events: int = 1


@dataclass(frozen=True, slots=True)
class TelemetryEvidence:
    """M2-H's complete evidence surface: no task outcome or invocation fields."""

    trace_id: str
    skill_id: str
    usage_count: int
    view_count: int
    patch_count: int
    recency_rank: int


@dataclass(frozen=True, slots=True)
class M2KTraceEvidence:
    """A trace plus registered regression baseline for M2-K only.

    The trace is verified at round execution time, including its raw-trace
    hash.  Incomplete actual-invocation evidence remains in the denominator but
    cannot become a decision reason.
    """

    trace: TraceRecord
    parent_verifier_passed: bool
    regression_group: str
    trace_record_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        # TraceRecord is a legacy mutable container.  Pin its normalized form at
        # evidence construction so a caller cannot alter task/outcome/selection
        # fields between a frozen round declaration and policy execution.
        object.__setattr__(self, "trace_record_sha256", content_sha256(self.trace))


@dataclass(frozen=True, slots=True)
class ManagementRoundInput:
    """Immutable policy-time input with evidence capabilities separated by arm."""

    contract: ManagementRunContract
    arm: ManagementArm
    policy_version: str
    parent_snapshot: LibrarySnapshotIdentity
    thresholds: ManagementThresholds
    allowed_actions: tuple[DecisionAction, ...]
    predeclared_exposure: tuple[TaskExposure, ...] = ()
    fixed_top_k_exposure: tuple[TaskExposure, ...] = ()
    m2h_telemetry: tuple[TelemetryEvidence, ...] = ()
    m2k_evidence: tuple[M2KTraceEvidence, ...] = ()

    @property
    def input_sha256(self) -> str:
        return content_sha256(self)


@dataclass(frozen=True, slots=True)
class ManagementDecision:
    """Read-only policy decision.  It is not a live library mutation."""

    scope: DecisionScope
    action: DecisionAction
    target_skill_id: str
    task_id: str | None
    reason: str
    evidence_trace_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceDenominator:
    """Policy-time eligibility accounting, including incomplete evidence."""

    total: int
    eligible: int
    excluded_incomplete: int
    excluded_no_oracle: int


@dataclass(frozen=True, slots=True)
class ManagementRoundOutput:
    """The immutable result of a policy review, before any candidate is run."""

    contract: ManagementRunContract
    arm: ManagementArm
    policy_version: str
    input_sha256: str
    parent_snapshot: LibrarySnapshotIdentity
    resulting_snapshot_id: str
    resulting_snapshot_sha256: str
    resulting_snapshot_active_library_capacity: int
    exposure_decisions: tuple[TaskExposure, ...]
    lifecycle_decisions: tuple[ManagementDecision, ...]
    decision_evidence_denominator: EvidenceDenominator
    library_mutated: bool = False
    resulting_snapshot_kind: str = "read_only_decision_plan"

    @property
    def output_sha256(self) -> str:
        return content_sha256(self)


@dataclass(frozen=True, slots=True)
class ManagementTaskReport:
    """Common per-trajectory report row for every comparison arm."""

    trace_id: str
    task_id: str
    verifier_passed: bool | None
    verifier_score: float | None
    provisioned_skill_ids: tuple[str, ...]
    selected_skill_ids: tuple[str, ...]
    actual_invoked_skill_ids: tuple[str, ...]
    oracle_skill_ids: tuple[str, ...]
    actual_invocation_evidence_complete: bool
    nmo_event: str | None
    route_class: str
    cost: float | None
    latency_s: float | None


@dataclass(frozen=True, slots=True)
class OptionalMetricSummary:
    present: int
    missing: int
    mean: float | None


@dataclass(frozen=True, slots=True)
class ManagementReportMetrics:
    """Same report schema for M0/M1/M2-H/M2-K; absent values stay explicit."""

    total_trajectories: int
    verifier_passed: int
    verifier_observed: int
    verifier_mean_score: float | None
    verifier_score_missing: int
    actual_metric_eligible: int
    actual_evidence_incomplete: int
    no_oracle: int
    n_count: int
    m_count: int
    o_count: int
    wrong_count: int
    mixed_count: int
    empty_count: int
    spurious_count: int
    pi_o: RateEstimate
    pi_wrong: RateEstimate
    pi_mixed: RateEstimate
    pi_empty: RateEstimate
    pi_m: RateEstimate
    spurious_rate: RateEstimate
    cost: OptionalMetricSummary
    latency: OptionalMetricSummary


@dataclass(frozen=True, slots=True)
class ManagementRoundReport:
    output: ManagementRoundOutput
    task_reports: tuple[ManagementTaskReport, ...]
    metrics: ManagementReportMetrics

    @property
    def report_sha256(self) -> str:
        return content_sha256(self)


@dataclass(frozen=True, slots=True)
class ManagementComparisonReport:
    """Deterministic fair-comparison envelope for the four policy arms."""

    common_contract_sha256: str
    reports: tuple[ManagementRoundReport, ...]

    @property
    def comparison_sha256(self) -> str:
        return content_sha256(self)


def _validate_snapshot(snapshot: LibrarySnapshotIdentity, *, label: str) -> None:
    _require_nonempty(snapshot.snapshot_id, label=f"{label}.snapshot_id")
    _require_sha256(snapshot.snapshot_sha256, label=f"{label}.snapshot_sha256")
    if snapshot.active_library_capacity < 0:
        raise ManagementContractError(f"{label}.active_library_capacity must be >= 0")
    if len(snapshot.active_skill_ids) != snapshot.active_library_capacity:
        raise ManagementContractError(f"{label} capacity must equal the frozen active skill-id count")
    if len(set(snapshot.active_skill_ids)) != len(snapshot.active_skill_ids):
        raise ManagementContractError(f"{label}.active_skill_ids must be unique")
    if any(not isinstance(skill_id, str) or not skill_id for skill_id in snapshot.active_skill_ids):
        raise ManagementContractError(f"{label}.active_skill_ids must be non-empty strings")


def validate_management_run_contract(contract: ManagementRunContract) -> None:
    if contract.schema_version != 1:
        raise ManagementContractError(f"unsupported management run schema_version: {contract.schema_version}")
    _validate_snapshot(contract.library_snapshot, label="library_snapshot")
    for label, value in (
        ("split_id", contract.split_id),
        ("base_agent_id", contract.base_agent_id),
        ("base_agent_version", contract.base_agent_version),
        ("backend", contract.backend),
        ("model_id", contract.model_id),
        ("budget_id", contract.budget_id),
    ):
        _require_nonempty(value, label=label)
    if contract.effort is not None:
        _require_nonempty(contract.effort, label="effort")
    if not contract.task_ids or len(set(contract.task_ids)) != len(contract.task_ids):
        raise ManagementContractError("task_ids must be a non-empty unique tuple")
    if any(not task_id for task_id in contract.task_ids):
        raise ManagementContractError("task_ids must contain non-empty strings")
    if len(set(contract.tools)) != len(contract.tools) or any(not tool for tool in contract.tools):
        raise ManagementContractError("tools must be unique non-empty strings")
    verifier_map = dict(contract.verifier_ids_by_task)
    if len(verifier_map) != len(contract.verifier_ids_by_task) or set(verifier_map) != set(contract.task_ids):
        raise ManagementContractError("verifier_ids_by_task must contain one verifier for every task")
    if any(not verifier_id for verifier_id in verifier_map.values()):
        raise ManagementContractError("verifier_ids_by_task values must be non-empty")
    if contract.repeats < 1:
        raise ManagementContractError("repeats must be >= 1")


def _exposure_map(
    exposures: tuple[TaskExposure, ...],
    contract: ManagementRunContract,
    *,
    label: str,
    require_expanded: bool,
) -> dict[str, tuple[str, ...]]:
    mapping = {exposure.task_id: exposure.skill_ids for exposure in exposures}
    if len(mapping) != len(exposures) or set(mapping) != set(contract.task_ids):
        raise ManagementContractError(f"{label} must contain one exposure decision for every frozen task")
    active_ids = set(contract.library_snapshot.active_skill_ids)
    for task_id, skill_ids in mapping.items():
        if len(set(skill_ids)) != len(skill_ids):
            raise ManagementContractError(f"{label} for {task_id} contains duplicate skill IDs")
        if set(skill_ids) - active_ids:
            raise ManagementContractError(f"{label} for {task_id} exposes a skill outside the frozen library")
        if len(skill_ids) > contract.library_snapshot.active_library_capacity:
            raise ManagementContractError(f"{label} for {task_id} exceeds equal active-library capacity")
        if require_expanded and tuple(skill_ids) != contract.library_snapshot.active_skill_ids:
            raise ManagementContractError(f"{label} for {task_id} must equal the predeclared expanded frozen library")
    return mapping


def _validate_thresholds(thresholds: ManagementThresholds) -> None:
    if thresholds.m2h_max_usage < 0:
        raise ManagementContractError("m2h_max_usage must be >= 0")
    if thresholds.m2h_min_recency_rank < 0:
        raise ManagementContractError("m2h_min_recency_rank must be >= 0")
    if thresholds.m2k_min_shadowing_events < 1:
        raise ManagementContractError("m2k_min_shadowing_events must be >= 1")


def _validate_trace_against_contract(trace: TraceRecord, contract: ManagementRunContract) -> Any:
    """Verify raw evidence and every fair-run field before it can be reported."""

    try:
        evidence = validate_agent_trace_evidence(trace, verify_raw_trace=True)
    except AgentContractError as exc:
        raise ManagementContractError(f"trace {trace.id} failed immutable evidence validation: {exc}") from exc
    agent_contract = evidence.contract
    expected_verifier = dict(contract.verifier_ids_by_task).get(trace.task_id)
    if trace.task_id not in contract.task_ids:
        raise ManagementContractError(f"trace {trace.id} task is outside the frozen split")
    for label, actual, expected in (
        ("agent_id", agent_contract.agent_id, contract.base_agent_id),
        ("agent_version", agent_contract.agent_version, contract.base_agent_version),
        ("backend", agent_contract.backend, contract.backend),
        ("model_id", agent_contract.model_id, contract.model_id),
        ("effort", agent_contract.effort, contract.effort),
        ("budget_id", agent_contract.budget_id, contract.budget_id),
        ("library_snapshot_id", agent_contract.library_snapshot_id, contract.library_snapshot.snapshot_id),
        ("library_snapshot_sha256", agent_contract.library_snapshot_sha256, contract.library_snapshot.snapshot_sha256),
        ("verifier_id", agent_contract.verifier_id, expected_verifier),
    ):
        if actual != expected:
            raise ManagementContractError(
                f"trace {trace.id} {label} mismatch: expected={expected!r} actual={actual!r}"
            )
    if trace.invocation is None:
        raise ManagementContractError(f"trace {trace.id} has no normalized invocation record")
    active_ids = set(contract.library_snapshot.active_skill_ids)
    if set(trace.invocation.provisioned_skill_ids) - active_ids:
        raise ManagementContractError(f"trace {trace.id} provisions skills outside the frozen library")
    if len(trace.invocation.provisioned_skill_ids) > contract.library_snapshot.active_library_capacity:
        raise ManagementContractError(f"trace {trace.id} exceeds equal active-library capacity")
    return evidence


def validate_management_round_input(round_input: ManagementRoundInput) -> None:
    validate_management_run_contract(round_input.contract)
    _validate_snapshot(round_input.parent_snapshot, label="parent_snapshot")
    if round_input.parent_snapshot != round_input.contract.library_snapshot:
        raise ManagementContractError("parent_snapshot must exactly match the frozen common library snapshot")
    _require_nonempty(round_input.policy_version, label="policy_version")
    _validate_thresholds(round_input.thresholds)
    if len(set(round_input.allowed_actions)) != len(round_input.allowed_actions):
        raise ManagementContractError("allowed_actions must not contain duplicates")

    if round_input.arm is ManagementArm.M0:
        if round_input.fixed_top_k_exposure or round_input.m2h_telemetry or round_input.m2k_evidence:
            raise ManagementContractError("M0 accepts no adaptation evidence or retrieval policy input")
        if round_input.allowed_actions:
            raise ManagementContractError("M0 permits no lifecycle action")
        _exposure_map(round_input.predeclared_exposure, round_input.contract, label="M0 expanded exposure", require_expanded=True)
        return

    if round_input.arm is ManagementArm.M1:
        if round_input.predeclared_exposure or round_input.m2h_telemetry or round_input.m2k_evidence:
            raise ManagementContractError("M1 accepts only its pre-registered fixed top-k mapping")
        if round_input.allowed_actions:
            raise ManagementContractError("M1 permits no lifecycle action or outcome threshold tuning")
        _exposure_map(round_input.fixed_top_k_exposure, round_input.contract, label="M1 fixed top-k exposure", require_expanded=False)
        return

    if round_input.arm is ManagementArm.M2_H:
        if round_input.fixed_top_k_exposure or round_input.m2k_evidence:
            raise ManagementContractError("M2-H may not receive outcome, invocation, shadowing, or regression evidence")
        if set(round_input.allowed_actions) != {DecisionAction.HIDE_SKILL}:
            raise ManagementContractError("M2-H must pre-register only skill-local hide decisions")
        _exposure_map(round_input.predeclared_exposure, round_input.contract, label="M2-H predeclared exposure", require_expanded=False)
        seen_telemetry: set[tuple[str, str]] = set()
        for telemetry in round_input.m2h_telemetry:
            if not telemetry.trace_id or telemetry.skill_id not in round_input.contract.library_snapshot.active_skill_ids:
                raise ManagementContractError("M2-H telemetry requires a trace ID and a frozen active skill")
            if min(telemetry.usage_count, telemetry.view_count, telemetry.patch_count, telemetry.recency_rank) < 0:
                raise ManagementContractError("M2-H telemetry values must be >= 0")
            key = (telemetry.trace_id, telemetry.skill_id)
            if key in seen_telemetry:
                raise ManagementContractError("M2-H telemetry cannot duplicate a trace/skill observation")
            seen_telemetry.add(key)
        return

    if round_input.arm is ManagementArm.M2_K:
        if round_input.fixed_top_k_exposure or round_input.m2h_telemetry:
            raise ManagementContractError("M2-K accepts only complete actual-invocation outcome/regression evidence")
        if set(round_input.allowed_actions) != {DecisionAction.GUARD_ROUTE}:
            raise ManagementContractError("M2-K must pre-register only route-local guard decisions")
        _exposure_map(round_input.predeclared_exposure, round_input.contract, label="M2-K predeclared exposure", require_expanded=False)
        trace_ids: set[str] = set()
        for item in round_input.m2k_evidence:
            if not isinstance(item.parent_verifier_passed, bool) or not item.regression_group:
                raise ManagementContractError("M2-K evidence requires a boolean parent outcome and regression group")
            if item.trace.id in trace_ids:
                raise ManagementContractError("M2-K evidence cannot duplicate a trace")
            trace_ids.add(item.trace.id)
            if content_sha256(item.trace) != item.trace_record_sha256:
                raise ManagementContractError("M2-K trace record changed after immutable evidence declaration")
            _validate_trace_against_contract(item.trace, round_input.contract)
        return

    raise ManagementContractError(f"unsupported management arm: {round_input.arm}")


def _m2k_eligibility(item: M2KTraceEvidence, contract: ManagementRunContract) -> tuple[Any, str | None]:
    evidence = _validate_trace_against_contract(item.trace, contract)
    if not evidence.actual_invocation_evidence_complete:
        return evidence, "incomplete"
    if item.trace.invocation is None or not item.trace.invocation.oracle_skill_ids:
        return evidence, "no_oracle"
    return evidence, None


def _plan_snapshot(round_input: ManagementRoundInput, exposures: tuple[TaskExposure, ...], decisions: tuple[ManagementDecision, ...]) -> tuple[str, str]:
    plan_payload = {
        "parent_snapshot": round_input.parent_snapshot,
        "arm": round_input.arm,
        "input_sha256": round_input.input_sha256,
        "exposure_decisions": exposures,
        "lifecycle_decisions": decisions,
        "library_mutated": False,
    }
    plan_sha256 = content_sha256(plan_payload)
    return f"{round_input.parent_snapshot.snapshot_id}:management-plan:{round_input.arm.value}:{plan_sha256[:12]}", plan_sha256


def run_management_round(round_input: ManagementRoundInput) -> ManagementRoundOutput:
    """Produce deterministic, read-only decisions from an arm's allowed evidence."""

    validate_management_round_input(round_input)
    contract = round_input.contract
    if round_input.arm is ManagementArm.M0:
        exposures = round_input.predeclared_exposure
        decisions: tuple[ManagementDecision, ...] = ()
        denominator = EvidenceDenominator(total=0, eligible=0, excluded_incomplete=0, excluded_no_oracle=0)
    elif round_input.arm is ManagementArm.M1:
        exposures = round_input.fixed_top_k_exposure
        decisions = ()
        denominator = EvidenceDenominator(total=0, eligible=0, excluded_incomplete=0, excluded_no_oracle=0)
    elif round_input.arm is ManagementArm.M2_H:
        exposures = round_input.predeclared_exposure
        candidates: dict[str, list[TelemetryEvidence]] = defaultdict(list)
        for telemetry in round_input.m2h_telemetry:
            if (
                telemetry.usage_count <= round_input.thresholds.m2h_max_usage
                and telemetry.patch_count == 0
                and telemetry.recency_rank >= round_input.thresholds.m2h_min_recency_rank
            ):
                candidates[telemetry.skill_id].append(telemetry)
        decisions = tuple(
            ManagementDecision(
                scope=DecisionScope.SKILL_LOCAL,
                action=DecisionAction.HIDE_SKILL,
                target_skill_id=skill_id,
                task_id=None,
                reason=(
                    "usage/view/patch/recency telemetry crossed the pre-registered M2-H stale-skill threshold; "
                    "no outcome or invocation evidence was available to this policy"
                ),
                evidence_trace_ids=tuple(sorted(item.trace_id for item in telemetry_items)),
            )
            for skill_id, telemetry_items in sorted(candidates.items())
        )
        denominator = EvidenceDenominator(
            total=len(round_input.m2h_telemetry),
            eligible=len(round_input.m2h_telemetry),
            excluded_incomplete=0,
            excluded_no_oracle=0,
        )
    else:
        exposures = round_input.predeclared_exposure
        routes: dict[tuple[str, str], list[str]] = defaultdict(list)
        incomplete = 0
        no_oracle = 0
        eligible = 0
        for item in round_input.m2k_evidence:
            evidence, excluded_reason = _m2k_eligibility(item, contract)
            if excluded_reason == "incomplete":
                incomplete += 1
                continue
            if excluded_reason == "no_oracle":
                no_oracle += 1
                continue
            eligible += 1
            assert item.trace.invocation is not None
            invoked = {event.skill_id for event in evidence.invocation_events}
            oracle = set(item.trace.invocation.oracle_skill_ids)
            wrong_ids = invoked - oracle
            current_passed = item.trace.invocation.success
            regression = item.parent_verifier_passed and current_passed is False
            if wrong_ids and regression:
                for skill_id in sorted(wrong_ids):
                    routes[(item.trace.task_id, skill_id)].append(item.trace.id)
        decisions = tuple(
            ManagementDecision(
                scope=DecisionScope.ROUTE_LOCAL,
                action=DecisionAction.GUARD_ROUTE,
                target_skill_id=skill_id,
                task_id=task_id,
                reason=(
                    "complete actual invocation evidence shows a non-oracle skill on a verifier regression; "
                    "this is a route-local guard proposal, not a skill-content diagnosis"
                ),
                evidence_trace_ids=tuple(sorted(trace_ids)),
            )
            for (task_id, skill_id), trace_ids in sorted(routes.items())
            if len(trace_ids) >= round_input.thresholds.m2k_min_shadowing_events
        )
        denominator = EvidenceDenominator(
            total=len(round_input.m2k_evidence),
            eligible=eligible,
            excluded_incomplete=incomplete,
            excluded_no_oracle=no_oracle,
        )

    exposure_tuple = tuple(exposures)
    decision_tuple = tuple(decisions)
    resulting_snapshot_id, resulting_snapshot_sha256 = _plan_snapshot(round_input, exposure_tuple, decision_tuple)
    return ManagementRoundOutput(
        contract=contract,
        arm=round_input.arm,
        policy_version=round_input.policy_version,
        input_sha256=round_input.input_sha256,
        parent_snapshot=round_input.parent_snapshot,
        resulting_snapshot_id=resulting_snapshot_id,
        resulting_snapshot_sha256=resulting_snapshot_sha256,
        resulting_snapshot_active_library_capacity=contract.library_snapshot.active_library_capacity,
        exposure_decisions=exposure_tuple,
        lifecycle_decisions=decision_tuple,
        decision_evidence_denominator=denominator,
    )


def _rate(numerator: int, denominator: int) -> RateEstimate:
    return RateEstimate(numerator=numerator, denominator=denominator, value=numerator / denominator if denominator else None)


def _optional_summary(values: Sequence[float | None]) -> OptionalMetricSummary:
    present = [value for value in values if value is not None]
    return OptionalMetricSummary(
        present=len(present),
        missing=len(values) - len(present),
        mean=sum(present) / len(present) if present else None,
    )


def _report_row(trace: TraceRecord, contract: ManagementRunContract) -> ManagementTaskReport:
    evidence = _validate_trace_against_contract(trace, contract)
    assert trace.invocation is not None
    invocation = trace.invocation
    validation = trace.validation[0] if trace.validation else None
    verifier_passed = invocation.success if invocation.success is not None else (validation.passed if validation else None)
    verifier_score = invocation.score if invocation.score is not None else (validation.score if validation else None)
    cost = invocation.cost if invocation.cost is not None else (validation.cost if validation else None)
    raw_latency = trace.metadata.get("latency_s")
    latency_s = float(raw_latency) if isinstance(raw_latency, (int, float)) and not isinstance(raw_latency, bool) else None
    complete = evidence.actual_invocation_evidence_complete
    invoked = tuple(dict.fromkeys(event.skill_id for event in evidence.invocation_events)) if complete else ()
    oracle = tuple(invocation.oracle_skill_ids)
    if not complete:
        nmo_event, route_class = None, "incomplete"
    elif not oracle:
        nmo_event = None
        route_class = "spurious" if invoked else "no_oracle_empty"
    elif not invoked:
        nmo_event, route_class = "n", "empty"
    elif set(invoked).issubset(set(oracle)):
        nmo_event, route_class = "o", "oracle_only"
    elif set(invoked) & set(oracle):
        nmo_event, route_class = "m", "mixed"
    else:
        nmo_event, route_class = "m", "wrong"
    return ManagementTaskReport(
        trace_id=trace.id,
        task_id=trace.task_id,
        verifier_passed=verifier_passed,
        verifier_score=verifier_score,
        provisioned_skill_ids=tuple(invocation.provisioned_skill_ids),
        selected_skill_ids=tuple(invocation.selected_skill_ids),
        actual_invoked_skill_ids=invoked,
        oracle_skill_ids=oracle,
        actual_invocation_evidence_complete=complete,
        nmo_event=nmo_event,
        route_class=route_class,
        cost=cost,
        latency_s=latency_s,
    )


def _validate_report_coverage(traces: Sequence[TraceRecord], contract: ManagementRunContract) -> None:
    expected = len(contract.task_ids) * contract.repeats
    if len(traces) != expected:
        raise ManagementContractError(f"report requires exactly {expected} traces for the frozen split/repeat contract")
    trace_ids = [trace.id for trace in traces]
    if len(set(trace_ids)) != len(trace_ids):
        raise ManagementContractError("report traces must have unique IDs")
    task_counts = Counter(trace.task_id for trace in traces)
    if set(task_counts) != set(contract.task_ids) or any(count != contract.repeats for count in task_counts.values()):
        raise ManagementContractError("report traces do not match frozen task coverage and repeat count")


def build_management_round_report(output: ManagementRoundOutput, traces: Sequence[TraceRecord]) -> ManagementRoundReport:
    """Create the common post-run report without granting policy extra evidence."""

    validate_management_run_contract(output.contract)
    if output.parent_snapshot != output.contract.library_snapshot:
        raise ManagementContractError("output parent snapshot no longer matches common frozen library")
    if output.resulting_snapshot_active_library_capacity != output.contract.library_snapshot.active_library_capacity:
        raise ManagementContractError("output changed active-library capacity instead of emitting a read-only plan")
    if output.library_mutated:
        raise ManagementContractError("management round outputs must not mutate a live library")
    _validate_report_coverage(traces, output.contract)
    task_order = {task_id: index for index, task_id in enumerate(output.contract.task_ids)}
    rows = tuple(sorted((_report_row(trace, output.contract) for trace in traces), key=lambda row: (task_order[row.task_id], row.trace_id)))
    verifier_observed = [row.verifier_passed for row in rows if row.verifier_passed is not None]
    scores = [row.verifier_score for row in rows]
    complete_oracle_rows = [row for row in rows if row.actual_invocation_evidence_complete and row.oracle_skill_ids]
    no_oracle_rows = [row for row in rows if row.actual_invocation_evidence_complete and not row.oracle_skill_ids]
    route_counts = Counter(row.route_class for row in rows)
    n_count = sum(1 for row in complete_oracle_rows if row.nmo_event == "n")
    m_count = sum(1 for row in complete_oracle_rows if row.nmo_event == "m")
    o_count = sum(1 for row in complete_oracle_rows if row.nmo_event == "o")
    wrong_count = route_counts["wrong"]
    mixed_count = route_counts["mixed"]
    empty_count = route_counts["empty"]
    spurious_count = route_counts["spurious"]
    eligible = len(complete_oracle_rows)
    metrics = ManagementReportMetrics(
        total_trajectories=len(rows),
        verifier_passed=sum(bool(value) for value in verifier_observed),
        verifier_observed=len(verifier_observed),
        verifier_mean_score=_optional_summary(scores).mean,
        verifier_score_missing=_optional_summary(scores).missing,
        actual_metric_eligible=eligible,
        actual_evidence_incomplete=route_counts["incomplete"],
        no_oracle=len(no_oracle_rows),
        n_count=n_count,
        m_count=m_count,
        o_count=o_count,
        wrong_count=wrong_count,
        mixed_count=mixed_count,
        empty_count=empty_count,
        spurious_count=spurious_count,
        pi_o=_rate(o_count, eligible),
        pi_wrong=_rate(wrong_count, eligible),
        pi_mixed=_rate(mixed_count, eligible),
        pi_empty=_rate(empty_count, eligible),
        pi_m=_rate(m_count, eligible),
        spurious_rate=_rate(spurious_count, len(no_oracle_rows)),
        cost=_optional_summary([row.cost for row in rows]),
        latency=_optional_summary([row.latency_s for row in rows]),
    )
    return ManagementRoundReport(output=output, task_reports=rows, metrics=metrics)


def compare_management_reports(reports: Iterable[ManagementRoundReport]) -> ManagementComparisonReport:
    """Reject arm drift and serialize the four required arms in a stable order."""

    items = list(reports)
    expected_arms = {ManagementArm.M0, ManagementArm.M1, ManagementArm.M2_H, ManagementArm.M2_K}
    arms = {report.output.arm for report in items}
    if arms != expected_arms or len(items) != len(expected_arms):
        raise ManagementContractError("comparison requires exactly one report for M0, M1, M2-H, and M2-K")
    first_contract = items[0].output.contract
    validate_management_run_contract(first_contract)
    for report in items:
        if report.output.contract != first_contract:
            raise ManagementContractError("arm comparison refused: frozen snapshot, split, agent, model, verifier, budget, or repeat contract differs")
        if report.output.resulting_snapshot_active_library_capacity != first_contract.library_snapshot.active_library_capacity:
            raise ManagementContractError("arm comparison refused: active-library capacity drifted")
    ordered = tuple(sorted(items, key=lambda report: report.output.arm.value))
    return ManagementComparisonReport(common_contract_sha256=first_contract.comparison_sha256, reports=ordered)


def management_report_to_dict(report: ManagementRoundReport | ManagementComparisonReport) -> dict[str, Any]:
    """Return a deterministic JSON-ready object without hiding null/missing metrics."""

    return _canonical(report)
