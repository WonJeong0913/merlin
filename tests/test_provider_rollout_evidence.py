from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.provider_rollout_evidence import (
    ProviderRolloutError,
    canonical_model_request_sha256,
    corroborate_skill_body_invocation,
    locate_rollout,
)
from src.merlin_harness.skill_body_invocation import (
    HarnessInvocationSigner,
    SkillBodyInvocationError,
    create_skill_body_invocation_event,
)

THREAD_ID = "019f9304-da0e-71a3-8446-4e9da172e607"
OTHER_THREAD_ID = "019ceaeb-706b-7243-9791-bb853a4fdcb4"
TURN_ONE = "turn-1"
TURN_TWO = "turn-2"
SKILL_BODY = "# Extract TODO Items\n\nRead backlog.todo and write todo-items.json.\n"
SIGNER = HarnessInvocationSigner(signer_id="test-harness", secret=b"x" * 32)
OTHER_SIGNER = HarnessInvocationSigner(signer_id="other-harness", secret=b"y" * 32)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rollout_lines(
    *,
    turns: tuple[tuple[str, str], ...],
    session_id: str = THREAD_ID,
    source: object = "exec",
    include_turn_ids: bool = True,
) -> list[str]:
    """Model the observed JSONL record shapes with an explicit turn binding.

    Current observed Codex rollouts have ``session_meta`` and user
    ``response_item`` records in this shape.  The v2 contract additionally
    requires a stable turn ID in ``turn_context``; without it the parser must
    reject instead of guessing from session order.
    """

    records: list[dict[str, object]] = [
        {
            "type": "session_meta",
            "timestamp": "2026-07-25T00:00:00Z",
            "payload": {
                "id": session_id,
                "cli_version": "0.146.0-alpha",
                "originator": "codex-tui",
                "source": source,
            },
        }
    ]
    for ordinal, (turn_id, request_text) in enumerate(turns, start=1):
        turn_payload: dict[str, object] = {
            "model": "gpt-5.6-terra",
            "effort": "high",
        }
        if include_turn_ids:
            turn_payload["turn_id"] = turn_id
        records.extend(
            (
                {
                    "type": "turn_context",
                    "timestamp": f"2026-07-25T00:00:{ordinal:02d}Z",
                    "payload": turn_payload,
                },
                {
                    "type": "response_item",
                    "timestamp": f"2026-07-25T00:01:{ordinal:02d}Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": request_text}
                        ],
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": f"2026-07-25T00:02:{ordinal:02d}Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                },
            )
        )
    return [json.dumps(record) for record in records]


class RolloutFixture:
    """A sessions root holding one synthetic, CLI-shaped rollout."""

    def __init__(self, directory: Path) -> None:
        self.root = directory / "sessions"
        self.skill_body = directory / "skill" / "SKILL.md"
        self.skill_body.parent.mkdir(parents=True)
        self.skill_body.write_text(SKILL_BODY, encoding="utf-8")

    def write(self, lines: list[str], *, thread_id: str = THREAD_ID) -> Path:
        day = self.root / "2026" / "07" / "25"
        day.mkdir(parents=True, exist_ok=True)
        path = day / f"rollout-2026-07-25T00-00-00-{thread_id}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def event(
        self,
        request_text: str,
        *,
        signer: HarnessInvocationSigner = SIGNER,
        skill_body_sha256: str | None = None,
        model_request_sha256: str | None = None,
    ):
        return create_skill_body_invocation_event(
            event_id="event-1",
            task_id="task-1",
            task_contract_sha256=_sha256("contract"),
            selected_skill_id="extract-todo-items",
            skill_body_sha256=skill_body_sha256 or _sha256(SKILL_BODY),
            model_request_sha256=(
                model_request_sha256
                if model_request_sha256 is not None
                else canonical_model_request_sha256(request_text)
            ),
            execution_trace_sha256=_sha256("trace"),
            verifier_result_sha256=_sha256("result"),
            verifier_passed=True,
            harness_policy_sha256=_sha256("policy"),
            signer=signer,
        )

    def corroborate(self, event, *, turn_id: str = TURN_ONE):
        return corroborate_skill_body_invocation(
            event,
            trusted_signer=SIGNER,
            thread_id=THREAD_ID,
            turn_id=turn_id,
            skill_body_path=self.skill_body,
            sessions_root=self.root,
        )


class LocateRolloutTests(unittest.TestCase):
    def test_locates_the_rollout_carrying_the_thread_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            expected = fixture.write(_rollout_lines(turns=((TURN_ONE, "hello"),)))
            self.assertEqual(
                locate_rollout(THREAD_ID, sessions_root=fixture.root), expected
            )

    def test_a_missing_rollout_is_an_error_not_an_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, "hello"),)))
            with self.assertRaises(ProviderRolloutError):
                locate_rollout(OTHER_THREAD_ID, sessions_root=fixture.root)

    def test_ambiguity_fails_closed_rather_than_picking_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, "hello"),)))
            duplicate = fixture.root / "2026" / "07" / "26"
            duplicate.mkdir(parents=True)
            (duplicate / f"rollout-copy-{THREAD_ID}.jsonl").write_text("{}\n")
            with self.assertRaises(ProviderRolloutError):
                locate_rollout(THREAD_ID, sessions_root=fixture.root)

    def test_a_malformed_thread_id_is_rejected(self) -> None:
        with self.assertRaises(ProviderRolloutError):
            locate_rollout("../../etc/passwd")


