from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.assemble_empirical_oracle_evidence import (
    CONDITION_KEYS,
    RESULT_KEYS,
    result_pointer_for_cell,
)
from experiments.skillsbench.create_empirical_oracle_estimation_manifest import (
    build_empirical_oracle_estimation_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file, sha256_json
from experiments.skillsbench.materialize_empirical_oracle_estimation_cell import (
    materialize_empirical_oracle_estimation_cell,
)
from experiments.skillsbench.record_empirical_oracle_cell_result import (
    EmpiricalOracleResultRecordingError,
    record_empirical_oracle_cell_result,
)


CONTRACT = {
    "model_id": "test-model",
    "backend": "test-backend",
    "harness_mode": "test-harness-v1",
    "tau": 0.1,
}
CREATED = "2026-07-19"


class EmpiricalOracleResultRecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = build_empirical_oracle_estimation_manifest(
            created=CREATED,
            **CONTRACT,
        )
        cls.no_skill_cell = next(
            cell for cell in cls.manifest["cells"] if cell["condition_kind"] == "no-skill"
        )
        cls.single_skill_cell = next(
            cell for cell in cls.manifest["cells"] if cell["condition_kind"] == "single-skill"
        )

    def _prepared_cell(self, root: Path, cell: dict) -> tuple[Path, Path]:
        manifest_path = root / "estimation-manifest.json"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        materialized = root / "materialized"
        materialize_empirical_oracle_estimation_cell(
            manifest_path=manifest_path,
            cell_id=cell["cell_id"],
            output_root=materialized,
        )
        return manifest_path, materialized / "cell-contract.json"

    @staticmethod
    def _write_external_evidence(
        root: Path,
        cell: dict,
        *,
        native_complete: bool,
        native_ids: list[str] | None,
    ) -> tuple[Path, Path]:
        raw = root / "executor.jsonl"
        raw.write_text(
            json.dumps({"cell_id": cell["cell_id"], "executor": "external", "status": "scored"}) + "\n",
            encoding="utf-8",
        )
        condition = root / "condition.json"
        condition.write_text(
            json.dumps(
                {
                    "expected_skill_variant_ids": cell["skill_variant_ids"],
                    "prompt_exposed_skill_variant_ids": cell["skill_variant_ids"],
                    "skill_variant_byte_hashes": cell["skill_variant_byte_hashes"],
                    "provider_native_invocation_evidence_complete": native_complete,
                    "provider_native_invoked_skill_variant_ids": native_ids,
                }
            ),
            encoding="utf-8",
        )
        return raw, condition

    def test_no_skill_result_records_exact_assembler_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cell = self.no_skill_cell
            manifest_path, contract_path = self._prepared_cell(root, cell)
            raw, condition = self._write_external_evidence(
                root,
                cell,
                native_complete=True,
                native_ids=[],
            )
            results_root = root / "results"
            result = record_empirical_oracle_cell_result(
                estimation_manifest_path=manifest_path,
                cell_contract_path=contract_path,
                raw_trace_path=raw,
                reward=0.25,
                condition_evidence_path=condition,
                results_root=results_root,
            )

            result_path = results_root / result_pointer_for_cell(cell["cell_id"])
            raw_path = results_root / f"raw/{sha256_json(cell['cell_id'])}.jsonl"
            self.assertEqual(set(result), RESULT_KEYS)
            self.assertEqual(set(result["condition_application"]), CONDITION_KEYS)
            self.assertEqual(result["reward"], 0.25)
            self.assertEqual(result["estimation_manifest_sha256"], sha256_file(manifest_path))
            self.assertEqual(result["cell_contract_sha256"], sha256_json(cell))
            self.assertEqual(result["raw_trace_pointer"], raw_path.relative_to(results_root).as_posix())
            self.assertEqual(result["raw_trace_sha256"], sha256_file(raw_path))
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), result)

    def test_single_skill_incomplete_native_evidence_stays_honest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cell = self.single_skill_cell
            manifest_path, contract_path = self._prepared_cell(root, cell)
            raw, condition = self._write_external_evidence(
                root,
                cell,
                native_complete=False,
                native_ids=None,
            )
            result = record_empirical_oracle_cell_result(
                estimation_manifest_path=manifest_path,
                cell_contract_path=contract_path,
                raw_trace_path=raw,
                reward=1,
                condition_evidence_path=condition,
                results_root=root / "results",
            )
            application = result["condition_application"]
            self.assertFalse(application["provider_native_invocation_evidence_complete"])
            self.assertIsNone(application["provider_native_invoked_skill_variant_ids"])
            self.assertEqual(application["expected_skill_variant_ids"], cell["skill_variant_ids"])

    def test_tampered_condition_or_cell_contract_is_rejected_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cell = self.single_skill_cell
            manifest_path, contract_path = self._prepared_cell(root, cell)
            raw, condition = self._write_external_evidence(
                root,
                cell,
                native_complete=True,
                native_ids=cell["skill_variant_ids"],
            )
            tampered = json.loads(condition.read_text(encoding="utf-8"))
            tampered["prompt_exposed_skill_variant_ids"] = []
            condition.write_text(json.dumps(tampered), encoding="utf-8")
            results_root = root / "results"
            with self.assertRaisesRegex(EmpiricalOracleResultRecordingError, "prompt exposure"):
                record_empirical_oracle_cell_result(
                    estimation_manifest_path=manifest_path,
                    cell_contract_path=contract_path,
                    raw_trace_path=raw,
                    reward=1.0,
                    condition_evidence_path=condition,
                    results_root=results_root,
                )
            self.assertFalse(results_root.exists())

            clean_raw, clean_condition = self._write_external_evidence(
                root,
                cell,
                native_complete=True,
                native_ids=cell["skill_variant_ids"],
            )
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["runtime_contract_sha256"] = "0" * 64
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(EmpiricalOracleResultRecordingError, "runtime_contract_sha256"):
                record_empirical_oracle_cell_result(
                    estimation_manifest_path=manifest_path,
                    cell_contract_path=contract_path,
                    raw_trace_path=clean_raw,
                    reward=1.0,
                    condition_evidence_path=clean_condition,
                    results_root=results_root,
                )
            self.assertFalse(results_root.exists())

    def test_existing_result_and_raw_destinations_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cell = self.no_skill_cell
            manifest_path, contract_path = self._prepared_cell(root, cell)
            raw, condition = self._write_external_evidence(
                root,
                cell,
                native_complete=True,
                native_ids=[],
            )
            results_root = root / "results"
            first = record_empirical_oracle_cell_result(
                estimation_manifest_path=manifest_path,
                cell_contract_path=contract_path,
                raw_trace_path=raw,
                reward=0.0,
                condition_evidence_path=condition,
                results_root=results_root,
            )
            result_path = results_root / result_pointer_for_cell(cell["cell_id"])
            raw_path = results_root / first["raw_trace_pointer"]
            before_result = result_path.read_bytes()
            before_raw = raw_path.read_bytes()
            with self.assertRaisesRegex(EmpiricalOracleResultRecordingError, "destination already exists"):
                record_empirical_oracle_cell_result(
                    estimation_manifest_path=manifest_path,
                    cell_contract_path=contract_path,
                    raw_trace_path=raw,
                    reward=1.0,
                    condition_evidence_path=condition,
                    results_root=results_root,
                )
            self.assertEqual(result_path.read_bytes(), before_result)
            self.assertEqual(raw_path.read_bytes(), before_raw)


if __name__ == "__main__":
    unittest.main()
