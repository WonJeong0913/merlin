from __future__ import annotations

import copy
import json
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    DEFAULT_TASKS_ROOT,
    LibraryScaleManifestError,
    build_library_scale_manifest,
    main,
    validate_library_scale_manifest,
)


class LibraryScaleManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = build_library_scale_manifest(created="2026-07-19")

    def test_full87_repeated_nested_schedule_covers_every_cell(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["task_count"], 87)
        self.assertEqual(manifest["skill_pool_count"], 209)
        self.assertEqual(manifest["trial_indices"], [1, 2, 3])
        self.assertEqual(manifest["distractor_counts"], [0, 10, 50, 100, "full"])
        self.assertEqual(manifest["expected_cells"], 87 * 3 * 5)
        self.assertEqual(len(manifest["cells"]), manifest["expected_cells"])
        self.assertEqual(len({cell["cell_id"] for cell in manifest["cells"]}), 1305)

        per_task = Counter(cell["task_id"] for cell in manifest["cells"])
        self.assertEqual(set(per_task.values()), {15})
        task_contracts = {item["task_id"]: item for item in manifest["task_contracts"]}
        self.assertEqual(set(per_task), set(task_contracts))

        grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for cell in manifest["cells"]:
            grouped[(cell["task_id"], cell["trial_index"])].append(cell)
            reference = set(cell["reference_skill_variants"])
            self.assertTrue(reference.issubset(cell["library_variant_ids"]))
            self.assertEqual(len(cell["library_variant_ids"]), cell["library_size"])
            self.assertEqual(len(set(cell["library_variant_ids"])), cell["library_size"])

        arm_order = {"curated": 0, "plus-10": 1, "plus-50": 2, "plus-100": 3, "full-209": 4}
        for cells in grouped.values():
            cells.sort(key=lambda cell: arm_order[cell["arm_id"]])
            previous: set[str] = set()
            for cell in cells:
                current = set(cell["library_variant_ids"])
                self.assertTrue(previous.issubset(current))
                previous = current
            self.assertEqual(cells[-1]["library_size"], 209)

    def test_manifest_is_deterministic_and_exactly_revalidates(self) -> None:
        second = build_library_scale_manifest(created="2026-07-19")
        self.assertEqual(self.manifest, second)
        validate_library_scale_manifest(self.manifest)

    def test_tampered_cell_or_frozen_input_is_rejected(self) -> None:
        for mutate in (
            lambda payload: payload["cells"][1].update(library_size=999),
            lambda payload: payload["frozen_inputs"].update(skills_index_sha256="0" * 64),
            lambda payload: payload["cells"][2]["library_variant_ids"].reverse(),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(self.manifest)
                mutate(tampered)
                with self.assertRaisesRegex(
                    LibraryScaleManifestError,
                    "does not reproduce",
                ):
                    validate_library_scale_manifest(tampered)

    def test_claim_boundary_does_not_promote_curated_or_selected_to_invocation(self) -> None:
        contract = self.manifest["evidence_contract"]
        self.assertFalse(contract["curated_bundle_is_empirical_oracle"])
        self.assertFalse(contract["selected_or_exposed_skill_ids_are_actual_invocations"])
        self.assertTrue(contract["actual_invocation_evidence_required_for_shadowing_metrics"])
        self.assertFalse(contract["headline_shadowing_claim_eligible"])
        self.assertIn("empirical oracle", contract["headline_blocker"])
        self.assertEqual(
            {item["reference_semantics"] for item in self.manifest["task_contracts"]},
            {"upstream_curated_bundle_not_empirical_oracle"},
        )

    def test_cli_writes_and_verifies_without_model_or_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "manifest.json"
            self.assertEqual(main(["--output", str(output)]), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["expected_cells"], 1305)
            self.assertEqual(main(["--verify", str(output)]), 0)

    def test_authoritative_corpus_paths_exist(self) -> None:
        self.assertTrue(DEFAULT_INDEX.is_file())
        self.assertTrue(DEFAULT_TASKS_ROOT.is_dir())
        self.assertTrue(DEFAULT_SKILLS_ROOT.is_dir())


if __name__ == "__main__":
    unittest.main()
