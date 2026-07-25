from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.skillsbench.run_library_scale_trial1_supervisor import (
    LibraryScaleSupervisorError,
    build_trial1_first_cell_admission,
    build_trial1_canary_admission,
    supervise_library_scale_trial1,
    validate_trial1_canary_admission,
    validate_trial1_first_cell_admission,
    write_trial1_canary_admission,
    write_trial1_first_cell_admission,
)
from src.merlin_harness.management import content_sha256


def _plan() -> dict:
    arms = ["curated", "plus-10", "plus-50", "plus-100", "full-209"]
    cells = []
    for ordinal in range(1, 436):
        task_number = (ordinal - 1) // 5
        arm = arms[(ordinal - 1) % 5]
        cells.append(
            {
                "ordinal": ordinal,
                "cell_id": f"cell-{ordinal:04d}",
                "task_id": f"task-{task_number:02d}",
                "trial_index": 1,
                "arm_id": arm,
            }
        )
    value = {"batch_id": "batch", "plan_sha256": "a" * 64, "cells": cells}
    return value


def _progress(sealed: int, *, partial: int | None = None) -> dict:
    cells = []
    for ordinal in range(1, 436):
        if ordinal <= sealed:
            status = "sealed_validated_trace"
            trace_hash = f"{ordinal:064x}"[-64:]
        elif partial == ordinal:
            status = "materialized_without_validated_trace"
            trace_hash = None
        else:
            status = "pending"
            trace_hash = None
        cells.append(
            {
                "ordinal": ordinal,
                "cell_id": f"cell-{ordinal:04d}",
                "work_key": f"work-{ordinal:04d}",
                "status": status,
                "trace_file_sha256": trace_hash,
            }
        )
    next_pending = None
    if sealed < 435:
        ordinal = partial or sealed + 1
        next_pending = {
            "ordinal": ordinal,
            "cell_id": f"cell-{ordinal:04d}",
            "work_key": f"work-{ordinal:04d}",
            "materialization_exists": partial is not None,
            "operator_audit_required_before_retry": partial is not None,
        }
    return {
        "snapshot_sha256": "b" * 64,
        "counts": {"sealed_validated_cells": sealed},
        "next_pending": next_pending,
        "cells": cells,
    }


