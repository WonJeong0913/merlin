from __future__ import annotations

import copy
import json
import unittest
from collections import defaultdict
from pathlib import Path

from experiments.skillsbench.bind_empirical_oracle_manifest import (
    OracleBoundManifestError,
    build_oracle_bound_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file, sha256_json


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_MANIFEST_PATH = REPO_ROOT / "experiments" / "skillsbench" / "library-scale-manifest.json"
INDEX_PATH = REPO_ROOT / "experiments" / "skillsbench" / "skills-index.json"
SKILLS_ROOT = REPO_ROOT / "experiments" / "skillsbench" / "skills"
CREATED = "2026-07-19"


class OracleBoundLibraryScaleManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_manifest = json.loads(BASE_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.mapping = {
            task["task_id"]: tuple(task["reference_skill_variants"][:1])
            for task in cls.base_manifest["task_contracts"]
        }
        cls.oracle_contract = {
            "backend": "pure-mapping-test",
            "model_id": "not-run",
            "repeats": 3,
            "tau": 0.1,
        }

    @classmethod
    def _build(cls, *, mapping: dict[str, tuple[str, ...]] | None = None) -> dict:
        return build_oracle_bound_manifest(
            base_manifest=cls.base_manifest,
            empirical_oracle_by_task=cls.mapping if mapping is None else mapping,
            oracle_estimation_contract=cls.oracle_contract,
            base_manifest_sha256=sha256_file(BASE_MANIFEST_PATH),
            empirical_oracle_manifest_sha256="a" * 64,
            index_path=INDEX_PATH,
            skills_root=SKILLS_ROOT,
            created=CREATED,
        )

    def test_pure_mapping_derives_full87_six_arm_nested_schedule(self) -> None:
        derived = self._build()

        self.assertEqual(derived["schema_version"], 2)
        self.assertEqual(derived["task_count"], 87)
        self.assertEqual(derived["trial_indices"], [1, 2, 3])
        self.assertEqual(derived["arm_count_per_trial"], 6)
        self.assertEqual(derived["expected_cells"], 87 * 3 * 6)
        self.assertEqual(len(derived["cells"]), 1566)
        self.assertEqual(len({cell["cell_id"] for cell in derived["cells"]}), 1566)
        self.assertTrue(derived["evidence_contract"]["empirical_oracle_bound"])
        self.assertFalse(derived["evidence_contract"]["headline_shadowing_claim_eligible"])

        grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for cell in derived["cells"]:
            grouped[(cell["task_id"], cell["trial_index"])].append(cell)

        expected_arms = ["oracle-only", "curated", "plus-10", "plus-50", "plus-100", "full-209"]
        self.assertEqual(len(grouped), 87 * 3)
        for (task_id, _trial_index), cells in grouped.items():
            self.assertEqual([cell["arm_id"] for cell in cells], expected_arms)
            by_arm = {cell["arm_id"]: cell for cell in cells}
            oracle = by_arm["oracle-only"]
            curated = by_arm["curated"]
            expected_oracle = self.mapping[task_id]

            self.assertEqual(oracle["empirical_oracle_skill_variants"], list(expected_oracle))
            self.assertEqual(
                oracle["library_variant_ids"],
                [variant for variant in curated["library_variant_ids"] if variant in set(expected_oracle)],
            )
            self.assertEqual(oracle["library_size"], len(expected_oracle))
            self.assertEqual(oracle["actual_distractor_count"], 0)
            self.assertTrue(set(oracle["library_variant_ids"]).issubset(curated["library_variant_ids"]))
            self.assertTrue(
                set(oracle["library_variant_ids"]).issubset(
                    set(curated["reference_skill_variants"])
                )
            )
            for cell in cells[1:]:
                self.assertEqual(
                    cell["empirical_oracle_skill_variants"], list(expected_oracle)
                )

    def test_derivation_is_deterministic_ordered_and_leaves_base_unchanged(self) -> None:
        original = copy.deepcopy(self.base_manifest)
        first = self._build()
        second = self._build()

        self.assertEqual(first, second)
        self.assertEqual(sha256_json(first), sha256_json(second))
        self.assertEqual(self.base_manifest, original)
        self.assertTrue(
            all(
                "empirical_oracle_skill_variants" not in task
                for task in self.base_manifest["task_contracts"]
            )
        )
        self.assertTrue(
            all(
                "empirical_oracle_skill_variants" not in cell
                for cell in self.base_manifest["cells"]
            )
        )

        expected_cell_ids = [
            f"{task_id}__t{trial_index}__{arm_id}"
            for task_id in sorted(self.mapping)
            for trial_index in (1, 2, 3)
            for arm_id in ("oracle-only", "curated", "plus-10", "plus-50", "plus-100", "full-209")
        ]
        self.assertEqual([cell["cell_id"] for cell in first["cells"]], expected_cell_ids)
        self.assertEqual(
            first["base_manifest"],
            {
                "experiment_id": self.base_manifest["experiment_id"],
                "sha256": sha256_file(BASE_MANIFEST_PATH),
            },
        )

    def test_invalid_mapping_fails_closed_before_any_execution(self) -> None:
        task_id = next(iter(self.mapping))
        cases = {
            "missing-task": {
                mapped_task: oracle
                for mapped_task, oracle in self.mapping.items()
                if mapped_task != task_id
            },
            "duplicate-skill": {
                **self.mapping,
                task_id: (self.mapping[task_id][0], self.mapping[task_id][0]),
            },
            "outside-curated-scope": {
                **self.mapping,
                task_id: ("not-a-curated-skill",),
            },
        }
        for label, invalid_mapping in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(OracleBoundManifestError):
                    self._build(mapping=invalid_mapping)

    def test_empty_empirical_oracle_is_a_valid_no_skill_reference_arm(self) -> None:
        task_id = next(iter(self.mapping))
        mapping = {**self.mapping, task_id: ()}

        derived = self._build(mapping=mapping)
        oracle_cells = [
            cell
            for cell in derived["cells"]
            if cell["task_id"] == task_id and cell["arm_id"] == "oracle-only"
        ]

        self.assertEqual(len(oracle_cells), 3)
        self.assertTrue(
            all(
                cell["library_variant_ids"] == []
                and cell["library_size"] == 0
                and cell["empirical_oracle_skill_variants"] == []
                for cell in oracle_cells
            )
        )


if __name__ == "__main__":
    unittest.main()
