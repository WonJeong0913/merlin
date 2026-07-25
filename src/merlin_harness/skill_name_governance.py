"""Deterministic same-name variant governance for prompt provisioning.

This module creates a read-only, name-unique projection of the active skill
library.  It never changes lifecycle state and does not claim that suppressed
variants are behaviorally inferior or safe to merge.  Merge/retire still
require their own verifier-backed lifecycle gates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from .models import LifecycleStatus, SkillArtifact


POLICY_VERSION = "declared-name-canonicalization-v1"


class SkillNameGovernanceError(ValueError):
    """Raised when a library cannot produce an auditable name projection."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _variant_preference(skill: SkillArtifact, declared_name: str) -> tuple[int, int, str]:
    """Match the frozen collision ablation's oracle-independent order."""

    return (
        0 if skill.id == declared_name else 1,
        0 if "@" not in skill.id else 1,
        skill.id,
    )


@dataclass(frozen=True, slots=True)
class SameNameCollisionGroup:
    declared_name: str
    variant_ids: tuple[str, ...]
    canonical_skill_id: str
    suppressed_skill_ids: tuple[str, ...]
    canonical_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared_name": self.declared_name,
            "variant_ids": list(self.variant_ids),
            "canonical_skill_id": self.canonical_skill_id,
            "suppressed_skill_ids": list(self.suppressed_skill_ids),
            "canonical_reason": self.canonical_reason,
        }


@dataclass(frozen=True, slots=True)
class NameUniqueProvisioningView:
    policy_version: str
    source_active_skill_ids: tuple[str, ...]
    provisionable_active_skill_ids: tuple[str, ...]
    suppressed_skill_ids: tuple[str, ...]
    collision_groups: tuple[SameNameCollisionGroup, ...]
    source_snapshot_sha256: str
    projection_sha256: str

    @property
    def source_active_count(self) -> int:
        return len(self.source_active_skill_ids)

    @property
    def provisionable_active_count(self) -> int:
        return len(self.provisionable_active_skill_ids)

    def canonical_for(self, skill_id: str) -> str | None:
        for group in self.collision_groups:
            if skill_id in group.variant_ids:
                return group.canonical_skill_id
        return None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "source_active_count": self.source_active_count,
            "provisionable_active_count": self.provisionable_active_count,
            "collision_group_count": len(self.collision_groups),
            "suppressed_variant_count": len(self.suppressed_skill_ids),
            "source_active_skill_ids": list(self.source_active_skill_ids),
            "provisionable_active_skill_ids": list(
                self.provisionable_active_skill_ids
            ),
            "suppressed_skill_ids": list(self.suppressed_skill_ids),
            "collision_groups": [group.to_dict() for group in self.collision_groups],
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "projection_sha256": self.projection_sha256,
            "boundary": {
                "prompt_provisioning_projection_only": True,
                "source_library_mutated": False,
                "behavioral_equivalence_claimed": False,
                "merge_or_retire_authorized": False,
                "task_utility_measured": False,
            },
        }


def build_name_unique_provisioning_view(
    skills: Iterable[SkillArtifact],
) -> NameUniqueProvisioningView:
    """Return a deterministic active-library projection without mutation."""

    materialized = tuple(skills)
    ids = [skill.id for skill in materialized]
    if len(ids) != len(set(ids)):
        raise SkillNameGovernanceError("skill IDs must be unique")
    if any(not skill.id.strip() for skill in materialized):
        raise SkillNameGovernanceError("skill IDs must be non-empty")

    active = sorted(
        (skill for skill in materialized if skill.status == LifecycleStatus.ACTIVE),
        key=lambda skill: skill.id,
    )
    if any(not skill.name.strip() for skill in active):
        raise SkillNameGovernanceError("active skill declared names must be non-empty")

    grouped: dict[str, list[SkillArtifact]] = {}
    for skill in active:
        grouped.setdefault(skill.name.strip(), []).append(skill)

    provisionable: list[str] = []
    groups: list[SameNameCollisionGroup] = []
    for declared_name in sorted(grouped):
        variants = grouped[declared_name]
        canonical = min(
            variants,
            key=lambda skill: _variant_preference(skill, declared_name),
        )
        provisionable.append(canonical.id)
        if len(variants) > 1:
            variant_ids = tuple(sorted(skill.id for skill in variants))
            if canonical.id == declared_name:
                reason = "variant_id_exactly_matches_declared_name"
            elif "@" not in canonical.id:
                reason = "lexical_unversioned_variant"
            else:
                reason = "lexical_versioned_variant"
            groups.append(
                SameNameCollisionGroup(
                    declared_name=declared_name,
                    variant_ids=variant_ids,
                    canonical_skill_id=canonical.id,
                    suppressed_skill_ids=tuple(
                        skill_id for skill_id in variant_ids if skill_id != canonical.id
                    ),
                    canonical_reason=reason,
                )
            )

    source_ids = tuple(skill.id for skill in active)
    provisionable_ids = tuple(sorted(provisionable))
    suppressed_ids = tuple(
        sorted(skill_id for group in groups for skill_id in group.suppressed_skill_ids)
    )
    source_records = [
        {
            "skill_id": skill.id,
            "declared_name": skill.name.strip(),
            "version": skill.version,
            "status": skill.status.value,
        }
        for skill in active
    ]
    projection = {
        "policy_version": POLICY_VERSION,
        "source_active_skill_ids": source_ids,
        "provisionable_active_skill_ids": provisionable_ids,
        "collision_groups": [group.to_dict() for group in groups],
    }
    return NameUniqueProvisioningView(
        policy_version=POLICY_VERSION,
        source_active_skill_ids=source_ids,
        provisionable_active_skill_ids=provisionable_ids,
        suppressed_skill_ids=suppressed_ids,
        collision_groups=tuple(groups),
        source_snapshot_sha256=_sha256(source_records),
        projection_sha256=_sha256(projection),
    )