class LibraryScaleTrial1SupervisorTests(unittest.TestCase):
    def test_canary_report_binds_exact_first_task_five_arms(self) -> None:
        report = build_trial1_canary_admission(plan=_plan(), progress=_progress(5))
        self.assertTrue(report["canary_passed"])
        self.assertEqual([row["arm_id"] for row in report["records"]], [
            "curated", "plus-10", "plus-50", "plus-100", "full-209"
        ])
        self.assertEqual(len({row["task_id"] for row in report["records"]}), 1)
        self.assertFalse(report["claim_boundary"]["canary_is_435_completion"])

    def test_first_cell_report_opens_only_remaining_canary_cells(self) -> None:
        report = build_trial1_first_cell_admission(
            plan=_plan(), progress=_progress(1)
        )
        self.assertTrue(report["additional_canary_cells_allowed"])
        self.assertEqual(report["first_cell"]["ordinal"], 1)
        self.assertFalse(report["claim_boundary"]["first_cell_is_five_cell_canary"])

    def test_canary_report_is_new_only_and_trace_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "canary.json"
            plan = _plan()
            progress = _progress(5)
            write_trial1_canary_admission(path=path, plan=plan, progress=progress)
            self.assertEqual(
                validate_trial1_canary_admission(
                    path=path, plan=plan, progress=progress
                )["canary_passed"],
                True,
            )
            with self.assertRaisesRegex(LibraryScaleSupervisorError, "new-only"):
                write_trial1_canary_admission(path=path, plan=plan, progress=progress)
            drifted = copy.deepcopy(progress)
            drifted["cells"][0]["trace_file_sha256"] = "f" * 64
            with self.assertRaisesRegex(LibraryScaleSupervisorError, "drifted"):
                validate_trial1_canary_admission(
                    path=path, plan=plan, progress=drifted
                )

    def test_partial_or_out_of_order_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(LibraryScaleSupervisorError, "operator audit"):
            build_trial1_canary_admission(plan=_plan(), progress=_progress(0, partial=1))
        out_of_order = _progress(0)
        out_of_order["cells"][1]["status"] = "sealed_validated_trace"
        out_of_order["cells"][1]["trace_file_sha256"] = "c" * 64
        out_of_order["counts"]["sealed_validated_cells"] = 1
        with self.assertRaisesRegex(LibraryScaleSupervisorError, "prefix"):
            build_trial1_canary_admission(plan=_plan(), progress=out_of_order)

    @patch(
        "experiments.skillsbench.run_library_scale_trial1_supervisor."
        "validate_library_scale_trial1_plan"
    )
    @patch(
        "experiments.skillsbench.run_library_scale_trial1_supervisor."
        "_save_progress_snapshot"
    )
    @patch(
        "experiments.skillsbench.run_library_scale_trial1_supervisor."
        "build_library_scale_progress"
    )
    def test_canary_advances_exactly_five_and_full_requires_admission(
        self, build_progress, _save_progress, validate_plan
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = _plan()
            validate_plan.return_value = plan
            states = [_progress(index) for index in range(6)]
            build_progress.side_effect = states

            class Result:
                returncode = 0
                stdout = "ok"
                stderr = ""

            canary = root / "canary.json"
            first_cell = root / "first-cell.json"
            report = supervise_library_scale_trial1(
                phase="canary",
                plan_path=root / "plan.json",
                source_plan_path=root / "source.json",
                manifest_path=root / "manifest.json",
                cell_root=root / "cells",
                trace_root=root / "traces",
                progress_root=root / "progress",
                first_cell_admission_path=first_cell,
                canary_admission_path=canary,
                executor_argv=["executor"],
                runner=lambda *args, **kwargs: Result(),
            )
            self.assertEqual(report["executed_cells"], 5)
            self.assertEqual(report["sealed_after"], 5)
            self.assertTrue(report["phase_complete"])
            self.assertTrue(canary.is_file())
            self.assertTrue(first_cell.is_file())

            build_progress.side_effect = [_progress(5), _progress(6)]
            full = supervise_library_scale_trial1(
                phase="full",
                plan_path=root / "plan.json",
                source_plan_path=root / "source.json",
                manifest_path=root / "manifest.json",
                cell_root=root / "cells",
                trace_root=root / "traces",
                progress_root=root / "progress",
                first_cell_admission_path=first_cell,
                canary_admission_path=canary,
                executor_argv=["executor"],
                max_cells=1,
                runner=lambda *args, **kwargs: Result(),
            )
            self.assertEqual(full["executed_cells"], 1)
            self.assertEqual(full["sealed_after"], 6)
            self.assertFalse(full["phase_complete"])

    @patch(
        "experiments.skillsbench.run_library_scale_trial1_supervisor."
        "validate_library_scale_trial1_plan"
    )
    @patch(
        "experiments.skillsbench.run_library_scale_trial1_supervisor."
        "_save_progress_snapshot"
    )
    @patch(
        "experiments.skillsbench.run_library_scale_trial1_supervisor."
        "build_library_scale_progress"
    )
    def test_zero_exit_without_exact_evidence_increment_fails_closed(
        self, build_progress, _save_progress, validate_plan
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validate_plan.return_value = _plan()
            build_progress.side_effect = [_progress(0), _progress(0)]

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            with self.assertRaisesRegex(LibraryScaleSupervisorError, "exactly one"):
                supervise_library_scale_trial1(
                    phase="canary",
                    plan_path=root / "plan.json",
                    source_plan_path=root / "source.json",
                    manifest_path=root / "manifest.json",
                    cell_root=root / "cells",
                    trace_root=root / "traces",
                    progress_root=root / "progress",
                    first_cell_admission_path=root / "first-cell.json",
                    canary_admission_path=root / "canary.json",
                    executor_argv=["executor"],
                    max_cells=1,
                    runner=lambda *args, **kwargs: Result(),
                )


if __name__ == "__main__":
    unittest.main()
