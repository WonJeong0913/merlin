from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from experiments.mvp.audit_model_authored_rejection_chain import (
    EXPECTED_CHECK_COUNT,
    validate_model_authored_rejection_audit,
)
from experiments.mvp.run_live_model_skill_rejection import (
    classify_quarantine_rejection,
)


ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = (
    ROOT
    / "experiments"
    / "mvp"
    / "results"
    / "model_authored_skill_rejection_live_v1"
)
EVIDENCE = RESULT_ROOT / "model_authored_skill_rejection_evidence.json"
AUDIT = RESULT_ROOT / "model_authored_skill_rejection_chain_audit.json"


def _rehash(report: dict[str, object]) -> None:
    report.pop("audit_sha256", None)
    canonical = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    report["audit_sha256"] = hashlib.sha256(canonical).hexdigest()


class ModelAuthoredRejectionChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.audit = json.loads(AUDIT.read_text(encoding="utf-8"))

    def test_retained_pre_execution_rejection_is_valid_and_safe(self) -> None:
        validate_model_authored_rejection_audit(self.audit)
        self.assertEqual(len(self.audit["checks"]), EXPECTED_CHECK_COUNT)
        self.assertFalse(self.evidence["adopted"])
        self.assertEqual(
            self.evidence["quarantine"]["rejection_code"],
            "network_or_process_import",
        )
        self.assertFalse(self.evidence["evidence_boundary"]["host_execution"])
        self.assertEqual(self.evidence["provider_reported_model_ids"], [])
        serialized = json.dumps(
            {"evidence": self.evidence, "audit": self.audit}, ensure_ascii=False
        )
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("/private/tmp/", serialized)
        self.assertNotIn("thread_id", serialized)
        self.assertNotIn("turn_id", serialized)

    def test_plain_hash_tamper_fails_closed(self) -> None:
        tampered = copy.deepcopy(self.audit)
        tampered["fresh_revalidation"]["candidate_executed"] = True
        with self.assertRaisesRegex(ValueError, "fresh-revalidation|SHA-256"):
            validate_model_authored_rejection_audit(tampered)

    def test_rehashed_claim_upgrade_fails_closed(self) -> None:
        tampered = copy.deepcopy(self.audit)
        tampered["claim_boundary"]["provider_resolved_model_identity"] = True
        _rehash(tampered)
        with self.assertRaisesRegex(ValueError, "claim boundary"):
            validate_model_authored_rejection_audit(tampered)

    def test_rehashed_extra_raw_pointer_fails_closed(self) -> None:
        tampered = copy.deepcopy(self.audit)
        tampered["raw_trace_path"] = "/private/tmp/provider.codex.jsonl"
        _rehash(tampered)
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            validate_model_authored_rejection_audit(tampered)

    def test_rejection_classifier_is_bounded(self) -> None:
        self.assertEqual(
            classify_quarantine_rejection("candidate imports quarantined modules: urllib"),
            "network_or_process_import",
        )
        self.assertEqual(
            classify_quarantine_rejection("unrelated validation failure"),
            "other_quarantine_policy",
        )


if __name__ == "__main__":
    unittest.main()
