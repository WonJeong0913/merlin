from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from experiments.mvp.audit_model_authored_repair_chain import (
    ModelAuthoredRepairChainAuditError,
)
from experiments.mvp.audit_model_authored_repair_family2_chain import (
    validate_model_authored_repair_family2_chain_audit,
)


ROOT = Path(__file__).resolve().parents[1]
AUDIT = (
    ROOT
    / "experiments/mvp/results/model_authored_skill_repair_family2_live_v1/"
    "model_authored_skill_repair_family2_chain_audit.json"
)


class ModelAuthoredRepairFamily2ChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = json.loads(AUDIT.read_text(encoding="utf-8"))

    def test_retained_family2_audit_is_hash_only_and_valid(self) -> None:
        validate_model_authored_repair_family2_chain_audit(self.report)
        self.assertEqual(self.report["status"], "passed")
        self.assertEqual(self.report["decision"], "promote")
        self.assertEqual(len(self.report["checks"]), 14)
        self.assertTrue(all(item["passed"] for item in self.report["checks"]))
        serialized = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("/private/tmp/", serialized)

    def test_family2_denominator_or_hash_tamper_fails_closed(self) -> None:
        tampered = copy.deepcopy(self.report)
        tampered["fresh_revalidation"]["v1_held_out"] = [0, 1]
        with self.assertRaisesRegex(
            ModelAuthoredRepairChainAuditError, "content hash"
        ):
            validate_model_authored_repair_family2_chain_audit(tampered)

        tampered = copy.deepcopy(self.report)
        tampered["audit_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            ModelAuthoredRepairChainAuditError, "content hash"
        ):
            validate_model_authored_repair_family2_chain_audit(tampered)


if __name__ == "__main__":
    unittest.main()
