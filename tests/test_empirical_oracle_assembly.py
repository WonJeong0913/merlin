from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.aggregate_library_scale_results import (
    load_empirical_oracle_mapping,
)
from experiments.skillsbench.assemble_empirical_oracle_evidence import (
    EmpiricalOracleAssemblyError,
    assemble_empirical_oracle_evidence,
    result_pointer_for_cell,
)
from experiments.skillsbench.bind_empirical_oracle_manifest import (
    build_oracle_bound_manifest_from_files,
)
from experiments.skillsbench.create_empirical_oracle_estimation_manifest import (
    DEFAULT_BASE_MANIFEST,
    build_empirical_oracle_estimation_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import (
    sha256_file,
    sha256_json,
)


class EmpiricalOracleAssemblyTests(unittest.TestCase):
    def _write_complete_fixture(self, root: Path) -> tuple[Path, Path, dict]:
        estimation = build_empirical_oracle_estimation_manifest(
            model_id="fake-model",
            backend="fake-provider",
            harness_mode="single-skill-explicit-prompt-v1",
            tau=0.1,
            repeats=3,
            created="2026-07-19",
        )
        estimation_path = root / "estimation.json"
        estimation_path.write_text(
            json.dumps(estimation, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        estimation_sha256 = sha256_file(estimation_path)
        results_root = root / "results"
        for cell in estimation["cells"]:
            raw_pointer = f"raw/{sha256_json(cell['cell_id'])}.jsonl"
            raw_path = results_root / raw_pointer
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(
                json.dumps(
                    {
                        "cell_id": cell["cell_id"],
                        "trial_seed": cell["trial_seed"],
                        "condition_id": cell["condition_id"],
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            result = {
                "schema_version": 1,
                "estimation_manifest_sha256": estimation_sha256,
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
            result_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return estimation_path, results_root, estimation

    def test_complete_957_cells_round_trip_into_1566_cell_oracle_bound_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            estimation_path, results_root, estimation = self._write_complete_fixture(root)
            output_root = root / "empirical"
            empirical_path = assemble_empirical_oracle_evidence(
                estimation_manifest_path=estimation_path,
                base_manifest_path=DEFAULT_BASE_MANIFEST,
                results_root=results_root,
                output_root=output_root,
            )

            base_manifest = json.loads(DEFAULT_BASE_MANIFEST.read_text(encoding="utf-8"))
            mapping, contract = load_empirical_oracle_mapping(
                empirical_path,
                manifest=base_manifest,
                manifest_path=DEFAULT_BASE_MANIFEST,
            )
            self.assertEqual(len(mapping), 87)
            self.assertEqual(contract, estimation["estimation_contract"])
            reference = {
                task["task_id"]: tuple(task["reference_skill_variants"])
                for task in estimation["task_contracts"]
            }
            self.assertEqual(mapping, reference)
            self.assertEqual(len(list((output_root / "raw").glob("*.jsonl"))), 957)
            self.assertEqual(len(list((output_root / "tasks").glob("*.json"))), 87)

            derived = build_oracle_bound_manifest_from_files(
                base_manifest_path=DEFAULT_BASE_MANIFEST,
                empirical_oracle_path=empirical_path,
                created="2026-07-19",
            )
            self.assertEqual(derived["expected_cells"], 1566)
            self.assertEqual(len(derived["cells"]), 1566)
            self.assertEqual(derived["arm_count_per_trial"], 6)

            first, second = estimation["cells"][:2]
            first_result_path = results_root / result_pointer_for_cell(first["cell_id"])
            second_result_path = results_root / result_pointer_for_cell(second["cell_id"])
            first_result = json.loads(first_result_path.read_text(encoding="utf-8"))
            second_result = json.loads(second_result_path.read_text(encoding="utf-8"))
            second_result["raw_trace_pointer"] = first_result["raw_trace_pointer"]
            second_result["raw_trace_sha256"] = first_result["raw_trace_sha256"]
            second_result_path.write_text(
                json.dumps(second_result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            rejected_output = root / "reused-evidence"
            with self.assertRaisesRegex(EmpiricalOracleAssemblyError, "raw trace evidence is reused"):
                assemble_empirical_oracle_evidence(
                    estimation_manifest_path=estimation_path,
                    base_manifest_path=DEFAULT_BASE_MANIFEST,
                    results_root=results_root,
                    output_root=rejected_output,
                )
            self.assertFalse(rejected_output.exists())

    def test_missing_result_coverage_fails_before_output_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            estimation = build_empirical_oracle_estimation_manifest(
                model_id="fake-model",
                backend="fake-provider",
                harness_mode="single-skill-explicit-prompt-v1",
                created="2026-07-19",
            )
            estimation_path = root / "estimation.json"
            estimation_path.write_text(json.dumps(estimation), encoding="utf-8")
            results_root = root / "results"
            (results_root / "cells").mkdir(parents=True)
            output_root = root / "empirical"
            with self.assertRaisesRegex(EmpiricalOracleAssemblyError, "missing=957"):
                assemble_empirical_oracle_evidence(
                    estimation_manifest_path=estimation_path,
                    base_manifest_path=DEFAULT_BASE_MANIFEST,
                    results_root=results_root,
                    output_root=output_root,
                )
            self.assertFalse(output_root.exists())


if __name__ == "__main__":
    unittest.main()
