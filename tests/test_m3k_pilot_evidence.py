from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.create_m3k_pilot_manifest import build_pilot_manifest
from experiments.skillsbench.m3k_external_evidence import (
    record_m3k_external_trajectory,
    sha256_file,
)
from experiments.skillsbench.validate_m3k_pilot_evidence import (
    M3KPilotEvidenceError,
    validate_m3k_pilot_evidence,
    validate_m3k_pilot_report,
)
from experiments.skillsbench.validate_m3k_first_cell_evidence import (
    M3KFirstCellEvidenceError,
    validate_m3k_first_cell_evidence,
    validate_m3k_first_cell_report,
)
from src.merlin_harness.management import content_sha256
from tests import test_m3k_external_evidence as external_evidence_tests


class M3KPilotEvidenceTests(unittest.TestCase):
    """Synthetic executor-admission tests; these never invoke a model/provider."""

    @classmethod
    def setUpClass(cls) -> None:
        # Reuse the external-evidence fixture's ready schema-v2 bound manifest
        # construction instead of creating an alternate M3-K contract here.
        external_evidence_tests.M3KExternalEvidenceTests.setUpClass()

    @staticmethod
    def _external_fixture() -> external_evidence_tests.M3KExternalEvidenceTests:
        return external_evidence_tests.M3KExternalEvidenceTests(methodName="runTest")

    def _write_pilot(self, root: Path) -> tuple[Path, dict, Path, dict, Path]:
        fixture = self._external_fixture()
        bound_path, bound = fixture._write_bound_manifest(root)
        pilot = build_pilot_manifest(
            bound_manifest=bound,
            bound_manifest_file_sha256=sha256_file(bound_path),
        )
        pilot_path = root / "m3k-six-cell-pilot.json"
        pilot_path.write_text(
            json.dumps(pilot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return bound_path, bound, pilot_path, pilot, root / "sealed-pilot-evidence"

    def _record_pilot(
        self,
        root: Path,
        *,
        count: int = 6,
        incomplete: bool = False,
    ) -> tuple[Path, Path, Path, dict]:
        bound_path, bound, pilot_path, pilot, evidence_root = self._write_pilot(root)
        fixture = self._external_fixture()
        incoming = root / "synthetic-pilot-input"
        incomplete_id = pilot["trajectories"][-1]["trajectory_id"] if incomplete else None
        for index, scheduled in enumerate(pilot["trajectories"][:count]):
            attestation_path = incoming / f"{index}.attestation.json"
            invocation_complete = scheduled["trajectory_id"] != incomplete_id
            attestation = fixture._attestation(
                bound=bound,
                bound_path=bound_path,
                scheduled=scheduled,
                passed=True,
                invocation_complete=invocation_complete,
            )
            raw_path, audit_path, event_path, artifact_root = fixture._execution_input(
                incoming / f"{index}-pack",
                bound=bound,
                attestation=attestation,
            )
            attestation_path.parent.mkdir(parents=True, exist_ok=True)
            attestation_path.write_text(
                json.dumps(attestation, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            record_m3k_external_trajectory(
                bound_manifest_path=bound_path,
                attestation_path=attestation_path,
                raw_provider_trace_path=raw_path,
                runtime_audit_path=audit_path,
                execution_event_path=event_path,
                raw_artifact_root=artifact_root,
                evidence_root=evidence_root,
            )
        return bound_path, pilot_path, evidence_root, pilot

    def test_complete_synthetic_six_cell_pilot_allows_expansion_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, pilot_path, evidence_root, _ = self._record_pilot(root)
            report_path = root / "pilot-report.json"
            report = validate_m3k_pilot_evidence(
                bound_manifest_path=bound_path,
                pilot_manifest_path=pilot_path,
                evidence_root=evidence_root,
                output_path=report_path,
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["coverage"]["expected_trajectories"], 6)
            self.assertEqual(report["coverage"]["recorded_trajectories"], 6)
            self.assertEqual(report["coverage"]["unique_execution_packs"], 6)
            self.assertTrue(report["coverage"]["complete"])
            self.assertTrue(report["scale_gate"]["contract_expansion_to_522_allowed"])
            self.assertFalse(report["scale_gate"]["promotion_decision_allowed"])
            self.assertFalse(report["claim_boundary"]["validation_is_model_execution"])
            self.assertFalse(report["claim_boundary"]["pilot_is_full87_result"])
            self.assertFalse(report["claim_boundary"]["candidate_promotion_claimed"])
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)
            self.assertEqual(
                validate_m3k_pilot_report(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    report_path=report_path,
                ),
                report,
            )

    def test_first_cell_report_opens_only_remaining_pilot_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, pilot_path, evidence_root, pilot = self._record_pilot(
                root, count=1
            )
            report_path = root / "first-cell-report.json"
            report = validate_m3k_first_cell_evidence(
                bound_manifest_path=bound_path,
                pilot_manifest_path=pilot_path,
                evidence_root=evidence_root,
                output_path=report_path,
            )
            self.assertEqual(
                report["first_cell"]["trajectory_id"],
                pilot["trajectories"][0]["trajectory_id"],
            )
            self.assertTrue(
                report["execution_order_gate"]["ordinals_2_through_6_allowed"]
            )
            self.assertFalse(
                report["execution_order_gate"]["six_cell_completion"]
            )
            self.assertFalse(
                report["execution_order_gate"]["contract_expansion_to_522_allowed"]
            )
            self.assertEqual(
                validate_m3k_first_cell_report(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    report_path=report_path,
                ),
                report,
            )

            tampered = json.loads(json.dumps(report))
            tampered["execution_order_gate"]["six_cell_completion"] = True
            tampered.pop("report_sha256")
            tampered["report_sha256"] = content_sha256(tampered)
            report_path.write_text(json.dumps(tampered, sort_keys=True) + "\n")
            with self.assertRaisesRegex(
                M3KFirstCellEvidenceError, "drifted from sealed evidence"
            ):
                validate_m3k_first_cell_report(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    report_path=report_path,
                )

    def test_first_cell_report_requires_ordinal_one_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, _bound, pilot_path, _pilot, evidence_root = self._write_pilot(
                root
            )
            with self.assertRaisesRegex(
                M3KFirstCellEvidenceError, "evidence_root is missing|missing=1"
            ):
                validate_m3k_first_cell_evidence(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    output_path=root / "must-not-exist.json",
                )
            self.assertFalse((root / "must-not-exist.json").exists())

    def test_report_tamper_rehash_and_evidence_drift_fail_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, pilot_path, evidence_root, _ = self._record_pilot(root)
            report_path = root / "pilot-report.json"
            original = validate_m3k_pilot_evidence(
                bound_manifest_path=bound_path,
                pilot_manifest_path=pilot_path,
                evidence_root=evidence_root,
                output_path=report_path,
            )

            tampered = json.loads(json.dumps(original))
            tampered["scale_gate"]["promotion_decision_allowed"] = True
            report_path.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(M3KPilotEvidenceError, "report hash mismatch"):
                validate_m3k_pilot_report(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    report_path=report_path,
                )

            tampered.pop("report_sha256")
            tampered["report_sha256"] = content_sha256(tampered)
            report_path.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(M3KPilotEvidenceError, "drifted from sealed evidence"):
                validate_m3k_pilot_report(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    report_path=report_path,
                )

            report_path.write_text(
                json.dumps(original, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            raw_path = next((evidence_root / "raw").iterdir())
            raw_path.write_bytes(raw_path.read_bytes() + b"tampered")
            with self.assertRaisesRegex(M3KPilotEvidenceError, "hash-invalid"):
                validate_m3k_pilot_report(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    report_path=report_path,
                )

    def test_five_of_six_and_incomplete_invocation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, pilot_path, evidence_root, _ = self._record_pilot(root, count=5)
            with self.assertRaisesRegex(M3KPilotEvidenceError, "missing=1"):
                validate_m3k_pilot_evidence(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    output_path=root / "five-of-six.json",
                )
            self.assertFalse((root / "five-of-six.json").exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, pilot_path, evidence_root, _ = self._record_pilot(root, incomplete=True)
            with self.assertRaisesRegex(M3KPilotEvidenceError, "complete actual-invocation"):
                validate_m3k_pilot_evidence(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    output_path=root / "incomplete.json",
                )
            self.assertFalse((root / "incomplete.json").exists())

    def test_bound_file_hash_drift_and_new_only_report_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, pilot_path, evidence_root, _ = self._record_pilot(root)
            report_path = root / "pilot-report.json"
            validate_m3k_pilot_evidence(
                bound_manifest_path=bound_path,
                pilot_manifest_path=pilot_path,
                evidence_root=evidence_root,
                output_path=report_path,
            )
            before = report_path.read_bytes()
            with self.assertRaisesRegex(M3KPilotEvidenceError, "output must be new-only"):
                validate_m3k_pilot_evidence(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    output_path=report_path,
                )
            self.assertEqual(report_path.read_bytes(), before)

            # The semantic manifest remains valid; only the exact bound file
            # bytes change, which must invalidate the pilot's source binding.
            bound_path.write_text(
                bound_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            drifted_output = root / "drifted-bound.json"
            with self.assertRaisesRegex(M3KPilotEvidenceError, "file hash drifted"):
                validate_m3k_pilot_evidence(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    evidence_root=evidence_root,
                    output_path=drifted_output,
                )
            self.assertFalse(drifted_output.exists())


if __name__ == "__main__":
    unittest.main()
