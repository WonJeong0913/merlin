from __future__ import annotations

import copy
import unittest

from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep
from src.merlin_harness.skill_repair import (
    RepairCase,
    RepairCaseResult,
    RepairDiagnosis,
    SkillRepairError,
    run_skill_repair,
    skill_artifact_sha256,
    skill_library_snapshot_sha256,
)
from src.merlin_harness.verifier_trust import VerifierTrustLevel, VerifierTrustProfile


TARGET = (RepairCase("target-1", "target", "verify-target-v1"),)
HELD_OUT = (RepairCase("held-1", "held_out", "verify-held-v1"),)
REGRESSION = (
    RepairCase("regression-1", "library_regression", "verify-library-v1"),
)


def _profiles() -> dict[str, VerifierTrustProfile]:
    return {
        case.verifier_id: VerifierTrustProfile(
            verifier_id=case.verifier_id,
            level=VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL,
            deterministic=True,
            requirement_ids=(f"requirement:{case.case_id}",),
            covered_requirement_ids=(f"requirement:{case.case_id}",),
            behavioral_assertion_count=1,
            author_independent_from_candidate=True,
            hidden_from_reviser=True,
            provenance_sha256="a" * 64,
        )
        for case in TARGET + HELD_OUT + REGRESSION
    }


def _skill(*, skill_id: str = "count-lines", version: int = 1) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name="Count Lines",
        description="Count non-empty lines. Use for line-count tasks.",
        trigger="Use when a task requests a non-empty line count.",
        do_not_use_when=["The task requests an unrelated file."],
        steps=[
            SkillStep(
                id="count",
                description="Count non-empty lines and write the result.",
                outputs=["count.txt"],
            )
        ],
        validators=["count-verifier-v1"],
        expected_artifacts=["count.txt"],
        provenance_trace_ids=["seed-trace"],
        status=LifecycleStatus.ACTIVE,
        version=version,
        metadata={
            "case_results": {"target-1": False, "held-1": True},
            "library_results": {"regression-1": True},
            "implementation": "counts blank lines by mistake",
        },
    )


class MetadataEvaluator:
    def __init__(self) -> None:
        self.skill_calls: list[tuple[int, tuple[str, ...]]] = []
        self.library_calls: list[tuple[str, ...]] = []

    def evaluate_skill(self, skill, cases):
        self.skill_calls.append((skill.version, tuple(case.case_id for case in cases)))
        values = skill.metadata["case_results"]
        return tuple(
            RepairCaseResult(
                case.case_id,
                case.verifier_id,
                bool(values[case.case_id]),
                score=float(bool(values[case.case_id])),
                evidence=f"skill-v{skill.version}",
            )
            for case in cases
        )

    def evaluate_library(self, skills, cases):
        self.library_calls.append(tuple(case.case_id for case in cases))
        target = next(skill for skill in skills if skill.id == "count-lines")
        values = target.metadata["library_results"]
        return tuple(
            RepairCaseResult(
                case.case_id,
                case.verifier_id,
                bool(values[case.case_id]),
                score=float(bool(values[case.case_id])),
                evidence=f"library-with-v{target.version}",
            )
            for case in cases
        )


class FixedReviser:
    def __init__(self, candidates: list[SkillArtifact]) -> None:
        self.candidates = candidates
        self.visible_feedback_ids: tuple[str, ...] = ()

    def propose(self, original, diagnosis, target_feedback, max_candidates):
        self.visible_feedback_ids = tuple(item.case_id for item in target_feedback)
        return copy.deepcopy(self.candidates)


def _diagnosis(library: tuple[SkillArtifact, ...], *, kind: str = "skill_local") -> RepairDiagnosis:
    return RepairDiagnosis(
        skill_id="count-lines",
        failure_kind=kind,
        trace_ids=("failed-trace-1",),
        failed_target_case_ids=("target-1",),
        verifier_feedback=("expected 3 non-empty lines; observed 5",),
        library_snapshot_sha256=skill_library_snapshot_sha256(library),
    )


