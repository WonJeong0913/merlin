from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.harnessx_aegis import (
    DEFAULT_AEGIS_ACTION_SPACE,
    ScriptedAegisStageAgent,
    default_scripted_aegis_responses,
)
from src.merlin_harness.harnessx_live_hook import (
    run_pre_tool_use,
    write_new_live_tool_policy,
)
from src.merlin_harness.harnessx_trace_ingestion import ingest_live_hook_audit
from src.merlin_harness.harnessx_verifier_suites import (
    FROZEN_50_TOOL_POLICY_VERIFIER_SUITE,
)
from src.merlin_harness.self_managing_controller import (
    ManagedAction,
    SelfManagingHarnessController,
    decide_skill_action,
    decide_trace_action,
    run_trace_triggered_aegis_round,
    signal_from_repair_diagnosis,
    signal_from_retirement_windows,
)
from src.merlin_harness.skill_repair import RepairDiagnosis
from src.merlin_harness.skill_retirement import RetirementObservationWindow


def _hook_payload(command: str) -> dict:
    return {
        "session_id": "session",
        "turn_id": "turn",
        "cwd": "/private/tmp/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": f"use-{command}",
        "tool_input": {"command": command},
        "permission_mode": "default",
    }


class SelfManagingControllerTests(unittest.TestCase):
    def test_live_trace_nominates_and_runs_same_suite_aegis(self) -> None:
        suite = FROZEN_50_TOOL_POLICY_VERIFIER_SUITE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = write_new_live_tool_policy(
                root / "policy.json",
                policy_id="parent",
                allowed_commands=("pwd", "/bin/pwd"),
            )
            audit = root / "audit.jsonl"
            run_pre_tool_use(
                _hook_payload("ls -1"),
                policy=policy,
                audit_path=audit,
            )
            ingestion = ingest_live_hook_audit(audit, verifier_suite=suite)
            report = run_trace_triggered_aegis_round(
                output_dir=root / "triggered",
                ingestion=ingestion,
                parent_variant=policy.variant,
                verifier_suite=suite,
                action_space=DEFAULT_AEGIS_ACTION_SPACE,
                stage_agent=ScriptedAegisStageAgent(
                    default_scripted_aegis_responses()
                ),
            )

            self.assertTrue(report["round_promoted"])
            self.assertTrue(report["round_replay_valid"])
            self.assertEqual(report["actionable_case_ids"], ["directory-list-read"])
            self.assertTrue((root / "triggered" / "trace-ingestion.json").is_file())

    def test_skill_local_and_route_local_signals_use_different_lanes(self) -> None:
        base = dict(
            skill_id="skill-a",
            trace_ids=("trace-1",),
            failed_target_case_ids=("case-1",),
            verifier_feedback=("failed",),
            library_snapshot_sha256="a" * 64,
        )
        skill_signal = signal_from_repair_diagnosis(
            RepairDiagnosis(failure_kind="skill_local", **base),
            verifier_trusted=True,
            actual_invocation_evidence_complete=True,
        )
        route_signal = signal_from_repair_diagnosis(
            RepairDiagnosis(failure_kind="route_local", **base),
            verifier_trusted=True,
            actual_invocation_evidence_complete=True,
        )

        self.assertEqual(decide_skill_action(skill_signal).action, ManagedAction.SKILL_REPAIR)
        self.assertEqual(
            decide_skill_action(route_signal).action,
            ManagedAction.PROVISIONING_REPAIR,
        )

    def test_retirement_requires_hidden_state_two_windows_and_complete_invocation(self) -> None:
        windows = tuple(
            RetirementObservationWindow(
                window_id=f"window-{index}",
                library_snapshot_sha256="b" * 64,
                raw_trace_sha256=str(index) * 64,
                case_ids=("case",),
                verifier_ids=("verifier",),
                passed_case_ids=("case",),
                target_selected_count=0,
                target_invocation_count=0,
                actual_invocation_evidence_complete=True,
            )
            for index in (1, 2)
        )
        eligible = signal_from_retirement_windows(
            skill_id="skill-a",
            already_hidden=True,
            windows=windows,
            verifier_trusted=True,
        )
        blocked = signal_from_retirement_windows(
            skill_id="skill-a",
            already_hidden=False,
            windows=windows[:1],
            verifier_trusted=True,
        )

        self.assertTrue(decide_skill_action(eligible).automatic_execution_allowed)
        blocked_decision = decide_skill_action(blocked)
        self.assertFalse(blocked_decision.automatic_execution_allowed)
        self.assertIn("skill_not_already_hidden", blocked_decision.blockers)
        self.assertIn("insufficient_independent_windows", blocked_decision.blockers)

    def test_controller_executes_only_registered_eligible_lane(self) -> None:
        diagnosis = RepairDiagnosis(
            skill_id="skill-a",
            failure_kind="skill_local",
            trace_ids=("trace-1",),
            failed_target_case_ids=("case-1",),
            verifier_feedback=("failed",),
            library_snapshot_sha256="c" * 64,
        )
        eligible = decide_skill_action(
            signal_from_repair_diagnosis(
                diagnosis,
                verifier_trusted=True,
                actual_invocation_evidence_complete=True,
            )
        )
        blocked = decide_skill_action(
            signal_from_repair_diagnosis(
                diagnosis,
                verifier_trusted=False,
                actual_invocation_evidence_complete=False,
            )
        )
        controller = SelfManagingHarnessController(
            {
                ManagedAction.SKILL_REPAIR: lambda decision: {
                    "adopted": True,
                    "target": decision.target_id,
                }
            }
        )

        self.assertTrue(controller.execute(eligible)["result"]["adopted"])
        self.assertFalse(controller.execute(blocked)["executed"])


if __name__ == "__main__":
    unittest.main()
