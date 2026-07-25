"""Copy-on-write skill repair with target, held-out, and library gates.

This module closes the lifecycle gap between a ``REPAIR`` decision and an
adoptable skill version.  It intentionally does not repair route-local
selection failures: those belong to provisioning or hide/merge policy.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence

from .lifecycle import all_passed, validate_aip_lite_skill
from .metrics import select_first_success_or_best_utility
from .models import LifecycleAction, LifecycleStatus, SkillArtifact, ValidationResult
from .verifier_trust import VerifierTrustProfile, assess_verifier_trust


MAX_REPAIR_CANDIDATES = 8


class SkillRepairError(ValueError):
    """Raised when a repair run violates its frozen contract."""


@dataclass(frozen=True, slots=True)
class RepairDiagnosis:
    skill_id: str
    failure_kind: Literal["skill_local", "route_local", "verifier_missing"]
    trace_ids: tuple[str, ...]
    failed_target_case_ids: tuple[str, ...]
    verifier_feedback: tuple[str, ...]
    library_snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class RepairCase:
    case_id: str
    split: Literal["target", "held_out", "library_regression"]
    verifier_id: str


@dataclass(frozen=True, slots=True)
class RepairCaseResult:
    case_id: str
    verifier_id: str
    passed: bool
    score: float | None = None
    evidence: str = ""


class RepairEvaluator(Protocol):
    def evaluate_skill(
        self, skill: SkillArtifact, cases: tuple[RepairCase, ...]
    ) -> Sequence[RepairCaseResult]:
        """Evaluate one skill version under the supplied frozen cases."""

    def evaluate_library(
        self, skills: tuple[SkillArtifact, ...], cases: tuple[RepairCase, ...]
    ) -> Sequence[RepairCaseResult]:
        """Evaluate a whole provisional library under frozen regression cases."""


class SkillReviser(Protocol):
    def propose(
        self,
        original: SkillArtifact,
        diagnosis: RepairDiagnosis,
        target_feedback: tuple[RepairCaseResult, ...],
        max_candidates: int,
    ) -> Sequence[SkillArtifact]:
        """Propose ordered versions without access to held-out outcomes."""


@dataclass(frozen=True, slots=True)
class RepairCandidateEvaluation:
    candidate_key: str
    version: int
    artifact_sha256: str
    structure_results: tuple[ValidationResult, ...]
    target_results: tuple[RepairCaseResult, ...]
    target_passed: bool
    target_utility: float


@dataclass(slots=True)
class SkillRepairResult:
    adopted: bool
    lifecycle_action: str
    reason: str
    recommended_action: str
    original_library_snapshot_sha256: str
    provisional_library_snapshot_sha256: str | None
    selected_candidate_key: str | None
    selected_candidate_version: int | None
    baseline_target_results: tuple[RepairCaseResult, ...] = ()
    baseline_held_out_results: tuple[RepairCaseResult, ...] = ()
    candidate_held_out_results: tuple[RepairCaseResult, ...] = ()
    baseline_library_results: tuple[RepairCaseResult, ...] = ()
    provisional_library_results: tuple[RepairCaseResult, ...] = ()
    candidate_evaluations: tuple[RepairCandidateEvaluation, ...] = ()
    gates: tuple[ValidationResult, ...] = ()
    resolved_library: tuple[SkillArtifact, ...] = field(default_factory=tuple, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "adopted": self.adopted,
            "lifecycle_action": self.lifecycle_action,
            "reason": self.reason,
            "recommended_action": self.recommended_action,
            "original_library_snapshot_sha256": self.original_library_snapshot_sha256,
            "provisional_library_snapshot_sha256": self.provisional_library_snapshot_sha256,
            "selected_candidate_key": self.selected_candidate_key,
            "selected_candidate_version": self.selected_candidate_version,
            "baseline_target_results": [asdict(item) for item in self.baseline_target_results],
            "baseline_held_out_results": [asdict(item) for item in self.baseline_held_out_results],
            "candidate_held_out_results": [asdict(item) for item in self.candidate_held_out_results],
            "baseline_library_results": [asdict(item) for item in self.baseline_library_results],
            "provisional_library_results": [asdict(item) for item in self.provisional_library_results],
            "candidate_evaluations": [
                {
                    "candidate_key": item.candidate_key,
                    "version": item.version,
                    "artifact_sha256": item.artifact_sha256,
                    "structure_results": [asdict(result) for result in item.structure_results],
                    "target_results": [asdict(result) for result in item.target_results],
                    "target_passed": item.target_passed,
                    "target_utility": item.target_utility,
                }
                for item in self.candidate_evaluations
            ],
            "gates": [asdict(item) for item in self.gates],
            "resolved_library": [skill.to_dict() for skill in self.resolved_library],
            "evidence_boundary": {
                "route_local_repair_allowed": False,
                "held_out_visible_to_reviser": False,
                "same_verifier_contract_required": True,
                "copy_on_write": True,
                "provider_native_invocation_claim": False,
            },
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def skill_artifact_sha256(skill: SkillArtifact) -> str:
    return _sha256(skill.to_dict())


def skill_library_snapshot_sha256(skills: Sequence[SkillArtifact]) -> str:
    records = [skill.to_dict() for skill in sorted(skills, key=lambda item: item.id)]
    return _sha256(records)


def _validate_cases(cases: tuple[RepairCase, ...], expected_split: str) -> None:
    if not cases:
        raise SkillRepairError(f"{expected_split} repair cases are required")
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise SkillRepairError(f"{expected_split} repair cases contain duplicate IDs")
    for case in cases:
        if case.split != expected_split:
            raise SkillRepairError(
                f"repair case {case.case_id!r} has split {case.split!r}, expected {expected_split!r}"
            )
        if not case.verifier_id.strip():
            raise SkillRepairError(f"repair case {case.case_id!r} has no verifier contract")


def _validate_verifier_profiles(
    cases: tuple[RepairCase, ...],
    profiles: Mapping[str, VerifierTrustProfile],
    *,
    purpose: Literal["repair_feedback", "promotion"],
) -> tuple[ValidationResult, ...]:
    checks: list[ValidationResult] = []
    for case in cases:
        profile = profiles.get(case.verifier_id)
        if profile is None:
            raise SkillRepairError(
                f"repair case {case.case_id!r} has no verifier trust profile"
            )
        if profile.verifier_id != case.verifier_id:
            raise SkillRepairError(
                f"verifier trust profile ID mismatch for {case.case_id!r}"
            )
        case_checks = assess_verifier_trust(profile, purpose=purpose)
        failed = [check.name for check in case_checks if not check.passed]
        checks.append(
            ValidationResult(
                f"verifier_trust:{case.case_id}",
                not failed,
                evidence=(
                    f"purpose={purpose}; level={profile.level.value}"
                    if not failed
                    else f"purpose={purpose}; failed={','.join(failed)}"
                ),
            )
        )
    return tuple(checks)


def _normalize_results(
    cases: tuple[RepairCase, ...],
    results: Sequence[RepairCaseResult],
    *,
    label: str,
) -> tuple[RepairCaseResult, ...]:
    by_id: dict[str, RepairCaseResult] = {}
    for result in results:
        if result.case_id in by_id:
            raise SkillRepairError(f"{label} returned duplicate result for {result.case_id!r}")
        by_id[result.case_id] = result
    expected = {case.case_id for case in cases}
    if set(by_id) != expected:
        missing = sorted(expected - set(by_id))
        extra = sorted(set(by_id) - expected)
        raise SkillRepairError(f"{label} result coverage mismatch: missing={missing}, extra={extra}")
    ordered: list[RepairCaseResult] = []
    for case in cases:
        result = by_id[case.case_id]
        if result.verifier_id != case.verifier_id:
            raise SkillRepairError(
                f"{label} changed verifier for {case.case_id!r}: "
                f"{case.verifier_id!r} -> {result.verifier_id!r}"
            )
        ordered.append(result)
    return tuple(ordered)


def _utility(results: tuple[RepairCaseResult, ...]) -> float:
    scores = [result.score for result in results if result.score is not None]
    if scores:
        return sum(scores) / len(scores)
    return sum(result.passed for result in results) / len(results)


def _non_regression(
    baseline: tuple[RepairCaseResult, ...], candidate: tuple[RepairCaseResult, ...]
) -> bool:
    candidate_by_id = {item.case_id: item for item in candidate}
    preserves_passes = all(
        not item.passed or candidate_by_id[item.case_id].passed for item in baseline
    )
    return preserves_passes and sum(item.passed for item in candidate) >= sum(
        item.passed for item in baseline
    )


def _routing_contract_unchanged(original: SkillArtifact, candidate: SkillArtifact) -> bool:
    return (
        candidate.id == original.id
        and candidate.name == original.name
        and candidate.description == original.description
        and candidate.trigger == original.trigger
        and candidate.do_not_use_when == original.do_not_use_when
        and candidate.validators == original.validators
        and candidate.expected_artifacts == original.expected_artifacts
    )


def _redirect_result(
    *,
    diagnosis: RepairDiagnosis,
    library: tuple[SkillArtifact, ...],
    snapshot: str,
) -> SkillRepairResult:
    if diagnosis.failure_kind == "route_local":
        reason = "route-local failure cannot be repaired by rewriting skill content"
        action = "hide_or_update_provisioning"
    else:
        reason = "repair requires complete verifier-backed skill-local evidence"
        action = "add_or_recover_verifier"
    return SkillRepairResult(
        adopted=False,
        lifecycle_action=LifecycleAction.REPAIR.value,
        reason=reason,
        recommended_action=action,
        original_library_snapshot_sha256=snapshot,
        provisional_library_snapshot_sha256=None,
        selected_candidate_key=None,
        selected_candidate_version=None,
        gates=(ValidationResult("repair_eligibility", False, evidence=reason),),
        resolved_library=copy.deepcopy(library),
    )


def run_skill_repair(
    *,
    diagnosis: RepairDiagnosis,
    library: tuple[SkillArtifact, ...],
    target_cases: tuple[RepairCase, ...],
    held_out_cases: tuple[RepairCase, ...],
    regression_cases: tuple[RepairCase, ...],
    evaluator: RepairEvaluator,
    reviser: SkillReviser,
    verifier_profiles: Mapping[str, VerifierTrustProfile],
    max_candidates: int = 3,
) -> SkillRepairResult:
    """Diagnose, revise, re-execute, and promote one skill via copy-on-write.

    The reviser receives only target feedback.  Held-out and library-regression
    cases are evaluated by this function after a target-passing version has
    been selected.
    """

    if not 1 <= max_candidates <= MAX_REPAIR_CANDIDATES:
        raise SkillRepairError(
            f"max_candidates must be between 1 and {MAX_REPAIR_CANDIDATES}"
        )
    if not library:
        raise SkillRepairError("repair requires a non-empty library")
    ids = [skill.id for skill in library]
    if len(ids) != len(set(ids)):
        raise SkillRepairError("repair library contains duplicate skill IDs")
    snapshot = skill_library_snapshot_sha256(library)
    if snapshot != diagnosis.library_snapshot_sha256:
        raise SkillRepairError("repair library snapshot drifted after diagnosis")
    matches = [skill for skill in library if skill.id == diagnosis.skill_id]
    if not matches:
        raise SkillRepairError(f"diagnosed skill not found: {diagnosis.skill_id}")
    original = matches[0]
    if original.status in {LifecycleStatus.RETIRED, LifecycleStatus.REJECTED}:
        raise SkillRepairError("retired or rejected skills cannot enter repair")
    if diagnosis.failure_kind != "skill_local":
        return _redirect_result(diagnosis=diagnosis, library=library, snapshot=snapshot)
    if not diagnosis.trace_ids or not diagnosis.failed_target_case_ids or not diagnosis.verifier_feedback:
        return _redirect_result(
            diagnosis=RepairDiagnosis(
                skill_id=diagnosis.skill_id,
                failure_kind="verifier_missing",
                trace_ids=diagnosis.trace_ids,
                failed_target_case_ids=diagnosis.failed_target_case_ids,
                verifier_feedback=diagnosis.verifier_feedback,
                library_snapshot_sha256=diagnosis.library_snapshot_sha256,
            ),
            library=library,
            snapshot=snapshot,
        )

    _validate_cases(target_cases, "target")
    _validate_cases(held_out_cases, "held_out")
    _validate_cases(regression_cases, "library_regression")
    verifier_trust_checks = (
        _validate_verifier_profiles(
            target_cases, verifier_profiles, purpose="repair_feedback"
        )
        + _validate_verifier_profiles(
            held_out_cases, verifier_profiles, purpose="promotion"
        )
        + _validate_verifier_profiles(
            regression_cases, verifier_profiles, purpose="promotion"
        )
    )
    failed_verifier_trust = [
        check.name for check in verifier_trust_checks if not check.passed
    ]
    if failed_verifier_trust:
        raise SkillRepairError(
            "repair verifier trust gate failed: " + ", ".join(failed_verifier_trust)
        )
    all_case_ids = [case.case_id for case in target_cases + held_out_cases + regression_cases]
    if len(all_case_ids) != len(set(all_case_ids)):
        raise SkillRepairError("target, held-out, and regression case IDs must be disjoint")
    if not set(diagnosis.failed_target_case_ids).issubset({case.case_id for case in target_cases}):
        raise SkillRepairError("diagnosis references a failure outside the frozen target split")

    original_hash = skill_artifact_sha256(original)
    baseline_target = _normalize_results(
        target_cases,
        evaluator.evaluate_skill(copy.deepcopy(original), target_cases),
        label="baseline target evaluator",
    )
    if all(item.passed for item in baseline_target):
        raise SkillRepairError("repair requires at least one reproduced target failure")
    baseline_held_out = _normalize_results(
        held_out_cases,
        evaluator.evaluate_skill(copy.deepcopy(original), held_out_cases),
        label="baseline held-out evaluator",
    )
    baseline_library = _normalize_results(
        regression_cases,
        evaluator.evaluate_library(copy.deepcopy(library), regression_cases),
        label="baseline library evaluator",
    )

    proposed = tuple(
        reviser.propose(
            copy.deepcopy(original),
            diagnosis,
            copy.deepcopy(baseline_target),
            max_candidates,
        )
    )
    if skill_artifact_sha256(original) != original_hash or skill_library_snapshot_sha256(library) != snapshot:
        raise SkillRepairError("reviser mutated the original skill or live library")
    if len(proposed) > max_candidates:
        raise SkillRepairError("reviser exceeded the frozen revision budget")

    evaluations: list[RepairCandidateEvaluation] = []
    candidate_by_key: dict[str, SkillArtifact] = {}
    for index, raw_candidate in enumerate(proposed, start=1):
        candidate = copy.deepcopy(raw_candidate)
        expected_version = original.version + index
        if candidate.version != expected_version:
            raise SkillRepairError(
                f"repair candidate {index} must use version {expected_version}, got {candidate.version}"
            )
        if not _routing_contract_unchanged(original, candidate):
            raise SkillRepairError(
                "skill-local repair changed routing, identity, artifact, or verifier contract"
            )
        candidate.status = LifecycleStatus.CANDIDATE
        candidate.provenance_trace_ids = list(
            dict.fromkeys(candidate.provenance_trace_ids + list(diagnosis.trace_ids))
        )
        structure = tuple(validate_aip_lite_skill(candidate))
        target_results: tuple[RepairCaseResult, ...] = ()
        if all_passed(list(structure)):
            target_results = _normalize_results(
                target_cases,
                evaluator.evaluate_skill(copy.deepcopy(candidate), target_cases),
                label=f"candidate v{candidate.version} target evaluator",
            )
        target_passed = bool(target_results) and all(item.passed for item in target_results)
        utility = _utility(target_results) if target_results else 0.0
        key = f"{candidate.id}@v{candidate.version}"
        candidate_by_key[key] = candidate
        evaluations.append(
            RepairCandidateEvaluation(
                candidate_key=key,
                version=candidate.version,
                artifact_sha256=skill_artifact_sha256(candidate),
                structure_results=structure,
                target_results=target_results,
                target_passed=target_passed,
                target_utility=utility,
            )
        )

    selected_key = select_first_success_or_best_utility(
        [(item.candidate_key, item.target_passed, item.target_utility) for item in evaluations]
    )
    selected_evaluation = next(
        (item for item in evaluations if item.candidate_key == selected_key), None
    )
    if selected_evaluation is None or not selected_evaluation.target_passed:
        reason = "no repair candidate passed the frozen target verifier suite"
        return SkillRepairResult(
            adopted=False,
            lifecycle_action=LifecycleAction.REPAIR.value,
            reason=reason,
            recommended_action="retain_repair_queue",
            original_library_snapshot_sha256=snapshot,
            provisional_library_snapshot_sha256=None,
            selected_candidate_key=selected_key,
            selected_candidate_version=(selected_evaluation.version if selected_evaluation else None),
            baseline_target_results=baseline_target,
            baseline_held_out_results=baseline_held_out,
            baseline_library_results=baseline_library,
            candidate_evaluations=tuple(evaluations),
            gates=(ValidationResult("target_repair", False, evidence=reason),),
            resolved_library=copy.deepcopy(library),
        )

    selected = copy.deepcopy(candidate_by_key[selected_key])
    selected.status = LifecycleStatus.ACTIVE
    candidate_held_out = _normalize_results(
        held_out_cases,
        evaluator.evaluate_skill(copy.deepcopy(selected), held_out_cases),
        label="selected candidate held-out evaluator",
    )
    provisional = tuple(
        copy.deepcopy(selected if skill.id == original.id else skill) for skill in library
    )
    provisional_library = _normalize_results(
        regression_cases,
        evaluator.evaluate_library(copy.deepcopy(provisional), regression_cases),
        label="provisional library evaluator",
    )
    unrelated_unchanged = all(
        skill_artifact_sha256(before) == skill_artifact_sha256(after)
        for before, after in zip(library, provisional)
        if before.id != original.id
    )
    gates = (
        ValidationResult("repair_eligibility", True, evidence="verifier-backed skill-local failure"),
        ValidationResult(
            "verifier_trust",
            True,
            evidence=f"{len(verifier_trust_checks)} frozen verifier profiles passed",
        ),
        ValidationResult("target_repair", True, evidence="first target-passing version selected"),
        ValidationResult(
            "held_out_non_regression",
            _non_regression(baseline_held_out, candidate_held_out),
            evidence="all previously passing held-out cases remain passing",
        ),
        ValidationResult(
            "library_regression_non_regression",
            _non_regression(baseline_library, provisional_library),
            evidence="same library-level cases and verifier IDs were re-run",
        ),
        ValidationResult(
            "copy_on_write_isolation",
            unrelated_unchanged and skill_library_snapshot_sha256(library) == snapshot,
            evidence="original and unrelated skill artifacts remained unchanged",
        ),
    )
    adopted = all_passed(list(gates))
    reason = (
        "repair candidate promoted after target, held-out, and library regression gates"
        if adopted
        else "repair candidate rejected; live library rolled back"
    )
    return SkillRepairResult(
        adopted=adopted,
        lifecycle_action=(LifecycleAction.ADOPT.value if adopted else LifecycleAction.REPAIR.value),
        reason=reason,
        recommended_action=("replace_active_version" if adopted else "retain_repair_queue"),
        original_library_snapshot_sha256=snapshot,
        provisional_library_snapshot_sha256=(
            skill_library_snapshot_sha256(provisional) if adopted else None
        ),
        selected_candidate_key=selected_key,
        selected_candidate_version=selected.version,
        baseline_target_results=baseline_target,
        baseline_held_out_results=baseline_held_out,
        candidate_held_out_results=candidate_held_out,
        baseline_library_results=baseline_library,
        provisional_library_results=provisional_library,
        candidate_evaluations=tuple(evaluations),
        gates=gates,
        resolved_library=(provisional if adopted else copy.deepcopy(library)),
    )
