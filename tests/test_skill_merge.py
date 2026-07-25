from __future__ import annotations

import copy
import hashlib
import json
import unittest
from dataclasses import replace

from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep
from src.merlin_harness.skill_merge import (
    MERGE_TOMBSTONE_KEY,
    MergeCase,
    MergeCaseResult,
    MergeDiagnosis,
    SkillMergeError,
    run_skill_merge,
)
from src.merlin_harness.skill_repair import (
    skill_artifact_sha256,
    skill_library_snapshot_sha256,
)
from src.merlin_harness.verifier_trust import VerifierTrustLevel, VerifierTrustProfile


CANONICAL_ID = "json-report-writer"
REDUNDANT_ID = "json-report-exporter"


def output_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def skill(
    skill_id: str,
    *,
    behavior: str = "same-output",
    status: LifecycleStatus = LifecycleStatus.ACTIVE,
) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name=skill_id.replace("-", " ").title(),
        description=f"Write the governed JSON report via {skill_id}",
        trigger="write governed json report",
        do_not_use_when=["plain text requested"],
        steps=[SkillStep(id="run", description="write deterministic report")],
        validators=["report-contract-v1"],
        expected_artifacts=["report.json"],
        provenance_trace_ids=[f"origin-{skill_id}"],
        status=status,
        metadata={"fixture": True, "behavior": behavior},
    )


EQUIVALENCE_CASES = (
    MergeCase("ascii-report", "equivalence", "ascii-report-v1"),
    MergeCase("korean-report", "equivalence", "korean-report-v1"),
)
REGRESSION_CASES = (
    MergeCase("protected-summary", "library_regression", "protected-summary-v1"),
    MergeCase("protected-index", "library_regression", "protected-index-v1"),
)


def profiles() -> dict[str, VerifierTrustProfile]:
    cases = EQUIVALENCE_CASES + REGRESSION_CASES
    return {
        case.verifier_id: VerifierTrustProfile(
            verifier_id=case.verifier_id,
            level=VerifierTrustLevel.HIDDEN_ORACLE,
            deterministic=True,
            requirement_ids=(case.case_id,),
            covered_requirement_ids=(case.case_id,),
            behavioral_assertion_count=2,
            author_independent_from_candidate=True,
            hidden_from_reviser=True,
            provenance_sha256=f"{index:x}" * 64,
        )
        for index, case in enumerate(cases, start=1)
    }


def diagnosis(
    library: tuple[SkillArtifact, ...],
    *,
    complete: bool = True,
) -> MergeDiagnosis:
    return MergeDiagnosis(
        canonical_skill_id=CANONICAL_ID,
        redundant_skill_id=REDUNDANT_ID,
        library_snapshot_sha256=skill_library_snapshot_sha256(library),
        raw_trace_sha256s=("a" * 64, "b" * 64),
        observed_task_ids=("ascii-report", "korean-report", "protected-summary"),
        overlapping_exposure_task_ids=("ascii-report", "korean-report"),
        overlap_selection_count=2,
        overlap_invocation_count=2,
        actual_invocation_evidence_complete=complete,
    )


class Evaluator:
    def __init__(self, *, regress_after_merge: bool = False) -> None:
        self.regress_after_merge = regress_after_merge

    def evaluate_skill(self, target, cases):
        value = target.metadata["behavior"]
        return tuple(
            MergeCaseResult(
                case_id=case.case_id,
                verifier_id=case.verifier_id,
                passed=True,
                score=1.0,
                output_sha256=output_hash(f"{case.case_id}:{value}"),
                evidence="deterministic equivalence fixture",
            )
            for case in cases
        )

    def evaluate_library(self, skills, cases):
        redundant = next(item for item in skills if item.id == REDUNDANT_ID)
        regressed = (
            self.regress_after_merge
            and redundant.status == LifecycleStatus.RETIRED
        )
        return tuple(
            MergeCaseResult(
                case_id=case.case_id,
                verifier_id=case.verifier_id,
                passed=not (regressed and case.case_id == "protected-index"),
                score=0.0 if regressed and case.case_id == "protected-index" else 1.0,
                output_sha256=output_hash(
                    f"{case.case_id}:regressed"
                    if regressed and case.case_id == "protected-index"
                    else f"{case.case_id}:stable"
                ),
                evidence="deterministic library fixture",
            )
            for case in cases
        )


