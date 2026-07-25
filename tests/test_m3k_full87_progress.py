from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.skillsbench.create_m3k_full87_batch_plan import (
    write_m3k_full87_batch_plan,
)
from experiments.skillsbench.m3k_external_evidence import (
    record_m3k_external_trajectory,
)
from experiments.skillsbench.m3k_full87_progress import (
    M3KFull87ProgressError,
    validate_m3k_full87_progress,
    write_m3k_full87_progress,
)
from experiments.skillsbench.run_m3k_codex_mcp_cell import (
    M3KCodexCellError,
    resolve_m3k_operator_cell,
)
from experiments.skillsbench.validate_m3k_pilot_evidence import (
    validate_m3k_pilot_evidence,
)
from src.merlin_harness.management import content_sha256
from tests.test_m3k_full87_batch_plan import LIBRARY_SCALE
from tests.test_m3k_pilot_evidence import M3KPilotEvidenceTests


class M3KFull87ProgressTests(unittest.TestCase):
    """Model-free restart and evidence-frontier tests."""

    @classmethod
    def setUpClass(cls) -> None:
        M3KPilotEvidenceTests.setUpClass()
        cls._baseline_temporary = tempfile.TemporaryDirectory()
        baseline = Path(cls._baseline_temporary.name)
        fixture = M3KPilotEvidenceTests(methodName="runTest")
        bound_path, pilot_path, evidence_root, _pilot = fixture._record_pilot(baseline)
        report_path = baseline / "pilot-report.json"
        validate_m3k_pilot_evidence(
            bound_manifest_path=bound_path,
            pilot_manifest_path=pilot_path,
            evidence_root=evidence_root,
            output_path=report_path,
        )
        plan_path = baseline / "m3k-full87-batch-plan.json"
        write_m3k_full87_batch_plan(
            output_path=plan_path,
            bound_manifest_path=bound_path,
            library_scale_manifest_path=LIBRARY_SCALE,
            pilot_manifest_path=pilot_path,
            pilot_report_path=report_path,
            evidence_root=evidence_root,
        )
        cls._baseline = baseline

    @classmethod
    def tearDownClass(cls) -> None:
        cls._baseline_temporary.cleanup()

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name) / "fixture"
        shutil.copytree(self._baseline, self.root)
        self.kwargs = {
            "plan_path": self.root / "m3k-full87-batch-plan.json",
            "bound_manifest_path": self.root / "bound-m3k.json",
            "library_scale_manifest_path": LIBRARY_SCALE,
            "pilot_manifest_path": self.root / "m3k-six-cell-pilot.json",
            "pilot_report_path": self.root / "pilot-report.json",
            "evidence_root": self.root / "sealed-pilot-evidence",
        }

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _seal_next_pending(self) -> str:
        plan = json.loads(self.kwargs["plan_path"].read_text(encoding="utf-8"))
        scheduled = next(
            cell for cell in plan["cells"] if cell["initial_status"] == "pending"
        )
        bound = json.loads(
            self.kwargs["bound_manifest_path"].read_text(encoding="utf-8")
        )
        bound_cell = next(
            cell for cell in bound["paired_cells"]
            if cell["trajectory_id"] == scheduled["trajectory_id"]
        )
        fixture = M3KPilotEvidenceTests(methodName="runTest")._external_fixture()
        attestation = fixture._attestation(
            bound=bound,
            bound_path=self.kwargs["bound_manifest_path"],
            scheduled=bound_cell,
            passed=True,
            invocation_complete=True,
        )
        incoming = self.root / "next-input"
        raw_path, audit_path, event_path, artifact_root = fixture._execution_input(
            incoming / "pack",
            bound=bound,
            attestation=attestation,
        )
        attestation_path = incoming / "attestation.json"
        attestation_path.parent.mkdir(parents=True, exist_ok=True)
        attestation_path.write_text(json.dumps(attestation) + "\n", encoding="utf-8")
        record_m3k_external_trajectory(
            bound_manifest_path=self.kwargs["bound_manifest_path"],
            attestation_path=attestation_path,
            raw_provider_trace_path=raw_path,
            runtime_audit_path=audit_path,
            execution_event_path=event_path,
            raw_artifact_root=artifact_root,
            evidence_root=self.kwargs["evidence_root"],
        )
        return scheduled["trajectory_id"]

    def test_initial_snapshot_is_six_sealed_and_516_pending(self) -> None:
        output = self.root / "progress-000006.json"
        snapshot = write_m3k_full87_progress(output_path=output, **self.kwargs)
        self.assertEqual(snapshot["status"], "awaiting_evidence")
        self.assertEqual(snapshot["counts"]["sealed_trajectories"], 6)
        self.assertEqual(snapshot["counts"]["sealed_expansion_trajectories"], 0)
        self.assertEqual(snapshot["counts"]["pending_trajectories"], 516)
        self.assertEqual(snapshot["counts"]["unique_execution_packs"], 6)
        self.assertIsNotNone(snapshot["next_pending"])
        self.assertFalse(any(snapshot["claim_boundary"].values()))
        self.assertEqual(
            validate_m3k_full87_progress(progress_path=output, **self.kwargs),
            snapshot,
        )
        before = output.read_bytes()
        with self.assertRaisesRegex(M3KFull87ProgressError, "new-only"):
            write_m3k_full87_progress(output_path=output, **self.kwargs)
        self.assertEqual(output.read_bytes(), before)

    def test_seventh_record_advances_only_the_evidence_derived_frontier(self) -> None:
        trajectory_id = self._seal_next_pending()
        output = self.root / "progress-000007.json"
        snapshot = write_m3k_full87_progress(output_path=output, **self.kwargs)
        self.assertEqual(snapshot["counts"]["sealed_trajectories"], 7)
        self.assertEqual(snapshot["counts"]["sealed_expansion_trajectories"], 1)
        self.assertEqual(snapshot["counts"]["pending_trajectories"], 515)
        state = next(
            item for item in snapshot["cells"] if item["trajectory_id"] == trajectory_id
        )
        self.assertEqual(state["status"], "sealed_execution_evidence")
        self.assertIsNotNone(state["record_sha256"])
        self.assertIsNotNone(state["execution_pack_sha256"])

    def test_rehashed_snapshot_unknown_record_and_orphan_raw_fail_closed(self) -> None:
        output = self.root / "progress.json"
        snapshot = write_m3k_full87_progress(output_path=output, **self.kwargs)
        tampered = json.loads(json.dumps(snapshot))
        tampered["counts"]["pending_trajectories"] = 0
        tampered.pop("snapshot_sha256")
        tampered["snapshot_sha256"] = content_sha256(tampered)
        output.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(M3KFull87ProgressError, "drifted from sealed evidence"):
            validate_m3k_full87_progress(progress_path=output, **self.kwargs)

        trajectory_bucket = self.kwargs["evidence_root"] / "trajectories"
        (trajectory_bucket / ("f" * 64 + ".json")).write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(M3KFull87ProgressError, "unscheduled trajectory"):
            write_m3k_full87_progress(
                output_path=self.root / "unknown-record.json", **self.kwargs
            )

        (trajectory_bucket / ("f" * 64 + ".json")).unlink()
        (self.kwargs["evidence_root"] / "raw" / ("e" * 64 + ".bin")).write_bytes(b"orphan")
        with self.assertRaisesRegex(M3KFull87ProgressError, "unexpected=1"):
            write_m3k_full87_progress(
                output_path=self.root / "orphan-raw.json", **self.kwargs
            )

    def test_runner_resolves_only_the_evidence_derived_next_pending_cell(self) -> None:
        progress_path = self.root / "progress-for-runner.json"
        snapshot = write_m3k_full87_progress(
            output_path=progress_path, **self.kwargs
        )
        next_pending = snapshot["next_pending"]
        cell_root = self.root / "next-cell"
        cell_root.mkdir()
        next_contract = {
            "trajectory": {"trajectory_id": next_pending["trajectory_id"]},
            "execution_contract_sha256": "a" * 64,
        }
        with patch(
            "experiments.skillsbench.run_m3k_codex_mcp_cell.validate_materialized_m3k_cell",
            return_value=next_contract,
        ):
            resolved = resolve_m3k_operator_cell(
                bound_manifest_path=self.kwargs["bound_manifest_path"],
                library_scale_manifest_path=LIBRARY_SCALE,
                pilot_manifest_path=self.kwargs["pilot_manifest_path"],
                pilot_report_path=self.kwargs["pilot_report_path"],
                evidence_root=self.kwargs["evidence_root"],
                materialized_cell_root=cell_root,
                batch_plan_path=self.kwargs["plan_path"],
                progress_path=progress_path,
            )
        self.assertEqual(resolved["execution_scope"], "post_pilot_full87")
        self.assertEqual(resolved["ordinal"], next_pending["ordinal"])
        self.assertEqual(
            resolved["contract"]["trajectory"]["trajectory_id"],
            next_pending["trajectory_id"],
        )
        self.assertEqual(
            resolved["operator_source"]["progress_snapshot_sha256"],
            snapshot["snapshot_sha256"],
        )

        plan = json.loads(self.kwargs["plan_path"].read_text(encoding="utf-8"))
        later = next(
            cell for cell in plan["cells"]
            if cell["initial_status"] == "pending"
            and cell["trajectory_id"] != next_pending["trajectory_id"]
        )
        wrong_root = self.root / "wrong-cell"
        wrong_root.mkdir()
        wrong_contract = {
            "trajectory": {"trajectory_id": later["trajectory_id"]},
            "execution_contract_sha256": "b" * 64,
        }
        with patch(
            "experiments.skillsbench.run_m3k_codex_mcp_cell.validate_materialized_m3k_cell",
            return_value=wrong_contract,
        ):
            with self.assertRaisesRegex(M3KCodexCellError, "not the next pending"):
                resolve_m3k_operator_cell(
                    bound_manifest_path=self.kwargs["bound_manifest_path"],
                    library_scale_manifest_path=LIBRARY_SCALE,
                    pilot_manifest_path=self.kwargs["pilot_manifest_path"],
                    pilot_report_path=self.kwargs["pilot_report_path"],
                    evidence_root=self.kwargs["evidence_root"],
                    materialized_cell_root=wrong_root,
                    batch_plan_path=self.kwargs["plan_path"],
                    progress_path=progress_path,
                )


if __name__ == "__main__":
    unittest.main()
