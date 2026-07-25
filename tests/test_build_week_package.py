from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.build_week_package import (
    INTEGRITY_FILENAME,
    PackageError,
    build_package,
    run_reproducibility_check,
    source_audit,
    verify_package,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class BuildWeekPackageTests(unittest.TestCase):
    def test_build_is_allowlisted_verified_and_reproducible(self) -> None:
        audit = source_audit(REPOSITORY_ROOT)
        self.assertFalse(audit["limit_violations"])
        self.assertFalse(audit["sensitive_hits"])
        self.assertIn("experiments/skillsbench", audit["excluded_roots_present"])

        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "merlin-build-week"
            build_package(REPOSITORY_ROOT, package)
            verification = verify_package(package)
            self.assertGreater(verification["file_count"], 10)
            self.assertTrue((package / INTEGRITY_FILENAME).is_file())
            self.assertTrue((package / ".gitignore").is_file())
            launcher = package / "apps" / "merlin-macos" / "scripts" / "run-app.sh"
            self.assertTrue(launcher.is_file())
            self.assertNotEqual(
                launcher.stat().st_mode & 0o111,
                0,
                "native app launcher must remain executable in the judge package",
            )
            self.assertFalse((package / "experiments" / "skillsbench").exists())
            self.assertFalse((package / "codex").exists())
            self.assertFalse((package / "NUL").exists())
            live_evidence_root = (
                package
                / "experiments"
                / "mvp"
                / "results"
                / "model_authored_skill_live_v1"
            )
            self.assertTrue(
                (live_evidence_root / "model_authored_skill_evidence.json").is_file()
            )
            self.assertTrue(
                (live_evidence_root / "model_authored_skill_chain_audit.json").is_file()
            )
            rejection_evidence_root = (
                package
                / "experiments"
                / "mvp"
                / "results"
                / "model_authored_skill_rejection_live_v1"
            )
            rejection_evidence = json.loads(
                (
                    rejection_evidence_root
                    / "model_authored_skill_rejection_evidence.json"
                ).read_text(encoding="utf-8")
            )
            self.assertFalse(rejection_evidence["adopted"])
            self.assertEqual(
                rejection_evidence["quarantine"]["rejection_code"],
                "network_or_process_import",
            )
            self.assertFalse(rejection_evidence["evidence_boundary"]["host_execution"])
            self.assertTrue(
                (
                    rejection_evidence_root
                    / "model_authored_skill_rejection_chain_audit.json"
                ).is_file()
            )
            rollback_root = (
                package
                / "experiments"
                / "mvp"
                / "results"
                / "model_authored_hidden_completion_live_v1"
            )
            rollback_evidence = json.loads(
                (
                    rollback_root
                    / "model_authored_hidden_completion_evidence.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(rollback_evidence["lifecycle_action"], "rollback")
            self.assertTrue(
                rollback_evidence["evidence_boundary"]["hidden_held_out_verifier_passed"]
            )
            self.assertFalse(
                rollback_evidence["evidence_boundary"]["negative_routing_verifier_passed"]
            )
            self.assertTrue(
                rollback_evidence["evidence_boundary"]["copy_on_write_rolled_back"]
            )
            rollback_audit = json.loads(
                (
                    rollback_root / "model_authored_hidden_completion_audit.json"
                ).read_text(encoding="utf-8")
            )
            self.assertTrue(rollback_audit["passed"])
            self.assertEqual(rollback_audit["checks_passed"], 9)
            self.assertTrue(
                (
                    live_evidence_root
                    / "quarantine"
                    / "candidate"
                    / "extract-todo-items"
                    / "scripts"
                    / "run.py"
                ).is_file()
            )
            repair_evidence_root = (
                package
                / "experiments"
                / "mvp"
                / "results"
                / "model_authored_skill_repair_live_v1"
            )
            self.assertTrue(
                (repair_evidence_root / "model_authored_skill_repair_evidence.json").is_file()
            )
            self.assertTrue(
                (repair_evidence_root / "model_authored_skill_repair_chain_audit.json").is_file()
            )
            self.assertTrue(
                (
                    repair_evidence_root
                    / "quarantine"
                    / "candidate"
                    / "extract-todo-items"
                    / "scripts"
                    / "run.py"
                ).is_file()
            )
            family2_evidence_root = (
                package
                / "experiments"
                / "mvp"
                / "results"
                / "model_authored_skill_repair_family2_live_v1"
            )
            family2_evidence = json.loads(
                (
                    family2_evidence_root
                    / "model_authored_skill_repair_family2_evidence.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(family2_evidence["decision"], "promote")
            self.assertFalse(
                family2_evidence["baseline_bundle"]["model_authorship_claim"]
            )
            self.assertTrue(
                (
                    family2_evidence_root
                    / "model_authored_skill_repair_family2_chain_audit.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    family2_evidence_root
                    / "candidate_quarantine"
                    / "candidate"
                    / "parse-key-value-config"
                    / "scripts"
                    / "run.py"
                ).is_file()
            )
            self.assertTrue(
                (package / "docs" / "live-model-authored-skill-v1.md").is_file()
            )
            self.assertTrue(
                (package / "docs" / "model-authored-rejection-v1.md").is_file()
            )
            self.assertTrue(
                (package / "docs" / "model-authored-routing-rollback-v1.md").is_file()
            )
            self.assertTrue(
                (package / "docs" / "gpt56-selection-shadowing-pilot-v1.md").is_file()
            )
            selection_pilot = json.loads(
                (
                    package
                    / "docs"
                    / "evidence"
                    / "gpt56-selection-shadowing-pilot-v1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(selection_pilot["provider_turns"], 8)
            self.assertEqual(
                [item["correct"] for item in selection_pilot["arms"]],
                [12, 12, 11, 12],
            )
            self.assertFalse(selection_pilot["claim_boundary"]["task_execution"])
            self.assertFalse(selection_pilot["claim_boundary"]["utility_verification"])
            self.assertFalse(
                selection_pilot["claim_boundary"]["full87_or_1305_cell_result"]
            )
            self.assertTrue(
                (package / "docs" / "skill-merge-lifecycle-v1.md").is_file()
            )
            self.assertTrue(
                (package / "docs" / "skill-retirement-lifecycle-v1.md").is_file()
            )
            self.assertTrue(
                (package / "src" / "merlin_harness" / "skill_retirement.py").is_file()
            )
            self.assertTrue(
                (package / "src" / "merlin_harness" / "skill_merge.py").is_file()
            )
            self.assertTrue((package / "tests" / "test_skill_retirement.py").is_file())
            self.assertTrue((package / "tests" / "test_skill_merge.py").is_file())
            self.assertTrue((package / "tests" / "test_skill_merge_demo.py").is_file())
            self.assertTrue(
                (package / "docs" / "harnessx-typed-runtime-v1.md").is_file()
            )
            self.assertTrue(
                (package / "tests" / "test_harnessx_runtime.py").is_file()
            )
            harnessx_evidence = json.loads(
                (
                    package
                    / "experiments"
                    / "mvp"
                    / "results"
                    / "harnessx_typed_runtime_v1"
                    / "harnessx_typed_runtime.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(harnessx_evidence["hook_coverage_count"], 8)
            self.assertTrue(harnessx_evidence["low_risk_reversible_change"]["accepted"])
            self.assertEqual(
                harnessx_evidence["high_risk_change"]["resolution"],
                "approval_required_parent_retained",
            )
            self.assertFalse(harnessx_evidence["frozen_435_execution_included"])
            merge_evidence = json.loads(
                (
                    package
                    / "experiments"
                    / "mvp"
                    / "results"
                    / "skill_merge_v1"
                    / "skill_merge.json"
                ).read_text(encoding="utf-8")
            )
            self.assertTrue(merge_evidence["result"]["merged"])
            self.assertEqual(merge_evidence["summary"]["gates_passed"], 9)
            self.assertFalse(
                merge_evidence["claim_boundary"]["actual_provider_trace_evidence"]
            )
            self.assertTrue(
                (
                    package
                    / "experiments"
                    / "mvp"
                    / "run_live_model_skill_creation.py"
                ).is_file()
            )
            self.assertTrue(
                (
                    package
                    / "experiments"
                    / "mvp"
                    / "audit_model_authored_skill_chain.py"
                ).is_file()
            )
            self.assertTrue(
                (
                    package
                    / "experiments"
                    / "mvp"
                    / "audit_model_authored_rejection_chain.py"
                ).is_file()
            )
            self.assertFalse(
                (
                    package
                    / "experiments"
                    / "mvp"
                    / "results"
                    / "semantic_router"
                ).exists()
            )

            run_reproducibility_check(package)
            # The check runs the package with bytecode output disabled, so its
            # integrity contract is still valid after executing the demo/tests.
            verify_package(package)

    def test_verifier_rejects_unexpected_files_and_license_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "merlin-build-week"
            build_package(REPOSITORY_ROOT, package)
            (package / "tmp").mkdir()
            (package / "tmp" / "leak.txt").write_text("not allowed", encoding="utf-8")
            with self.assertRaisesRegex(PackageError, "unexpected=.*tmp/leak.txt"):
                verify_package(package)

        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "merlin-build-week"
            build_package(REPOSITORY_ROOT, package)
            (package / "workspaces").mkdir()
            with self.assertRaisesRegex(PackageError, "forbidden=workspaces"):
                verify_package(package)

        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "merlin-build-week"
            build_package(REPOSITORY_ROOT, package)
            with self.assertRaisesRegex(PackageError, "Public-release check blocked"):
                verify_package(package, require_license=True)

    def test_builder_refuses_to_write_inside_source_repository(self) -> None:
        with self.assertRaisesRegex(PackageError, "outside the source repository"):
            build_package(REPOSITORY_ROOT, REPOSITORY_ROOT / "unsafe-build-week-output")

    def test_integrity_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "merlin-build-week"
            build_package(REPOSITORY_ROOT, package)
            target = package / "experiments" / "mvp" / "tasks" / "answer-yes.json"
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload["instruction"] = "tampered"
            target.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(PackageError, "checksums do not match"):
                verify_package(package)


if __name__ == "__main__":
    unittest.main()
