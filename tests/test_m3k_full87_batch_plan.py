from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.create_m3k_full87_batch_plan import (
    M3KBatchPlanError,
    validate_m3k_full87_batch_plan,
    write_m3k_full87_batch_plan,
)
from experiments.skillsbench.m3k_external_evidence import (
    record_m3k_external_trajectory,
)
from experiments.skillsbench.validate_m3k_pilot_evidence import (
    validate_m3k_pilot_evidence,
)
from src.merlin_harness.management import content_sha256
from tests.test_m3k_pilot_evidence import M3KPilotEvidenceTests


LIBRARY_SCALE = Path("experiments/skillsbench/library-scale-manifest.json")


class M3KFull87BatchPlanTests(unittest.TestCase):
    """Model-free tests for the immutable 522-cell post-pilot plan."""

    @classmethod
    def setUpClass(cls) -> None:
        M3KPilotEvidenceTests.setUpClass()

    @staticmethod
    def _fixture() -> M3KPilotEvidenceTests:
        return M3KPilotEvidenceTests(methodName="runTest")

    def _write_plan(self, root: Path) -> tuple[Path, dict, dict]:
        bound_path, pilot_path, evidence_root, pilot = self._fixture()._record_pilot(root)
        report_path = root / "pilot-report.json"
        validate_m3k_pilot_evidence(
            bound_manifest_path=bound_path,
            pilot_manifest_path=pilot_path,
            evidence_root=evidence_root,
            output_path=report_path,
        )
        plan_path = root / "m3k-full87-batch-plan.json"
        kwargs = {
            "bound_manifest_path": bound_path,
            "library_scale_manifest_path": LIBRARY_SCALE,
            "pilot_manifest_path": pilot_path,
            "pilot_report_path": report_path,
            "evidence_root": evidence_root,
        }
        plan = write_m3k_full87_batch_plan(output_path=plan_path, **kwargs)
        return plan_path, plan, kwargs

    def test_freezes_exact_522_order_and_only_six_sealed_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan_path, plan, kwargs = self._write_plan(Path(temporary))
            self.assertEqual(plan["counts"]["scheduled_trajectories"], 522)
            self.assertEqual(plan["counts"]["sealed_pilot_trajectories"], 6)
            self.assertEqual(plan["counts"]["pending_trajectories"], 516)
            self.assertEqual(len(plan["cells"]), 522)
            self.assertEqual(
                sum(cell["initial_status"] == "sealed_pilot_evidence" for cell in plan["cells"]),
                6,
            )
            self.assertEqual(
                sum(cell["initial_status"] == "pending" for cell in plan["cells"]),
                516,
            )
            self.assertFalse(any(plan["claim_boundary"].values()))
            self.assertEqual(
                validate_m3k_full87_batch_plan(plan_path=plan_path, **kwargs),
                plan,
            )

    def test_rehashed_plan_drift_and_existing_output_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan_path, plan, kwargs = self._write_plan(Path(temporary))
            before = plan_path.read_bytes()
            with self.assertRaisesRegex(M3KBatchPlanError, "new-only"):
                write_m3k_full87_batch_plan(output_path=plan_path, **kwargs)
            self.assertEqual(plan_path.read_bytes(), before)

            tampered = json.loads(json.dumps(plan))
            tampered["cells"][6]["initial_status"] = "sealed_pilot_evidence"
            tampered.pop("plan_sha256")
            tampered["plan_sha256"] = content_sha256(tampered)
            plan_path.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(M3KBatchPlanError, "drifted from frozen dependencies"):
                validate_m3k_full87_batch_plan(plan_path=plan_path, **kwargs)

    def test_library_file_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path, _plan, kwargs = self._write_plan(root)
            copied_library = root / "library-scale-manifest.json"
            copied_library.write_bytes(LIBRARY_SCALE.read_bytes() + b"\n")
            kwargs["library_scale_manifest_path"] = copied_library
            with self.assertRaisesRegex(M3KBatchPlanError, "file hash drifted"):
                validate_m3k_full87_batch_plan(plan_path=plan_path, **kwargs)

    def test_plan_reopens_after_one_additional_trajectory_is_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path, plan, kwargs = self._write_plan(root)
            bound = json.loads(kwargs["bound_manifest_path"].read_text(encoding="utf-8"))
            scheduled = next(
                cell for cell in bound["paired_cells"]
                if cell["trajectory_id"] not in {
                    item["trajectory_id"]
                    for item in plan["cells"]
                    if item["initial_status"] == "sealed_pilot_evidence"
                }
            )
            fixture = self._fixture()._external_fixture()
            attestation = fixture._attestation(
                bound=bound,
                bound_path=kwargs["bound_manifest_path"],
                scheduled=scheduled,
                passed=True,
                invocation_complete=True,
            )
            incoming = root / "seventh-input"
            raw_path, audit_path, event_path, artifact_root = fixture._execution_input(
                incoming / "pack",
                bound=bound,
                attestation=attestation,
            )
            attestation_path = incoming / "attestation.json"
            attestation_path.parent.mkdir(parents=True, exist_ok=True)
            attestation_path.write_text(json.dumps(attestation) + "\n", encoding="utf-8")
            record_m3k_external_trajectory(
                bound_manifest_path=kwargs["bound_manifest_path"],
                attestation_path=attestation_path,
                raw_provider_trace_path=raw_path,
                runtime_audit_path=audit_path,
                execution_event_path=event_path,
                raw_artifact_root=artifact_root,
                evidence_root=kwargs["evidence_root"],
            )
            self.assertEqual(
                validate_m3k_full87_batch_plan(plan_path=plan_path, **kwargs),
                plan,
            )


if __name__ == "__main__":
    unittest.main()
