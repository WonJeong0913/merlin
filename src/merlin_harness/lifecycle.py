"""Validation and lifecycle gates for skill artifacts and harness policy changes."""

from __future__ import annotations

import copy

from .metrics import self_harness_accept
from .models import (
    HarnessPolicyChange,
    LifecycleAction,
    LifecycleDecision,
    LifecyclePromotionCriteria,
    LifecyclePromotionResult,
    LifecycleStatus,
    LifecycleVerificationSnapshot,
    ProvisionalLifecycleChange,
    SkillArtifact,
    ValidationResult,
)


def validate_aip_lite_skill(skill: SkillArtifact) -> list[ValidationResult]:
    """Check the minimum structure needed before a skill can be activated."""

    checks = [
        ValidationResult("has_name", bool(skill.name.strip())),
        ValidationResult("has_description", bool(skill.description.strip())),
        ValidationResult("has_trigger", bool(skill.trigger.strip())),
        ValidationResult("has_steps", bool(skill.steps)),
        ValidationResult("has_provenance_or_metadata", bool(skill.provenance_trace_ids or skill.metadata)),
    ]
    for step in skill.steps:
        checks.append(ValidationResult(f"step:{step.id}:has_description", bool(step.description.strip())))
    return checks


def all_passed(results: list[ValidationResult]) -> bool:
    """Require positive validation evidence, not only an absence of failures."""

    return bool(results) and all(result.passed for result in results)


def decide_candidate_lifecycle(
    skill: SkillArtifact,
    structure_results: list[ValidationResult],
    target_results: list[ValidationResult],
    regression_results: list[ValidationResult],
) -> LifecycleDecision:
    """Decide whether a candidate can become active."""

    all_results = structure_results + target_results + regression_results
    if not all_passed(structure_results):
        return LifecycleDecision(skill.id, LifecycleAction.REJECT, "AIP-lite structure gate failed", validation_results=all_results)
    if not target_results:
        return LifecycleDecision(skill.id, LifecycleAction.REPAIR, "target validation evidence missing", validation_results=all_results)
    if not all_passed(target_results):
        return LifecycleDecision(skill.id, LifecycleAction.REPAIR, "target validation failed", validation_results=all_results)
    if not regression_results:
        return LifecycleDecision(skill.id, LifecycleAction.REPAIR, "regression validation evidence missing", validation_results=all_results)
    if not all_passed(regression_results):
        return LifecycleDecision(skill.id, LifecycleAction.REPAIR, "regression gate failed", validation_results=all_results)
    return LifecycleDecision(skill.id, LifecycleAction.ADOPT, "structure, target, and regression gates passed", validation_results=all_results)


def apply_lifecycle_decision(skill: SkillArtifact, decision: LifecycleDecision) -> SkillArtifact:
    if decision.action == LifecycleAction.ADOPT:
        skill.status = LifecycleStatus.ACTIVE
    elif decision.action == LifecycleAction.HIDE:
        skill.status = LifecycleStatus.HIDDEN
    elif decision.action == LifecycleAction.REPAIR:
        skill.status = LifecycleStatus.REPAIR
    elif decision.action in {LifecycleAction.REJECT, LifecycleAction.RETIRE}:
        skill.status = LifecycleStatus.REJECTED if decision.action == LifecycleAction.REJECT else LifecycleStatus.RETIRED
    return skill


def stage_provisional_lifecycle_change(
    skills: list[SkillArtifact], decisions: list[LifecycleDecision]
) -> tuple[list[SkillArtifact], ProvisionalLifecycleChange]:
    """Apply lifecycle decisions to a copy of a library, never its live state.

    The caller runs its deterministic verifier suite against the returned
    provisional library.  It can safely retain the original library whenever
    ``evaluate_lifecycle_promotion`` rejects the candidate.
    """

    original_statuses = {skill.id: skill.status.value for skill in skills}
    provisional = copy.deepcopy(skills)
    provisional_by_id = {skill.id: skill for skill in provisional}
    unknown_skill_ids = sorted({decision.skill_id for decision in decisions} - set(provisional_by_id))
    if unknown_skill_ids:
        raise ValueError(f"lifecycle decisions reference unknown skills: {', '.join(unknown_skill_ids)}")

    for decision in decisions:
        apply_lifecycle_decision(provisional_by_id[decision.skill_id], decision)

    return provisional, ProvisionalLifecycleChange(
        decisions=list(decisions),
        original_statuses=original_statuses,
        provisional_statuses={skill.id: skill.status.value for skill in provisional},
    )


