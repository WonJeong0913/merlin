"""Internal held-in/held-out/regression evaluation for bounded M3-K variants.

Unlike ``evaluate_harness_evolution`` in the core scaffold, this module never
accepts caller-supplied deltas.  It freezes a parent and candidate harness,
constructs the complete paired task/trial schedule, runs each variant through a
fresh executor, validates immutable lineage and actual invocation evidence,
computes split deltas itself, and then promotes or rolls back.

The module is research-only so the frozen Build Week package remains unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, Sequence

from src.merlin_harness.harness import (
    HarnessEvolutionProposal,
    HarnessVariantSpec,
    Hook,
    build_runtime_from_variant,
)
from src.merlin_harness.management import content_sha256


class M3KContractError(ValueError):
    """Raised when a policy experiment cannot support a promotion claim."""


class M3KSplit(str, Enum):
    HELD_IN = "held_in"
    HELD_OUT = "held_out"
    REGRESSION = "regression"


class VariantRole(str, Enum):
    PARENT = "parent"
    CANDIDATE = "candidate"


def _json_ready(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class M3KTaskContract:
    task_id: str
    split: M3KSplit
    verifier_id: str
    task_instruction_sha256: str


@dataclass(frozen=True, slots=True)
class M3KEvaluationContract:
    experiment_id: str
    split_manifest_sha256: str
    task_contract_source_sha256: str
    tasks: tuple[M3KTaskContract, ...]
    repeats: int
    base_agent_id: str
    base_agent_version: str
    backend: str
    model_id: str
    effort: str | None
    tools: tuple[str, ...]
    budget_id: str
    held_out_visible_to_proposer: bool = False
    schema_version: int = 1

    @property
    def contract_sha256(self) -> str:
        return content_sha256(self)


@dataclass(frozen=True, slots=True)
class M3KCell:
    cell_id: str
    task_id: str
    split: M3KSplit
    trial_index: int
    verifier_id: str
    task_instruction_sha256: str


@dataclass(frozen=True, slots=True)
class M3KVariantLineage:
    evaluation_contract_sha256: str
    variant_role: VariantRole
    variant_id: str
    variant_sha256: str
    parent_variant_id: str | None


@dataclass(frozen=True, slots=True)
class M3KTrajectoryResult:
    cell_id: str
    task_id: str
    split: M3KSplit
    trial_index: int
    verifier_id: str
    task_instruction_sha256: str
    variant_role: VariantRole
    variant_id: str
    variant_sha256: str
    evaluation_contract_sha256: str
    trace_id: str
    raw_trace_sha256: str
    verifier_passed: bool
    verifier_score: float
    cost: float | None
    actual_invocation_evidence_complete: bool
    invoked_skill_ids: tuple[str, ...]
    oracle_skill_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class M3KSplitSummary:
    split: M3KSplit
    trajectories: int
    pass_rate: float
    mean_score: float
    shadowing_rate: float
    shadowing_eligible: int
    actual_evidence_incomplete: int
    mean_cost: float | None


@dataclass(frozen=True, slots=True)
class M3KSplitDelta:
    split: M3KSplit
    pass_rate_delta: float
    mean_score_delta: float
    shadowing_rate_delta: float
    mean_cost_ratio: float | None


@dataclass(frozen=True, slots=True)
class M3KPromotionCriteria:
    min_held_in_pass_rate_delta: float = 0.0
    min_held_out_pass_rate_delta: float = 0.0
    min_regression_pass_rate_delta: float = 0.0
    min_positive_primary_delta: float = 1e-12
    max_shadowing_rate_increase: float = 0.0
    max_mean_cost_ratio: float = 1.25
    require_complete_actual_invocation: bool = True


@dataclass(frozen=True, slots=True)
class M3KPromotionCheck:
    name: str
    passed: bool
    score: float | None
    evidence: str


@dataclass(frozen=True, slots=True)
class M3KPolicyEvaluationResult:
    accepted: bool
    rollback_required: bool
    resolution: str
    reason: str
    contract: M3KEvaluationContract
    proposal_id: str
    parent_variant_id: str
    parent_variant_sha256: str
    candidate_variant_id: str
    candidate_variant_sha256: str
    regression_candidate_task_count: int
    regression_eligible_task_ids: tuple[str, ...]
    criteria: M3KPromotionCriteria
    parent_summaries: tuple[M3KSplitSummary, ...]
    candidate_summaries: tuple[M3KSplitSummary, ...]
    deltas: tuple[M3KSplitDelta, ...]
    checks: tuple[M3KPromotionCheck, ...]
    parent_trajectories: tuple[M3KTrajectoryResult, ...]
    candidate_trajectories: tuple[M3KTrajectoryResult, ...]
    resolved_variant_id: str
    resolved_variant_sha256: str

    def to_dict(self) -> dict[str, object]:
        return _json_ready({
            "schema_version": 1,
            "accepted": self.accepted,
            "rollback_required": self.rollback_required,
            "resolution": self.resolution,
            "reason": self.reason,
            "contract": asdict(self.contract),
            "contract_sha256": self.contract.contract_sha256,
            "proposal_id": self.proposal_id,
            "parent_variant_id": self.parent_variant_id,
            "parent_variant_sha256": self.parent_variant_sha256,
            "candidate_variant_id": self.candidate_variant_id,
            "candidate_variant_sha256": self.candidate_variant_sha256,
            "regression_candidate_task_count": self.regression_candidate_task_count,
            "regression_eligible_task_ids": self.regression_eligible_task_ids,
            "criteria": asdict(self.criteria),
            "parent_summaries": [asdict(item) for item in self.parent_summaries],
            "candidate_summaries": [asdict(item) for item in self.candidate_summaries],
            "deltas": [asdict(item) for item in self.deltas],
            "checks": [asdict(item) for item in self.checks],
            "parent_trajectories": [asdict(item) for item in self.parent_trajectories],
            "candidate_trajectories": [asdict(item) for item in self.candidate_trajectories],
            "resolved_variant_id": self.resolved_variant_id,
            "resolved_variant_sha256": self.resolved_variant_sha256,
            "evidence_boundary": {
                "caller_supplied_deltas_accepted": False,
                "candidate_frozen_before_held_out": True,
                "held_out_visible_to_proposer": self.contract.held_out_visible_to_proposer,
                "fresh_executor_per_variant": True,
                "actual_invocation_required": self.criteria.require_complete_actual_invocation,
            },
        })


class M3KVariantExecutor(Protocol):
    def run(
        self,
        variant: HarnessVariantSpec,
        cells: tuple[M3KCell, ...],
        lineage: M3KVariantLineage,
    ) -> Sequence[M3KTrajectoryResult]: ...


class M3KExecutorFactory(Protocol):
    def __call__(self, role: VariantRole) -> M3KVariantExecutor: ...


def _require_sha256(value: str, *, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise M3KContractError(f"{label} must be a lowercase SHA-256")


def _require_nonempty(value: str, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise M3KContractError(f"{label} must be non-empty")


def validate_m3k_contract(contract: M3KEvaluationContract) -> None:
    if contract.schema_version != 1:
        raise M3KContractError("unsupported M3-K contract schema")
    for label, value in (
        ("experiment_id", contract.experiment_id),
        ("base_agent_id", contract.base_agent_id),
        ("base_agent_version", contract.base_agent_version),
        ("backend", contract.backend),
        ("model_id", contract.model_id),
        ("budget_id", contract.budget_id),
    ):
        _require_nonempty(value, label=label)
    _require_sha256(contract.split_manifest_sha256, label="split_manifest_sha256")
    _require_sha256(
        contract.task_contract_source_sha256,
        label="task_contract_source_sha256",
    )
    if contract.effort is not None:
        _require_nonempty(contract.effort, label="effort")
    if contract.repeats < 1:
        raise M3KContractError("M3-K repeats must be >= 1")
    if contract.held_out_visible_to_proposer:
        raise M3KContractError("M3-K held-out tasks must remain hidden from the proposer")
    if len(set(contract.tools)) != len(contract.tools) or any(not item for item in contract.tools):
        raise M3KContractError("M3-K tools must be unique non-empty strings")
    task_ids = [task.task_id for task in contract.tasks]
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise M3KContractError("M3-K tasks must be non-empty and unique across splits")
    present_splits = {task.split for task in contract.tasks}
    if present_splits != set(M3KSplit):
        raise M3KContractError("M3-K requires non-empty held-in, held-out, and regression splits")
    for task in contract.tasks:
        _require_nonempty(task.task_id, label="task_id")
        _require_nonempty(task.verifier_id, label=f"verifier_id[{task.task_id}]")
        _require_sha256(
            task.task_instruction_sha256,
            label=f"task_instruction_sha256[{task.task_id}]",
        )


def _validate_proposal(
    parent: HarnessVariantSpec,
    proposal: HarnessEvolutionProposal,
) -> tuple[str, str]:
    if proposal.parent_variant_id != parent.id:
        raise M3KContractError("proposal parent does not match the supplied parent variant")
    if proposal.candidate.parent_id != parent.id:
        raise M3KContractError("candidate parent_id does not match the frozen parent")
    if proposal.candidate.id == parent.id:
        raise M3KContractError("candidate variant must have a new ID")
    for label, value in (
        ("proposal.id", proposal.id),
        ("proposal.rationale", proposal.rationale),
        ("candidate.summary", proposal.candidate.summary),
    ):
        _require_nonempty(value, label=label)
    if not proposal.changed_hooks:
        raise M3KContractError("proposal must declare changed hooks")
    unknown_hooks = sorted(set(proposal.changed_hooks) - {hook.value for hook in Hook})
    if unknown_hooks:
        raise M3KContractError(f"proposal contains unknown changed hooks: {', '.join(unknown_hooks)}")
    if not proposal.evidence_trace_ids or any(not item for item in proposal.evidence_trace_ids):
        raise M3KContractError("proposal requires non-empty evidence trace IDs")
    try:
        build_runtime_from_variant(copy.deepcopy(parent))
        build_runtime_from_variant(copy.deepcopy(proposal.candidate))
    except (KeyError, TypeError, ValueError) as exc:
        raise M3KContractError(f"harness variant is not reconstructable: {exc}") from exc
    return content_sha256(parent), content_sha256(proposal.candidate)


def _validate_criteria(criteria: M3KPromotionCriteria) -> None:
    for name, value in asdict(criteria).items():
        if name == "require_complete_actual_invocation":
            if not isinstance(value, bool):
                raise M3KContractError(f"{name} must be boolean")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise M3KContractError(f"{name} must be a finite number")
        if value < 0:
            raise M3KContractError(f"{name} must be >= 0")


def build_cells(contract: M3KEvaluationContract) -> tuple[M3KCell, ...]:
    validate_m3k_contract(contract)
    order = {split: index for index, split in enumerate(M3KSplit)}
    tasks = sorted(contract.tasks, key=lambda item: (order[item.split], item.task_id))
    return tuple(
        M3KCell(
            cell_id=f"{task.split.value}:{task.task_id}:t{trial_index}",
            task_id=task.task_id,
            split=task.split,
            trial_index=trial_index,
            verifier_id=task.verifier_id,
            task_instruction_sha256=task.task_instruction_sha256,
        )
        for task in tasks
        for trial_index in range(1, contract.repeats + 1)
    )


def _validate_trajectories(
    *,
    results: Sequence[M3KTrajectoryResult],
    cells: tuple[M3KCell, ...],
    lineage: M3KVariantLineage,
) -> tuple[M3KTrajectoryResult, ...]:
    rows = tuple(results)
    by_cell = {row.cell_id: row for row in rows}
    if len(by_cell) != len(rows) or set(by_cell) != {cell.cell_id for cell in cells}:
        raise M3KContractError(
            f"{lineage.variant_role.value} results must cover every frozen cell exactly once"
        )
    trace_ids = [row.trace_id for row in rows]
    raw_hashes = [row.raw_trace_sha256 for row in rows]
    if len(set(trace_ids)) != len(trace_ids):
        raise M3KContractError(f"{lineage.variant_role.value} trace IDs must be unique")
    if len(set(raw_hashes)) != len(raw_hashes):
        raise M3KContractError(f"{lineage.variant_role.value} raw trace hashes must be unique")
    for cell in cells:
        row = by_cell[cell.cell_id]
        expected = (
            cell.task_id,
            cell.split,
            cell.trial_index,
            cell.verifier_id,
            cell.task_instruction_sha256,
            lineage.variant_role,
            lineage.variant_id,
            lineage.variant_sha256,
            lineage.evaluation_contract_sha256,
        )
        actual = (
            row.task_id,
            row.split,
            row.trial_index,
            row.verifier_id,
            row.task_instruction_sha256,
            row.variant_role,
            row.variant_id,
            row.variant_sha256,
            row.evaluation_contract_sha256,
        )
        if actual != expected:
            raise M3KContractError(f"trajectory lineage drifted for {cell.cell_id}")
        _require_nonempty(row.trace_id, label=f"trace_id[{cell.cell_id}]")
        _require_sha256(row.raw_trace_sha256, label=f"raw_trace_sha256[{cell.cell_id}]")
        if not isinstance(row.verifier_passed, bool):
            raise M3KContractError(f"verifier_passed[{cell.cell_id}] must be boolean")
        if (
            isinstance(row.verifier_score, bool)
            or not isinstance(row.verifier_score, (int, float))
            or not math.isfinite(row.verifier_score)
            or not 0 <= row.verifier_score <= 1
        ):
            raise M3KContractError(f"verifier_score[{cell.cell_id}] must be finite in [0,1]")
        if row.cost is not None and (
            isinstance(row.cost, bool)
            or not isinstance(row.cost, (int, float))
            or not math.isfinite(row.cost)
            or row.cost < 0
        ):
            raise M3KContractError(f"cost[{cell.cell_id}] must be finite and >= 0")
        if len(set(row.invoked_skill_ids)) != len(row.invoked_skill_ids):
            raise M3KContractError(f"invoked skill IDs duplicate in {cell.cell_id}")
        if len(set(row.oracle_skill_ids)) != len(row.oracle_skill_ids):
            raise M3KContractError(f"oracle skill IDs duplicate in {cell.cell_id}")
    return tuple(by_cell[cell.cell_id] for cell in cells)


def _route_event(row: M3KTrajectoryResult) -> str:
    invoked = set(row.invoked_skill_ids)
    oracle = set(row.oracle_skill_ids)
    if not oracle:
        return "spurious" if invoked else "no_oracle_empty"
    if not invoked:
        return "empty"
    if invoked.issubset(oracle):
        return "oracle_only"
    if invoked & oracle:
        return "mixed"
    return "wrong"


def _summary(
    rows: Sequence[M3KTrajectoryResult],
    split: M3KSplit,
    *,
    allowed_task_ids: set[str] | None = None,
    allow_empty: bool = False,
) -> M3KSplitSummary:
    selected = [
        row
        for row in rows
        if row.split is split
        and (allowed_task_ids is None or row.task_id in allowed_task_ids)
    ]
    if not selected and not allow_empty:
        raise M3KContractError(f"no trajectories found for {split.value}")
    if not selected:
        return M3KSplitSummary(
            split=split,
            trajectories=0,
            pass_rate=0.0,
            mean_score=0.0,
            shadowing_rate=0.0,
            shadowing_eligible=0,
            actual_evidence_incomplete=0,
            mean_cost=None,
        )
    eligible = [row for row in selected if row.oracle_skill_ids]
    shadowing = sum(_route_event(row) in {"wrong", "mixed"} for row in eligible)
    costs = [float(row.cost) for row in selected if row.cost is not None]
    return M3KSplitSummary(
        split=split,
        trajectories=len(selected),
        pass_rate=sum(row.verifier_passed for row in selected) / len(selected),
        mean_score=sum(float(row.verifier_score) for row in selected) / len(selected),
        shadowing_rate=shadowing / len(eligible) if eligible else 0.0,
        shadowing_eligible=len(eligible),
        actual_evidence_incomplete=sum(
            not row.actual_invocation_evidence_complete for row in selected
        ),
        mean_cost=sum(costs) / len(costs) if costs else None,
    )


def _cost_ratio(candidate: float | None, parent: float | None) -> float | None:
    if candidate is None or parent is None:
        return None
    if parent == 0:
        return 1.0 if candidate == 0 else math.inf
    return candidate / parent


def _delta(parent: M3KSplitSummary, candidate: M3KSplitSummary) -> M3KSplitDelta:
    if parent.split is not candidate.split or parent.trajectories != candidate.trajectories:
        raise M3KContractError("parent/candidate split summaries are not paired")
    return M3KSplitDelta(
        split=parent.split,
        pass_rate_delta=candidate.pass_rate - parent.pass_rate,
        mean_score_delta=candidate.mean_score - parent.mean_score,
        shadowing_rate_delta=candidate.shadowing_rate - parent.shadowing_rate,
        mean_cost_ratio=_cost_ratio(candidate.mean_cost, parent.mean_cost),
    )


def run_m3k_policy_evaluation(
    *,
    contract: M3KEvaluationContract,
    parent: HarnessVariantSpec,
    proposal: HarnessEvolutionProposal,
    executor_factory: M3KExecutorFactory,
    criteria: M3KPromotionCriteria | None = None,
) -> M3KPolicyEvaluationResult:
    """Run both variants internally and promote only the recomputed evidence."""

    validate_m3k_contract(contract)
    active_criteria = criteria or M3KPromotionCriteria()
    _validate_criteria(active_criteria)
    parent_sha256, candidate_sha256 = _validate_proposal(parent, proposal)
    cells = build_cells(contract)

    def run_variant(role: VariantRole, spec: HarnessVariantSpec, spec_sha256: str):
        lineage = M3KVariantLineage(
            evaluation_contract_sha256=contract.contract_sha256,
            variant_role=role,
            variant_id=spec.id,
            variant_sha256=spec_sha256,
            parent_variant_id=spec.parent_id,
        )
        executor = executor_factory(role)
        return _validate_trajectories(
            results=executor.run(copy.deepcopy(spec), cells, lineage),
            cells=cells,
            lineage=lineage,
        )

    parent_rows = run_variant(VariantRole.PARENT, parent, parent_sha256)
    candidate_rows = run_variant(
        VariantRole.CANDIDATE, proposal.candidate, candidate_sha256
    )
    if content_sha256(parent) != parent_sha256 or content_sha256(proposal.candidate) != candidate_sha256:
        raise M3KContractError("parent or candidate variant mutated after freeze")
    all_trace_ids = [row.trace_id for row in parent_rows + candidate_rows]
    all_raw_hashes = [row.raw_trace_sha256 for row in parent_rows + candidate_rows]
    if len(set(all_trace_ids)) != len(all_trace_ids):
        raise M3KContractError("parent and candidate reused a trace ID")
    if len(set(all_raw_hashes)) != len(all_raw_hashes):
        raise M3KContractError("parent and candidate reused raw trace bytes")

    regression_candidate_tasks = sorted(
        task.task_id for task in contract.tasks if task.split is M3KSplit.REGRESSION
    )
    parent_regression_rows = {
        task_id: [
            row
            for row in parent_rows
            if row.split is M3KSplit.REGRESSION and row.task_id == task_id
        ]
        for task_id in regression_candidate_tasks
    }
    regression_eligible_task_ids = tuple(
        task_id
        for task_id in regression_candidate_tasks
        if len(parent_regression_rows[task_id]) == contract.repeats
        and all(row.verifier_passed for row in parent_regression_rows[task_id])
    )
    regression_eligible = set(regression_eligible_task_ids)
    parent_summaries = tuple(
        _summary(
            parent_rows,
            split,
            allowed_task_ids=(regression_eligible if split is M3KSplit.REGRESSION else None),
            allow_empty=split is M3KSplit.REGRESSION,
        )
        for split in M3KSplit
    )
    candidate_summaries = tuple(
        _summary(
            candidate_rows,
            split,
            allowed_task_ids=(regression_eligible if split is M3KSplit.REGRESSION else None),
            allow_empty=split is M3KSplit.REGRESSION,
        )
        for split in M3KSplit
    )
    deltas = tuple(
        _delta(parent_summary, candidate_summary)
        for parent_summary, candidate_summary in zip(
            parent_summaries, candidate_summaries, strict=True
        )
    )
    delta_by_split = {item.split: item for item in deltas}
    primary_best = max(
        delta_by_split[M3KSplit.HELD_IN].pass_rate_delta,
        delta_by_split[M3KSplit.HELD_OUT].pass_rate_delta,
    )
    incomplete = sum(
        not row.actual_invocation_evidence_complete
        for row in parent_rows + candidate_rows
    )
    max_shadowing_increase = max(item.shadowing_rate_delta for item in deltas)
    cost_ratios = [
        item.mean_cost_ratio for item in deltas if item.mean_cost_ratio is not None
    ]
    max_cost_ratio = max(cost_ratios) if cost_ratios else None
    checks = (
        M3KPromotionCheck(
            "paired_schedule_complete",
            len(parent_rows) == len(cells) == len(candidate_rows),
            float(len(cells)),
            f"cells={len(cells)}; parent={len(parent_rows)}; candidate={len(candidate_rows)}",
        ),
        M3KPromotionCheck(
            "candidate_frozen_and_reconstructable",
            True,
            None,
            f"parent_sha256={parent_sha256}; candidate_sha256={candidate_sha256}",
        ),
        M3KPromotionCheck(
            "actual_invocation_complete",
            (not active_criteria.require_complete_actual_invocation) or incomplete == 0,
            float(incomplete),
            f"incomplete_trajectories={incomplete}",
        ),
        M3KPromotionCheck(
            "held_in_non_regression",
            delta_by_split[M3KSplit.HELD_IN].pass_rate_delta
            >= active_criteria.min_held_in_pass_rate_delta,
            delta_by_split[M3KSplit.HELD_IN].pass_rate_delta,
            f"required>={active_criteria.min_held_in_pass_rate_delta}",
        ),
        M3KPromotionCheck(
            "held_out_non_regression",
            delta_by_split[M3KSplit.HELD_OUT].pass_rate_delta
            >= active_criteria.min_held_out_pass_rate_delta,
            delta_by_split[M3KSplit.HELD_OUT].pass_rate_delta,
            f"required>={active_criteria.min_held_out_pass_rate_delta}",
        ),
        M3KPromotionCheck(
            "primary_split_positive",
            primary_best >= active_criteria.min_positive_primary_delta,
            primary_best,
            f"required>={active_criteria.min_positive_primary_delta}",
        ),
        M3KPromotionCheck(
            "regression_baseline_eligible",
            bool(regression_eligible_task_ids),
            float(len(regression_eligible_task_ids)),
            (
                f"eligible_tasks={len(regression_eligible_task_ids)}/"
                f"{len(regression_candidate_tasks)}; eligibility requires parent pass on every repeat"
            ),
        ),
        M3KPromotionCheck(
            "regression_split_non_regression",
            delta_by_split[M3KSplit.REGRESSION].pass_rate_delta
            >= active_criteria.min_regression_pass_rate_delta,
            delta_by_split[M3KSplit.REGRESSION].pass_rate_delta,
            f"required>={active_criteria.min_regression_pass_rate_delta}",
        ),
        M3KPromotionCheck(
            "shadowing_non_regression_all_splits",
            max_shadowing_increase <= active_criteria.max_shadowing_rate_increase,
            max_shadowing_increase,
            f"required<={active_criteria.max_shadowing_rate_increase}",
        ),
        M3KPromotionCheck(
            "cost_guardrail_all_splits",
            max_cost_ratio is not None
            and max_cost_ratio <= active_criteria.max_mean_cost_ratio,
            max_cost_ratio,
            f"required<={active_criteria.max_mean_cost_ratio}",
        ),
    )
    accepted = all(check.passed for check in checks)
    if accepted:
        resolution = "candidate_harness_promoted"
        reason = "M3-K candidate passed internal held-in/held-out/regression evaluation"
        resolved_id = proposal.candidate.id
        resolved_sha256 = candidate_sha256
    else:
        resolution = "candidate_harness_rolled_back"
        failed = ", ".join(check.name for check in checks if not check.passed)
        reason = f"M3-K candidate rejected by: {failed}"
        resolved_id = parent.id
        resolved_sha256 = parent_sha256
    return M3KPolicyEvaluationResult(
        accepted=accepted,
        rollback_required=not accepted,
        resolution=resolution,
        reason=reason,
        contract=contract,
        proposal_id=proposal.id,
        parent_variant_id=parent.id,
        parent_variant_sha256=parent_sha256,
        candidate_variant_id=proposal.candidate.id,
        candidate_variant_sha256=candidate_sha256,
        regression_candidate_task_count=len(regression_candidate_tasks),
        regression_eligible_task_ids=regression_eligible_task_ids,
        criteria=active_criteria,
        parent_summaries=parent_summaries,
        candidate_summaries=candidate_summaries,
        deltas=deltas,
        checks=checks,
        parent_trajectories=parent_rows,
        candidate_trajectories=candidate_rows,
        resolved_variant_id=resolved_id,
        resolved_variant_sha256=resolved_sha256,
    )


def build_full87_m3k_contract(
    *,
    split_manifest: Path,
    library_scale_manifest: Path,
    experiment_id: str,
    base_agent_id: str,
    base_agent_version: str,
    backend: str,
    model_id: str,
    effort: str | None,
    tools: tuple[str, ...],
    budget_id: str,
    repeats: int = 3,
) -> M3KEvaluationContract:
    """Bind the canonical 87-task split to verifier/task hashes for M3-K."""

    split_bytes = split_manifest.read_bytes()
    scale_bytes = library_scale_manifest.read_bytes()
    split_payload = json.loads(split_bytes)
    scale_payload = json.loads(scale_bytes)
    if split_payload.get("task_count") != 87 or scale_payload.get("task_count") != 87:
        raise M3KContractError("full-87 M3-K contract requires both 87-task manifests")
    scale_contracts = {
        item["task_id"]: item for item in scale_payload.get("task_contracts", [])
    }
    split_name_map = {
        "adaptation": M3KSplit.HELD_IN,
        "held_out": M3KSplit.HELD_OUT,
        "regression": M3KSplit.REGRESSION,
    }
    tasks: list[M3KTaskContract] = []
    for source_name, split in split_name_map.items():
        for item in split_payload.get("splits", {}).get(source_name, []):
            task_id = item.get("task_id")
            task_contract = scale_contracts.get(task_id)
            if task_contract is None:
                raise M3KContractError(f"task contract missing for split task: {task_id}")
            tasks.append(
                M3KTaskContract(
                    task_id=task_id,
                    split=split,
                    verifier_id=task_contract["verifier_contract_sha256"],
                    task_instruction_sha256=task_contract["task_instruction_sha256"],
                )
            )
    if len(tasks) != 87 or len({item.task_id for item in tasks}) != 87:
        raise M3KContractError("full-87 split and task contracts must match exactly")
    contract = M3KEvaluationContract(
        experiment_id=experiment_id,
        split_manifest_sha256=hashlib.sha256(split_bytes).hexdigest(),
        task_contract_source_sha256=hashlib.sha256(scale_bytes).hexdigest(),
        tasks=tuple(tasks),
        repeats=repeats,
        base_agent_id=base_agent_id,
        base_agent_version=base_agent_version,
        backend=backend,
        model_id=model_id,
        effort=effort,
        tools=tools,
        budget_id=budget_id,
    )
    validate_m3k_contract(contract)
    return contract
