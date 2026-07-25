"""Export a safe model-free audit of runtime same-name skill governance."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from experiments.skillsbench.run_gpt56_name_collision_ablation import build_plan
from experiments.skillsbench.run_gpt56_selection_shadowing_pilot import (
    _arm,
    _canonical_json,
    declared_skill_name,
)
from src.merlin_harness.models import LifecycleStatus, SkillArtifact
from src.merlin_harness.skill_name_governance import build_name_unique_provisioning_view


EVIDENCE_ID = "runtime-name-governance-on-frozen-56-v1"


class RuntimeNameGovernanceEvidenceError(ValueError):
    """Raised when safe collision-governance evidence drifts."""


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def build_report() -> dict[str, Any]:
    plan = build_plan()
    raw = _arm(plan, "raw-56")
    records = plan["skill_records"]
    library = tuple(
        SkillArtifact(
            id=skill_id,
            name=declared_skill_name(skill_id),
            description=records[skill_id]["description"],
            trigger=records[skill_id]["description"],
            status=LifecycleStatus.ACTIVE,
        )
        for skill_id in raw["skill_ids"]
    )
    before = [skill.to_dict() for skill in library]
    frozen = copy.deepcopy(library)
    view = build_name_unique_provisioning_view(library)
    after = [skill.to_dict() for skill in library]
    if before != after:
        raise RuntimeNameGovernanceEvidenceError("runtime projection mutated its source")
    groups = [group.to_dict() for group in view.collision_groups]
    if {
        group["declared_name"]: group["canonical_skill_id"] for group in groups
    } != {"docx": "docx", "pdf": "pdf"}:
        raise RuntimeNameGovernanceEvidenceError("frozen canonical variants drifted")
    if not all(
        left.to_dict() == right.to_dict() for left, right in zip(library, frozen)
    ):
        raise RuntimeNameGovernanceEvidenceError("copy comparison detected mutation")

    body: dict[str, Any] = {
        "schema_version": 1,
        "evidence_id": EVIDENCE_ID,
        "source": {
            "ablation_plan_sha256": plan["plan_sha256"],
            "raw_56_membership_sha256": raw["membership_sha256"],
            "source_active_snapshot_sha256": view.source_snapshot_sha256,
        },
        "policy": {
            "runtime_policy_version": view.policy_version,
            "canonical_preference_order": [
                "variant ID exactly equals declared name",
                "unversioned variant ID",
                "lexical variant ID",
            ],
            "oracle_labels_used": False,
        },
        "audit": {
            "source_variant_count": view.source_active_count,
            "source_declared_name_count": len(
                {declared_skill_name(skill_id) for skill_id in raw["skill_ids"]}
            ),
            "collision_group_count": len(view.collision_groups),
            "suppressed_variant_count": len(view.suppressed_skill_ids),
            "runtime_prompt_candidate_count": view.provisionable_active_count,
            "collision_groups": groups,
            "suppressed_skill_ids": list(view.suppressed_skill_ids),
            "projection_sha256": view.projection_sha256,
            "source_library_before_sha256": _sha256(before),
            "source_library_after_sha256": _sha256(after),
            "source_library_mutated": before != after,
        },
        "experiment_mapping": {
            "runtime_projection_reduces_56_to_53": True,
            "confirmatory_ablation_holds_size_56_by_deterministic_replacement": True,
            "same_canonical_preference_implementation": True,
            "actual_confirmatory_provider_result_available": False,
        },
        "claim_boundary": {
            "model_free": True,
            "provider_turns": 0,
            "selection_accuracy_measured": False,
            "task_execution_or_utility_measured": False,
            "source_library_mutated": False,
            "merge_or_retire_authorized": False,
            "full87_result": False,
        },
    }
    return {**body, "evidence_sha256": _sha256(body)}


def validate_report(report: dict[str, Any]) -> None:
    expected = build_report()
    if report != expected:
        raise RuntimeNameGovernanceEvidenceError(
            "runtime same-name governance evidence does not match frozen reconstruction"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve(strict=False)
    if output.exists() or output.is_symlink():
        parser.error("refusing to overwrite runtime name-governance evidence")
    report = build_report()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"saved -> {output}")
    print(
        f"variants={report['audit']['source_variant_count']} "
        f"names={report['audit']['source_declared_name_count']} "
        f"collisions={report['audit']['collision_group_count']} "
        f"suppressed={report['audit']['suppressed_variant_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
