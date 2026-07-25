"""Derive the frozen 435-cell trial-1 plan from the canonical 1,305 plan.

The selection rule is structural and outcome-blind: retain every task, retain
all five canonical arms, and retain only ``trial_index == 1``.  The derived
plan never weakens or rewrites the source manifest or source batch plan.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from experiments.skillsbench.create_library_scale_batch_plan import (
    LibraryScaleBatchPlanError,
    validate_library_scale_batch_plan,
)
from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    sha256_file,
)
from src.merlin_harness.management import content_sha256


TRIAL_INDEX = 1
ARM_ORDER = ("curated", "plus-10", "plus-50", "plus-100", "full-209")
EXPECTED_TASKS = 87
EXPECTED_CELLS = EXPECTED_TASKS * len(ARM_ORDER)
PLAN_KIND = "full87-library-scale-trial1-five-arm-plan-v1"


class LibraryScaleTrial1PlanError(ValueError):
    """Raised when the structural 435-cell subset cannot be reproduced."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise LibraryScaleTrial1PlanError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryScaleTrial1PlanError(f"{label} is missing or invalid") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise LibraryScaleTrial1PlanError(f"{label} must be a regular JSON object")
    return resolved, value


def build_library_scale_trial1_plan(
    *,
    source_plan_path: Path,
    manifest_path: Path,
    base_manifest_path: Path | None = None,
    empirical_oracle_path: Path | None = None,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    try:
        source = validate_library_scale_batch_plan(
            plan_path=source_plan_path,
            manifest_path=manifest_path,
            base_manifest_path=base_manifest_path,
            empirical_oracle_path=empirical_oracle_path,
            index_path=index_path,
            skills_root=skills_root,
        )
    except LibraryScaleBatchPlanError as exc:
        raise LibraryScaleTrial1PlanError(str(exc)) from exc
    counts = source.get("counts")
    cells = source.get("cells")
    if (
        source.get("plan_kind") != "full87-library-scale-evidence-plan-v1"
        or source.get("arm_order") != list(ARM_ORDER)
        or not isinstance(counts, dict)
        or counts.get("scheduled_cells") != 1305
        or counts.get("task_count") != EXPECTED_TASKS
        or counts.get("trial_count") != 3
        or counts.get("arm_count") != len(ARM_ORDER)
        or not isinstance(cells, list)
        or len(cells) != 1305
    ):
        raise LibraryScaleTrial1PlanError(
            "source plan is not the canonical 87-task, 3-trial, five-arm schedule"
        )

    selected_source = [cell for cell in cells if cell.get("trial_index") == TRIAL_INDEX]
    if len(selected_source) != EXPECTED_CELLS:
        raise LibraryScaleTrial1PlanError("trial-1 selection denominator drifted")
    task_order: list[str] = []
    task_groups: dict[str, list[dict[str, Any]]] = {}
    for cell in selected_source:
        task_id = cell.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise LibraryScaleTrial1PlanError("selected cell has an invalid task ID")
        if task_id not in task_groups:
            task_order.append(task_id)
            task_groups[task_id] = []
        task_groups[task_id].append(cell)
    if len(task_order) != EXPECTED_TASKS:
        raise LibraryScaleTrial1PlanError("trial-1 task denominator drifted")
    for task_id in task_order:
        group = task_groups[task_id]
        if [cell.get("arm_id") for cell in group] != list(ARM_ORDER):
            raise LibraryScaleTrial1PlanError(
                f"trial-1 arm order or coverage drifted: {task_id}"
            )
        if len({cell.get("pair_key") for cell in group}) != 1:
            raise LibraryScaleTrial1PlanError(f"trial-1 pair binding drifted: {task_id}")

    planned: list[dict[str, Any]] = []
    for ordinal, cell in enumerate(selected_source, start=1):
        item = dict(cell)
        item["source_ordinal"] = item["ordinal"]
        item["ordinal"] = ordinal
        planned.append(item)

    source_plan_file, _ = _load_json(source_plan_path, label="source batch plan")
    dependencies = {
        **source["dependencies"],
        "source_batch_id": source["batch_id"],
        "source_batch_plan_sha256": source["plan_sha256"],
        "source_batch_plan_file_sha256": sha256_file(source_plan_file),
    }
    selection_contract = {
        "policy": "all_tasks_trial_1_all_five_arms",
        "trial_indices": [TRIAL_INDEX],
        "arm_order": list(ARM_ORDER),
        "task_selection": "all_87_in_source_order",
        "outcome_fields_read": [],
        "outcome_based_selection_allowed": False,
        "cherry_picking_allowed": False,
    }
    plan: dict[str, Any] = {
        "schema_version": 1,
        "plan_kind": PLAN_KIND,
        "batch_id": (
            "library-scale-trial1-"
            + content_sha256(
                {"dependencies": dependencies, "selection": selection_contract}
            )[:20]
        ),
        "dependencies": dependencies,
        "selection_contract": selection_contract,
        "counts": {
            "scheduled_cells": EXPECTED_CELLS,
            "source_scheduled_cells": 1305,
            "task_count": EXPECTED_TASKS,
            "trial_count": 1,
            "arm_count": len(ARM_ORDER),
            "pending_cells": EXPECTED_CELLS,
        },
        "arm_order": list(ARM_ORDER),
        "cells": planned,
        "execution_policy": {
            **source["execution_policy"],
            "order": "source_plan_order_filtered_by_frozen_trial_1_predicate",
            "concurrency": 1,
        },
        "claim_boundary": {
            "plan_is_model_execution": False,
            "plan_is_benchmark_result": False,
            "full_1305_completion_claimed": False,
            "trial1_435_completion_claimed": False,
            "shadowing_curve_claimed": False,
            "provider_resolved_model_identity_claimed": False,
        },
    }
    plan["plan_sha256"] = content_sha256(plan)
    return plan


def write_library_scale_trial1_plan(
    *, output_path: Path, **kwargs: Any
) -> dict[str, Any]:
    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise LibraryScaleTrial1PlanError("derived plan output must be new-only")
    plan = build_library_scale_trial1_plan(**kwargs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise LibraryScaleTrial1PlanError("derived plan output must be new-only") from exc
    return plan


def validate_library_scale_trial1_plan(
    *, plan_path: Path, **kwargs: Any
) -> dict[str, Any]:
    _path, stored = _load_json(plan_path, label="derived trial-1 plan")
    stored_hash = stored.get("plan_sha256")
    unhashed = dict(stored)
    unhashed.pop("plan_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise LibraryScaleTrial1PlanError("derived plan hash mismatch")
    expected = build_library_scale_trial1_plan(**kwargs)
    if stored != expected:
        raise LibraryScaleTrial1PlanError(
            "derived plan drifted from the frozen source plan and selection rule"
        )
    return stored


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path)
    parser.add_argument("--empirical-oracle", type=Path)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--plan", type=Path)
    args = parser.parse_args(argv)
    kwargs = {
        "source_plan_path": args.source_plan,
        "manifest_path": args.manifest,
        "base_manifest_path": args.base_manifest,
        "empirical_oracle_path": args.empirical_oracle,
        "index_path": args.index,
        "skills_root": args.skills_root,
    }
    try:
        plan = (
            validate_library_scale_trial1_plan(plan_path=args.plan, **kwargs)
            if args.plan is not None
            else write_library_scale_trial1_plan(output_path=args.output, **kwargs)
        )
    except LibraryScaleTrial1PlanError as exc:
        parser.error(str(exc))
    print("Merlin 435-cell trial-1 derived plan")
    print("status=revalidated" if args.plan is not None else "status=created")
    print(f"batch_id={plan['batch_id']}")
    print(f"scheduled={plan['counts']['scheduled_cells']}")
    print("model_execution=false")
    print("benchmark_result=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
