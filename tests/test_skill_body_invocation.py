from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from src.merlin_harness.skill_body_invocation import (
    HarnessInvocationSigner,
    SkillBodyInvocationError,
    create_skill_body_invocation_event,
    load_skill_body_sha256,
    skill_body_invocation_event_from_dict,
    validate_skill_body_invocation_event,
)


class SkillBodyInvocationEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = HarnessInvocationSigner(
            signer_id="merlin-test-harness-v1",
            secret=b"test-only-harness-signing-secret-0001",
        )
        self.event = create_skill_body_invocation_event(
            event_id="invoke-pw-ke-01-managed-skill-1",
            task_id="pw-ke-01",
            task_contract_sha256="a" * 64,
            selected_skill_id="managed-skill",
            skill_body_sha256="b" * 64,
            model_request_sha256="c" * 64,
            execution_trace_sha256="d" * 64,
            verifier_result_sha256="e" * 64,
            verifier_passed=True,
            harness_policy_sha256="f" * 64,
            signer=self.signer,
        )

    def test_valid_event_round_trips_and_verifies(self) -> None:
        restored = skill_body_invocation_event_from_dict(self.event.to_dict())
        self.assertEqual(restored, self.event)
        validate_skill_body_invocation_event(restored, signer=self.signer)

    def test_any_signed_chain_member_tampering_is_rejected(self) -> None:
        for field, value in (
            ("selected_skill_id", "other-skill"),
            ("skill_body_sha256", "0" * 64),
            ("model_request_sha256", "0" * 64),
            ("execution_trace_sha256", "0" * 64),
            ("verifier_result_sha256", "0" * 64),
            ("verifier_passed", False),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    SkillBodyInvocationError, "signature verification"
                ):
                    validate_skill_body_invocation_event(
                        replace(self.event, **{field: value}), signer=self.signer
                    )

    def test_untrusted_signer_and_missing_required_fields_fail_closed(self) -> None:
        other_signer = HarnessInvocationSigner(
            signer_id="other-harness-v1",
            secret=b"a-different-test-only-signing-secret",
        )
        with self.assertRaisesRegex(SkillBodyInvocationError, "signer"):
            validate_skill_body_invocation_event(self.event, signer=other_signer)
        payload = self.event.to_dict()
        del payload["execution_trace_sha256"]
        with self.assertRaisesRegex(SkillBodyInvocationError, "schema"):
            skill_body_invocation_event_from_dict(payload)

    def test_skill_body_loader_hashes_the_exact_regular_skill_file(self) -> None:
        with TemporaryDirectory() as temporary:
            skill_path = Path(temporary) / "SKILL.md"
            skill_path.write_text("# Exact skill\n", encoding="utf-8")
            self.assertEqual(
                load_skill_body_sha256(skill_path),
                "f35eb9921d4fe1ae3ee838d9c839974815700152b78d32fb904b0c5824cb2596",
            )
            other_path = Path(temporary) / "skill.md"
            other_path.write_text("# Exact skill\n", encoding="utf-8")
            with self.assertRaisesRegex(SkillBodyInvocationError, "SKILL.md"):
                load_skill_body_sha256(other_path)


if __name__ == "__main__":
    unittest.main()
