from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.complete_live_model_hidden_campaign import (
    _validate_candidate_manifest,
    audit_completion,
)
from experiments.mvp.run_live_model_skill_hidden_rollback import (
    CANDIDATE_ID,
    campaign_contract,
    frozen_cases,
    generator_prompt,
    resolve_lifecycle_outcome,
)


RESULTS_ROOT = Path(__file__).resolve().parents[1] / "experiments" / "mvp" / "results"


class LiveModelHiddenRollbackTests(unittest.TestCase):
    def test_contract_binds_prompt_and_every_frozen_case(self) -> None:
        contract = campaign_contract()
        self.assertEqual(contract["candidate_skill_id"], CANDIDATE_ID)
        self.assertEqual(len(contract["case_commitments"]), len(frozen_cases()))
        self.assertEqual(len(frozen_cases()), 5)
        self.assertEqual(
            {item["split"] for item in contract["case_commitments"]},
            {"target", "held_out", "negative"},
        )
        self.assertTrue(
            all(len(item["content_sha256"]) == 64 for item in contract["case_commitments"])
        )

    def test_exact_hidden_case_is_not_in_generator_prompt(self) -> None:
        hidden = next(case for case in frozen_cases() if case.split == "held_out")
        prompt = generator_prompt()
        self.assertNotIn("Hidden fake", prompt)
        self.assertNotIn("Still fake", prompt)
        self.assertNotIn(hidden.input_files[0][1], prompt)
        self.assertIn("frozen hidden Markdown edge case", prompt)

    def test_lifecycle_resolution_is_closed_world(self) -> None:
        cases = (
            (True, True, True, "adopt"),
            (True, False, True, "rollback"),
            (True, True, False, "rollback"),
            (False, False, False, "reject"),
        )
        for pre_hidden, hidden, negative, expected in cases:
            with self.subTest(expected=expected, hidden=hidden, negative=negative):
                self.assertEqual(
                    resolve_lifecycle_outcome(
                        pre_hidden_passed=pre_hidden,
                        hidden_passed=hidden,
                        negative_passed=negative,
                    ),
                    expected,
                )

    def test_contract_is_json_serializable_and_deterministic(self) -> None:
        first = json.dumps(campaign_contract(), ensure_ascii=False, sort_keys=True)
        second = json.dumps(campaign_contract(), ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second)

    def test_candidate_manifest_revalidation_rejects_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "quarantine"
            candidate = root / "candidate" / CANDIDATE_ID
            candidate.mkdir(parents=True)
            skill = candidate / "SKILL.md"
            skill.write_text("original", encoding="utf-8")
            manifest = {
                "files": [
                    {
                        "path": "SKILL.md",
                        "bytes": len(b"original"),
                        "sha256": hashlib.sha256(b"original").hexdigest(),
                    }
                ]
            }
            _validate_candidate_manifest(root, manifest)
            skill.write_text("drifted", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bytes drifted"):
                _validate_candidate_manifest(root, manifest)

    def test_retained_completion_chain_passes_safe_audit(self) -> None:
        report = audit_completion(
            evidence_root=RESULTS_ROOT / "model_authored_hidden_completion_live_v1",
            prior_evidence_root=RESULTS_ROOT / "model_authored_hidden_rollback_live_v1",
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["checks_passed"], report["checks_total"])
        self.assertEqual(report["checks_total"], 9)

    def test_safe_audit_rejects_tampered_rollback_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            evidence_root = temporary_root / "completion"
            prior_root = temporary_root / "prior"
            shutil.copytree(
                RESULTS_ROOT / "model_authored_hidden_completion_live_v1", evidence_root
            )
            shutil.copytree(
                RESULTS_ROOT / "model_authored_hidden_rollback_live_v1", prior_root
            )
            path = evidence_root / "model_authored_hidden_completion_evidence.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["resolved_library_snapshot_sha256"] = "0" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cow_rollback_resolution"):
                audit_completion(
                    evidence_root=evidence_root,
                    prior_evidence_root=prior_root,
                )


if __name__ == "__main__":
    unittest.main()
