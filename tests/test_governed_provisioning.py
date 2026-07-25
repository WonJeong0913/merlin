from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.evaluate_provisioning import evaluate_fixed_sample
from src.merlin_harness.governed_provisioning import GovernedProvisioner
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep
from src.merlin_harness.task_io import load_tasks


REPO_ROOT = Path(__file__).resolve().parents[1]


def skill(
    skill_id: str,
    *,
    trigger: str = "create file artifact",
    description: str = "create a named file artifact",
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
    artifacts: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
    status: LifecycleStatus = LifecycleStatus.ACTIVE,
) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name=skill_id,
        description=description,
        trigger=trigger,
        do_not_use_when=list(blocked),
        steps=[
            SkillStep(
                id="step-1",
                description="bounded deterministic step",
                inputs=list(inputs),
                outputs=list(outputs),
            )
        ],
        expected_artifacts=list(artifacts),
        status=status,
    )


class GovernedProvisionerTests(unittest.TestCase):
    def test_exact_artifact_and_input_anchors_restrict_candidate_pool(self) -> None:
        provisioner = GovernedProvisioner(exposure_budget=3)
        skills = [
            skill(
                "line-summary",
                trigger="read input and create summary count",
                description="read input file and create summary file",
                inputs=("input.txt",),
                artifacts=("summary.txt",),
            ),
            skill(
                "plausible-wrong",
                trigger="read input and create summary count",
                description="read input file and create summary file",
            ),
        ]

        decision = provisioner.decide("Read input.txt and create summary.txt", skills)

        self.assertEqual(decision.provisioned_ids, ("line-summary",))
        self.assertEqual(decision.explicit_input_anchors, ("input.txt",))
        self.assertEqual(decision.explicit_artifact_anchors, ("summary.txt",))
        self.assertIn(
            "not_in_exact_anchor_pool",
            decision.candidate("plausible-wrong").exclusion_reasons,
        )

    def test_exact_artifact_anchor_is_language_independent_positive_evidence(self) -> None:
        candidate = skill(
            "file-artifact-basic",
            trigger="create a named file in the workspace",
            description="write the requested artifact",
            artifacts=("result.txt",),
        )

        decision = GovernedProvisioner().decide("result.txt 만들어줘", [candidate])

        record = decision.candidate("file-artifact-basic")
        self.assertEqual(record.positive_score, 0.0)
        self.assertTrue(record.exact_anchor_evidence)
        self.assertTrue(record.eligible)
        self.assertEqual(decision.provisioned_ids, ("file-artifact-basic",))

    def test_negative_guard_excludes_even_with_positive_evidence(self) -> None:
        candidate = skill(
            "blocked",
            trigger="create unrelated file artifact",
            description="create unrelated file artifact",
            blocked=("create unrelated file artifact",),
        )

        decision = GovernedProvisioner().decide("create unrelated file artifact", [candidate])

        record = decision.candidate("blocked")
        self.assertGreaterEqual(record.positive_score, 0.5)
        self.assertGreaterEqual(record.negative_score, 0.5)
        self.assertFalse(record.eligible)
        self.assertIn("do_not_use_guard_at_0.500", record.exclusion_reasons)
        self.assertEqual(decision.abstain_reason, "no_candidate_met_minimum_evidence")

    def test_hidden_skill_is_recorded_but_never_eligible(self) -> None:
        hidden = skill(
            "hidden",
            trigger="write report",
            description="write report",
            status=LifecycleStatus.HIDDEN,
        )

        decision = GovernedProvisioner().decide("write report", [hidden])

        self.assertEqual(decision.active_library_size, 0)
        self.assertEqual(decision.provisioned_ids, ())
        self.assertIn(
            "lifecycle_status_not_active",
            decision.candidate("hidden").exclusion_reasons,
        )
        self.assertEqual(decision.abstain_reason, "no_active_skills")

    def test_low_evidence_abstains(self) -> None:
        decision = GovernedProvisioner().decide(
            "Return exactly yes",
            [skill("file", trigger="create file", description="write workspace artifact")],
        )

        self.assertEqual(decision.ranked_ids, ())
        self.assertEqual(decision.provisioned_ids, ())
        self.assertEqual(decision.abstain_reason, "no_candidate_met_minimum_evidence")

    def test_ties_and_snapshot_are_deterministic(self) -> None:
        first = skill("a-skill", trigger="alpha beta", description="alpha beta")
        second = skill("b-skill", trigger="alpha beta", description="alpha beta")
        provisioner = GovernedProvisioner(exposure_budget=2)

        left = provisioner.decide("alpha beta", [second, first])
        right = provisioner.decide("alpha beta", [first, second])

        self.assertEqual(left.ranked_ids, ("a-skill", "b-skill"))
        self.assertEqual(left.to_safe_dict(), right.to_safe_dict())

    def test_same_declared_name_variant_is_suppressed_before_ranking(self) -> None:
        canonical = skill("docx", trigger="write offer letter", description="write docx")
        canonical.name = "docx"
        competing = skill(
            "docx@d3cfe519dca2",
            trigger="write offer letter",
            description="write docx",
        )
        competing.name = "docx"

        decision = GovernedProvisioner(exposure_budget=2).decide(
            "write offer letter docx", [competing, canonical]
        )

        self.assertEqual(decision.ranked_ids, ("docx",))
        self.assertEqual(decision.provisioned_ids, ("docx",))
        self.assertIn(
            "declared_name_collision_suppressed:docx",
            decision.candidate("docx@d3cfe519dca2").exclusion_reasons,
        )
        governance = decision.to_safe_dict()["name_collision_governance"]
        self.assertEqual(governance["collision_group_count"], 1)
        self.assertEqual(governance["suppressed_variant_count"], 1)
        self.assertFalse(governance["boundary"]["merge_or_retire_authorized"])

    def test_safe_payload_has_hashes_not_raw_query_or_skill_body(self) -> None:
        query = "create secret-output.txt opaque-user-token-9173"
        candidate = skill(
            "safe-id",
            trigger="create secret output",
            description="private-skill-body-token-4481",
            artifacts=("secret-output.txt",),
        )

        payload = GovernedProvisioner().decide(query, [candidate]).to_safe_dict()
        rendered = json.dumps(payload, sort_keys=True)

        self.assertNotIn(query, rendered)
        self.assertNotIn("secret-output.txt", rendered)
        self.assertNotIn("opaque-user-token-9173", rendered)
        self.assertNotIn("private-skill-body-token-4481", rendered)
        self.assertEqual(payload["query_chars"], len(query))
        self.assertEqual(len(payload["query_sha256"]), 64)
        self.assertEqual(payload["explicit_filename_anchor_count"], 1)
        self.assertEqual(
            len(payload["explicit_filename_anchor_evidence"][0]["sha256"]), 64
        )
        self.assertFalse(payload["query_stored"])
        self.assertIsNone(payload["boundary"]["provider_native_loaded_skill_ids"])
        self.assertFalse(payload["boundary"]["actual_invocation_evidence_complete"])
        self.assertEqual(
            payload["candidates"][0]["skillops_contract_fields_present"],
            ["P", "O", "A"],
        )
        self.assertEqual(payload["candidates"][0]["aip_declared_step_count"], 1)
        self.assertIsNone(payload["research_contract"]["skillsbench_normalized_gain"])
        self.assertFalse(
            payload["research_contract"]["more_skills_invoked_evidence_available"]
        )

    def test_fixed_ten_task_evaluator_meets_controlled_acceptance(self) -> None:
        tasks = load_tasks(REPO_ROOT / "experiments" / "mvp" / "tasks")
        skills = (
            FileSkillLibrary(REPO_ROOT / "experiments" / "mvp" / "skills").list()
            + FileSkillLibrary(REPO_ROOT / "experiments" / "mvp" / "distractors").list()
        )

        report = evaluate_fixed_sample(tasks=tasks, skills=skills)

        self.assertTrue(report["same_library_snapshot_for_both_policies"])
        self.assertTrue(report["acceptance_passed"])
        self.assertEqual(report["governed"]["clean_oracle_only_count"], 9)
        self.assertEqual(report["governed"]["control_abstain_count"], 1)
        self.assertEqual(report["governed"]["mixed_exposure_count"], 0)
        self.assertEqual(report["governed"]["distractor_exposure_count"], 0)
        self.assertEqual(report["naive_lexical"]["distractor_exposure_count"], 9)
        self.assertFalse(report["headline_claim_allowed"])
        self.assertIsNone(report["research_contract"]["skillsbench"]["normalized_gain"])
        self.assertEqual(
            report["research_contract"]["more_skills"]["shadowing_proxy_scope"],
            "mixed/distractor prompt exposure only",
        )


if __name__ == "__main__":
    unittest.main()
