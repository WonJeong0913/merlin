from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.chat_lifecycle import (
    ChatLifecycleEvidenceError,
    ChatVerifierContract,
    assess_lifecycle_eligibility,
    load_chat_lifecycle_observation,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ChatLifecycleObservationTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, outcome: str = "fail") -> tuple[Path, Path]:
        trace_root = root / "session"
        trace_root.mkdir()
        raw_path = trace_root / "turn-0001.codex.jsonl"
        raw_path.write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        raw = {"pointer": raw_path.name, "sha256": digest}
        routing = {
            "schema_version": 1,
            "routing_mode": "semantic",
            "routing_source": "semantic",
            "query_sha256": _sha("request"),
            "query_chars": 7,
            "query_stored": False,
            "active_skill_count": 1,
            "candidate_skill_count": 1,
            "candidate_skill_ids": ["report-writer"],
            "anchor_pool_preferred": False,
            "semantic_ranked_ids": ["report-writer"],
            "semantic_negative_excluded_ids": [],
            "semantic_abstained": False,
            "deterministic_guard_excluded_ids": [],
            "final_provisioned_ids": ["report-writer"],
            "final_abstain_reason": None,
            "authoritative_final_decision": True,
            "fallback_error_class": None,
            "model_call_skipped_no_active_skills": False,
            "requested_model_id": "gpt-5.6-terra",
            "requested_effort": "low",
            "provider_reported_model_ids": ["gpt-5.6-terra"],
            "raw_trace": None,
            "ranked_ids_are_prompt_exposure_not_invocation": True,
        }
        turn = {
            "schema_version": 1,
            "turn_number": 1,
            "provider_thread_id": "thread-1",
            "provider_turn_id": "turn-1",
            "resumed": False,
            "user_input_sha256": _sha("request"),
            "user_input_chars": 7,
            "user_input_stored": False,
            "assistant_answer_sha256": _sha("answer"),
            "assistant_answer_chars": 6,
            "assistant_answer_stored": False,
            "provisioned_skills": [
                {
                    "skill_id": "report-writer",
                    "name": "Report writer",
                    "score": 1.0,
                    "why": "semantic rank=1",
                }
            ],
            "deterministic_reference_decision": {"policy_version": "governed-provisioning-v1"},
            "routing_decision": routing,
            "prompt_provisioning_is_provider_native_invocation": False,
            "actual_invocation_evidence_complete": False,
            "raw_trace": raw,
            "backend_metadata": {"provider": "fake"},
            "feedback_status": "pending",
            "lifecycle_automatic_change": "deferred",
        }
        feedback = {
            "schema_version": 1,
            "turn_number": 1,
            "outcome": outcome,
            "raw_trace": raw,
            "provisioned_skill_ids": ["report-writer"],
            "automatic_lifecycle_change": False,
            "lifecycle_note": "feedback is health evidence only",
        }
        turn_path = trace_root / "turn-0001.meta.json"
        feedback_path = trace_root / "feedback-turn-0001.json"
        turn_path.write_text(json.dumps(turn, indent=2), encoding="utf-8")
        feedback_path.write_text(json.dumps(feedback, indent=2), encoding="utf-8")
        return trace_root, turn_path

    def test_pass_and_fail_preserve_exposure_only_observations(self) -> None:
        for outcome in ("pass", "fail"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary:
                trace_root, _turn_path = self._write_fixture(Path(temporary), outcome=outcome)
                observation = load_chat_lifecycle_observation(trace_root, turn_number=1)

                self.assertEqual(observation.feedback_outcome, outcome)
                self.assertEqual(observation.exposure_skill_ids, ("report-writer",))
                self.assertEqual(observation.evidence_level, "exposure_outcome_proxy")
                self.assertFalse(observation.actual_invocation_evidence_complete)
                serialized = observation.to_dict()
                self.assertSetEqual(
                    set(serialized),
                    {
                        "schema_version",
                        "turn_number",
                        "feedback_outcome",
                        "exposure_skill_ids",
                        "raw_trace",
                        "evidence_level",
                        "actual_invocation_evidence_complete",
                    },
                )
                self.assertNotIn("selected_skill_ids", serialized)
                self.assertNotIn("invoked_skill_ids", serialized)
                self.assertNotIn("shadowing_rate", serialized)

    def test_missing_feedback_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root, _turn_path = self._write_fixture(Path(temporary))
            (trace_root / "feedback-turn-0001.json").unlink()
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "feedback ledger is missing"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

    def test_hash_tamper_and_turn_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root, _turn_path = self._write_fixture(Path(temporary))
            (trace_root / "turn-0001.codex.jsonl").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "SHA-256 mismatch"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

        with tempfile.TemporaryDirectory() as temporary:
            trace_root, _turn_path = self._write_fixture(Path(temporary))
            feedback_path = trace_root / "feedback-turn-0001.json"
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
            feedback["turn_number"] = 2
            feedback_path.write_text(json.dumps(feedback), encoding="utf-8")
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "turn_number does not match"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

    def test_path_escape_and_symlink_raw_trace_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root, turn_path = self._write_fixture(Path(temporary))
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            turn["raw_trace"]["pointer"] = "../outside.codex.jsonl"
            turn_path.write_text(json.dumps(turn), encoding="utf-8")
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "pointer is unsafe"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace_root, turn_path = self._write_fixture(root)
            outside = root / "outside.codex.jsonl"
            outside.write_text("outside\n", encoding="utf-8")
            raw_path = trace_root / "turn-0001.codex.jsonl"
            raw_path.unlink()
            try:
                os.symlink(outside, raw_path)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            turn["raw_trace"]["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
            feedback_path = trace_root / "feedback-turn-0001.json"
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
            feedback["raw_trace"] = dict(turn["raw_trace"])
            turn_path.write_text(json.dumps(turn), encoding="utf-8")
            feedback_path.write_text(json.dumps(feedback), encoding="utf-8")
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "must not be a symlink"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

    def test_router_raw_trace_hash_is_verified_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root, turn_path = self._write_fixture(Path(temporary))
            router_raw = trace_root / "router-turn-0001.codex.jsonl"
            router_raw.write_text('{"type":"router"}\n', encoding="utf-8")
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            turn["routing_decision"]["raw_trace"] = {
                "pointer": router_raw.name,
                "sha256": hashlib.sha256(router_raw.read_bytes()).hexdigest(),
            }
            turn_path.write_text(json.dumps(turn), encoding="utf-8")
            self.assertEqual(
                load_chat_lifecycle_observation(trace_root, turn_number=1).turn_number,
                1,
            )

            router_raw.write_text("router trace tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "routing_decision raw trace SHA-256 mismatch"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

    def test_unknown_duplicate_and_unsafe_statuses_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root, turn_path = self._write_fixture(Path(temporary))
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            turn["unexpected"] = True
            turn_path.write_text(json.dumps(turn), encoding="utf-8")
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "unknown keys"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

        with tempfile.TemporaryDirectory() as temporary:
            trace_root, turn_path = self._write_fixture(Path(temporary))
            turn_path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "duplicate JSON key"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

        with tempfile.TemporaryDirectory() as temporary:
            trace_root, turn_path = self._write_fixture(Path(temporary))
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            turn["lifecycle_automatic_change"] = "applied"
            turn_path.write_text(json.dumps(turn), encoding="utf-8")
            with self.assertRaisesRegex(ChatLifecycleEvidenceError, "unsafe lifecycle status"):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

    def test_fail_feedback_never_allows_hide_and_missing_verifier_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root, _turn_path = self._write_fixture(Path(temporary), outcome="fail")
            observation = load_chat_lifecycle_observation(trace_root, turn_number=1)
            eligibility = assess_lifecycle_eligibility(observation)

            self.assertTrue(eligibility.observe_only)
            self.assertFalse(eligibility.action_allowed)
            self.assertEqual(eligibility.status, "verifier_missing")
            self.assertEqual(eligibility.exposure_skill_ids, ("report-writer",))
            self.assertIn("feedback_is_observational_not_a_verifier", eligibility.blockers)
            self.assertIn("actual_invocation_evidence_missing", eligibility.blockers)

    def test_verifier_contract_still_reports_actual_invocation_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root, _turn_path = self._write_fixture(Path(temporary), outcome="pass")
            observation = load_chat_lifecycle_observation(trace_root, turn_number=1)
            contract = ChatVerifierContract(
                task_id="chat-report-task",
                verifier_id="file-exists",
                contract_sha256=_sha("frozen-contract"),
            )
            eligibility = assess_lifecycle_eligibility(
                observation, verifier_contract=contract
            )

            self.assertTrue(eligibility.observe_only)
            self.assertFalse(eligibility.action_allowed)
            self.assertEqual(eligibility.status, "actual_invocation_evidence_missing")
            self.assertNotIn("verifier_missing", eligibility.blockers)
            self.assertIn("not selected, loaded, invoked", eligibility.evidence_boundary)

    def test_controlled_lexical_mode_is_accepted_as_exposure_only_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root, turn_path = self._write_fixture(Path(temporary), outcome="pass")
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            turn["routing_decision"]["routing_mode"] = "controlled_lexical"
            turn["routing_decision"]["routing_source"] = "controlled_lexical"
            turn_path.write_text(json.dumps(turn), encoding="utf-8")

            observation = load_chat_lifecycle_observation(trace_root, turn_number=1)

            self.assertEqual(observation.exposure_skill_ids, ("report-writer",))
            self.assertFalse(observation.actual_invocation_evidence_complete)


if __name__ == "__main__":
    unittest.main()
