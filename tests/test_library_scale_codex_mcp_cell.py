from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.skillsbench.materialize_library_scale_cell import (
    materialize_library_scale_cell,
)
from experiments.skillsbench.run_library_scale_codex_mcp_cell import (
    LibraryScaleCodexCellError,
    _plan_cell,
    build_library_scale_trace,
    derive_metadata_first_provisioning,
    validate_first_cell_expansion_gate,
)
from src.merlin_harness.library_scale_results import (
    LibraryScaleResultError,
    validate_library_scale_cell_trace,
)
from src.merlin_harness.traces import FileTraceStore


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "experiments" / "skillsbench" / "library-scale-manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
CELL = next(
    cell
    for cell in MANIFEST["cells"]
    if cell["task_id"] == "3d-scan-calc"
    and cell["trial_index"] == 1
    and cell["arm_id"] == "plus-10"
)


class LibraryScaleCodexMcpCellTests(unittest.TestCase):
    def _materialized(self, root: Path) -> tuple[dict, dict]:
        materialized = materialize_library_scale_cell(
            manifest_path=MANIFEST_PATH,
            cell_id=CELL["cell_id"],
            output_root=root / CELL["cell_id"],
        )
        provisioning = derive_metadata_first_provisioning(
            cell_root=root / CELL["cell_id"],
            contract=materialized,
            exposure_budget=3,
        )
        return materialized, provisioning

    def test_governed_metadata_first_provisioning_is_bounded_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialized, provisioning = self._materialized(root)
            provisioned = provisioning["provisioned_skill_ids"]
            self.assertLessEqual(len(provisioned), 3)
            self.assertTrue(set(provisioned).issubset(materialized["presentation_order"]))
            self.assertEqual(provisioning["candidate_library_size"], 11)
            self.assertTrue(
                provisioning["boundary"]["candidate_library_is_not_prompt_body_exposure"]
            )
            self.assertFalse(
                provisioning["boundary"]["provider_native_skill_invocation_claimed"]
            )

    def test_normalized_trace_separates_candidate_exposure_and_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialized, provisioning = self._materialized(root)
            raw = root / "raw"
            raw.mkdir()
            (raw / "empty-workspace").mkdir()
            trace_path = raw / "codex.jsonl"
            trace_path.write_text('{"type":"turn.completed"}\n', encoding="utf-8")
            runtime = {
                "contract_sha256": "b" * 64,
                "requested_model_contract": {
                    "backend": "codex-cli-fixed-container-mcp",
                    "model_id": "gpt-5.6-terra",
                    "effort": "high",
                },
                "harness_contract": {"mode": "metadata-first-staged-body-v1"},
            }
            invoked = provisioning["provisioned_skill_ids"][:1]
            trace = build_library_scale_trace(
                cell=CELL,
                materialized=materialized,
                runtime_contract=runtime,
                raw_root=raw,
                raw_trace_path=trace_path,
                provisioning=provisioning,
                invoked_skill_ids=invoked,
                verifier_passed=True,
                reward=1.0,
                staged_verifier_tree_sha256=materialized["hidden_verifier"][
                    "records_sha256"
                ],
                wall_time_sec=1.5,
                provider_reported_model_ids=[],
            )
            self.assertEqual(
                trace.invocation.provisioned_skill_ids,
                provisioning["provisioned_skill_ids"],
            )
            self.assertEqual(
                len(trace.metadata["agent_run_evidence"]["invocation_events"]),
                len(invoked),
            )
            stored = FileTraceStore(root / "traces").save_immutable(trace)
            self.assertTrue(stored.is_file())
            validated = validate_library_scale_cell_trace(
                manifest_cell=CELL,
                materialization_contract=materialized,
                trace=trace,
            )
            self.assertEqual(validated.invoked_skill_ids, tuple(invoked))
            self.assertLessEqual(len(validated.selected_skill_ids), 3)

            tampered = copy.deepcopy(trace)
            tampered.metadata["candidate_library_order_sha256"] = "f" * 64
            with self.assertRaisesRegex(LibraryScaleResultError, "order drifted"):
                validate_library_scale_cell_trace(
                    manifest_cell=CELL,
                    materialization_contract=materialized,
                    trace=tampered,
                )

    def test_plan_cell_must_resolve_once(self) -> None:
        plan = {"cells": [{"cell_id": "one"}]}
        self.assertEqual(_plan_cell(plan, "one")["cell_id"], "one")
        with self.assertRaisesRegex(LibraryScaleCodexCellError, "exactly once"):
            _plan_cell(plan, "missing")

    def test_first_cell_gate_requires_new_admission_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kwargs = {
                "planned": {"ordinal": 1, "cell_id": "cell-0001"},
                "first_cell_admission_path": root / "first-cell.json",
                "plan": {"cells": []},
                "plan_path": root / "plan.json",
                "source_plan_path": root / "source.json",
                "manifest_path": root / "manifest.json",
                "cell_root": root / "cells",
                "trace_root": root / "traces",
                "index_path": root / "index.json",
                "skills_root": root / "skills",
            }
            validate_first_cell_expansion_gate(**kwargs)
            kwargs["first_cell_admission_path"].write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                LibraryScaleCodexCellError, "requires a new"
            ):
                validate_first_cell_expansion_gate(**kwargs)

    @patch(
        "experiments.skillsbench.run_library_scale_codex_mcp_cell."
        "validate_trial1_first_cell_admission"
    )
    @patch(
        "experiments.skillsbench.run_library_scale_codex_mcp_cell."
        "build_library_scale_progress"
    )
    def test_later_cell_gate_requires_exact_sealed_frontier(
        self, build_progress, validate_admission
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            planned = {"ordinal": 2, "cell_id": "cell-0002"}
            build_progress.return_value = {
                "counts": {"sealed_validated_cells": 1},
                "next_pending": {"ordinal": 2, "cell_id": "cell-0002"},
            }
            validate_first_cell_expansion_gate(
                planned=planned,
                first_cell_admission_path=root / "first-cell.json",
                plan={"cells": []},
                plan_path=root / "plan.json",
                source_plan_path=root / "source.json",
                manifest_path=root / "manifest.json",
                cell_root=root / "cells",
                trace_root=root / "traces",
                index_path=root / "index.json",
                skills_root=root / "skills",
            )
            validate_admission.assert_called_once()
            build_progress.return_value["counts"]["sealed_validated_cells"] = 0
            with self.assertRaisesRegex(
                LibraryScaleCodexCellError, "exact next ordinal"
            ):
                validate_first_cell_expansion_gate(
                    planned=planned,
                    first_cell_admission_path=root / "first-cell.json",
                    plan={"cells": []},
                    plan_path=root / "plan.json",
                    source_plan_path=root / "source.json",
                    manifest_path=root / "manifest.json",
                    cell_root=root / "cells",
                    trace_root=root / "traces",
                    index_path=root / "index.json",
                    skills_root=root / "skills",
                )


if __name__ == "__main__":
    unittest.main()
