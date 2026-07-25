"""Conservative duplicate-skill merge with copy-on-write rollback.

Merge v1 does not synthesize a new skill body. It retains one byte-identical
active canonical skill and converts one independently verified equivalent
active skill into a non-provisionable retired alias tombstone. Any scope,
equivalence, verifier, regression, provenance, or isolation failure retains the
original library with both skills active.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence

from .lifecycle import all_passed
from .models import LifecycleAction, LifecycleStatus, SkillArtifact, ValidationResult
from .skill_repair import skill_artifact_sha256, skill_library_snapshot_sha256
from .verifier_trust import VerifierTrustProfile, assess_verifier_trust


SHA256_RE = re.compile(r"[0-9a-f]{64}")
MIN_MERGE_TRACE_WINDOWS = 2
MERGE_TOMBSTONE_KEY = "merge_tombstone"


class SkillMergeError(ValueError):
    """Raised when merge evidence or its frozen contract is malformed."""


@dataclass(frozen=True, slots=True)
class MergeDiagnosis:
    canonical_skill_id: str
    redundant_skill_id: str
    library_snapshot_sha256: str
    raw_trace_sha256s: tuple[str, ...]
    observed_task_ids: tuple[str, ...]
    overlapping_exposure_task_ids: tuple[str, ...]
    overlap_selection_count: int
    overlap_invocation_count: int
    actual_invocation_evidence_complete: bool


@dataclass(frozen=True, slots=True)
class MergeCase:
    case_id: str
    split: Literal["equivalence", "library_regression"]
    verifier_id: str


@dataclass(frozen=True, slots=True)
class MergeCaseResult:
    case_id: str
    verifier_id: str
    passed: bool
    score: float
    output_sha256: str
    evidence: str = ""


class MergeEvaluator(Protocol):
    def evaluate_skill(
        self,
        skill: SkillArtifact,
        cases: tuple[MergeCase, ...],
    ) -> Sequence[MergeCaseResult]: ...

    def evaluate_library(
        self,
        skills: tuple[SkillArtifact, ...],
        cases: tuple[MergeCase, ...],
    ) -> Sequence[MergeCaseResult]: ...


@dataclass(slots=True)
class SkillMergeResult:
    merged: bool
    lifecycle_action: str
    reason: str
    recommended_action: str
    canonical_skill_id: str
    redundant_skill_id: str
    original_library_snapshot_sha256: str
    provisional_library_snapshot_sha256: str | None
    canonical_results: tuple[MergeCaseResult, ...] = ()
    redundant_results: tuple[MergeCaseResult, ...] = ()
    baseline_library_results: tuple[MergeCaseResult, ...] = ()
    provisional_library_results: tuple[MergeCaseResult, ...] = ()
    gates: tuple[ValidationResult, ...] = ()
    resolved_library: tuple[SkillArtifact, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "merged": self.merged,
            "lifecycle_action": self.lifecycle_action,
            "reason": self.reason,
            "recommended_action": self.recommended_action,
            "canonical_skill_id": self.canonical_skill_id,
            "redundant_skill_id": self.redundant_skill_id,
            "original_library_snapshot_sha256": self.original_library_snapshot_sha256,
            "provisional_library_snapshot_sha256": self.provisional_library_snapshot_sha256,
            "canonical_results": [asdict(item) for item in self.canonical_results],
            "redundant_results": [asdict(item) for item in self.redundant_results],
            "baseline_library_results": [
                asdict(item) for item in self.baseline_library_results
            ],
            "provisional_library_results": [
                asdict(item) for item in self.provisional_library_results
            ],
            "gates": [asdict(item) for item in self.gates],
            "resolved_library": [skill.to_dict() for skill in self.resolved_library],
            "evidence_boundary": {
                "duplicate_equivalence_only": True,
                "new_skill_body_synthesis": False,
                "canonical_artifact_byte_identity_required": True,
                "minimum_independent_trace_windows": MIN_MERGE_TRACE_WINDOWS,
                "actual_invocation_evidence_required": True,
                "same_verifier_contract_required": True,
                "copy_on_write": True,
                "physical_artifact_deletion": False,
                "provider_native_invocation_claim": False,
            },
        }


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_diagnosis(diagnosis: MergeDiagnosis, *, snapshot: str) -> bool:
    if diagnosis.canonical_skill_id == diagnosis.redundant_skill_id:
        raise SkillMergeError("merge requires two distinct skill IDs")
    if diagnosis.library_snapshot_sha256 != snapshot:
        raise SkillMergeError("merge diagnosis library snapshot drifted")
    if len(diagnosis.raw_trace_sha256s) < MIN_MERGE_TRACE_WINDOWS:
        raise SkillMergeError(
            f"merge requires at least {MIN_MERGE_TRACE_WINDOWS} trace windows"
        )
    if (
        len(set(diagnosis.raw_trace_sha256s)) != len(diagnosis.raw_trace_sha256s)
        or any(not SHA256_RE.fullmatch(value) for value in diagnosis.raw_trace_sha256s)
    ):
        raise SkillMergeError("merge diagnosis trace hashes must be distinct SHA-256 values")
    if (
        not diagnosis.observed_task_ids
        or len(set(diagnosis.observed_task_ids)) != len(diagnosis.observed_task_ids)
        or any(not value.strip() for value in diagnosis.observed_task_ids)
    ):
        raise SkillMergeError("merge diagnosis observed task IDs are invalid")
    if (
        not diagnosis.overlapping_exposure_task_ids
        or len(set(diagnosis.overlapping_exposure_task_ids))
        != len(diagnosis.overlapping_exposure_task_ids)
        or not set(diagnosis.overlapping_exposure_task_ids).issubset(
            diagnosis.observed_task_ids
        )
    ):
        raise SkillMergeError("merge overlap tasks must be a non-empty observed subset")
    if diagnosis.overlap_selection_count < 0 or diagnosis.overlap_invocation_count < 0:
        raise SkillMergeError("merge overlap counts must be non-negative")
    return bool(
        diagnosis.actual_invocation_evidence_complete
        and diagnosis.overlap_selection_count > 0
        and diagnosis.overlap_invocation_count > 0
    )


def _validate_cases(cases: tuple[MergeCase, ...], *, split: str) -> None:
    if not cases:
        raise SkillMergeError(f"{split} merge cases are required")
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise SkillMergeError(f"{split} merge cases contain duplicate IDs")
    for case in cases:
        if case.split != split:
            raise SkillMergeError(
                f"merge case {case.case_id!r} has split {case.split!r}, expected {split!r}"
            )
        if not case.case_id.strip() or not case.verifier_id.strip():
            raise SkillMergeError("merge case and verifier IDs must be non-empty")


def _validate_profiles(
    cases: tuple[MergeCase, ...],
    profiles: Mapping[str, VerifierTrustProfile],
) -> tuple[ValidationResult, ...]:
    checks: list[ValidationResult] = []
    for case in cases:
        profile = profiles.get(case.verifier_id)
        if profile is None or profile.verifier_id != case.verifier_id:
            raise SkillMergeError(
                f"merge case {case.case_id!r} has no matching verifier trust profile"
            )
        trust = assess_verifier_trust(profile, purpose="promotion")
        failed = [item.name for item in trust if not item.passed]
        if failed:
            raise SkillMergeError(
                f"merge verifier trust gate failed for {case.case_id!r}: "
                + ", ".join(failed)
            )
        checks.append(
            ValidationResult(
                f"verifier_trust:{case.case_id}",
                True,
                evidence=f"level={profile.level.value}; promotion eligible",
            )
        )
    return tuple(checks)


def _normalize_results(
    cases: tuple[MergeCase, ...],
    results: Sequence[MergeCaseResult],
    *,
    label: str,
) -> tuple[MergeCaseResult, ...]:
    by_id: dict[str, MergeCaseResult] = {}
    for result in results:
        if result.case_id in by_id:
            raise SkillMergeError(f"{label} returned a duplicate case result")
        by_id[result.case_id] = result
    if set(by_id) != {case.case_id for case in cases}:
        raise SkillMergeError(f"{label} result coverage does not match frozen cases")
    ordered: list[MergeCaseResult] = []
    for case in cases:
        result = by_id[case.case_id]
        if result.verifier_id != case.verifier_id:
            raise SkillMergeError(f"{label} verifier ID drifted for {case.case_id!r}")
        if not 0.0 <= result.score <= 1.0:
            raise SkillMergeError(f"{label} score is outside [0, 1]")
        if not SHA256_RE.fullmatch(result.output_sha256):
            raise SkillMergeError(f"{label} output hash is invalid")
        ordered.append(result)
    return tuple(ordered)


def _scope_compatible(canonical: SkillArtifact, redundant: SkillArtifact) -> bool:
    return (
        canonical.trigger == redundant.trigger
        and canonical.do_not_use_when == redundant.do_not_use_when
        and canonical.validators == redundant.validators
        and canonical.expected_artifacts == redundant.expected_artifacts
    )


def _behaviorally_equivalent(
    canonical: tuple[MergeCaseResult, ...],
    redundant: tuple[MergeCaseResult, ...],
) -> bool:
    return bool(canonical) and all(
        left.case_id == right.case_id
        and left.verifier_id == right.verifier_id
        and left.passed
        and right.passed
        and abs(left.score - right.score) <= 1e-12
        and left.output_sha256 == right.output_sha256
        for left, right in zip(canonical, redundant)
    )


def _exact_regression_preserved(
    baseline: tuple[MergeCaseResult, ...],
    provisional: tuple[MergeCaseResult, ...],
) -> bool:
    return bool(baseline) and all(
        before.case_id == after.case_id
        and before.verifier_id == after.verifier_id
        and before.passed
        and after.passed
        and after.score >= before.score
        and after.output_sha256 == before.output_sha256
        for before, after in zip(baseline, provisional)
    )


def run_skill_merge(
    *,
    diagnosis: MergeDiagnosis,
    library: tuple[SkillArtifact, ...],
    equivalence_cases: tuple[MergeCase, ...],
    regression_cases: tuple[MergeCase, ...],
    evaluator: MergeEvaluator,
    verifier_profiles: Mapping[str, VerifierTrustProfile],
) -> SkillMergeResult:
    """Retire one proven duplicate into an alias tombstone or roll back."""

    if not library:
        raise SkillMergeError("merge requires a non-empty library")
    ids = [skill.id for skill in library]
    if len(ids) != len(set(ids)):
        raise SkillMergeError("merge library contains duplicate skill IDs")
    snapshot = skill_library_snapshot_sha256(library)
    complete_overlap = _validate_diagnosis(diagnosis, snapshot=snapshot)
    by_id = {skill.id: skill for skill in library}
    if diagnosis.canonical_skill_id not in by_id or diagnosis.redundant_skill_id not in by_id:
        raise SkillMergeError("merge diagnosis references an unknown skill")
    canonical = by_id[diagnosis.canonical_skill_id]
    redundant = by_id[diagnosis.redundant_skill_id]
    if canonical.status != LifecycleStatus.ACTIVE or redundant.status != LifecycleStatus.ACTIVE:
        raise SkillMergeError("merge v1 requires two active skills")
    if MERGE_TOMBSTONE_KEY in redundant.metadata:
        raise SkillMergeError("redundant skill already contains a merge tombstone")
    _validate_cases(equivalence_cases, split="equivalence")
    _validate_cases(regression_cases, split="library_regression")
    case_ids = [case.case_id for case in equivalence_cases + regression_cases]
    if len(case_ids) != len(set(case_ids)):
        raise SkillMergeError("equivalence and regression case IDs must be disjoint")
    trust_checks = _validate_profiles(
        equivalence_cases + regression_cases,
        verifier_profiles,
    )

    original_hashes = {skill.id: skill_artifact_sha256(skill) for skill in library}
    canonical_results = _normalize_results(
        equivalence_cases,
        evaluator.evaluate_skill(copy.deepcopy(canonical), equivalence_cases),
        label="canonical equivalence evaluator",
    )
    redundant_results = _normalize_results(
        equivalence_cases,
        evaluator.evaluate_skill(copy.deepcopy(redundant), equivalence_cases),
        label="redundant equivalence evaluator",
    )
    baseline = _normalize_results(
        regression_cases,
        evaluator.evaluate_library(copy.deepcopy(library), regression_cases),
        label="baseline merge evaluator",
    )

    provisional = copy.deepcopy(library)
    provisional_by_id = {skill.id: skill for skill in provisional}
    source_after = provisional_by_id[redundant.id]
    tombstone = {
        "schema_version": 1,
        "canonical_skill_id": canonical.id,
        "canonical_artifact_sha256": original_hashes[canonical.id],
        "redundant_artifact_sha256": original_hashes[redundant.id],
        "evidence_trace_sha256s": list(diagnosis.raw_trace_sha256s),
        "equivalence_case_ids": [case.case_id for case in equivalence_cases],
        "diagnosis_sha256": _canonical_sha256(asdict(diagnosis)),
    }
    source_after.metadata = copy.deepcopy(source_after.metadata)
    source_after.metadata[MERGE_TOMBSTONE_KEY] = tombstone
    source_after.status = LifecycleStatus.RETIRED
    provisional_results = _normalize_results(
        regression_cases,
        evaluator.evaluate_library(copy.deepcopy(provisional), regression_cases),
        label="provisional merge evaluator",
    )

    canonical_unchanged = (
        skill_artifact_sha256(provisional_by_id[canonical.id])
        == original_hashes[canonical.id]
    )
    source_before_payload = redundant.to_dict()
    source_after_payload = source_after.to_dict()
    source_before_payload.pop("status")
    source_after_payload.pop("status")
    source_before_metadata = source_before_payload.pop("metadata")
    source_after_metadata = source_after_payload.pop("metadata")
    tombstone_isolated = (
        source_before_payload == source_after_payload
        and source_after_metadata
        == {**copy.deepcopy(source_before_metadata), MERGE_TOMBSTONE_KEY: tombstone}
    )
    unrelated_unchanged = all(
        skill_artifact_sha256(provisional_by_id[skill.id]) == original_hashes[skill.id]
        for skill in library
        if skill.id not in {canonical.id, redundant.id}
    )
    cow_isolated = (
        skill_library_snapshot_sha256(library) == snapshot
        and [skill.id for skill in provisional] == ids
        and source_after.status == LifecycleStatus.RETIRED
        and canonical_unchanged
        and tombstone_isolated
        and unrelated_unchanged
    )
    gates = (
        ValidationResult(
            "merge_eligibility",
            True,
            evidence="two distinct active skills exist in the frozen library",
        ),
        ValidationResult(
            "complete_overlap_evidence",
            complete_overlap,
            evidence=(
                f"trace_windows={len(diagnosis.raw_trace_sha256s)}; "
                f"selection_overlap={diagnosis.overlap_selection_count}; "
                f"invocation_overlap={diagnosis.overlap_invocation_count}"
            ),
        ),
        ValidationResult(
            "routing_scope_compatible",
            _scope_compatible(canonical, redundant),
            evidence="trigger, exclusions, validators, and expected artifacts match",
        ),
        ValidationResult(
            "trusted_equivalence_and_regression_verifiers",
            bool(trust_checks),
            evidence=f"trusted_profiles={len(trust_checks)}",
        ),
        ValidationResult(
            "behavioral_equivalence",
            _behaviorally_equivalent(canonical_results, redundant_results),
            evidence="both skills passed with equal scores and exact output hashes",
        ),
        ValidationResult(
            "baseline_library_clean",
            bool(baseline) and all(item.passed for item in baseline),
            evidence="all protected cases passed before merge",
        ),
        ValidationResult(
            "same_verifier_exact_non_regression",
            _exact_regression_preserved(baseline, provisional_results),
            evidence="same verifier IDs, passes, scores, and output hashes were preserved",
        ),
        ValidationResult(
            "canonical_artifact_identity",
            canonical_unchanged,
            evidence="active canonical artifact remained byte-identical",
        ),
        ValidationResult(
            "copy_on_write_tombstone_isolation",
            cow_isolated,
            evidence="only redundant status and hash-bound tombstone metadata changed",
        ),
    )
    merged = all_passed(list(gates))
    resolved = provisional if merged else copy.deepcopy(library)
    return SkillMergeResult(
        merged=merged,
        lifecycle_action=LifecycleAction.MERGE.value if merged else "rollback",
        reason=(
            "equivalent duplicate retired into a canonical alias tombstone"
            if merged
            else "merge rejected; original active library retained"
        ),
        recommended_action=(
            "retain_merge_tombstone" if merged else "retain_both_active"
        ),
        canonical_skill_id=canonical.id,
        redundant_skill_id=redundant.id,
        original_library_snapshot_sha256=snapshot,
        provisional_library_snapshot_sha256=(
            skill_library_snapshot_sha256(provisional) if merged else None
        ),
        canonical_results=canonical_results,
        redundant_results=redundant_results,
        baseline_library_results=baseline,
        provisional_library_results=provisional_results,
        gates=gates,
        resolved_library=tuple(resolved),
    )
