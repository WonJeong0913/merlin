from __future__ import annotations

import unittest

from src.merlin_harness.verifier_trust import (
    VerifierTrustLevel,
    VerifierTrustProfile,
    assess_verifier_trust,
    verifier_is_trusted,
)


def _profile(level: VerifierTrustLevel) -> VerifierTrustProfile:
    return VerifierTrustProfile(
        verifier_id="verify-output-v1",
        level=level,
        deterministic=level != VerifierTrustLevel.INDEPENDENT_SURROGATE,
        requirement_ids=("output-content", "edge-case"),
        covered_requirement_ids=("output-content", "edge-case"),
        behavioral_assertion_count=(0 if level == VerifierTrustLevel.STRUCTURAL else 2),
        author_independent_from_candidate=True,
        hidden_from_reviser=True,
        provenance_sha256="c" * 64,
    )


class VerifierTrustTests(unittest.TestCase):
    def test_structural_file_existence_profile_is_not_repair_evidence(self) -> None:
        profile = _profile(VerifierTrustLevel.STRUCTURAL)
        checks = assess_verifier_trust(profile, purpose="repair_feedback")

        self.assertFalse(verifier_is_trusted(profile, purpose="repair_feedback"))
        self.assertFalse(
            next(item for item in checks if item.name == "verifier_behavioral_depth").passed
        )

    def test_independent_surrogate_can_guide_repair_but_cannot_promote(self) -> None:
        profile = _profile(VerifierTrustLevel.INDEPENDENT_SURROGATE)

        self.assertTrue(verifier_is_trusted(profile, purpose="repair_feedback"))
        self.assertFalse(verifier_is_trusted(profile, purpose="promotion"))

    def test_hidden_deterministic_behavioral_verifier_can_promote(self) -> None:
        profile = _profile(VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL)

        self.assertTrue(verifier_is_trusted(profile, purpose="repair_feedback"))
        self.assertTrue(verifier_is_trusted(profile, purpose="promotion"))

    def test_incomplete_requirement_coverage_or_bad_provenance_fails_closed(self) -> None:
        profile = VerifierTrustProfile(
            verifier_id="verify-output-v1",
            level=VerifierTrustLevel.HIDDEN_ORACLE,
            deterministic=True,
            requirement_ids=("a", "b"),
            covered_requirement_ids=("a",),
            behavioral_assertion_count=2,
            author_independent_from_candidate=True,
            hidden_from_reviser=True,
            provenance_sha256="not-a-hash",
        )

        checks = assess_verifier_trust(profile, purpose="promotion")
        failed = {item.name for item in checks if not item.passed}
        self.assertIn("verifier_requirement_coverage", failed)
        self.assertIn("verifier_provenance_hashed", failed)
        self.assertFalse(verifier_is_trusted(profile, purpose="promotion"))


if __name__ == "__main__":
    unittest.main()
