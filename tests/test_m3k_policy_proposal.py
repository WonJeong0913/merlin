from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.create_m3k_policy_proposal_bundle import write_bundle
from experiments.skillsbench.m3k_policy_proposal import (
    CANDIDATE_BUDGET,
    CANDIDATE_ID,
    EVIDENCE_SOURCE_PATH,
    M3KPolicyProposalError,
    PARENT_BUDGET,
    PARENT_ID,
    build_canonical_bundle,
    validate_canonical_bundle,
)
from src.merlin_harness.harness import HarnessVariantSpec, build_runtime_from_variant
from src.merlin_harness.management import content_sha256


class M3KPolicyProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence_path = Path(EVIDENCE_SOURCE_PATH)
        cls.evidence_bytes = cls.evidence_path.read_bytes()
        cls.evidence = json.loads(cls.evidence_bytes)
        cls.file_sha256 = hashlib.sha256(cls.evidence_bytes).hexdigest()

    def _bundle(self) -> dict:
        return build_canonical_bundle(
            copy.deepcopy(self.evidence), evidence_file_sha256=self.file_sha256
        )

    def test_bundle_is_deterministic_reconstructable_and_held_out_clean(self) -> None:
        first = self._bundle()
        second = self._bundle()
        self.assertEqual(first, second)
        self.assertEqual(content_sha256(first), content_sha256(second))
        self.assertEqual(first["parent_variant"]["id"], PARENT_ID)
        self.assertEqual(first["parent_variant"]["policy"]["exposure_budget"], PARENT_BUDGET)
        candidate = first["proposal"]["candidate"]
        self.assertEqual(candidate["id"], CANDIDATE_ID)
        self.assertEqual(candidate["parent_id"], PARENT_ID)
        self.assertEqual(candidate["policy"]["exposure_budget"], CANDIDATE_BUDGET)
        self.assertEqual(
            first["construction_evidence"][
                "full87_held_out_task_ids_used_for_construction"
            ],
            [],
        )
        self.assertTrue(all(value is False for value in first["claim_boundary"].values()))
        for payload in (first["parent_variant"], candidate):
            build_runtime_from_variant(HarnessVariantSpec(**payload))
        validate_canonical_bundle(first)

    def test_evidence_file_semantic_metric_and_trace_drift_fail_closed(self) -> None:
        with self.assertRaisesRegex(M3KPolicyProposalError, "file SHA-256 drifted"):
            build_canonical_bundle(copy.deepcopy(self.evidence), evidence_file_sha256="0" * 64)

        semantic = copy.deepcopy(self.evidence)
        semantic["title"] = "tampered"
        with self.assertRaisesRegex(M3KPolicyProposalError, "semantic SHA-256 drifted"):
            build_canonical_bundle(semantic, evidence_file_sha256=self.file_sha256)

    def test_bundle_policy_lineage_provenance_and_claim_tamper_fail_closed(self) -> None:
        mutations = {
            "parent_budget": lambda value: value["parent_variant"]["policy"].update(
                exposure_budget=9
            ),
            "candidate_budget": lambda value: value["proposal"]["candidate"]["policy"].update(
                exposure_budget=2
            ),
            "lineage": lambda value: value["proposal"]["candidate"].update(parent_id="other"),
            "evidence": lambda value: value["construction_evidence"].update(
                source_file_sha256="0" * 64
            ),
            "held_out": lambda value: value["construction_evidence"].update(
                full87_held_out_task_ids_used_for_construction=["hidden-task"]
            ),
            "claim": lambda value: value["claim_boundary"].update(candidate_is_promoted=True),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                bundle = self._bundle()
                mutate(bundle)
                with self.assertRaises(M3KPolicyProposalError):
                    validate_canonical_bundle(bundle)

    def test_writer_is_new_only_and_emits_the_exact_validated_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "proposal.json"
            expected = self._bundle()
            written = write_bundle(evidence_path=self.evidence_path, output=output)
            self.assertEqual(written, expected)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), expected)
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                write_bundle(evidence_path=self.evidence_path, output=output)


if __name__ == "__main__":
    unittest.main()
