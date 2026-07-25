from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from experiments.skillsbench.export_runtime_name_governance_evidence import (
    RuntimeNameGovernanceEvidenceError,
    build_report,
    validate_report,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class RuntimeNameGovernanceEvidenceTests(unittest.TestCase):
    def test_packaged_safe_evidence_matches_frozen_runtime_reconstruction(self) -> None:
        recorded = json.loads(
            (
                REPO_ROOT
                / "docs"
                / "evidence"
                / "runtime-name-governance-on-frozen-56-v1.json"
            ).read_text(encoding="utf-8")
        )
        validate_report(recorded)
        self.assertEqual(recorded, build_report())
        self.assertEqual(recorded["audit"]["source_variant_count"], 56)
        self.assertEqual(recorded["audit"]["source_declared_name_count"], 53)
        self.assertEqual(recorded["audit"]["collision_group_count"], 2)
        self.assertEqual(recorded["audit"]["suppressed_variant_count"], 3)
        self.assertFalse(recorded["audit"]["source_library_mutated"])

    def test_tampering_fails_closed(self) -> None:
        tampered = copy.deepcopy(build_report())
        tampered["audit"]["runtime_prompt_candidate_count"] = 56
        with self.assertRaisesRegex(
            RuntimeNameGovernanceEvidenceError, "does not match"
        ):
            validate_report(tampered)


if __name__ == "__main__":
    unittest.main()
