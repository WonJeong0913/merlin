from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path

from src.merlin_harness.personal_workload_campaign import (
    EXPECTED_FAMILY_COUNTS,
    MatchedWorkloadObservation,
    PERSONAL_WORKLOAD_50_TASKS,
    PERSONAL_WORKLOAD_MANIFEST_SHA256,
    PersonalWorkloadCampaignError,
    WorkloadArmEvidence,
    append_personal_workload_observation,
    personal_workload_manifest_payload,
    personal_workload_schedule_payload,
    run_personal_workload_campaign,
    validate_personal_workload_campaign,
)
from src.merlin_harness.skill_body_invocation import (
    HarnessInvocationSigner,
    create_skill_body_invocation_event,
)


TEST_SIGNER = HarnessInvocationSigner(
    signer_id="test-harness-v1",
    secret=b"test-only-harness-signing-secret-0001",
)


def arm(
    *,
    turns: int,
    skill: str = "",
    invocation_complete: bool = True,
    task_id: str = "pw-ke-01",
    task_contract_sha256: str = "d" * 64,
) -> WorkloadArmEvidence:
    skills = (skill,) if skill else ()
    model_request_sha256 = "c" * 64
    trace_sha256 = "a" * 64
    verifier_result_sha256 = "e" * 64
    events = (
        (
            create_skill_body_invocation_event(
                event_id=f"event-{task_id}-{skill}",
                task_id=task_id,
                task_contract_sha256=task_contract_sha256,
                selected_skill_id=skill,
                skill_body_sha256="f" * 64,
                model_request_sha256=model_request_sha256,
                execution_trace_sha256=trace_sha256,
                verifier_result_sha256=verifier_result_sha256,
                verifier_passed=True,
                harness_policy_sha256="1" * 64,
                signer=TEST_SIGNER,
            ),
        )
        if skill
        else ()
    )
    return WorkloadArmEvidence(
        success=True,
        verifier_passed=True,
        execution_turns=turns,
        trace_sha256=trace_sha256,
        output_sha256="b" * 64,
        model_request_sha256=model_request_sha256,
        verifier_result_sha256=verifier_result_sha256,
        actual_invocation_evidence_complete=invocation_complete,
        selected_skill_ids=skills,
        invoked_skill_ids=skills,
        invocation_events=events,
        total_tokens=turns * 100,
        latency_s=float(turns),
    )


def observation(
    task_index: int = 0,
    *,
    repetition: int = 1,
    observed_at_utc: str = "2026-07-24T12:00:00Z",
    lifecycle_action_ids: tuple[str, ...] = ("promotion-1",),
    lifecycle_action_kinds: tuple[str, ...] = ("promote",),
) -> MatchedWorkloadObservation:
    task = PERSONAL_WORKLOAD_50_TASKS[task_index]
    ordinal = task_index + 1
    arm_order = (
        ("baseline", "managed")
        if (ordinal + repetition) % 2 == 0
        else ("managed", "baseline")
    )
    return MatchedWorkloadObservation(
        observation_id=f"obs-{task.task_id}-r{repetition}",
        pair_id=f"{task.task_id}-r{repetition}",
        task_id=task.task_id,
        repetition=repetition,
        arm_order=arm_order,
        observed_at_utc=observed_at_utc,
        manifest_sha256=PERSONAL_WORKLOAD_MANIFEST_SHA256,
        task_contract_sha256=task.to_dict()["contract_sha256"],
        verifier_epoch_id="verifier-v1",
        quota_window_id="account-window-v1",
        provider_id="codex-account",
        model_id="gpt-5.6",
        effort="high",
        input_snapshot_sha256="c" * 64,
        baseline=arm(
            turns=5,
            task_id=task.task_id,
            task_contract_sha256=task.to_dict()["contract_sha256"],
        ),
        managed=arm(
            turns=2,
            skill="managed-skill",
            task_id=task.task_id,
            task_contract_sha256=task.to_dict()["contract_sha256"],
        ),
        governance_turns=1,
        governance_total_tokens=100,
        governance_latency_s=1.0,
        lifecycle_action_ids=lifecycle_action_ids,
        lifecycle_action_kinds=lifecycle_action_kinds,
        human_review_passed=True,
    )


