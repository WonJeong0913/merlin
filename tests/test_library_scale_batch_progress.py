from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.create_library_scale_batch_plan import (
    LibraryScaleBatchPlanError,
    validate_library_scale_batch_plan,
    write_library_scale_batch_plan,
)
from experiments.skillsbench.bind_empirical_oracle_manifest import (
    build_oracle_bound_manifest_from_files,
)
from experiments.skillsbench.create_library_scale_manifest import write_json_atomic
from experiments.skillsbench.library_scale_progress import (
    LibraryScaleProgressError,
    validate_library_scale_progress,
    write_library_scale_progress,
)
from src.merlin_harness.management import content_sha256


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "experiments" / "skillsbench" / "library-scale-manifest.json"


class LibraryScaleBatchProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan_path = self.root / "batch-plan.json"
        self.plan = write_library_scale_batch_plan(
            output_path=self.plan_path,
            manifest_path=MANIFEST,
        )
        self.cell_root = self.root / "cells"
        self.trace_root = self.root / "traces"
        self.cell_root.mkdir()
        self.trace_root.mkdir()
        self.progress_kwargs = {
            "plan_path": self.plan_path,
            "manifest_path": MANIFEST,
            "cell_root": self.cell_root,
            "trace_root": self.trace_root,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_freezes_all_1305_nested_repeated_cells(self) -> None:
        self.assertEqual(
            self.plan["counts"],
            {
                "scheduled_cells": 1305,
                "task_count": 87,
                "trial_count": 3,
                "arm_count": 5,
                "pending_cells": 1305,
            },
        )
        self.assertEqual(self.plan["cells"][0]["ordinal"], 1)
        self.assertEqual(self.plan["cells"][-1]["ordinal"], 1305)
        first_pair = self.plan["cells"][:5]
        self.assertEqual(len({item["pair_key"] for item in first_pair}), 1)
        self.assertEqual(
            [item["arm_id"] for item in first_pair],
            ["curated", "plus-10", "plus-50", "plus-100", "full-209"],
        )
        self.assertTrue(all(value is False for value in self.plan["claim_boundary"].values()))
        self.assertEqual(
            validate_library_scale_batch_plan(
                plan_path=self.plan_path,
                manifest_path=MANIFEST,
            ),
            self.plan,
        )

    def test_plan_is_new_only_and_rehashed_schedule_drift_fails(self) -> None:
        before = self.plan_path.read_bytes()
        with self.assertRaisesRegex(LibraryScaleBatchPlanError, "new-only"):
            write_library_scale_batch_plan(
                output_path=self.plan_path,
                manifest_path=MANIFEST,
            )
        self.assertEqual(self.plan_path.read_bytes(), before)

        tampered = json.loads(json.dumps(self.plan))
        tampered["cells"][0]["ordinal"] = 2
        tampered.pop("plan_sha256")
        tampered["plan_sha256"] = content_sha256(tampered)
        self.plan_path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(LibraryScaleBatchPlanError, "drifted"):
            validate_library_scale_batch_plan(
                plan_path=self.plan_path,
                manifest_path=MANIFEST,
            )

    def test_oracle_bound_plan_freezes_all_1566_cells(self) -> None:
        from tests.test_library_scale_aggregation_cli import (
            LibraryScaleAggregationCLITests,
        )

        oracle_path = LibraryScaleAggregationCLITests()._oracle_manifest(self.root)
        derived_path = self.root / "library-scale-oracle-bound.json"
        derived = build_oracle_bound_manifest_from_files(
            base_manifest_path=MANIFEST,
            empirical_oracle_path=oracle_path,
            created="2026-07-20",
        )
        write_json_atomic(derived_path, derived)
        plan_path = self.root / "oracle-bound-plan.json"
        plan = write_library_scale_batch_plan(
            output_path=plan_path,
            manifest_path=derived_path,
            base_manifest_path=MANIFEST,
            empirical_oracle_path=oracle_path,
        )
        self.assertEqual(plan["counts"]["scheduled_cells"], 1566)
        self.assertEqual(plan["counts"]["arm_count"], 6)
        self.assertEqual(plan["arm_order"][0], "oracle-only")
        self.assertEqual(len(plan["cells"]), 1566)
        self.assertEqual(
            validate_library_scale_batch_plan(
                plan_path=plan_path,
                manifest_path=derived_path,
                base_manifest_path=MANIFEST,
                empirical_oracle_path=oracle_path,
            ),
            plan,
        )

    def test_empty_evidence_progress_is_exact_and_cannot_claim_results(self) -> None:
        output = self.root / "progress-000000.json"
        snapshot = write_library_scale_progress(
            output_path=output,
            **self.progress_kwargs,
        )
        self.assertEqual(snapshot["status"], "awaiting_evidence")
        self.assertEqual(snapshot["counts"]["sealed_validated_cells"], 0)
        self.assertEqual(snapshot["counts"]["pending_unmaterialized_cells"], 1305)
        self.assertEqual(snapshot["next_pending"]["ordinal"], 1)
        self.assertFalse(snapshot["aggregate_boundary"]["full_denominator_observed"])
        self.assertEqual(snapshot["aggregate_boundary"]["shadowing_status"], "unavailable")
        self.assertTrue(all(value is False for value in snapshot["claim_boundary"].values()))
        self.assertEqual(
            validate_library_scale_progress(
                progress_path=output,
                **self.progress_kwargs,
            ),
            snapshot,
        )

    def test_partial_or_unknown_materialization_fails_safe_resume(self) -> None:
        first = self.plan["cells"][0]
        (self.cell_root / first["cell_id"]).mkdir()
        output = self.root / "partial-progress.json"
        partial = write_library_scale_progress(
            output_path=output,
            **self.progress_kwargs,
        )
        self.assertEqual(partial["status"], "operator_audit_required")
        self.assertEqual(
            partial["counts"]["materialized_without_validated_trace"], 1
        )
        self.assertTrue(partial["next_pending"]["materialization_exists"])
        self.assertTrue(partial["next_pending"]["operator_audit_required_before_retry"])

        (self.cell_root / "not-a-scheduled-cell").mkdir()
        with self.assertRaisesRegex(
            LibraryScaleProgressError, "unscheduled materialization"
        ):
            write_library_scale_progress(
                output_path=self.root / "unsafe-progress.json",
                **self.progress_kwargs,
            )

        (self.cell_root / "not-a-scheduled-cell").rmdir()
        (self.trace_root / "ignored.tmp").write_text("unsafe\n", encoding="utf-8")
        with self.assertRaisesRegex(LibraryScaleProgressError, "trace root.*unsafe"):
            write_library_scale_progress(
                output_path=self.root / "unsafe-trace-progress.json",
                **self.progress_kwargs,
            )

    def test_rehashed_progress_inflation_fails_against_evidence(self) -> None:
        output = self.root / "progress.json"
        snapshot = write_library_scale_progress(
            output_path=output,
            **self.progress_kwargs,
        )
        tampered = json.loads(json.dumps(snapshot))
        tampered["counts"]["sealed_validated_cells"] = 1305
        tampered.pop("snapshot_sha256")
        tampered["snapshot_sha256"] = content_sha256(tampered)
        output.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(LibraryScaleProgressError, "drifted"):
            validate_library_scale_progress(
                progress_path=output,
                **self.progress_kwargs,
            )


if __name__ == "__main__":
    unittest.main()
