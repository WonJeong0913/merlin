from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.assemble_empirical_oracle_evidence import (
    result_pointer_for_cell,
)
from experiments.skillsbench.create_empirical_oracle_estimation_manifest import (
    build_empirical_oracle_estimation_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file, sha256_json
from experiments.skillsbench.validate_empirical_oracle_pilot import (
    EmpiricalOraclePilotError,
    validate_empirical_oracle_task_pilot,
)


TASK_ID = "earthquake-plate-calculation"


class EmpiricalOraclePilotValidationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, list[dict]]:
        manifest = build_empirical_oracle_estimation_manifest(
            model_id="gpt-5.6-terra",
            backend="test-backend",
            harness_mode="single-skill-explicit-prompt-v1",
            tau=0.1,
            repeats=3,
            created="2026-07-19",
        )
        manifest_path = root / "estimation.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_sha256 = sha256_file(manifest_path)
        cells = [cell for cell in manifest["cells"] if cell["task_id"] == TASK_ID]
        self.assertEqual(len(cells), 6)
        results_root = root / "results"
        for cell in cells:
            raw_pointer = f"raw/{sha256_json(cell['cell_id'])}.jsonl"
            raw_path = results_root / raw_pointer
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(
                json.dumps({"cell_id": cell["cell_id"], "trial_seed": cell["trial_seed"]}) + "\n",
                encoding="utf-8",
            )
            result = {
                "schema_version": 1,
                "estimation_manifest_sha256": manifest_sha256,
                "cell_id": cell["cell_id"],
                "cell_contract_sha256": sha256_json(cell),
                "terminal_status": "scored",
                "reward": 0.0 if cell["condition_kind"] == "no-skill" else 1.0,
                "verifier_contract_sha256": cell["verifier_contract_sha256"],
                "runtime_contract_sha256": cell["runtime_contract_sha256"],
                "condition_application": {
                    "expected_skill_variant_ids": cell["skill_variant_ids"],
                    "prompt_exposed_skill_variant_ids": cell["skill_variant_ids"],
                    "skill_variant_byte_hashes": cell["skill_variant_byte_hashes"],
                    "provider_native_invocation_evidence_complete": False,
                    "provider_native_invoked_skill_variant_ids": None,
                },
                "raw_trace_pointer": raw_pointer,
                "raw_trace_sha256": sha256_file(raw_path),
            }
            result_path = results_root / result_pointer_for_cell(cell["cell_id"])
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(result), encoding="utf-8")
        return manifest_path, results_root, cells

    def test_task_complete_six_cell_pilot_allows_contract_expansion_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, results_root, _cells = self._fixture(root)
            report = validate_empirical_oracle_task_pilot(
                estimation_manifest_path=manifest_path,
                results_root=results_root,
                task_id=TASK_ID,
            )

            self.assertEqual(report["validated_cell_count"], 6)
            self.assertEqual(report["condition_count"], 2)
            self.assertEqual(report["repeats"], 3)
            self.assertEqual(report["no_skill"]["trial_rewards"], [0.0, 0.0, 0.0])
            self.assertEqual(report["candidates"][0]["trial_rewards"], [1.0, 1.0, 1.0])
            self.assertEqual(report["task_local_uplift_set"], ["geospatial-analysis"])
            self.assertTrue(report["expansion_contract_gate"]["accepted"])
            self.assertEqual(
                report["native_invocation_evidence"],
                {
                    "complete_cell_count": 0,
                    "incomplete_cell_count": 6,
                    "all_cells_complete": False,
                },
            )
            self.assertTrue(report["evidence_boundary"]["task_local_pilot_only"])
            self.assertFalse(report["evidence_boundary"]["is_full_87_empirical_oracle"])

    def test_missing_or_reused_pilot_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, results_root, cells = self._fixture(root)
            missing_path = results_root / result_pointer_for_cell(cells[-1]["cell_id"])
            saved = missing_path.read_bytes()
            missing_path.unlink()
            with self.assertRaisesRegex(EmpiricalOraclePilotError, "missing=1"):
                validate_empirical_oracle_task_pilot(
                    estimation_manifest_path=manifest_path,
                    results_root=results_root,
                    task_id=TASK_ID,
                )
            missing_path.write_bytes(saved)

            first_path = results_root / result_pointer_for_cell(cells[0]["cell_id"])
            second_path = results_root / result_pointer_for_cell(cells[1]["cell_id"])
            first = json.loads(first_path.read_text(encoding="utf-8"))
            second = json.loads(second_path.read_text(encoding="utf-8"))
            second["raw_trace_pointer"] = first["raw_trace_pointer"]
            second["raw_trace_sha256"] = first["raw_trace_sha256"]
            second_path.write_text(json.dumps(second), encoding="utf-8")
            with self.assertRaisesRegex(EmpiricalOraclePilotError, "raw trace evidence is reused"):
                validate_empirical_oracle_task_pilot(
                    estimation_manifest_path=manifest_path,
                    results_root=results_root,
                    task_id=TASK_ID,
                )


if __name__ == "__main__":
    unittest.main()
