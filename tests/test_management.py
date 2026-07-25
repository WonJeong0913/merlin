from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from experiments.mvp.run_management_policy_comparison import (
    _contract,
    _inputs,
    _snapshot,
    _traces_for_arm,
    reuse_codex_smoke_artifact,
)
from src.merlin_harness.management import (
    DecisionAction,
    M2KTraceEvidence,
    ManagementArm,
    ManagementContractError,
    ManagementRoundInput,
    TaskExposure,
    TelemetryEvidence,
    build_management_round_report,
    compare_management_reports,
    content_sha256,
    management_report_to_dict,
    run_management_round,
)
from src.merlin_harness.traces import validate_agent_trace_evidence


class ManagementRoundTests(unittest.TestCase):
    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name)
        contract = _contract(_snapshot())
        traces = {arm: _traces_for_arm(output, contract, arm) for arm in ManagementArm}
        inputs = _inputs(contract, traces)
        outputs = {round_input.arm: run_management_round(round_input) for round_input in inputs}
        reports = {
            arm: build_management_round_report(outputs[arm], traces[arm])
            for arm in ManagementArm
        }
        return output, contract, traces, {round_input.arm: round_input for round_input in inputs}, outputs, reports

    def test_comparison_rejects_snapshot_budget_verifier_and_model_drift(self) -> None:
        _, contract, _, _, _, reports = self._fixture()
        baseline = list(reports.values())
        variants = (
            replace(contract, library_snapshot=replace(contract.library_snapshot, snapshot_id="other-snapshot")),
            replace(contract, budget_id="other-budget"),
            replace(contract, model_id="other-model"),
            replace(contract, verifier_ids_by_task=tuple((task_id, "other-verifier") for task_id in contract.task_ids)),
        )
        for variant in variants:
            bad_report = replace(reports[ManagementArm.M1], output=replace(reports[ManagementArm.M1].output, contract=variant))
            candidate_reports = [report for report in baseline if report.output.arm is not ManagementArm.M1] + [bad_report]
            with self.assertRaisesRegex(ManagementContractError, "arm comparison refused"):
                compare_management_reports(candidate_reports)

    def test_m2h_rejects_outcome_invocation_shadowing_and_regression_evidence(self) -> None:
        _, _, traces, inputs, _, _ = self._fixture()
        forbidden = M2KTraceEvidence(
            trace=traces[ManagementArm.M2_K][1],
            parent_verifier_passed=True,
            regression_group="forbidden-to-m2h",
        )
        with self.assertRaisesRegex(ManagementContractError, "M2-H may not receive"):
            run_management_round(replace(inputs[ManagementArm.M2_H], m2k_evidence=(forbidden,)))

    def test_m2k_incomplete_is_excluded_not_empty_and_tamper_is_rejected(self) -> None:
        _, _, traces, inputs, _, _ = self._fixture()
        complete_input = inputs[ManagementArm.M2_K]
        complete_trace = traces[ManagementArm.M2_K][1]
        complete_trace.metadata["mutated_after_declaration"] = True
        with self.assertRaisesRegex(ManagementContractError, "trace record changed"):
            run_management_round(complete_input)
        complete_trace.metadata.pop("mutated_after_declaration")
        evidence = validate_agent_trace_evidence(complete_trace, verify_raw_trace=True)
        raw_path = Path(evidence.contract.raw_trace_root) / evidence.raw_trace.pointer
        raw_path.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ManagementContractError, "raw trace sha256 mismatch"):
            run_management_round(complete_input)

        smoke_path = Path(
            "experiments/mvp/results/agent_trace_contract/live_gpt56/traces/"
            "codex-gpt56-smoke-20260718T042332Z-f2315a0c.json"
        )
        smoke = reuse_codex_smoke_artifact(smoke_path)
        self.assertFalse(smoke["actual_invocation_evidence_complete"])
        self.assertEqual(smoke["paper_metric_eligible"], 0)
        self.assertEqual(smoke["paper_metric_incomplete"], 1)
        self.assertEqual((smoke["n_count"], smoke["m_count"], smoke["o_count"]), (0, 0, 0))
        denominator = smoke["m2k_decision_denominator"]
        self.assertEqual(denominator["excluded_incomplete"], 1)

    def test_equal_active_capacity_is_required_in_reports_and_comparison(self) -> None:
        _, contract, _, _, outputs, reports = self._fixture()
        self.assertEqual(outputs[ManagementArm.M0].resulting_snapshot_active_library_capacity, contract.library_snapshot.active_library_capacity)
        drifted_output = replace(
            reports[ManagementArm.M2_H].output,
            resulting_snapshot_active_library_capacity=contract.library_snapshot.active_library_capacity + 1,
        )
        drifted_report = replace(reports[ManagementArm.M2_H], output=drifted_output)
        candidate_reports = [report for arm, report in reports.items() if arm is not ManagementArm.M2_H] + [drifted_report]
        with self.assertRaisesRegex(ManagementContractError, "active-library capacity drifted"):
            compare_management_reports(candidate_reports)

    def test_m0_and_m1_cannot_accept_adaptation_evidence(self) -> None:
        _, _, traces, inputs, _, _ = self._fixture()
        telemetry = TelemetryEvidence("adaptation-trace", "distractor", 0, 1, 0, 9)
        m2k_evidence = M2KTraceEvidence(traces[ManagementArm.M2_K][1], True, "adaptation")
        with self.assertRaisesRegex(ManagementContractError, "M0 accepts no adaptation"):
            run_management_round(replace(inputs[ManagementArm.M0], m2h_telemetry=(telemetry,)))
        with self.assertRaisesRegex(ManagementContractError, "M1 accepts only"):
            run_management_round(replace(inputs[ManagementArm.M1], m2k_evidence=(m2k_evidence,)))

    def test_serialization_hash_and_parent_lineage_are_deterministic(self) -> None:
        _, contract, _, inputs, _, _ = self._fixture()
        first = run_management_round(inputs[ManagementArm.M2_H])
        second = run_management_round(inputs[ManagementArm.M2_H])
        self.assertEqual(first.input_sha256, second.input_sha256)
        self.assertEqual(first.output_sha256, second.output_sha256)
        self.assertEqual(first.parent_snapshot, contract.library_snapshot)
        self.assertEqual(first.resulting_snapshot_active_library_capacity, contract.library_snapshot.active_library_capacity)
        self.assertFalse(first.library_mutated)
        self.assertEqual(content_sha256(management_report_to_dict(first)), content_sha256(management_report_to_dict(second)))

    def test_m2h_and_m2k_share_report_schema_but_use_different_evidence_and_decisions(self) -> None:
        _, _, _, _, outputs, reports = self._fixture()
        h_report = management_report_to_dict(reports[ManagementArm.M2_H])
        k_report = management_report_to_dict(reports[ManagementArm.M2_K])
        self.assertEqual(set(h_report["metrics"]), set(k_report["metrics"]))
        self.assertEqual(set(h_report["task_reports"][0]), set(k_report["task_reports"][0]))
        h_decision = outputs[ManagementArm.M2_H].lifecycle_decisions[0]
        k_decision = outputs[ManagementArm.M2_K].lifecycle_decisions[0]
        self.assertEqual(h_decision.action, DecisionAction.HIDE_SKILL)
        self.assertEqual(h_decision.scope.value, "skill_local")
        self.assertEqual(k_decision.action, DecisionAction.GUARD_ROUTE)
        self.assertEqual(k_decision.scope.value, "route_local")
        self.assertEqual(k_decision.task_id, "management-wrong")

    def test_report_rejects_capacity_exposure_outside_frozen_snapshot(self) -> None:
        _, contract, _, inputs, _, _ = self._fixture()
        invalid_exposure = tuple(TaskExposure(task_id, ("oracle", "distractor", "utility", "outside")) for task_id in contract.task_ids)
        with self.assertRaisesRegex(ManagementContractError, "outside the frozen library"):
            run_management_round(replace(inputs[ManagementArm.M0], predeclared_exposure=invalid_exposure))


if __name__ == "__main__":
    unittest.main()
