from __future__ import annotations

from dataclasses import replace
import unittest

from src.merlin_harness.chat_campaign import (
    ChatCampaignError,
    ChatCampaignPromotionCriteria,
    ChatCampaignTurnEvidence,
    ChatLifecycleCampaign,
    evaluate_chat_campaign_promotion,
)
from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep, TaskSpec, VerifierSpec


def _skill(skill_id: str, *, status: LifecycleStatus = LifecycleStatus.ACTIVE) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name=skill_id,
        description=skill_id,
        trigger=skill_id,
        steps=[SkillStep(id=f"{skill_id}-step", description="bounded")],
        status=status,
    )


def _task(task_id: str, *, oracle: list[str]) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        instruction=f"complete {task_id}",
        verifier=VerifierSpec(name=f"verify-{task_id}", kind="exact_match", expected="OK"),
        oracle_skill_ids=oracle,
    )


class DeterministicExposureExecutor:
    def __init__(self, *, recover: bool = True, fail_oracle_only: bool = False) -> None:
        self.recover = recover
        self.fail_oracle_only = fail_oracle_only

    def run_turn(self, *, task, skills, arm, ordinal):
        active = {skill.id for skill in skills if skill.status == LifecycleStatus.ACTIVE}
        if not task.oracle_skill_ids:
            exposure = ()
            passed = True
        elif self.fail_oracle_only:
            exposure = tuple(task.oracle_skill_ids)
            passed = False
        elif arm == "baseline":
            exposure = ("distractor",)
            passed = False
        elif not self.recover:
            # The staged distractor is no longer active, so a failed turn can
            # remain empty without falsely re-exposing a hidden skill.
            exposure = ()
            passed = False
        else:
            exposure = tuple(task.oracle_skill_ids) if "distractor" not in active else ("distractor",)
            passed = exposure == tuple(task.oracle_skill_ids)
        return ChatCampaignTurnEvidence(
            task_id=task.id,
            verifier_id=task.verifier.name,
            verifier_passed=passed,
            exposure_skill_ids=exposure,
            oracle_skill_ids=tuple(task.oracle_skill_ids),
            raw_trace_pointer=f"{arm}-{ordinal}.jsonl",
            raw_trace_sha256="a" * 64,
        )


class InvalidEvidenceExecutor:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def run_turn(self, *, task, skills, arm, ordinal):
        evidence = ChatCampaignTurnEvidence(
            task_id=task.id,
            verifier_id=task.verifier.name,
            verifier_passed=True,
            exposure_skill_ids=("good",),
            oracle_skill_ids=tuple(task.oracle_skill_ids),
            raw_trace_pointer=f"{arm}-{ordinal}.jsonl",
            raw_trace_sha256="b" * 64,
        )
        if self.kind == "unknown":
            return replace(evidence, exposure_skill_ids=("unknown",))
        if self.kind == "budget":
            return replace(evidence, exposure_skill_ids=("good", "distractor"))
        if self.kind == "oracle":
            return replace(evidence, oracle_skill_ids=("distractor",))
        if self.kind == "raw":
            return replace(evidence, raw_trace_sha256="not-a-hash")
        if self.kind == "actual":
            return replace(evidence, actual_invocation_evidence_complete=True)
        if self.kind == "task":
            return replace(evidence, task_id="other-task")
        raise AssertionError(self.kind)


