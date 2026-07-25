from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    bind_manifest,
    validate_bound_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_json
from experiments.skillsbench.create_m3k_evaluation_manifest import build_manifest
from experiments.skillsbench.m3k_policy_proposal import (
    EVIDENCE_SOURCE_PATH,
    build_canonical_bundle,
)
from src.merlin_harness.management import content_sha256


SPLIT = Path("experiments/skillsbench/split-manifest.json")
SCALE = Path("experiments/skillsbench/library-scale-manifest.json")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _proposal_bundle() -> dict:
    path = Path(EVIDENCE_SOURCE_PATH)
    return build_canonical_bundle(
        json.loads(path.read_text(encoding="utf-8")),
        evidence_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


class M3KLibraryBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schedule = build_manifest(split_manifest=SPLIT, library_scale_manifest=SCALE)
        cls.library_scale_manifest = json.loads(SCALE.read_text(encoding="utf-8"))
        cls.library_scale_file_sha256 = hashlib.sha256(SCALE.read_bytes()).hexdigest()

    def _bound_manifest(self) -> dict:
        return bind_manifest(
            schedule=copy.deepcopy(self.schedule),
            schedule_file_sha256=_sha("synthetic-m3k-schedule-file"),
            library_scale_manifest=copy.deepcopy(self.library_scale_manifest),
            library_scale_file_sha256=self.library_scale_file_sha256,
            bundle=_proposal_bundle(),
            bundle_file_sha256=_sha("synthetic-m3k-proposal-file"),
            capability=None,
            capability_file_sha256=None,
        )

    @staticmethod
    def _rehash(payload: dict) -> None:
        unhashed = dict(payload)
        unhashed.pop("manifest_sha256", None)
        payload["manifest_sha256"] = content_sha256(unhashed)

    def test_all_522_trajectories_bind_exactly_to_the_matching_full_209_cell(self) -> None:
        bound = self._bound_manifest()
        binding = bound["library_binding"]
        self.assertEqual(binding["arm_id"], "full-209")
        self.assertEqual(binding["source_manifest_file_sha256"], self.library_scale_file_sha256)
        self.assertEqual(binding["source_manifest_semantic_sha256"], sha256_json(self.library_scale_manifest))
        self.assertEqual(
            binding["counts"],
            {
                "source_task_count": 87,
                "source_expected_cell_count": 1305,
                "source_skill_pool_count": 209,
                "full_arm_cell_count": 261,
                "paired_trajectory_count": 522,
            },
        )

        source = {
            (cell["task_id"], cell["trial_index"]): cell
            for cell in self.library_scale_manifest["cells"]
            if cell["arm_id"] == "full-209"
        }
        self.assertEqual(len(source), 261)
        self.assertEqual(len(bound["paired_cells"]), 522)
        pairs: dict[tuple[str, int], list[dict]] = {}
        for trajectory in bound["paired_cells"]:
            key = (trajectory["task_id"], trajectory["trial_index"])
            expected = source[key]
            self.assertEqual(trajectory["library_arm_id"], "full-209")
            self.assertEqual(trajectory["library_size"], 209)
            self.assertEqual(trajectory["library_snapshot_sha256"], expected["library_snapshot_sha256"])
            self.assertEqual(trajectory["library_variant_ids"], expected["library_variant_ids"])
            self.assertEqual(trajectory["library_order_sha256"], sha256_json(expected["library_variant_ids"]))
            self.assertEqual(trajectory["library_trial_seed"], expected["trial_seed"])
            pairs.setdefault(key, []).append(trajectory)
        self.assertEqual({len(value) for value in pairs.values()}, {2})
        for trajectories in pairs.values():
            self.assertEqual(
                {
                    key: value
                    for key, value in trajectories[0].items()
                    if key.startswith("library_")
                },
                {
                    key: value
                    for key, value in trajectories[1].items()
                    if key.startswith("library_")
                },
            )
        validate_bound_manifest(bound)

    def test_noncanonical_source_payload_or_file_hash_fails_before_binding(self) -> None:
        tampered_library = copy.deepcopy(self.library_scale_manifest)
        tampered_library["cells"][0]["library_size"] = 999
        with self.assertRaisesRegex(M3KProposalBindingError, "canonical library-scale manifest is invalid"):
            bind_manifest(
                schedule=copy.deepcopy(self.schedule),
                schedule_file_sha256=_sha("synthetic-m3k-schedule-file"),
                library_scale_manifest=tampered_library,
                library_scale_file_sha256=self.library_scale_file_sha256,
                bundle=_proposal_bundle(),
                bundle_file_sha256=_sha("synthetic-m3k-proposal-file"),
                capability=None,
                capability_file_sha256=None,
            )

    def test_legacy_or_arbitrary_proposal_cannot_open_the_execution_binding(self) -> None:
        canonical = _proposal_bundle()
        legacy = {
            "schema_version": 1,
            "parent_variant": canonical["parent_variant"],
            "proposal": canonical["proposal"],
        }
        with self.assertRaisesRegex(
            M3KProposalBindingError, "canonical pre-registered proposal"
        ):
            bind_manifest(
                schedule=copy.deepcopy(self.schedule),
                schedule_file_sha256=_sha("synthetic-m3k-schedule-file"),
                library_scale_manifest=copy.deepcopy(self.library_scale_manifest),
                library_scale_file_sha256=self.library_scale_file_sha256,
                bundle=legacy,
                bundle_file_sha256=_sha("legacy-proposal-file"),
                capability=None,
                capability_file_sha256=None,
            )
        with self.assertRaisesRegex(M3KProposalBindingError, "not bound to this canonical library-scale file"):
            bind_manifest(
                schedule=copy.deepcopy(self.schedule),
                schedule_file_sha256=_sha("synthetic-m3k-schedule-file"),
                library_scale_manifest=copy.deepcopy(self.library_scale_manifest),
                library_scale_file_sha256="0" * 64,
                bundle=_proposal_bundle(),
                bundle_file_sha256=_sha("synthetic-m3k-proposal-file"),
                capability=None,
                capability_file_sha256=None,
            )

    def test_missing_duplicate_arm_order_hash_count_and_parent_candidate_drift_fail_closed(self) -> None:
        bound = self._bound_manifest()

        mutations = {
            "missing": lambda payload: payload["paired_cells"].pop(),
            "duplicate": lambda payload: payload["paired_cells"][2].update(
                task_id=payload["paired_cells"][0]["task_id"],
                trial_index=payload["paired_cells"][0]["trial_index"],
            ),
            "wrong_arm": lambda payload: payload["paired_cells"][0].update(library_arm_id="curated"),
            "wrong_order": lambda payload: payload["paired_cells"][0]["library_variant_ids"].reverse(),
            "wrong_snapshot": lambda payload: payload["paired_cells"][0].update(
                library_snapshot_sha256="0" * 64
            ),
            "wrong_count": lambda payload: payload["paired_cells"][1].update(library_size=208),
            "parent_candidate_difference": lambda payload: payload["paired_cells"][1].update(
                library_trial_seed=payload["paired_cells"][1]["library_trial_seed"] + 1
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                candidate = copy.deepcopy(bound)
                mutate(candidate)
                self._rehash(candidate)
                with self.assertRaises(M3KProposalBindingError):
                    validate_bound_manifest(candidate)


if __name__ == "__main__":
    unittest.main()
