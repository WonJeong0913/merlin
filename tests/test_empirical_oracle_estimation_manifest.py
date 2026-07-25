from __future__ import annotations

import copy
import json
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from experiments.skillsbench.create_empirical_oracle_estimation_manifest import (
    DEFAULT_BASE_MANIFEST,
    EmpiricalOracleEstimationManifestError,
    build_empirical_oracle_estimation_manifest,
    main,
    validate_empirical_oracle_estimation_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file, sha256_json


CREATED = "2026-07-19"
CONTRACT = {
    "model_id": "test-model",
    "backend": "test-backend",
    "harness_mode": "test-harness-v1",
    "tau": 0.1,
}


class EmpiricalOracleEstimationManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = build_empirical_oracle_estimation_manifest(
            created=CREATED,
            **CONTRACT,
        )

    def test_full87_no_skill_and_curated_single_skill_schedule(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], 1)
        self.assertTrue(manifest["schedule_only"])
        self.assertEqual(manifest["library_scale_manifest_sha256"], sha256_file(DEFAULT_BASE_MANIFEST))
        self.assertEqual(manifest["estimation_contract"], {
            **CONTRACT,
            "repeats": 3,
            "candidate_pool_sha256": manifest["frozen_inputs"]["skill_pool_sha256"],
        })
        self.assertEqual(set(manifest["estimation_contract"]), {
            "model_id",
            "backend",
            "harness_mode",
            "tau",
            "repeats",
            "candidate_pool_sha256",
        })
        self.assertEqual(manifest["expected_counts"], {
            "task_count": 87,
            "no_skill_conditions": 87,
            "single_skill_conditions": 232,
            "condition_count": 319,
            "repeats": 3,
            "cell_count": 957,
        })
        self.assertEqual(len(manifest["task_contracts"]), 87)
        self.assertEqual(len(manifest["cells"]), 957)
        self.assertEqual(len({cell["cell_id"] for cell in manifest["cells"]}), 957)

        contract_by_task = {task["task_id"]: task for task in manifest["task_contracts"]}
        self.assertEqual(len(contract_by_task), 87)
        grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for cell in manifest["cells"]:
            self.assertEqual(cell["runtime_contract_sha256"], sha256_json(manifest["estimation_contract"]))
            grouped[(cell["task_id"], cell["trial_index"])].append(cell)
            if cell["condition_kind"] == "no-skill":
                self.assertEqual(cell["skill_variant_ids"], [])
                self.assertEqual(cell["skill_variant_byte_hashes"], [])
            else:
                self.assertEqual(cell["condition_kind"], "single-skill")
                self.assertEqual(len(cell["skill_variant_ids"]), 1)
                self.assertEqual(len(cell["skill_variant_byte_hashes"]), 1)

        self.assertEqual(len(grouped), 87 * 3)
        for (task_id, trial_index), cells in grouped.items():
            task = contract_by_task[task_id]
            self.assertEqual(cells[0]["condition_id"], "no-skill")
            self.assertEqual(
                [cell["skill_variant_ids"] for cell in cells[1:]],
                [[variant] for variant in task["reference_skill_variants"]],
            )
            self.assertTrue(all(cell["trial_seed"] == cells[0]["trial_seed"] for cell in cells))
            self.assertEqual(cells[0]["trial_index"], trial_index)

        self.assertEqual(
            Counter(cell["condition_kind"] for cell in manifest["cells"]),
            {"no-skill": 87 * 3, "single-skill": 232 * 3},
        )

    def test_byte_hashes_and_schedule_only_boundary_are_explicit(self) -> None:
        manifest = self.manifest
        for task in manifest["task_contracts"]:
            self.assertEqual(len(task["task_instruction_sha256"]), 64)
            self.assertEqual(len(task["task_tree_sha256"]), 64)
            self.assertEqual(len(task["verifier_contract_sha256"]), 64)
            self.assertEqual(len(task["verifier_tree_sha256"]), 64)
            self.assertEqual(
                [record["variant"] for record in task["candidate_variant_byte_hashes"]],
                task["reference_skill_variants"],
            )
            self.assertTrue(
                all(len(record["tree_sha256"]) == 64 for record in task["candidate_variant_byte_hashes"])
            )
        boundary = manifest["evidence_contract"]
        self.assertFalse(boundary["actual_model_results_present"])
        self.assertFalse(boundary["actual_invocation_evidence_present"])
        self.assertFalse(boundary["empirical_oracle_membership_estimated"])
        self.assertTrue(boundary["schedule_only_not_result"])

    def test_manifest_recomputes_exactly_and_rejects_tampering(self) -> None:
        duplicate = build_empirical_oracle_estimation_manifest(created=CREATED, **CONTRACT)
        self.assertEqual(self.manifest, duplicate)
        validate_empirical_oracle_estimation_manifest(self.manifest)

        mutations = (
            lambda payload: payload["cells"][0].update(trial_seed=0),
            lambda payload: payload["cells"][1].update(skill_variant_ids=[]),
            lambda payload: payload["frozen_inputs"].update(skill_pool_sha256="0" * 64),
            lambda payload: payload["estimation_contract"].update(candidate_pool_sha256="0" * 64),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(self.manifest)
                mutate(tampered)
                with self.assertRaisesRegex(
                    EmpiricalOracleEstimationManifestError,
                    "does not reproduce",
                ):
                    validate_empirical_oracle_estimation_manifest(tampered)

    def test_tampered_base_manifest_is_rejected_before_schedule_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base_path = Path(temporary) / "library-scale-manifest.json"
            base = json.loads(DEFAULT_BASE_MANIFEST.read_text(encoding="utf-8"))
            base["cells"][0]["library_size"] = 999
            base_path.write_text(json.dumps(base), encoding="utf-8")
            with self.assertRaisesRegex(EmpiricalOracleEstimationManifestError, "does not reproduce"):
                build_empirical_oracle_estimation_manifest(
                    base_manifest_path=base_path,
                    created=CREATED,
                    **CONTRACT,
                )

    def test_cli_writes_then_verifies_without_model_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "schedule.json"
            arguments = [
                "--output",
                str(output),
                "--model-id",
                CONTRACT["model_id"],
                "--backend",
                CONTRACT["backend"],
                "--harness-mode",
                CONTRACT["harness_mode"],
            ]
            self.assertEqual(main(arguments), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["schedule_only"])
            self.assertEqual(payload["expected_counts"]["cell_count"], 957)
            self.assertEqual(main(["--verify", str(output)]), 0)


if __name__ == "__main__":
    unittest.main()