class PersonalWorkloadManifestTests(unittest.TestCase):
    def test_manifest_contains_50_grounded_contracts(self) -> None:
        self.assertEqual(len(PERSONAL_WORKLOAD_50_TASKS), 50)
        self.assertEqual(
            Counter(task.family for task in PERSONAL_WORKLOAD_50_TASKS),
            EXPECTED_FAMILY_COUNTS,
        )
        self.assertEqual(
            len({task.task_id for task in PERSONAL_WORKLOAD_50_TASKS}),
            50,
        )
        manifest = personal_workload_manifest_payload()
        self.assertEqual(
            manifest["evidence_boundary"]["task_executions_completed_at_freeze"],
            0,
        )
        self.assertFalse(
            manifest["conditions"]["low_cost_model_comparison_included"]
        )

    def test_schedule_is_balanced_and_contains_100_pairs(self) -> None:
        schedule = personal_workload_schedule_payload()
        self.assertEqual(schedule["pair_count"], 100)
        rows = schedule["pairs"]
        self.assertEqual(len({row["pair_id"] for row in rows}), 100)
        for task in PERSONAL_WORKLOAD_50_TASKS:
            task_rows = [row for row in rows if row["task_id"] == task.task_id]
            self.assertEqual(len(task_rows), 2)
            self.assertNotEqual(
                task_rows[0]["arm_order"],
                task_rows[1]["arm_order"],
            )

    def test_new_campaign_is_valid_and_has_no_fabricated_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            summary = run_personal_workload_campaign(output)
            validation = validate_personal_workload_campaign(output)
            self.assertTrue(validation["valid"])
            self.assertEqual(summary["matched_observation_count"], 0)
            self.assertIsNone(summary["g_over_s"])
            self.assertEqual(
                summary["g_over_s_status"],
                "unavailable-no-verified-direct-savings",
            )
            self.assertEqual(
                (output / "observations.jsonl").read_text(encoding="utf-8"),
                "",
            )