class SkillRepairTests(unittest.TestCase):
    def test_skill_local_failure_promotes_first_passing_version_copy_on_write(self) -> None:
        original = _skill()
        unrelated = _skill(skill_id="unrelated")
        library = (original, unrelated)
        original_hashes = tuple(skill_artifact_sha256(skill) for skill in library)
        v2 = copy.deepcopy(original)
        v2.version = 2
        v2.metadata["implementation"] = "ignores blank lines"
        v2.metadata["case_results"] = {"target-1": True, "held-1": True}
        v3 = copy.deepcopy(v2)
        v3.version = 3
        evaluator = MetadataEvaluator()
        reviser = FixedReviser([v2, v3])

        result = run_skill_repair(
            diagnosis=_diagnosis(library),
            library=library,
            target_cases=TARGET,
            held_out_cases=HELD_OUT,
            regression_cases=REGRESSION,
            evaluator=evaluator,
            reviser=reviser,
            verifier_profiles=_profiles(),
        )

        self.assertTrue(result.adopted)
        self.assertEqual(result.lifecycle_action, "adopt")
        self.assertEqual(result.selected_candidate_key, "count-lines@v2")
        self.assertEqual(result.selected_candidate_version, 2)
        self.assertEqual(reviser.visible_feedback_ids, ("target-1",))
        self.assertNotIn("held-1", reviser.visible_feedback_ids)
        repaired = next(skill for skill in result.resolved_library if skill.id == "count-lines")
        self.assertEqual(repaired.version, 2)
        self.assertEqual(repaired.status, LifecycleStatus.ACTIVE)
        self.assertIn("failed-trace-1", repaired.provenance_trace_ids)
        self.assertEqual(
            tuple(skill_artifact_sha256(skill) for skill in library), original_hashes
        )
        self.assertEqual(
            skill_artifact_sha256(
                next(skill for skill in result.resolved_library if skill.id == "unrelated")
            ),
            original_hashes[1],
        )
        self.assertTrue(all(gate.passed for gate in result.gates))
        self.assertFalse(result.to_dict()["evidence_boundary"]["held_out_visible_to_reviser"])

    def test_held_out_regression_rejects_candidate_and_rolls_back(self) -> None:
        original = _skill()
        library = (original,)
        v2 = copy.deepcopy(original)
        v2.version = 2
        v2.metadata["case_results"] = {"target-1": True, "held-1": False}

        result = run_skill_repair(
            diagnosis=_diagnosis(library),
            library=library,
            target_cases=TARGET,
            held_out_cases=HELD_OUT,
            regression_cases=REGRESSION,
            evaluator=MetadataEvaluator(),
            reviser=FixedReviser([v2]),
            verifier_profiles=_profiles(),
        )

        self.assertFalse(result.adopted)
        self.assertIsNone(result.provisional_library_snapshot_sha256)
        self.assertEqual(result.resolved_library[0].version, 1)
        self.assertFalse(
            next(gate for gate in result.gates if gate.name == "held_out_non_regression").passed
        )

    def test_library_regression_rejects_candidate_and_rolls_back(self) -> None:
        original = _skill()
        library = (original,)
        v2 = copy.deepcopy(original)
        v2.version = 2
        v2.metadata["case_results"] = {"target-1": True, "held-1": True}
        v2.metadata["library_results"] = {"regression-1": False}

        result = run_skill_repair(
            diagnosis=_diagnosis(library),
            library=library,
            target_cases=TARGET,
            held_out_cases=HELD_OUT,
            regression_cases=REGRESSION,
            evaluator=MetadataEvaluator(),
            reviser=FixedReviser([v2]),
            verifier_profiles=_profiles(),
        )

        self.assertFalse(result.adopted)
        self.assertEqual(result.resolved_library[0].version, 1)
        self.assertFalse(
            next(
                gate for gate in result.gates if gate.name == "library_regression_non_regression"
            ).passed
        )

    def test_route_local_failure_is_redirected_without_running_reviser(self) -> None:
        original = _skill()
        library = (original,)
        evaluator = MetadataEvaluator()
        reviser = FixedReviser([])

        result = run_skill_repair(
            diagnosis=_diagnosis(library, kind="route_local"),
            library=library,
            target_cases=TARGET,
            held_out_cases=HELD_OUT,
            regression_cases=REGRESSION,
            evaluator=evaluator,
            reviser=reviser,
            verifier_profiles=_profiles(),
        )

        self.assertFalse(result.adopted)
        self.assertEqual(result.recommended_action, "hide_or_update_provisioning")
        self.assertEqual(evaluator.skill_calls, [])
        self.assertEqual(reviser.visible_feedback_ids, ())

    def test_missing_verifier_feedback_cannot_enter_repair(self) -> None:
        original = _skill()
        library = (original,)
        diagnosis = _diagnosis(library)
        diagnosis = RepairDiagnosis(
            skill_id=diagnosis.skill_id,
            failure_kind=diagnosis.failure_kind,
            trace_ids=diagnosis.trace_ids,
            failed_target_case_ids=diagnosis.failed_target_case_ids,
            verifier_feedback=(),
            library_snapshot_sha256=diagnosis.library_snapshot_sha256,
        )

        result = run_skill_repair(
            diagnosis=diagnosis,
            library=library,
            target_cases=TARGET,
            held_out_cases=HELD_OUT,
            regression_cases=REGRESSION,
            evaluator=MetadataEvaluator(),
            reviser=FixedReviser([]),
            verifier_profiles=_profiles(),
        )

        self.assertFalse(result.adopted)
        self.assertEqual(result.recommended_action, "add_or_recover_verifier")

    def test_snapshot_drift_and_routing_contract_changes_are_rejected(self) -> None:
        original = _skill()
        library = (original,)
        drifted = _diagnosis(library)
        drifted = RepairDiagnosis(
            skill_id=drifted.skill_id,
            failure_kind=drifted.failure_kind,
            trace_ids=drifted.trace_ids,
            failed_target_case_ids=drifted.failed_target_case_ids,
            verifier_feedback=drifted.verifier_feedback,
            library_snapshot_sha256="0" * 64,
        )
        with self.assertRaisesRegex(SkillRepairError, "snapshot drifted"):
            run_skill_repair(
                diagnosis=drifted,
                library=library,
                target_cases=TARGET,
                held_out_cases=HELD_OUT,
                regression_cases=REGRESSION,
                evaluator=MetadataEvaluator(),
                reviser=FixedReviser([]),
                verifier_profiles=_profiles(),
            )

        v2 = copy.deepcopy(original)
        v2.version = 2
        v2.trigger = "Use for every task."
        with self.assertRaisesRegex(SkillRepairError, "changed routing"):
            run_skill_repair(
                diagnosis=_diagnosis(library),
                library=library,
                target_cases=TARGET,
                held_out_cases=HELD_OUT,
                regression_cases=REGRESSION,
                evaluator=MetadataEvaluator(),
                reviser=FixedReviser([v2]),
                verifier_profiles=_profiles(),
            )

    def test_structural_only_verifier_cannot_authorize_repair(self) -> None:
        original = _skill()
        library = (original,)
        v2 = copy.deepcopy(original)
        v2.version = 2
        profiles = _profiles()
        target_profile = profiles[TARGET[0].verifier_id]
        profiles[TARGET[0].verifier_id] = VerifierTrustProfile(
            verifier_id=target_profile.verifier_id,
            level=VerifierTrustLevel.STRUCTURAL,
            deterministic=True,
            requirement_ids=target_profile.requirement_ids,
            covered_requirement_ids=target_profile.covered_requirement_ids,
            behavioral_assertion_count=0,
            author_independent_from_candidate=True,
            hidden_from_reviser=True,
            provenance_sha256="b" * 64,
        )

        with self.assertRaisesRegex(SkillRepairError, "verifier trust gate failed"):
            run_skill_repair(
                diagnosis=_diagnosis(library),
                library=library,
                target_cases=TARGET,
                held_out_cases=HELD_OUT,
                regression_cases=REGRESSION,
                evaluator=MetadataEvaluator(),
                reviser=FixedReviser([v2]),
                verifier_profiles=profiles,
            )


if __name__ == "__main__":
    unittest.main()
