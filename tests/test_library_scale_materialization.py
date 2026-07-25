from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.materialize_library_scale_cell import (
    LibraryScaleMaterializationError,
    materialize_library_scale_cell,
    validate_materialized_library_scale_cell,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "experiments" / "skillsbench" / "library-scale-manifest.json"
CELL_ID = "3d-scan-calc__t1__curated"


class LibraryScaleMaterializationTests(unittest.TestCase):
    def test_materializes_exact_new_bundle_with_byte_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "cell"
            contract = materialize_library_scale_cell(
                manifest_path=MANIFEST,
                cell_id=CELL_ID,
                output_root=output,
            )
            self.assertEqual(contract["cell_id"], CELL_ID)
            self.assertEqual(contract["library_size"], 1)
            self.assertEqual(contract["presentation_order"], ["mesh-analysis"])
            self.assertTrue(contract["source_and_staged_bytes_match"])
            self.assertEqual(
                contract["variant_records"][0]["source_tree_sha256"],
                contract["variant_records"][0]["staged_tree_sha256"],
            )
            self.assertTrue((output / "skills" / "mesh-analysis" / "SKILL.md").is_file())
            self.assertTrue((output / "task-visible" / "task.md").is_file())
            self.assertTrue((output / "verifier-hidden" / "test.sh").is_file())
            self.assertEqual(list((output / "task-visible" / "environment" / "skills").iterdir()), [])
            self.assertTrue(contract["task_environment_source_skills_excluded"])
            self.assertFalse(contract["oracle_copied"])
            self.assertEqual(
                json.loads((output / "cell-contract.json").read_text(encoding="utf-8")),
                contract,
            )
            self.assertEqual(
                validate_materialized_library_scale_cell(
                    output, expected_cell_id=CELL_ID
                ),
                contract,
            )
            self.assertFalse(contract["evidence_boundary"]["materialization_is_model_execution"])
            self.assertFalse(contract["evidence_boundary"]["materialization_is_actual_invocation"])

    def test_existing_output_is_preserved_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "cell"
            output.mkdir()
            sentinel = output / "preserve.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(
                LibraryScaleMaterializationError,
                "must not already exist",
            ):
                materialize_library_scale_cell(
                    manifest_path=MANIFEST,
                    cell_id=CELL_ID,
                    output_root=output,
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_tampered_manifest_fails_before_output_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
            tampered = copy.deepcopy(payload)
            tampered["cells"][0]["library_size"] = 999
            manifest = root / "tampered.json"
            manifest.write_text(json.dumps(tampered), encoding="utf-8")
            output = root / "cell"
            with self.assertRaisesRegex(
                LibraryScaleMaterializationError,
                "does not reproduce",
            ):
                materialize_library_scale_cell(
                    manifest_path=manifest,
                    cell_id=CELL_ID,
                    output_root=output,
                )
            self.assertFalse(output.exists())

    def test_hidden_verifier_tamper_fails_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "cell"
            materialize_library_scale_cell(
                manifest_path=MANIFEST,
                cell_id=CELL_ID,
                output_root=output,
            )
            verifier = output / "verifier-hidden" / "test.sh"
            verifier.write_text(verifier.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
            with self.assertRaisesRegex(
                LibraryScaleMaterializationError, "hidden verifier bytes drifted"
            ):
                validate_materialized_library_scale_cell(
                    output, expected_cell_id=CELL_ID
                )


if __name__ == "__main__":
    unittest.main()
