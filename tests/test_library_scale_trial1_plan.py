from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.create_library_scale_batch_plan import (
    write_library_scale_batch_plan,
)
from experiments.skillsbench.derive_library_scale_trial1_plan import (
    ARM_ORDER,
    EXPECTED_CELLS,
    LibraryScaleTrial1PlanError,
    validate_library_scale_trial1_plan,
    write_library_scale_trial1_plan,
)
from experiments.skillsbench.library_scale_progress import (
    write_library_scale_progress,
)
from src.merlin_harness.management import content_sha256


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "experiments" / "skillsbench" / "library-scale-manifest.json"


class LibraryScaleTrial1PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "source-plan.json"
        self.source = write_library_scale_batch_plan(
            output_path=self.source_path,
            manifest_path=MANIFEST,
        )
        self.derived_path = self.root / "trial1-plan.json"
        self.derived = write_library_scale_trial1_plan(
            output_path=self.derived_path,
            source_plan_path=self.source_path,
            manifest_path=MANIFEST,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_outcome_blind_435_cell_selection_is_frozen(self) -> None:
        self.assertEqual(self.derived["counts"]["scheduled_cells"], EXPECTED_CELLS)
        self.assertEqual(self.derived["counts"]["task_count"], 87)
        self.assertEqual(self.derived["counts"]["trial_count"], 1)
        self.assertEqual(self.derived["arm_order"], list(ARM_ORDER))
        self.assertEqual(
            self.derived["selection_contract"],
            {
                "policy": "all_tasks_trial_1_all_five_arms",
                "trial_indices": [1],
                "arm_order": list(ARM_ORDER),
                "task_selection": "all_87_in_source_order",
                "outcome_fields_read": [],
                "outcome_based_selection_allowed": False,
                "cherry_picking_allowed": False,
            },
        )
        self.assertEqual(
            [cell["source_ordinal"] for cell in self.derived["cells"][:10]],
            [1, 2, 3, 4, 5, 16, 17, 18, 19, 20],
        )
        self.assertEqual(
            [cell["ordinal"] for cell in self.derived["cells"]],
            list(range(1, EXPECTED_CELLS + 1)),
        )
        self.assertTrue(
            all(cell["trial_index"] == 1 for cell in self.derived["cells"])
        )
        self.assertEqual(
            validate_library_scale_trial1_plan(
                plan_path=self.derived_path,
                source_plan_path=self.source_path,
                manifest_path=MANIFEST,
            ),
            self.derived,
        )

    def test_plan_is_new_only_and_tamper_fails_exact_reproduction(self) -> None:
        before = self.derived_path.read_bytes()
        with self.assertRaisesRegex(LibraryScaleTrial1PlanError, "new-only"):
            write_library_scale_trial1_plan(
                output_path=self.derived_path,
                source_plan_path=self.source_path,
                manifest_path=MANIFEST,
            )
        self.assertEqual(self.derived_path.read_bytes(), before)

        tampered = json.loads(json.dumps(self.derived))
        tampered["selection_contract"]["outcome_fields_read"] = ["score"]
        tampered.pop("plan_sha256")
        tampered["plan_sha256"] = content_sha256(tampered)
        self.derived_path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(LibraryScaleTrial1PlanError, "drifted"):
            validate_library_scale_trial1_plan(
                plan_path=self.derived_path,
                source_plan_path=self.source_path,
                manifest_path=MANIFEST,
            )

    def test_source_plan_drift_invalidates_derived_plan(self) -> None:
        source = json.loads(json.dumps(self.source))
        source["cells"][0]["ordinal"] = 99
        source.pop("plan_sha256")
        source["plan_sha256"] = content_sha256(source)
        self.source_path.write_text(json.dumps(source) + "\n", encoding="utf-8")
        with self.assertRaises(LibraryScaleTrial1PlanError):
            validate_library_scale_trial1_plan(
                plan_path=self.derived_path,
                source_plan_path=self.source_path,
                manifest_path=MANIFEST,
            )

    def test_progress_accepts_only_explicitly_bound_derived_plan(self) -> None:
        cells = self.root / "cells"
        traces = self.root / "traces"
        cells.mkdir()
        traces.mkdir()
        progress = write_library_scale_progress(
            output_path=self.root / "progress.json",
            plan_path=self.derived_path,
            source_plan_path=self.source_path,
            manifest_path=MANIFEST,
            cell_root=cells,
            trace_root=traces,
        )
        self.assertEqual(progress["counts"]["scheduled_cells"], EXPECTED_CELLS)
        self.assertEqual(progress["counts"]["pending_unmaterialized_cells"], EXPECTED_CELLS)
        self.assertEqual(progress["next_pending"]["ordinal"], 1)
        self.assertEqual(progress["next_pending"]["cell_id"], self.derived["cells"][0]["cell_id"])


if __name__ == "__main__":
    unittest.main()
