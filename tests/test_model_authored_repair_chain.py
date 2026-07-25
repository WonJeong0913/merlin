from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from experiments.mvp.audit_model_authored_repair_chain import (
    ModelAuthoredRepairChainAuditError,
    validate_model_authored_repair_chain_audit,
)


ROOT = Path(__file__).resolve().parents[1]
AUDIT = (
    ROOT
    / "experiments/mvp/results/model_authored_skill_repair_live_v1/"
    "model_authored_skill_repair_chain_audit.json"
)


class ModelAuthoredRepairChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = json.loads(AUDIT.read_text(encoding="utf-8"))

    def test_retained_hash_only_repair_chain_is_valid(self) -> None:
        validate_model_authored_repair_chain_audit(self.report)
        self.assertEqual(self.report["status"], "passed")
        self.assertEqual(len(self.report["checks"]), 13)
        self.assertTrue(all(item["passed"] for item in self.report["checks"]))
        serialized = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("/private/tmp/", serialized)
        self.assertNotIn("019f7ad1-3619-7722-9bec-88450cbfc07a", serialized)

    def test_hash_or_denominator_tamper_fails_closed(self) -> None:
        tampered = copy.deepcopy(self.report)
        tampered["fresh_revalidation"]["v2_held_out"] = [0, 1]
        with self.assertRaisesRegex(
            ModelAuthoredRepairChainAuditError, "content hash"
        ):
            validate_model_authored_repair_chain_audit(tampered)

        tampered = copy.deepcopy(self.report)
        tampered["audit_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            ModelAuthoredRepairChainAuditError, "content hash"
        ):
            validate_model_authored_repair_chain_audit(tampered)


if __name__ == "__main__":
    unittest.main()