def _validate_promotion_criteria(criteria: LifecyclePromotionCriteria) -> None:
    for name, value in (
        ("min_pass_rate_delta", criteria.min_pass_rate_delta),
        ("min_pi_o_delta", criteria.min_pi_o_delta),
        ("min_pi_m_reduction", criteria.min_pi_m_reduction),
    ):
        if value < 0:
            raise ValueError(f"{name} must be >= 0")


def _validate_verification_snapshot(snapshot: LifecycleVerificationSnapshot, *, label: str) -> None:
    if not snapshot.task_ids:
        raise ValueError(f"{label} verification snapshot requires at least one task")
    if len(set(snapshot.task_ids)) != len(snapshot.task_ids):
        raise ValueError(f"{label} verification snapshot has duplicate task IDs")
    if set(snapshot.verifier_ids_by_task) != set(snapshot.task_ids):
        raise ValueError(f"{label} verification snapshot must identify verifiers for every task")
    if not 0 <= snapshot.passed <= len(snapshot.task_ids):
        raise ValueError(f"{label} verification snapshot has an invalid passed count")
    for name, value in (("pass_rate", snapshot.pass_rate), ("pi_o", snapshot.pi_o), ("pi_m", snapshot.pi_m)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{label} verification snapshot has an invalid {name}")


def evaluate_lifecycle_promotion(
    baseline: LifecycleVerificationSnapshot,
    provisional: LifecycleVerificationSnapshot,
    criteria: LifecyclePromotionCriteria | None = None,
) -> LifecyclePromotionResult:
    """Promote a lifecycle edit only if its fixed safety contract passes.

    The gate is intentionally deterministic: the caller supplies an overloaded
    baseline and the re-run of the provisional library, both from the same
    verifier surface.  A rejected result is an instruction to discard the
    provisional copy and keep the original library state.
    """

    active_criteria = criteria or LifecyclePromotionCriteria()
    _validate_promotion_criteria(active_criteria)
    _validate_verification_snapshot(baseline, label="baseline")
    _validate_verification_snapshot(provisional, label="provisional")

    checks = [
        ValidationResult(
            "same_task_coverage",
            not active_criteria.require_same_task_coverage or baseline.task_ids == provisional.task_ids,
            evidence="same ordered task IDs were re-run",
        ),
        ValidationResult(
            "same_verifier_contract",
            not active_criteria.require_same_verifier_contract
            or baseline.verifier_ids_by_task == provisional.verifier_ids_by_task,
            evidence="same per-task deterministic verifiers were re-run",
        ),
        ValidationResult(
            "pass_rate_non_regression",
            provisional.pass_rate >= baseline.pass_rate + active_criteria.min_pass_rate_delta,
            score=provisional.pass_rate - baseline.pass_rate,
            evidence=(
                f"observed delta={provisional.pass_rate - baseline.pass_rate:+.6f}; "
                f"required >= {active_criteria.min_pass_rate_delta:+.6f}"
            ),
        ),
        ValidationResult(
            "clean_oracle_routing_non_regression",
            provisional.pi_o >= baseline.pi_o + active_criteria.min_pi_o_delta,
            score=provisional.pi_o - baseline.pi_o,
            evidence=(
                f"observed delta={provisional.pi_o - baseline.pi_o:+.6f}; "
                f"required >= {active_criteria.min_pi_o_delta:+.6f}"
            ),
        ),
        ValidationResult(
            "shadowing_reduction",
            baseline.pi_m - provisional.pi_m >= active_criteria.min_pi_m_reduction,
            score=baseline.pi_m - provisional.pi_m,
            evidence=(
                f"observed reduction={baseline.pi_m - provisional.pi_m:+.6f}; "
                f"required >= {active_criteria.min_pi_m_reduction:+.6f}"
            ),
        ),
    ]
    accepted = all_passed(checks)
    failed = [check.name for check in checks if not check.passed]
    return LifecyclePromotionResult(
        accepted=accepted,
        reason=(
            "provisional lifecycle change accepted after deterministic verifier re-run"
            if accepted
            else f"provisional lifecycle change rejected: {', '.join(failed)}"
        ),
        criteria=active_criteria,
        baseline=baseline,
        provisional=provisional,
        checks=checks,
        rollback_required=not accepted,
    )


def evaluate_policy_change(change: HarnessPolicyChange) -> HarnessPolicyChange:
    change.accepted = self_harness_accept(change.delta_in, change.delta_held_out)
    return change
