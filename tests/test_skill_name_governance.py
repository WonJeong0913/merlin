from __future__ import annotations

import copy
import unittest

from src.merlin_harness.models import LifecycleStatus, SkillArtifact
from src.merlin_harness.skill_name_governance import (
    SkillNameGovernanceError,
    build_name_unique_provisioning_view,
)


def skill(
    skill_id: str,
    name: str,
    *,
    status: LifecycleStatus = LifecycleStatus.ACTIVE,
) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name=name,
        description=f"description for {skill_id}",
        trigger=f"trigger for {skill_id}",
        status=status,
    )


class SkillNameGovernanceTests(unittest.TestCase):
    def test_exact_declared_name_id_is_canonical_without_mutating_library(self) -> None:
        library = (
            skill("docx@d3cfe519dca2", "docx"),
            skill("docx", "docx"),
            skill("pdf", "pdf"),
        )
        before = copy.deepcopy(library)

        view = build_name_unique_provisioning_view(library)

        self.assertEqual(view.source_active_count, 3)
        self.assertEqual(view.provisionable_active_count, 2)
        self.assertEqual(view.provisionable_active_skill_ids, ("docx", "pdf"))
        self.assertEqual(view.suppressed_skill_ids, ("docx@d3cfe519dca2",))
        self.assertEqual(view.collision_groups[0].canonical_skill_id, "docx")
        self.assertEqual(
            view.collision_groups[0].canonical_reason,
            "variant_id_exactly_matches_declared_name",
        )
        self.assertEqual(
            [item.to_dict() for item in library],
            [item.to_dict() for item in before],
        )
        self.assertFalse(view.to_safe_dict()["boundary"]["merge_or_retire_authorized"])

    def test_unversioned_then_lexical_fallback_is_deterministic(self) -> None:
        unversioned = build_name_unique_provisioning_view(
            [skill("word-writer@2", "writer"), skill("word-writer", "writer")]
        )
        self.assertEqual(unversioned.provisionable_active_skill_ids, ("word-writer",))
        self.assertEqual(
            unversioned.collision_groups[0].canonical_reason,
            "lexical_unversioned_variant",
        )

        versioned = build_name_unique_provisioning_view(
            [skill("writer@b", "writer"), skill("writer@a", "writer")]
        )
        self.assertEqual(versioned.provisionable_active_skill_ids, ("writer@a",))
        self.assertEqual(
            versioned.collision_groups[0].canonical_reason,
            "lexical_versioned_variant",
        )

    def test_order_and_inactive_variants_do_not_change_projection(self) -> None:
        left = [
            skill("docx@alt", "docx", status=LifecycleStatus.HIDDEN),
            skill("pdf", "pdf"),
            skill("docx", "docx"),
        ]
        right = list(reversed(left))
        first = build_name_unique_provisioning_view(left)
        second = build_name_unique_provisioning_view(right)
        self.assertEqual(first, second)
        self.assertEqual(first.provisionable_active_skill_ids, ("docx", "pdf"))
        self.assertEqual(first.collision_groups, ())

    def test_duplicate_ids_and_empty_active_names_fail_closed(self) -> None:
        duplicate = skill("same", "one")
        with self.assertRaisesRegex(SkillNameGovernanceError, "IDs must be unique"):
            build_name_unique_provisioning_view([duplicate, copy.deepcopy(duplicate)])
        with self.assertRaisesRegex(SkillNameGovernanceError, "names must be non-empty"):
            build_name_unique_provisioning_view([skill("empty", " ")])


if __name__ == "__main__":
    unittest.main()