class CorroborationTests(unittest.TestCase):
    def test_exact_positive_case_binds_every_required_link(self) -> None:
        request = f"Task: do the thing\n\n{SKILL_BODY}"
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)))
            result = fixture.corroborate(fixture.event(request))
        self.assertTrue(result.corroborated)
        self.assertEqual(result.source, "exec")
        self.assertEqual(result.cli_version, "0.146.0-alpha")
        self.assertEqual(result.session_id, THREAD_ID)
        self.assertEqual(result.turn_id, TURN_ONE)
        self.assertEqual(result.turn_ids, (TURN_ONE,))
        self.assertTrue(result.invocation_signature_valid)
        self.assertTrue(result.session_bound)
        self.assertTrue(result.turn_bound)
        self.assertTrue(result.request_hash_bound)
        self.assertTrue(result.skill_body_hash_bound)
        self.assertEqual(len(result.rollout_sha256), 64)

    def test_forged_signature_is_rejected_before_rollout_is_trusted(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)))
            forged = dataclasses.replace(fixture.event(request), signature="0" * 64)
            with self.assertRaisesRegex(SkillBodyInvocationError, "signature"):
                fixture.corroborate(forged)

    def test_event_signed_by_a_different_harness_is_rejected(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)))
            with self.assertRaisesRegex(SkillBodyInvocationError, "signer"):
                fixture.corroborate(fixture.event(request, signer=OTHER_SIGNER))

    def test_filename_thread_and_session_meta_id_must_both_match(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(
                _rollout_lines(
                    turns=((TURN_ONE, request),), session_id=OTHER_THREAD_ID
                )
            )
            with self.assertRaisesRegex(ProviderRolloutError, "session_meta.id"):
                fixture.corroborate(fixture.event(request))

    def test_a_skill_body_in_another_turn_cannot_corroborate_target_turn(self) -> None:
        target_request = "Task: do the thing without any skill body"
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(
                _rollout_lines(
                    turns=((TURN_ONE, f"Old turn\n{SKILL_BODY}"), (TURN_TWO, target_request))
                )
            )
            with self.assertRaisesRegex(ProviderRolloutError, "skill body is absent"):
                fixture.corroborate(fixture.event(target_request), turn_id=TURN_TWO)

    def test_recorded_request_hash_must_match_the_signed_event(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)))
            event = fixture.event(
                request, model_request_sha256=canonical_model_request_sha256("other")
            )
            with self.assertRaisesRegex(ProviderRolloutError, "request hash"):
                fixture.corroborate(event)

    def test_a_body_that_drifted_since_signing_is_rejected(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)))
            event = fixture.event(
                request, skill_body_sha256=_sha256("a different body")
            )
            with self.assertRaisesRegex(SkillBodyInvocationError, "does not match"):
                fixture.corroborate(event)

    def test_near_miss_skill_body_is_rejected(self) -> None:
        request = SKILL_BODY.rstrip("\n")
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)))
            with self.assertRaisesRegex(ProviderRolloutError, "skill body is absent"):
                fixture.corroborate(fixture.event(request))

    def test_an_unidentified_turn_is_rejected_instead_of_guessed_from_order(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(
                _rollout_lines(
                    turns=((TURN_ONE, request),), include_turn_ids=False
                )
            )
            with self.assertRaisesRegex(ProviderRolloutError, "provider turn"):
                fixture.corroborate(fixture.event(request))

    def test_a_rollout_without_a_user_request_in_target_turn_is_an_error(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            lines = [
                line
                for line in _rollout_lines(turns=((TURN_ONE, request),))
                if '"role": "user"' not in line
            ]
            fixture.write(lines)
            with self.assertRaisesRegex(ProviderRolloutError, "exactly one user request"):
                fixture.corroborate(fixture.event(request))

    def test_a_malformed_rollout_line_is_an_error(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)) + ["{not json"])
            with self.assertRaisesRegex(ProviderRolloutError, "malformed"):
                fixture.corroborate(fixture.event(request))

    def test_a_nested_source_object_is_normalized(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(
                _rollout_lines(
                    turns=((TURN_ONE, request),),
                    source={"subagent": {"other": "guardian"}},
                )
            )
            result = fixture.corroborate(fixture.event(request))
        self.assertEqual(result.source, "subagent")


class BoundaryTests(unittest.TestCase):
    def test_the_record_never_returns_the_request_text(self) -> None:
        secret = "PRIVATE-USER-CONTENT-9f3a"
        request = f"{secret}\n{SKILL_BODY}"
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)))
            result = fixture.corroborate(fixture.event(request))
        self.assertNotIn(secret, json.dumps(result.to_dict()))
        self.assertTrue(result.recorded_request_chars > 0)

    def test_the_record_states_what_it_does_not_establish(self) -> None:
        request = SKILL_BODY
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.write(_rollout_lines(turns=((TURN_ONE, request),)))
            payload = fixture.corroborate(fixture.event(request)).to_dict()
        boundary = payload["evidence_boundary"]
        self.assertFalse(boundary["provider_server_attested"])
        self.assertIn("not use", boundary["does_not_establish"])
        self.assertIn("Codex CLI", boundary["residual_trust"])


if __name__ == "__main__":
    unittest.main()
