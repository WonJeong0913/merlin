"""Freeze an immutable execution plan for a full-87 library-scale manifest.

The plan is orchestration evidence, not model execution or a benchmark result.
It supports both the 1,305-cell curated-reference manifest and the 1,566-cell
empirical-oracle-bound manifest without weakening either validation contract.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    LibraryScaleManifestError,
    sha256_file,
    sha256_json,
    validate_library_scale_manifest,
)
from src.merlin_harness.management import content_sha256


class LibraryScaleBatchPlanError(ValueError):
    """Raised when a library-scale execution plan cannot be frozen."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise LibraryScaleBatchPlanError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryScaleBatchPlanError(f"{label} is missing or invalid") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise LibraryScaleBatchPlanError(f"{label} must be a regular JSON object")
    return resolved, value


def _validated_manifest(
    *,
    manifest_path: Path,
    base_manifest_path: Path | None,
    empirical_oracle_path: Path | None,
    index_path: Path,
    skills_root: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    manifest_file, manifest = _load_json(manifest_path, label="library-scale manifest")
    schema_version = manifest.get("schema_version")
    dependencies: dict[str, Any] = {
        "manifest_file_sha256": sha256_file(manifest_file),
        "manifest_semantic_sha256": sha256_json(manifest),
        "base_manifest_file_sha256": None,
        "empirical_oracle_file_sha256": None,
    }
    if schema_version == 1:
        if base_manifest_path is not None or empirical_oracle_path is not None:
            raise LibraryScaleBatchPlanError(
                "schema 1 planning does not accept derived-manifest dependencies"
            )
        try:
            validate_library_scale_manifest(
                manifest,
                index_path=index_path,
                skills_root=skills_root,
            )
        except LibraryScaleManifestError as exc:
            raise LibraryScaleBatchPlanError(str(exc)) from exc
    elif schema_version == 2:
        if base_manifest_path is None or empirical_oracle_path is None:
            raise LibraryScaleBatchPlanError(
                "schema 2 planning requires base manifest and empirical oracle"
            )
        from experiments.skillsbench.bind_empirical_oracle_manifest import (
            OracleBoundManifestError,
            validate_oracle_bound_manifest,
        )

        try:
            validate_oracle_bound_manifest(
                manifest,
                base_manifest_path=base_manifest_path,
                empirical_oracle_path=empirical_oracle_path,
                index_path=index_path,
                skills_root=skills_root,
            )
            dependencies["base_manifest_file_sha256"] = sha256_file(
                base_manifest_path.resolve(strict=True)
            )
            dependencies["empirical_oracle_file_sha256"] = sha256_file(
                empirical_oracle_path.resolve(strict=True)
            )
        except (OracleBoundManifestError, OSError) as exc:
            raise LibraryScaleBatchPlanError(str(exc)) from exc
    else:
        raise LibraryScaleBatchPlanError("manifest schema version must be 1 or 2")
    return manifest_file, manifest, dependencies


def build_library_scale_batch_plan(
    *,
    manifest_path: Path,
    base_manifest_path: Path | None = None,
    empirical_oracle_path: Path | None = None,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    manifest_file, manifest, dependencies = _validated_manifest(
        manifest_path=manifest_path,
        base_manifest_path=base_manifest_path,
        empirical_oracle_path=empirical_oracle_path,
        index_path=index_path,
        skills_root=skills_root,
    )
    cells = manifest.get("cells")
    if not isinstance(cells, list) or not cells:
        raise LibraryScaleBatchPlanError("manifest cells must be a non-empty list")
    planned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ordinal, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict):
            raise LibraryScaleBatchPlanError("manifest contains a non-object cell")
        cell_id = cell.get("cell_id")
        if not isinstance(cell_id, str) or not cell_id or cell_id in seen:
            raise LibraryScaleBatchPlanError("manifest cell IDs must be unique strings")
        seen.add(cell_id)
        planned.append(
            {
                "ordinal": ordinal,
                "cell_id": cell_id,
                "task_id": cell["task_id"],
                "trial_index": cell["trial_index"],
                "trial_seed": cell["trial_seed"],
                "arm_id": cell["arm_id"],
                "library_size": cell["library_size"],
                "library_snapshot_sha256": cell["library_snapshot_sha256"],
                "verifier_contract_sha256": cell["verifier_contract_sha256"],
                "pair_key": content_sha256(
                    {"task_id": cell["task_id"], "trial_index": cell["trial_index"]}
                ),
                "work_key": content_sha256({"cell_id": cell_id}),
                "initial_status": "pending",
            }
        )
    task_ids = {cell["task_id"] for cell in cells}
    trial_indices = manifest.get("trial_indices")
    arms = list(dict.fromkeys(cell["arm_id"] for cell in cells))
    if len(cells) != manifest.get("expected_cells"):
        raise LibraryScaleBatchPlanError("manifest expected-cell denominator drifted")
    dependencies = {
        "experiment_id": manifest.get("experiment_id"),
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_path": manifest_file.name,
        **dependencies,
    }
    plan: dict[str, Any] = {
        "schema_version": 1,
        "plan_kind": "full87-library-scale-evidence-plan-v1",
        "batch_id": f"library-scale-{content_sha256(dependencies)[:20]}",
        "dependencies": dependencies,
        "counts": {
            "scheduled_cells": len(cells),
            "task_count": len(task_ids),
            "trial_count": len(trial_indices) if isinstance(trial_indices, list) else None,
            "arm_count": len(arms),
            "pending_cells": len(cells),
        },
        "arm_order": arms,
        "cells": planned,
        "execution_policy": {
            "order": "validated_manifest_cell_order",
            "concurrency": 1,
            "materialization": "new-only-per-cell",
            "trace_record": "immutable-per-cell",
            "raw_provider_trace": "hash-bound-outside-safe-summary",
            "resume_source": "revalidated_cell_contract_plus_trace_only",
            "automatic_cleanup": False,
            "timeout_zero_requires_preregistered_numeric_record": True,
            "infrastructure_failure_must_remain_unscored": True,
        },
        "claim_boundary": {
            "plan_is_model_execution": False,
            "plan_is_benchmark_result": False,
            "full87_completion_claimed": False,
            "shadowing_curve_claimed": False,
            "provider_resolved_model_identity_claimed": False,
        },
    }
    plan["plan_sha256"] = content_sha256(plan)
    return plan


def write_library_scale_batch_plan(*, output_path: Path, **kwargs: Any) -> dict[str, Any]:
    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise LibraryScaleBatchPlanError("batch plan output must be new-only")
    plan = build_library_scale_batch_plan(**kwargs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise LibraryScaleBatchPlanError("batch plan output must be new-only") from exc
    return plan


def validate_library_scale_batch_plan(*, plan_path: Path, **kwargs: Any) -> dict[str, Any]:
    _path, stored = _load_json(plan_path, label="library-scale batch plan")
    stored_hash = stored.get("plan_sha256")
    unhashed = dict(stored)
    unhashed.pop("plan_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise LibraryScaleBatchPlanError("batch plan hash mismatch")
    expected = build_library_scale_batch_plan(**kwargs)
    if stored != expected:
        raise LibraryScaleBatchPlanError("batch plan drifted from frozen dependencies")
    return stored


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
        "manifest_path": args.manifest,
        "base_manifest_path": args.base_manifest,
        "empirical_oracle_path": args.empirical_oracle,
        "index_path": args.index,
        "skills_root": args.skills_root,
    }
    try:
        plan = (
            validate_library_scale_batch_plan(plan_path=args.plan, **kwargs)
            if args.plan is not None
            else write_library_scale_batch_plan(output_path=args.output, **kwargs)
        )
    except LibraryScaleBatchPlanError as exc:
        parser.error(str(exc))
    print("Merlin full-87 library-scale batch plan")
    print("status=revalidated" if args.plan is not None else "status=created")
    print(f"batch_id={plan['batch_id']}")
    print(f"scheduled={plan['counts']['scheduled_cells']}")
    print("model_execution=false")
    print("benchmark_result=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
