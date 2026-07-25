from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.assemble_empirical_oracle_evidence import result_pointer_for_cell
from experiments.skillsbench.create_empirical_oracle_estimation_manifest import (
    DEFAULT_BASE_MANIFEST,
    build_empirical_oracle_estimation_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file, sha256_json, tree_sha256
from experiments.skillsbench.materialize_empirical_oracle_estimation_cell import (
    EmpiricalOracleEstimationMaterializationError,
    materialize_empirical_oracle_estimation_cell,
)


CONTRACT = {
    "model_id": "test-model",
    "backend": "test-backend",
    "harness_mode": "test-harness-v1",
    "tau": 0.1,
}
CREATED = "2026-07-19"


class EmpiricalOracleEstimationMaterializationTests(unittest.TestCase):
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

    def _write_manifest(self, root: Path, manifest: dict | None = None) -> Path:
        path = root / "estimation-manifest.json"
        path.write_text(
            json.dumps(self.manifest if manifest is None else manifest),
            encoding="utf-8",
        )
        return path

    def test_no_skill_cell_materializes_an_empty_skills_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._write_manifest(root)
            output = root / "no-skill-cell"
            contract = materialize_empirical_oracle_estimation_cell(
                manifest_path=manifest_path,
                cell_id=self.no_skill_cell["cell_id"],
                output_root=output,
            )

            self.assertTrue((output / "skills").is_dir())
            self.assertEqual(list((output / "skills").iterdir()), [])
            self.assertEqual(contract["skill_variant_ids"], [])
            self.assertEqual(contract["variant_records"], [])
            self.assertEqual(contract["execution_status"], "not_run")
            self.assertEqual(contract["expected_result_pointer"], result_pointer_for_cell(contract["cell_id"]))
            self.assertTrue(contract["source_cell_staged_bytes_match"])
            self.assertEqual(contract["estimation_manifest_sha256"], sha256_file(manifest_path))
            self.assertEqual(contract["estimation_cell_sha256"], sha256_json(self.no_skill_cell))
            self.assertEqual(contract["cell_contract_sha256"], sha256_json(self.no_skill_cell))
            self.assertEqual(
                json.loads((output / "cell-contract.json").read_text(encoding="utf-8")),
                contract,
            )

    def test_single_skill_cell_copies_only_its_verified_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._write_manifest(root)
            output = root / "single-skill-cell"
            cell = self.single_skill_cell
            contract = materialize_empirical_oracle_estimation_cell(
                manifest_path=manifest_path,
                cell_id=cell["cell_id"],
                output_root=output,
            )

            variant = cell["skill_variant_ids"][0]
            staged = output / "skills" / variant
            self.assertEqual([path.name for path in (output / "skills").iterdir()], [variant])
            self.assertTrue((staged / "SKILL.md").is_file())
            self.assertEqual(len(contract["variant_records"]), 1)
            record = contract["variant_records"][0]
            self.assertEqual(record["variant"], variant)
            self.assertEqual(record["expected_cell_tree_sha256"], cell["skill_variant_byte_hashes"][0]["tree_sha256"])
            self.assertEqual(record["source_tree_sha256"], tree_sha256(staged))
            self.assertEqual(record["staged_tree_sha256"], tree_sha256(staged))
            self.assertEqual(contract["trial_index"], cell["trial_index"])
            self.assertEqual(contract["trial_seed"], cell["trial_seed"])
            self.assertEqual(contract["runtime_contract_sha256"], cell["runtime_contract_sha256"])
            self.assertEqual(contract["source_task_tree_sha256"], cell["task_tree_sha256"])
            self.assertEqual(contract["source_verifier_tree_sha256"], cell["verifier_tree_sha256"])

    def test_tampered_manifest_is_rejected_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tampered = copy.deepcopy(self.manifest)
            tampered["cells"][0]["trial_seed"] = 0
            manifest_path = self._write_manifest(root, tampered)
            output = root / "must-not-exist"
            with self.assertRaisesRegex(
                EmpiricalOracleEstimationMaterializationError,
                "does not reproduce",
            ):
                materialize_empirical_oracle_estimation_cell(
                    manifest_path=manifest_path,
                    cell_id=self.no_skill_cell["cell_id"],
                    output_root=output,
                )
            self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._write_manifest(root)
            output = root / "existing-output"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(
                EmpiricalOracleEstimationMaterializationError,
                "must not already exist",
            ):
                materialize_empirical_oracle_estimation_cell(
                    manifest_path=manifest_path,
                    cell_id=self.single_skill_cell["cell_id"],
                    output_root=output,
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
