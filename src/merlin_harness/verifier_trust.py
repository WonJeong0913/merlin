"""Evidence contracts for verifier authorship, depth, and independence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from .models import ValidationResult


SHA256_RE = re.compile(r"[0-9a-f]{64}")


class VerifierTrustLevel(str, Enum):
    STRUCTURAL = "structural"
    DETERMINISTIC_BEHAVIORAL = "deterministic_behavioral"
    INDEPENDENT_SURROGATE = "independent_surrogate"
    HIDDEN_ORACLE = "hidden_oracle"


@dataclass(frozen=True, slots=True)
class VerifierTrustProfile:
    verifier_id: str
    level: VerifierTrustLevel
    deterministic: bool
    requirement_ids: tuple[str, ...]
    covered_requirement_ids: tuple[str, ...]
    behavioral_assertion_count: int
    author_independent_from_candidate: bool
    hidden_from_reviser: bool
    provenance_sha256: str


def assess_verifier_trust(
    profile: VerifierTrustProfile,
    *,
    purpose: Literal["repair_feedback", "promotion"],
) -> tuple[ValidationResult, ...]:
    """Return explicit trust checks for one verifier use.

    Surrogate verifiers can provide repair feedback, but cannot by themselves
    promote a skill. Promotion requires deterministic behavioral or hidden
    oracle evidence that is independent and hidden from the reviser.
    """

    if purpose not in {"repair_feedback", "promotion"}:
        raise ValueError(f"unsupported verifier trust purpose: {purpose}")
    required = set(profile.requirement_ids)
    covered = set(profile.covered_requirement_ids)
    base = [
        ValidationResult(
            "verifier_has_stable_id",
            bool(profile.verifier_id.strip()),
            evidence=profile.verifier_id,
        ),
        ValidationResult(
            "verifier_provenance_hashed",
            bool(SHA256_RE.fullmatch(profile.provenance_sha256)),
            evidence="SHA-256 provenance is present",
        ),
        ValidationResult(
            "verifier_requirements_declared",
            bool(required),
            evidence=f"declared={len(required)}",
        ),
        ValidationResult(
            "verifier_requirement_coverage",
            bool(required) and required <= covered,
            score=(len(required & covered) / len(required) if required else 0.0),
            evidence=f"covered={len(required & covered)}/{len(required)}",
        ),
        ValidationResult(
            "verifier_behavioral_depth",
            profile.behavioral_assertion_count > 0
            and profile.level != VerifierTrustLevel.STRUCTURAL,
            score=float(profile.behavioral_assertion_count),
            evidence=f"behavioral_assertions={profile.behavioral_assertion_count}",
        ),
        ValidationResult(
            "verifier_author_independence",
            profile.author_independent_from_candidate,
            evidence="verifier authoring/evaluation is isolated from candidate authoring",
        ),
    ]
    if purpose == "repair_feedback":
        base.append(
            ValidationResult(
                "verifier_feedback_eligible",
                profile.level
                in {
                    VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL,
                    VerifierTrustLevel.INDEPENDENT_SURROGATE,
                    VerifierTrustLevel.HIDDEN_ORACLE,
                }
                and (profile.deterministic or profile.author_independent_from_candidate),
                evidence=f"level={profile.level.value}",
            )
        )
    else:
        base.extend(
            [
                ValidationResult(
                    "verifier_promotion_level",
                    profile.level
                    in {
                        VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL,
                        VerifierTrustLevel.HIDDEN_ORACLE,
                    }
                    and profile.deterministic,
                    evidence=f"level={profile.level.value}; deterministic={profile.deterministic}",
                ),
                ValidationResult(
                    "verifier_hidden_from_reviser",
                    profile.hidden_from_reviser,
                    evidence="held-out content is not exposed to the reviser",
                ),
            ]
        )
    return tuple(base)


def verifier_is_trusted(
    profile: VerifierTrustProfile,
    *,
    purpose: Literal["repair_feedback", "promotion"],
) -> bool:
    return all(result.passed for result in assess_verifier_trust(profile, purpose=purpose))
