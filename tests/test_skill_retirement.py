from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep
from src.merlin_harness.skill_repair import skill_library_snapshot_sha256
from src.merlin_harness.skill_retirement import (
    RetirementCase,
    RetirementCaseResult,
    RetirementObservationWindow,
    SkillRetirementError,
    run_skill_retirement,
)
from src.merlin_harness.verifier_trust import VerifierTrustLevel, VerifierTrustProfile


TARGET_ID = "legacy-report-writer"


def skill(skill_id: str, status: LifecycleStatus) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name=skill_id.replace("-", " ").title(),
        description=f"Portable behavior for {skill_id}",
        trigger=f"trigger {skill_id}",
        steps=[SkillStep(id="run", description="execute deterministic behavior")],
        validators=["protected-library-v1"],
        provenance_trace_ids=["fixture-trace"],
        status=status,
        metadata={"fixture": True},
    )


CASES = (
    RetirementCase("protected-report", "protected-report-v1"),
    RetirementCase("protected-summary", "protected-summary-v1"),
)


def profiles() -> dict[str, VerifierTrustProfile]:
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
            provenance_sha256=("a" if index == 1 else "b") * 64,
        )
        for index, case in enumerate(CASES, start=1)
    }


def windows(
    library: tuple[SkillArtifact, ...],
    *,
    complete: bool = True,
) -> tuple[RetirementObservationWindow, ...]:
    snapshot = skill_library_snapshot_sha256(library)
    return tuple(
        RetirementObservationWindow(
            window_id=f"window-{index}",
            library_snapshot_sha256=snapshot,
            raw_trace_sha256=str(index) * 64,
            case_ids=tuple(case.case_id for case in CASES),
            verifier_ids=tuple(case.verifier_id for case in CASES),
            passed_case_ids=tuple(case.case_id for case in CASES),
            target_selected_count=0,
            target_invocation_count=0,
            actual_invocation_evidence_complete=complete,
        )
        for index in (1, 2)
    )


class Evaluator:
    def __init__(self, *, regress_after_retire: bool = False) -> None:
        self.regress_after_retire = regress_after_retire

    def evaluate_library(self, skills, cases):
        target = next(item for item in skills if item.id == TARGET_ID)
        return tuple(
            RetirementCaseResult(
                case_id=case.case_id,
                verifier_id=case.verifier_id,
                passed=not (
                    self.regress_after_retire
                    and target.status == LifecycleStatus.RETIRED
                    and case.case_id == "protected-summary"
                ),
                score=(
                    0.0
                    if self.regress_after_retire
                    and target.status == LifecycleStatus.RETIRED
                    and case.case_id == "protected-summary"
                    else 1.0
                ),
                evidence="deterministic fixture verifier",
            )
            for case in cases
        )


