from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.create_m3k_evaluation_manifest import (
    M3KManifestError,
    build_manifest,
    validate_manifest,
    write_manifest,
)
from src.merlin_harness.management import content_sha256


SPLIT = Path("experiments/skillsbench/split-manifest.json")
SCALE = Path("experiments/skillsbench/library-scale-manifest.json")
FROZEN = Path("experiments/skillsbench/m3k-evaluation-manifest.json")


class M3KEvaluationManifestTests(unittest.TestCase):
    def test_frozen_manifest_reproduces_exactly_and_stays_not_run(self) -> None:
        stored = json.loads(FROZEN.read_text(encoding="utf-8"))
        rebuilt = build_manifest(
            split_manifest=SPLIT,
            library_scale_manifest=SCALE,
        )
        self.assertEqual(stored, rebuilt)
        validate_manifest(stored)
        self.assertEqual(stored["summary"]["expected_trajectories"], 522)
        self.assertFalse(stored["execution_gate"]["execution_allowed"])
        self.assertFalse(stored["claim_boundary"]["full87_result"])
        self.assertEqual(
            stored["proposal_binding"]["binding_status"],
            "required_before_execution",
        )

    def test_hash_pairing_and_claim_tamper_fail_closed(self) -> None:
        payload = build_manifest(
            split_manifest=SPLIT,
            library_scale_manifest=SCALE,
        )
        candidate = copy.deepcopy(payload)
        candidate["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(M3KManifestError, "hash mismatch"):
            validate_manifest(candidate)

        candidate = copy.deepcopy(payload)
        candidate["summary"]["expected_trajectories"] = 521
        without_hash = dict(candidate)
        without_hash.pop("manifest_sha256")
        candidate["manifest_sha256"] = content_sha256(without_hash)
        with self.assertRaisesRegex(M3KManifestError, "paired parent/candidate"):
            validate_manifest(candidate)

        candidate = copy.deepcopy(payload)
        candidate["claim_boundary"]["full87_result"] = True
        without_hash = dict(candidate)
        without_hash.pop("manifest_sha256")
        candidate["manifest_sha256"] = content_sha256(without_hash)
        with self.assertRaisesRegex(M3KManifestError, "cannot claim"):
            validate_manifest(candidate)

    def test_writer_is_new_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "manifest.json"
            first = write_manifest(
                output,
                split_manifest=SPLIT,
                library_scale_manifest=SCALE,
            )
            validate_manifest(first)
            with self.assertRaises(FileExistsError):
                write_manifest(
                    output,
                    split_manifest=SPLIT,
                    library_scale_manifest=SCALE,
                )


if __name__ == "__main__":
    unittest.main()