class PersonalWorkloadLedgerTests(unittest.TestCase):
    def test_managed_skill_requires_a_trusted_signed_invocation_event(self) -> None:
        item = observation()
        with self.assertRaisesRegex(
            PersonalWorkloadCampaignError, "one event per invoked skill"
        ):
            replace(item.managed, invocation_events=())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            with self.assertRaisesRegex(
                PersonalWorkloadCampaignError, "trusted harness signer"
            ):
                append_personal_workload_observation(output, item)

    def test_valid_pair_is_appended_and_accounted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            summary = append_personal_workload_observation(
                output,
                observation(),
                invocation_signer=TEST_SIGNER,
            )
            self.assertEqual(summary["matched_observation_count"], 1)
            self.assertEqual(summary["verified_turn_savings"], 3)
            self.assertEqual(summary["governance_turns_spent"], 1)
            self.assertAlmostEqual(summary["g_over_s"], 1 / 3)
            self.assertTrue(
                validate_personal_workload_campaign(
                    output, invocation_signer=TEST_SIGNER
                )["valid"]
            )

    def test_duplicate_pair_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            item = observation()
            append_personal_workload_observation(
                output, item, invocation_signer=TEST_SIGNER
            )
            with self.assertRaisesRegex(
                PersonalWorkloadCampaignError, "duplicate"
            ):
                append_personal_workload_observation(
                    output, item, invocation_signer=TEST_SIGNER
                )

    def test_scheduled_arm_order_drift_is_rejected(self) -> None:
        item = observation()
        wrong_order = MatchedWorkloadObservation(
            **{
                **item.to_dict(),
                "baseline": item.baseline,
                "managed": item.managed,
                "arm_order": tuple(reversed(item.arm_order)),
                "lifecycle_action_ids": item.lifecycle_action_ids,
                "lifecycle_action_kinds": item.lifecycle_action_kinds,
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            with self.assertRaisesRegex(
                PersonalWorkloadCampaignError, "scheduled pair"
            ):
                append_personal_workload_observation(
                    output, wrong_order, invocation_signer=TEST_SIGNER
                )

    def test_missing_human_review_or_invocation_evidence_is_rejected(self) -> None:
        item = observation()
        no_review = MatchedWorkloadObservation(
            **{
                **item.to_dict(),
                "baseline": item.baseline,
                "managed": item.managed,
                "arm_order": item.arm_order,
                "lifecycle_action_ids": item.lifecycle_action_ids,
                "lifecycle_action_kinds": item.lifecycle_action_kinds,
                "human_review_passed": None,
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            with self.assertRaisesRegex(
                PersonalWorkloadCampaignError, "human review"
            ):
                append_personal_workload_observation(
                    output, no_review, invocation_signer=TEST_SIGNER
                )

        incomplete_invocation = MatchedWorkloadObservation(
            **{
                **item.to_dict(),
                "baseline": arm(
                    turns=5,
                    invocation_complete=False,
                    task_id=item.task_id,
                    task_contract_sha256=item.task_contract_sha256,
                ),
                "managed": item.managed,
                "arm_order": item.arm_order,
                "lifecycle_action_ids": item.lifecycle_action_ids,
                "lifecycle_action_kinds": item.lifecycle_action_kinds,
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            with self.assertRaisesRegex(
                PersonalWorkloadCampaignError, "actual invocation"
            ):
                append_personal_workload_observation(
                    output,
                    incomplete_invocation,
                    invocation_signer=TEST_SIGNER,
                )

    def test_level_7_is_derived_only_after_full_longitudinal_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            summary = {}
            for repetition in (1, 2):
                for task_index in range(50):
                    global_index = (repetition - 1) * 50 + task_index
                    if global_index == 0:
                        action_ids = ("field-promotion-1",)
                        action_kinds = ("promote",)
                    elif global_index == 1:
                        action_ids = ("field-rollback-1",)
                        action_kinds = ("rollback",)
                    elif global_index < 10:
                        action_ids = (f"field-repair-{global_index}",)
                        action_kinds = ("repair",)
                    else:
                        action_ids = ()
                        action_kinds = ()
                    summary = append_personal_workload_observation(
                        output,
                        observation(
                            task_index,
                            repetition=repetition,
                            observed_at_utc=(
                                "2026-07-24T12:00:00Z"
                                if global_index == 0
                                else "2026-08-08T12:00:00Z"
                            ),
                            lifecycle_action_ids=action_ids,
                            lifecycle_action_kinds=action_kinds,
                        ),
                        invocation_signer=TEST_SIGNER,
                    )
            self.assertEqual(summary["matched_observation_count"], 100)
            self.assertEqual(summary["unique_task_count_completed"], 50)
            self.assertEqual(summary["observed_elapsed_days"], 15.0)
            self.assertTrue(summary["level_7_achieved"])
            self.assertEqual(summary["unmet_level_7_checks"], [])

    def test_tampered_ledger_and_manifest_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            append_personal_workload_observation(
                output, observation(), invocation_signer=TEST_SIGNER
            )
            ledger_path = output / "observations.jsonl"
            envelope = json.loads(ledger_path.read_text(encoding="utf-8"))
            envelope["observation"]["governance_turns"] = 99
            ledger_path.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                PersonalWorkloadCampaignError, "hash drift"
            ):
                validate_personal_workload_campaign(
                    output, invocation_signer=TEST_SIGNER
                )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            run_personal_workload_campaign(output)
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["evidence_boundary"]["task_executions_completed_at_freeze"] = 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                PersonalWorkloadCampaignError, "validation failed"
            ):
                validate_personal_workload_campaign(output)


if __name__ == "__main__":
    unittest.main()
