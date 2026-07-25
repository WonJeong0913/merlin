from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.mvp.reporting import render_control_room
from experiments.mvp.run_lifecycle_recovery_demo import main, open_generated_report, run_lifecycle_recovery_demo
from src.merlin_harness.models import LifecyclePromotionCriteria


class LifecycleRecoveryDemoTests(unittest.TestCase):
    def test_controlled_shadowing_is_recovered_by_hiding_repeatedly_harmful_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = run_lifecycle_recovery_demo(output)

            overloaded = report["conditions"]["Overloaded library"]
            recovered = report["conditions"]["Lifecycle recovered"]
            hidden_ids = {decision["skill_id"] for decision in report["lifecycle_decisions"]}

            self.assertGreater(overloaded["pi_m"], 0.0)
            self.assertGreater(recovered["pass_rate"], overloaded["pass_rate"])
            self.assertEqual(recovered["pi_m"], 0.0)
            self.assertEqual(recovered["pi_o"], 1.0)
            self.assertEqual(hidden_ids, {"aa-file-artifact-distractor", "aa-line-count-distractor"})
            self.assertTrue(report["promotion"]["accepted"])
            self.assertTrue(all(check["passed"] for check in report["promotion"]["checks"]))
            self.assertEqual(report["library_resolution"]["mode"], "provisional_promoted")
            self.assertEqual(
                report["provisional_change"]["original_statuses"]["aa-file-artifact-distractor"], "active"
            )
            self.assertEqual(
                report["provisional_change"]["provisional_statuses"]["aa-file-artifact-distractor"], "hidden"
            )
            self.assertEqual(report["schema_version"], 2)
            self.assertEqual(
                report["scope_boundary"]["active_in_this_demo"],
                [
                    "task-conditioned provisioning records",
                    "deterministic selection records",
                    "trace and outcome validation",
                    "trace-backed hide lifecycle action",
                    "copy-on-write promotion after a same-verifier gate",
                ],
            )
            self.assertEqual(
                report["scope_boundary"]["deferred"],
                [
                    "general multi-family model-authored skill generation",
                    "repair, merge, and retire actions",
                    "learned harness co-evolution",
                    "full-87 and model-backed evaluation",
                ],
            )
            self.assertIn("not provider-native skill-body invocation evidence", report["scope_boundary"]["actual_invocation_boundary"])

            loop = {stage["id"]: stage for stage in report["governance_loop"]["stages"]}
            self.assertEqual(report["governance_loop"]["schema_version"], 1)
            self.assertEqual(
                list(loop),
                ["provision", "select", "observe_trace", "lifecycle_action", "same_verifier_gate"],
            )
            self.assertEqual(loop["provision"]["evidence"]["task_count"], 10)
            self.assertEqual(loop["provision"]["evidence"]["unique_skill_count"], 4)
            self.assertEqual(
                set(loop["provision"]["evidence"]["skill_ids"]),
                {"file-artifact-basic", "line-summary", "aa-file-artifact-distractor", "aa-line-count-distractor"},
            )
            self.assertEqual(loop["select"]["evidence"]["selection_count"], 9)
            self.assertEqual(loop["observe_trace"]["evidence"]["trace_count"], 10)
            self.assertEqual(loop["observe_trace"]["evidence"]["route_risk_trace_count"], 8)
            self.assertEqual(loop["lifecycle_action"]["evidence"]["decision_count"], 2)
            self.assertEqual(loop["lifecycle_action"]["evidence"]["evidence_trace_count"], 8)
            self.assertTrue(loop["lifecycle_action"]["evidence"]["copy_on_write"])
            self.assertEqual(loop["same_verifier_gate"]["status"], "accepted")
            self.assertEqual(loop["same_verifier_gate"]["evidence"]["re_run_task_count"], 10)
            self.assertEqual(loop["same_verifier_gate"]["evidence"]["verifier_id_count"], 10)
            self.assertEqual(loop["same_verifier_gate"]["evidence"]["promotion_check_count"], 5)
            self.assertEqual(loop["same_verifier_gate"]["evidence"]["passed_promotion_check_count"], 5)
            self.assertIn(
                "conditions['Overloaded library'].tasks[].provisioned_skill_ids",
                loop["provision"]["evidence_keys"],
            )
            self.assertIn("provisional_verification.tasks[].verifier_ids", loop["same_verifier_gate"]["evidence_keys"])
            self.assertTrue(all("provisioned_skill_ids" in task for task in overloaded["tasks"]))
            self.assertTrue((output / "lifecycle_recovery.json").is_file())
            self.assertTrue((output / "lifecycle_recovery.html").is_file())
            exported = json.loads((output / "lifecycle_recovery.json").read_text(encoding="utf-8"))
            self.assertEqual(exported["governance_loop"], report["governance_loop"])
            self.assertEqual(exported["scope_boundary"], report["scope_boundary"])

    def test_control_room_contains_the_required_interactive_evidence_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = run_lifecycle_recovery_demo(output)
            document = (output / "lifecycle_recovery.html").read_text(encoding="utf-8")

            self.assertIn("Merlin Control Room", document)
            self.assertIn('data-stage-button="reference"', document)
            self.assertIn('data-stage-button="overloaded"', document)
            self.assertIn('data-stage-button="recovered"', document)
            self.assertIn('id="route-matrix"', document)
            self.assertIn('id="decision-list"', document)
            self.assertIn('id="safety-list"', document)
            self.assertIn('id="replay"', document)
            self.assertIn('id="pause-playback"', document)
            self.assertIn('id="presentation-mode"', document)
            self.assertIn('id="download-json"', document)
            self.assertIn('id="governance-loop"', document)
            self.assertIn('data-governance-stage', document)
            self.assertIn("Harness Governance Loop", document)
            self.assertIn('id="scope-boundary"', document)
            self.assertIn("Active in this demo", document)
            self.assertIn("Same-verifier gate", document)
            self.assertIn("not provider-native skill-body invocation evidence", document)
            self.assertIn("aa-line-count-distractor", document)
            self.assertIn("same_verifier_contract", document)
            self.assertIn("gpt-5.6-terra", document)
            self.assertIn('"pass_rate_gain":0.8', document)
            self.assertEqual(report["recovery_delta"]["pass_rate_gain"], 0.8)
            self.assertNotIn("[PASTE", document)
            self.assertNotIn("/Users/", document)
            self.assertNotIn("/private/", document)
            self.assertNotIn("https://", document)

    def test_control_room_escapes_embedded_data_and_removes_private_values(self) -> None:
        report = {
            "conditions": {},
            "scope": "</script><script>window.bad=true</script>",
            "workspace_root": "/Users/example/private-workspace",
            "raw_trace": "file:///private/raw.jsonl",
            "governance_loop": {"provider_raw_data": "private-provider-output"},
            "scope_boundary": {"active_in_this_demo": ["/private/hidden"]},
        }
        document = render_control_room(report)

        self.assertNotIn("</script><script>window.bad", document)
        self.assertNotIn("/Users/example/private-workspace", document)
        self.assertNotIn("file:///private/raw.jsonl", document)
        self.assertNotIn("private-provider-output", document)
        self.assertNotIn("/private/hidden", document)
        self.assertIn("\\u003c/script\\u003e", document)
        self.assertIn("redacted from standalone report", document)

    @patch("experiments.mvp.run_lifecycle_recovery_demo.open_generated_report")
    def test_cli_open_is_explicit_and_opt_in(self, mocked_open: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "demo"
            self.assertEqual(main(["--output", str(output), "--open"]), 0)

            mocked_open.assert_called_once_with(str(output))

    @patch("experiments.mvp.run_lifecycle_recovery_demo.subprocess.run")
    def test_open_helper_uses_macos_open_without_a_shell(self, mocked_run: unittest.mock.Mock) -> None:
        output = Path("/private/tmp/merlin-open-helper")
        open_generated_report(output)

        mocked_run.assert_called_once_with(["open", str(output / "lifecycle_recovery.html")], check=False)

    def test_rejected_promotion_retains_original_library_and_records_rollback_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = run_lifecycle_recovery_demo(
                Path(temporary),
                promotion_criteria=LifecyclePromotionCriteria(min_pi_m_reduction=1.0),
            )

            self.assertFalse(report["promotion"]["accepted"])
            self.assertTrue(report["promotion"]["rollback_required"])
            self.assertIn("shadowing_reduction", report["promotion"]["reason"])
            self.assertEqual(report["library_resolution"]["mode"], "original_retained")
            self.assertTrue(report["library_resolution"]["rollback"]["performed"])
            self.assertEqual(
                report["library_resolution"]["final_statuses"],
                report["provisional_change"]["original_statuses"],
            )
            self.assertTrue(
                all(status == "active" for status in report["library_resolution"]["final_statuses"].values())
            )


if __name__ == "__main__":
    unittest.main()