class SkillRetirementTests(unittest.TestCase):
    def make_library(self) -> tuple[SkillArtifact, ...]:
        return (
            skill(TARGET_ID, LifecycleStatus.HIDDEN),
            skill("active-summary-writer", LifecycleStatus.ACTIVE),
        )

    def test_hidden_unused_skill_retires_copy_on_write_after_same_verifiers(self) -> None:
        library = self.make_library()
        before = copy.deepcopy(library)
        result = run_skill_retirement(
            skill_id=TARGET_ID,
            library=library,
            observation_windows=windows(library),
            regression_cases=CASES,
            evaluator=Evaluator(),
            verifier_profiles=profiles(),
        )

        self.assertTrue(result.retired)
        self.assertEqual(result.lifecycle_action, "retire")
        self.assertEqual(len(result.gates), 7)
        self.assertTrue(all(gate.passed for gate in result.gates))
        retired = next(item for item in result.resolved_library if item.id == TARGET_ID)
        self.assertEqual(retired.status, LifecycleStatus.RETIRED)
        self.assertEqual(library[0].status, LifecycleStatus.HIDDEN)
        self.assertEqual(
            [item.to_dict() for item in library],
            [item.to_dict() for item in before],
        )
        payload = result.to_dict()
        self.assertFalse(payload["evidence_boundary"]["physical_artifact_deletion"])
        self.assertTrue(payload["evidence_boundary"]["copy_on_write"])

    def test_same_verifier_regression_rolls_back_to_hidden_parent(self) -> None:
        library = self.make_library()
        result = run_skill_retirement(
            skill_id=TARGET_ID,
            library=library,
            observation_windows=windows(library),
            regression_cases=CASES,
            evaluator=Evaluator(regress_after_retire=True),
            verifier_profiles=profiles(),
        )

        self.assertFalse(result.retired)
        self.assertEqual(result.lifecycle_action, "hide")
        self.assertIsNone(result.provisional_library_snapshot_sha256)
        self.assertEqual(
            next(item for item in result.resolved_library if item.id == TARGET_ID).status,
            LifecycleStatus.HIDDEN,
        )
        gates = {gate.name: gate.passed for gate in result.gates}
        self.assertFalse(gates["same_verifier_non_regression"])
        self.assertTrue(gates["copy_on_write_isolation"])

    def test_incomplete_invocation_evidence_retains_hidden_skill(self) -> None:
        library = self.make_library()
        result = run_skill_retirement(
            skill_id=TARGET_ID,
            library=library,
            observation_windows=windows(library, complete=False),
            regression_cases=CASES,
            evaluator=Evaluator(),
            verifier_profiles=profiles(),
        )

        self.assertFalse(result.retired)
        gates = {gate.name: gate.passed for gate in result.gates}
        self.assertFalse(gates["complete_zero_use_evidence"])

    def test_snapshot_trace_and_verifier_tamper_fail_closed(self) -> None:
        library = self.make_library()
        valid = windows(library)
        drifted = (
            replace(valid[0], library_snapshot_sha256="f" * 64),
            valid[1],
        )
        with self.assertRaisesRegex(SkillRetirementError, "snapshot drifted"):
            run_skill_retirement(
                skill_id=TARGET_ID,
                library=library,
                observation_windows=drifted,
                regression_cases=CASES,
                evaluator=Evaluator(),
                verifier_profiles=profiles(),
            )

        duplicate_trace = (valid[0], RetirementObservationWindow(
            window_id="window-other",
            library_snapshot_sha256=valid[1].library_snapshot_sha256,
            raw_trace_sha256=valid[0].raw_trace_sha256,
            case_ids=valid[1].case_ids,
            verifier_ids=valid[1].verifier_ids,
            passed_case_ids=valid[1].passed_case_ids,
            target_selected_count=0,
            target_invocation_count=0,
            actual_invocation_evidence_complete=True,
        ))
        with self.assertRaisesRegex(SkillRetirementError, "distinct raw traces"):
            run_skill_retirement(
                skill_id=TARGET_ID,
                library=library,
                observation_windows=duplicate_trace,
                regression_cases=CASES,
                evaluator=Evaluator(),
                verifier_profiles=profiles(),
            )

        bad_profiles = profiles()
        profile = bad_profiles[CASES[0].verifier_id]
        bad_profiles[CASES[0].verifier_id] = VerifierTrustProfile(
            verifier_id=profile.verifier_id,
            level=VerifierTrustLevel.STRUCTURAL,
            deterministic=True,
            requirement_ids=profile.requirement_ids,
            covered_requirement_ids=profile.covered_requirement_ids,
            behavioral_assertion_count=0,
            author_independent_from_candidate=True,
            hidden_from_reviser=True,
            provenance_sha256=profile.provenance_sha256,
        )
        with self.assertRaisesRegex(SkillRetirementError, "trust gate failed"):
            run_skill_retirement(
                skill_id=TARGET_ID,
                library=library,
                observation_windows=valid,
                regression_cases=CASES,
                evaluator=Evaluator(),
                verifier_profiles=bad_profiles,
            )

    def test_active_or_retired_skill_cannot_skip_hidden_observation_stage(self) -> None:
        for status in (LifecycleStatus.ACTIVE, LifecycleStatus.RETIRED):
            library = (
                skill(TARGET_ID, status),
                skill("active-summary-writer", LifecycleStatus.ACTIVE),
            )
            with self.subTest(status=status):
                with self.assertRaisesRegex(SkillRetirementError, "already-hidden"):
                    run_skill_retirement(
                        skill_id=TARGET_ID,
                        library=library,
                        observation_windows=windows(library),
                        regression_cases=CASES,
                        evaluator=Evaluator(),
                        verifier_profiles=profiles(),
                    )


if __name__ == "__main__":
    unittest.main()