class SkillMergeTests(unittest.TestCase):
    def make_library(self) -> tuple[SkillArtifact, ...]:
        return (
            skill(CANONICAL_ID),
            skill(REDUNDANT_ID),
            skill("unrelated-csv-writer", behavior="csv-output"),
        )

    def run_merge(self, library, **kwargs):
        return run_skill_merge(
            diagnosis=kwargs.pop("diagnosis", diagnosis(library)),
            library=library,
            equivalence_cases=EQUIVALENCE_CASES,
            regression_cases=REGRESSION_CASES,
            evaluator=kwargs.pop("evaluator", Evaluator()),
            verifier_profiles=kwargs.pop("verifier_profiles", profiles()),
            **kwargs,
        )

    def test_equivalent_duplicate_merges_into_cow_alias_tombstone(self) -> None:
        library = self.make_library()
        before = copy.deepcopy(library)
        canonical_hash = skill_artifact_sha256(library[0])
        redundant_hash = skill_artifact_sha256(library[1])
        result = self.run_merge(library)

        self.assertTrue(result.merged)
        self.assertEqual(result.lifecycle_action, "merge")
        self.assertEqual(len(result.gates), 9)
        self.assertTrue(all(gate.passed for gate in result.gates))
        canonical = next(
            item for item in result.resolved_library if item.id == CANONICAL_ID
        )
        redundant = next(
            item for item in result.resolved_library if item.id == REDUNDANT_ID
        )
        self.assertEqual(canonical.status, LifecycleStatus.ACTIVE)
        self.assertEqual(skill_artifact_sha256(canonical), canonical_hash)
        self.assertEqual(redundant.status, LifecycleStatus.RETIRED)
        tombstone = redundant.metadata[MERGE_TOMBSTONE_KEY]
        self.assertEqual(tombstone["canonical_skill_id"], CANONICAL_ID)
        self.assertEqual(tombstone["canonical_artifact_sha256"], canonical_hash)
        self.assertEqual(tombstone["redundant_artifact_sha256"], redundant_hash)
        self.assertEqual(
            [item.to_dict() for item in library],
            [item.to_dict() for item in before],
        )
        payload = result.to_dict()
        self.assertFalse(payload["evidence_boundary"]["new_skill_body_synthesis"])
        self.assertFalse(payload["evidence_boundary"]["physical_artifact_deletion"])

    def test_same_verifier_regression_rolls_back_both_active(self) -> None:
        library = self.make_library()
        result = self.run_merge(
            library,
            evaluator=Evaluator(regress_after_merge=True),
        )
        self.assertFalse(result.merged)
        self.assertEqual(result.lifecycle_action, "rollback")
        self.assertIsNone(result.provisional_library_snapshot_sha256)
        self.assertTrue(
            all(item.status == LifecycleStatus.ACTIVE for item in result.resolved_library)
        )
        gates = {gate.name: gate.passed for gate in result.gates}
        self.assertFalse(gates["same_verifier_exact_non_regression"])
        self.assertTrue(gates["copy_on_write_tombstone_isolation"])

    def test_behavior_or_scope_mismatch_rolls_back(self) -> None:
        behavior_mismatch = (
            skill(CANONICAL_ID),
            skill(REDUNDANT_ID, behavior="different-output"),
            skill("unrelated-csv-writer", behavior="csv-output"),
        )
        result = self.run_merge(behavior_mismatch)
        self.assertFalse(result.merged)
        self.assertFalse(
            {gate.name: gate.passed for gate in result.gates}["behavioral_equivalence"]
        )

        scope_mismatch = self.make_library()
        scope_mismatch[1].trigger = "write a completely different report"
        result = self.run_merge(scope_mismatch)
        self.assertFalse(result.merged)
        self.assertFalse(
            {gate.name: gate.passed for gate in result.gates}["routing_scope_compatible"]
        )

    def test_incomplete_invocation_evidence_rolls_back(self) -> None:
        library = self.make_library()
        result = self.run_merge(
            library,
            diagnosis=diagnosis(library, complete=False),
        )
        self.assertFalse(result.merged)
        gates = {gate.name: gate.passed for gate in result.gates}
        self.assertFalse(gates["complete_overlap_evidence"])

    def test_snapshot_trace_and_verifier_tamper_fail_closed(self) -> None:
        library = self.make_library()
        with self.assertRaisesRegex(SkillMergeError, "snapshot drifted"):
            self.run_merge(
                library,
                diagnosis=replace(
                    diagnosis(library),
                    library_snapshot_sha256="f" * 64,
                ),
            )
        with self.assertRaisesRegex(SkillMergeError, "distinct SHA-256"):
            self.run_merge(
                library,
                diagnosis=replace(
                    diagnosis(library),
                    raw_trace_sha256s=("a" * 64, "a" * 64),
                ),
            )

        bad_profiles = profiles()
        profile = bad_profiles[EQUIVALENCE_CASES[0].verifier_id]
        bad_profiles[profile.verifier_id] = replace(
            profile,
            level=VerifierTrustLevel.STRUCTURAL,
            behavioral_assertion_count=0,
        )
        with self.assertRaisesRegex(SkillMergeError, "trust gate failed"):
            self.run_merge(library, verifier_profiles=bad_profiles)

    def test_non_active_or_existing_tombstone_cannot_reenter_merge(self) -> None:
        hidden = (
            skill(CANONICAL_ID),
            skill(REDUNDANT_ID, status=LifecycleStatus.HIDDEN),
        )
        with self.assertRaisesRegex(SkillMergeError, "two active"):
            self.run_merge(hidden)

        marked = self.make_library()
        marked[1].metadata[MERGE_TOMBSTONE_KEY] = {"tampered": True}
        with self.assertRaisesRegex(SkillMergeError, "already contains"):
            self.run_merge(marked)


if __name__ == "__main__":
    unittest.main()
