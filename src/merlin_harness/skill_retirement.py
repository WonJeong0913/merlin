"""Conservative copy-on-write retirement for already-hidden skills.

Retirement is deliberately stricter than hiding.  A skill must already be
hidden, remain unused in at least two independent complete-evidence windows,
and pass the same trusted library verifiers before and after its status is
changed to ``retired``.  The live tuple is never mutated; any failed gate keeps
the hidden parent library intact.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from .lifecycle import all_passed, stage_provisional_lifecycle_change
from .models import (
    LifecycleAction,
    LifecycleDecision,
    LifecycleStatus,
    SkillArtifact,
    ValidationResult,
)
from .skill_repair import skill_artifact_sha256, skill_library_snapshot_sha256
from .verifier_trust import VerifierTrustProfile, assess_verifier_trust


SHA256_RE = re.compile(r"[0-9a-f]{64}")
MIN_RETIREMENT_WINDOWS = 2


class SkillRetirementError(ValueError):
    """Raised when retirement evidence or a verifier contract is malformed."""


@dataclass(frozen=True, slots=True)
class RetirementCase:
    case_id: str
    verifier_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetirementCaseResult:
    case_id: str
    verifier_id: str
    passed: bool
    score: float = 0.0
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class RetirementObservationWindow:
    """Hash-bound no-use observation over the exact hidden library snapshot."""

    window_id: str
    library_snapshot_sha256: str
    raw_trace_sha256: str
    case_ids: tuple[str, ...]
    verifier_ids: tuple[str, ...]
    passed_case_ids: tuple[str, ...]
    target_selected_count: int
    target_invocation_count: int
    actual_invocation_evidence_complete: bool


class RetirementEvaluator(Protocol):
    def evaluate_library(
        self,
        skills: tuple[SkillArtifact, ...],
        cases: tuple[RetirementCase, ...],
    ) -> Sequence[RetirementCaseResult]: ...


@dataclass(slots=True)
class SkillRetirementResult:
    retired: bool
    lifecycle_action: str
    reason: str
    recommended_action: str
    skill_id: str
    original_library_snapshot_sha256: str
    provisional_library_snapshot_sha256: str | None
    baseline_results: tuple[RetirementCaseResult, ...] = ()
    provisional_results: tuple[RetirementCaseResult, ...] = ()
    gates: tuple[ValidationResult, ...] = ()
    resolved_library: tuple[SkillArtifact, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "retired": self.retired,
            "lifecycle_action": self.lifecycle_action,
            "reason": self.reason,
            "recommended_action": self.recommended_action,
            "skill_id": self.skill_id,
            "original_library_snapshot_sha256": self.original_library_snapshot_sha256,
            "provisional_library_snapshot_sha256": self.provisional_library_snapshot_sha256,
            "baseline_results": [asdict(item) for item in self.baseline_results],
            "provisional_results": [asdict(item) for item in self.provisional_results],
            "gates": [asdict(item) for item in self.gates],
            "resolved_library": [skill.to_dict() for skill in self.resolved_library],
            "evidence_boundary": {
                "already_hidden_skill_only": True,
                "minimum_independent_windows": MIN_RETIREMENT_WINDOWS,
                "complete_invocation_evidence_required": True,
                "same_verifier_contract_required": True,
                "copy_on_write": True,
                "physical_artifact_deletion": False,
                "provider_native_invocation_claim": False,
            },
        }


def _validate_cases(cases: tuple[RetirementCase, ...]) -> None:
    if not cases:
        raise SkillRetirementError("retirement requires protected library cases")
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise SkillRetirementError("retirement cases contain duplicate IDs")
    for case in cases:
        if not case.case_id.strip() or not case.verifier_id.strip():
            raise SkillRetirementError("retirement case IDs and verifier IDs must be non-empty")


def _validate_profiles(
    cases: tuple[RetirementCase, ...],
    profiles: Mapping[str, VerifierTrustProfile],
) -> tuple[ValidationResult, ...]:
    checks: list[ValidationResult] = []
    for case in cases:
        profile = profiles.get(case.verifier_id)
        if profile is None or profile.verifier_id != case.verifier_id:
            raise SkillRetirementError(
                f"retirement case {case.case_id!r} has no matching verifier trust profile"
            )
        trust = assess_verifier_trust(profile, purpose="promotion")
        failed = [item.name for item in trust if not item.passed]
        if failed:
            raise SkillRetirementError(
                f"retirement verifier trust gate failed for {case.case_id!r}: "
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


def _validate_windows(
    windows: tuple[RetirementObservationWindow, ...],
    *,
    snapshot_sha256: str,
    cases: tuple[RetirementCase, ...],
) -> None:
    if len(windows) < MIN_RETIREMENT_WINDOWS:
        raise SkillRetirementError(
            f"retirement requires at least {MIN_RETIREMENT_WINDOWS} observation windows"
        )
    window_ids = [window.window_id for window in windows]
    trace_hashes = [window.raw_trace_sha256 for window in windows]
    if len(window_ids) != len(set(window_ids)) or any(not value.strip() for value in window_ids):
        raise SkillRetirementError("retirement observation window IDs must be unique and non-empty")
    if len(trace_hashes) != len(set(trace_hashes)):
        raise SkillRetirementError("retirement observation windows must bind distinct raw traces")
    expected_cases = tuple(case.case_id for case in cases)
    expected_verifiers = tuple(case.verifier_id for case in cases)
    for window in windows:
        if not SHA256_RE.fullmatch(window.raw_trace_sha256):
            raise SkillRetirementError("retirement observation raw trace hash is invalid")
        if window.library_snapshot_sha256 != snapshot_sha256:
            raise SkillRetirementError("retirement observation library snapshot drifted")
        if window.case_ids != expected_cases or window.verifier_ids != expected_verifiers:
            raise SkillRetirementError("retirement observation verifier coverage drifted")
        if window.passed_case_ids != expected_cases:
            raise SkillRetirementError("retirement observation has incomplete verifier success")
        if window.target_selected_count < 0 or window.target_invocation_count < 0:
            raise SkillRetirementError("retirement observation counts must be non-negative")


def _normalize_results(
    cases: tuple[RetirementCase, ...],
    results: Sequence[RetirementCaseResult],
    *,
    label: str,
) -> tuple[RetirementCaseResult, ...]:
    by_id: dict[str, RetirementCaseResult] = {}
    for result in results:
        if result.case_id in by_id:
            raise SkillRetirementError(f"{label} returned a duplicate case result")
        by_id[result.case_id] = result
    expected = {case.case_id for case in cases}
    if set(by_id) != expected:
        raise SkillRetirementError(f"{label} result coverage does not match frozen cases")
    ordered: list[RetirementCaseResult] = []
    for case in cases:
        result = by_id[case.case_id]
        if result.verifier_id != case.verifier_id:
            raise SkillRetirementError(f"{label} verifier ID drifted for {case.case_id!r}")
        if not 0.0 <= result.score <= 1.0:
            raise SkillRetirementError(f"{label} score is outside [0, 1]")
        ordered.append(result)
    return tuple(ordered)


def _non_regression(
    baseline: tuple[RetirementCaseResult, ...],
    provisional: tuple[RetirementCaseResult, ...],
) -> bool:
    return all(
        before.case_id == after.case_id
        and before.verifier_id == after.verifier_id
        and (not before.passed or after.passed)
        and after.score >= before.score
        for before, after in zip(baseline, provisional)
    )


def _content_equal_except_status(before: SkillArtifact, after: SkillArtifact) -> bool:
    left = before.to_dict()
    right = after.to_dict()
    left.pop("status", None)
    right.pop("status", None)
    return left == right


def run_skill_retirement(
    *,
    skill_id: str,
    library: tuple[SkillArtifact, ...],
    observation_windows: tuple[RetirementObservationWindow, ...],
    regression_cases: tuple[RetirementCase, ...],
    evaluator: RetirementEvaluator,
    verifier_profiles: Mapping[str, VerifierTrustProfile],
) -> SkillRetirementResult:
    """Retire one hidden skill only after no-use and same-verifier COW gates."""

    if not library:
        raise SkillRetirementError("retirement requires a non-empty library")
    ids = [skill.id for skill in library]
    if len(ids) != len(set(ids)):
        raise SkillRetirementError("retirement library contains duplicate skill IDs")
    matches = [skill for skill in library if skill.id == skill_id]
    if len(matches) != 1:
        raise SkillRetirementError(f"retirement skill not found: {skill_id}")
    original = matches[0]
    if original.status != LifecycleStatus.HIDDEN:
        raise SkillRetirementError("only an already-hidden skill can enter retirement")

    snapshot = skill_library_snapshot_sha256(library)
    _validate_cases(regression_cases)
    trust_checks = _validate_profiles(regression_cases, verifier_profiles)
    _validate_windows(
        observation_windows,
        snapshot_sha256=snapshot,
        cases=regression_cases,
    )

    original_hashes = {skill.id: skill_artifact_sha256(skill) for skill in library}
    baseline = _normalize_results(
        regression_cases,
        evaluator.evaluate_library(copy.deepcopy(library), regression_cases),
        label="baseline retirement evaluator",
    )
    provisional_list, _change = stage_provisional_lifecycle_change(
        list(copy.deepcopy(library)),
        [
            LifecycleDecision(
                skill_id=skill_id,
                action=LifecycleAction.RETIRE,
                reason="hidden skill remained unused across independent verified windows",
                evidence_trace_ids=[window.raw_trace_sha256 for window in observation_windows],
            )
        ],
    )
    provisional = tuple(provisional_list)
    provisional_results = _normalize_results(
        regression_cases,
        evaluator.evaluate_library(copy.deepcopy(provisional), regression_cases),
        label="provisional retirement evaluator",
    )

    after_by_id = {skill.id: skill for skill in provisional}
    target_after = after_by_id[skill_id]
    unused_windows = all(
        window.actual_invocation_evidence_complete
        and window.target_selected_count == 0
        and window.target_invocation_count == 0
        for window in observation_windows
    )
    cow_isolated = (
        skill_library_snapshot_sha256(library) == snapshot
        and target_after.status == LifecycleStatus.RETIRED
        and _content_equal_except_status(original, target_after)
        and all(
            original_hashes[skill.id] == skill_artifact_sha256(after_by_id[skill.id])
            for skill in library
            if skill.id != skill_id
        )
    )
    gates = (
        ValidationResult(
            "retirement_eligibility",
            True,
            evidence="target exists and is already hidden",
        ),
        ValidationResult(
            "independent_observation_windows",
            len(observation_windows) >= MIN_RETIREMENT_WINDOWS,
            evidence=f"distinct_windows={len(observation_windows)}",
        ),
        ValidationResult(
            "complete_zero_use_evidence",
            unused_windows,
            evidence="complete invocation evidence reports zero selection and invocation",
        ),
        ValidationResult(
            "verifier_trust",
            bool(trust_checks),
            evidence=f"trusted_profiles={len(trust_checks)}",
        ),
        ValidationResult(
            "baseline_library_clean",
            bool(baseline) and all(item.passed for item in baseline),
            evidence="all protected cases passed before retirement",
        ),
        ValidationResult(
            "same_verifier_non_regression",
            _non_regression(baseline, provisional_results),
            evidence="same case and verifier IDs were re-run after retirement",
        ),
        ValidationResult(
            "copy_on_write_isolation",
            cow_isolated,
            evidence="only target lifecycle status changed in the provisional copy",
        ),
    )
    retired = all_passed(list(gates))
    resolved = provisional if retired else copy.deepcopy(library)
    return SkillRetirementResult(
        retired=retired,
        lifecycle_action=(
            LifecycleAction.RETIRE.value if retired else LifecycleAction.HIDE.value
        ),
        reason=(
            "hidden skill retired after independent no-use and same-verifier gates"
            if retired
            else "retirement rejected; hidden parent library retained"
        ),
        recommended_action=("retain_retired_tombstone" if retired else "retain_hidden"),
        skill_id=skill_id,
        original_library_snapshot_sha256=snapshot,
        provisional_library_snapshot_sha256=(
            skill_library_snapshot_sha256(provisional) if retired else None
        ),
        baseline_results=baseline,
        provisional_results=provisional_results,
        gates=gates,
        resolved_library=tuple(resolved),
    )
