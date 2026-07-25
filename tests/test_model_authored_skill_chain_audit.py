from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from experiments.mvp.audit_model_authored_skill_chain import (
    ModelAuthoredSkillChainAuditError,
    validate_model_authored_skill_chain_audit,
)
from src.merlin_harness.management import content_sha256


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = (
    REPOSITORY_ROOT
    / "experiments"
    / "mvp"
    / "results"
    / "model_authored_skill_live_v1"
    / "model_authored_skill_chain_audit.json"
)


def _rehash(report: dict[str, object]) -> None:
    report.pop("audit_sha256", None)
    report["audit_sha256"] = content_sha256(report)


class ModelAuthoredSkillChainAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))

    def test_frozen_safe_audit_is_valid(self) -> None:
        validate_model_authored_skill_chain_audit(self.report)

    def test_plain_tamper_fails_content_hash(self) -> None:
        tampered = copy.deepcopy(self.report)
        tampered["fresh_revalidation"]["target_passed"] = [1, 2]
        with self.assertRaisesRegex(ModelAuthoredSkillChainAuditError, "content hash"):
            validate_model_authored_skill_chain_audit(tampered)

    def test_rehashed_result_inflation_still_fails_schema_contract(self) -> None:
        tampered = copy.deepcopy(self.report)
        tampered["fresh_revalidation"]["target_passed"] = [3, 3]
        _rehash(tampered)
        with self.assertRaisesRegex(ModelAuthoredSkillChainAuditError, "denominator"):
            validate_model_authored_skill_chain_audit(tampered)

    def test_rehashed_provider_identity_upgrade_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.report)
        tampered["provider_contract"]["provider_reported_model_ids"] = ["gpt-5.6-terra"]
        tampered["claim_boundary"]["requested_model_is_provider_resolved_model"] = True
        _rehash(tampered)
        with self.assertRaisesRegex(ModelAuthoredSkillChainAuditError, "provider contract"):
            validate_model_authored_skill_chain_audit(tampered)

    def test_rehashed_raw_path_field_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.report)
        tampered["raw_trace_path"] = "/private/tmp/provider.codex.jsonl"
        _rehash(tampered)
        with self.assertRaisesRegex(ModelAuthoredSkillChainAuditError, "schema"):
            validate_model_authored_skill_chain_audit(tampered)


if __name__ == "__main__":
    unittest.main()