class ChatLifecycleCampaignTests(unittest.TestCase):
    def _campaign(self, executor, *, tasks=None, budget=1, criteria=None):
        return ChatLifecycleCampaign(
            tasks=tasks or (
                _task("task-a", oracle=["good"]),
                _task("task-b", oracle=["good"]),
                _task("task-no-oracle", oracle=[]),
            ),
            library_snapshot=(_skill("good"), _skill("distractor")),
            executor=executor,
            exposure_budget=budget,
            promotion_criteria=criteria or ChatCampaignPromotionCriteria(),
        )

    def test_successful_recovery_is_route_local_and_copy_on_write(self) -> None:
        campaign = self._campaign(DeterministicExposureExecutor(recover=True))
        baseline = campaign.run_baseline()
        self.assertEqual(baseline.route_counts["wrong"], 2)
        self.assertEqual(baseline.route_counts["no_oracle_empty"], 1)
        self.assertEqual(baseline.exposure_shadowing_rate, 1.0)
        decisions = campaign.diagnose_route_local(min_route_risk_events=2)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].skill_id, "distractor")
        self.assertEqual(decisions[0].action.value, "hide")
        self.assertEqual(len(decisions[0].evidence_trace_ids), 2)

        staged = campaign.stage_copy_on_write()
        self.assertEqual(
            {skill.id: skill.status.value for skill in campaign.library_snapshot}["distractor"],
            "active",
        )
        self.assertEqual({skill.id: skill.status.value for skill in staged}["distractor"], "hidden")
        result = campaign.run_provisional_and_promote()

        self.assertTrue(result.accepted)
        self.assertEqual(result.library_resolution, "provisional_promoted")
        self.assertEqual(result.provisional.exposure_shadowing_rate, 0.0)
        self.assertEqual({skill.id: skill.status.value for skill in campaign.resolved_library()}["distractor"], "hidden")
        self.assertEqual(
            [check.name for check in result.checks],
            [
                "same_task_coverage",
                "same_verifier_contract",
                "pass_rate_non_regression",
                "clean_oracle_exposure_non_regression",
                "exposure_shadowing_reduction",
            ],
        )

    def test_rollback_retains_original_library_when_provisional_does_not_recover(self) -> None:
        campaign = self._campaign(
            DeterministicExposureExecutor(recover=False),
            criteria=ChatCampaignPromotionCriteria(min_pass_rate_delta=0.01),
        )
        campaign.run_baseline()
        campaign.diagnose_route_local(min_route_risk_events=2)
        campaign.stage_copy_on_write()
        result = campaign.run_provisional_and_promote()

        self.assertFalse(result.accepted)
        self.assertTrue(result.rollback_required)
        self.assertEqual(result.library_resolution, "original_retained")
        self.assertEqual({skill.id: skill.status.value for skill in campaign.resolved_library()}["distractor"], "active")

    def test_contract_mismatch_is_rejected_by_promotion_gate(self) -> None:
        campaign = self._campaign(DeterministicExposureExecutor())
        baseline = campaign.run_baseline()
        mismatched = replace(baseline, ordered_task_ids=tuple(reversed(baseline.ordered_task_ids)))
        result = evaluate_chat_campaign_promotion(baseline, mismatched)

        self.assertFalse(result.accepted)
        self.assertIn("same_task_coverage", result.reason)
        self.assertFalse(result.checks[0].passed)

    def test_failed_oracle_only_outcome_does_not_blame_skill_content(self) -> None:
        campaign = self._campaign(DeterministicExposureExecutor(fail_oracle_only=True))
        baseline = campaign.run_baseline()
        self.assertEqual(baseline.passed, 1)
        self.assertEqual(baseline.route_counts["oracle_only"], 2)
        self.assertEqual(campaign.diagnose_route_local(min_route_risk_events=2), ())

    def test_exposure_summary_has_no_invocation_metric_contamination(self) -> None:
        campaign = self._campaign(DeterministicExposureExecutor())
        payload = campaign.run_baseline().to_dict()
        rendered = repr(payload).lower()
        self.assertNotIn("pi_o", rendered)
        self.assertNotIn("pi_m", rendered)
        self.assertNotIn("invocation_count", rendered)
        self.assertNotIn("selected_skill", rendered)
        self.assertIn("exposure_skill_ids", rendered)

    def test_invalid_campaign_evidence_fails_closed(self) -> None:
        for kind in ("unknown", "budget", "oracle", "raw", "actual", "task"):
            with self.subTest(kind=kind):
                campaign = self._campaign(InvalidEvidenceExecutor(kind))
                with self.assertRaises(ChatCampaignError):
                    campaign.run_baseline()

    def test_duplicate_or_missing_task_contract_is_rejected(self) -> None:
        duplicate = (_task("same", oracle=["good"]), _task("same", oracle=["good"]))
        with self.assertRaisesRegex(ChatCampaignError, "duplicate task IDs"):
            self._campaign(DeterministicExposureExecutor(), tasks=duplicate)

        campaign = self._campaign(InvalidEvidenceExecutor("task"))
        with self.assertRaisesRegex(ChatCampaignError, "returned task_id"):
            campaign.run_baseline()

    def test_criteria_and_campaign_state_require_valid_transitions(self) -> None:
        with self.assertRaises(ChatCampaignError):
            ChatCampaignPromotionCriteria(min_exposure_shadowing_reduction=-1)
        campaign = self._campaign(DeterministicExposureExecutor())
        with self.assertRaisesRegex(ChatCampaignError, "run baseline"):
            campaign.stage_copy_on_write()
        campaign.run_baseline()
        with self.assertRaisesRegex(ChatCampaignError, "stage copy-on-write"):
            campaign.run_provisional_and_promote()


if __name__ == "__main__":
    unittest.main()
