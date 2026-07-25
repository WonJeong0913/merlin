"""Bind verified empirical oracles into an oracle-only library-scale arm."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.skillsbench.aggregate_library_scale_results import (
    LibraryScaleAggregationError,
    load_empirical_oracle_mapping,
)
from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    LibraryScaleManifestError,
    _load_index,
    indexed_variant_snapshot_records,
    sha256_file,
    sha256_json,
    validate_library_scale_manifest,
    write_json_atomic,
)


DEFAULT_BASE_MANIFEST = Path(__file__).resolve().parent / "library-scale-manifest.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "library-scale-oracle-bound-manifest.json"


class OracleBoundManifestError(ValueError):
    """Raised when empirical oracle evidence cannot produce a frozen schedule."""


def build_oracle_bound_manifest(
    *,
    base_manifest: Mapping[str, Any],
    empirical_oracle_by_task: Mapping[str, Sequence[str]],
    oracle_estimation_contract: Mapping[str, Any],
    base_manifest_sha256: str,
    empirical_oracle_manifest_sha256: str,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
    created: str | None = None,
) -> dict[str, Any]:
    """Create a six-arm derived schedule without modifying the base manifest."""

    task_contracts_source = base_manifest.get("task_contracts")
    base_cells = base_manifest.get("cells")
    if not isinstance(task_contracts_source, list) or not isinstance(base_cells, list):
        raise OracleBoundManifestError("base manifest lacks task contracts or cells")
    task_ids = {task.get("task_id") for task in task_contracts_source if isinstance(task, dict)}
    if None in task_ids or set(empirical_oracle_by_task) != task_ids:
        raise OracleBoundManifestError("empirical oracle mapping must cover every base task exactly")
    for label, value in (
        ("base manifest", base_manifest_sha256),
        ("empirical oracle manifest", empirical_oracle_manifest_sha256),
    ):
        if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise OracleBoundManifestError(f"{label} hash must be a lowercase SHA-256")

    index = _load_index(index_path)
    _, variants = indexed_variant_snapshot_records(index, skills_root)
    task_contracts: list[dict[str, Any]] = []
    normalized_oracles: dict[str, tuple[str, ...]] = {}
    for source in task_contracts_source:
        task_id = source["task_id"]
        oracle_value = empirical_oracle_by_task[task_id]
        if not isinstance(oracle_value, (list, tuple)) or any(
            not isinstance(skill_id, str) or not skill_id for skill_id in oracle_value
        ):
            raise OracleBoundManifestError(f"empirical oracle for {task_id} is invalid")
        oracle_ids = tuple(oracle_value)
        if len(oracle_ids) != len(set(oracle_ids)):
            raise OracleBoundManifestError(f"empirical oracle for {task_id} contains duplicates")
        reference = tuple(source.get("reference_skill_variants", []))
        if not set(oracle_ids).issubset(reference):
            raise OracleBoundManifestError(
                f"empirical oracle for {task_id} escapes the curated candidate scope"
            )
        normalized_oracles[task_id] = oracle_ids
        task_contract = dict(source)
        task_contract["empirical_oracle_skill_variants"] = list(oracle_ids)
        task_contracts.append(task_contract)

    base_by_task_trial: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for cell in base_cells:
        key = (cell["task_id"], cell["trial_index"])
        base_by_task_trial.setdefault(key, []).append(cell)

    cells: list[dict[str, Any]] = []
    expected_base_arm_order = ["curated", "plus-10", "plus-50", "plus-100", f"full-{base_manifest['skill_pool_count']}"]
    for task_id in sorted(task_ids):
        oracle_ids = normalized_oracles[task_id]
        for trial_index in base_manifest["trial_indices"]:
            group = base_by_task_trial.get((task_id, trial_index), [])
            by_arm = {cell["arm_id"]: cell for cell in group}
            if set(by_arm) != set(expected_base_arm_order):
                raise OracleBoundManifestError(
                    f"base arms are incomplete for {task_id} trial {trial_index}"
                )
            curated = by_arm["curated"]
            presentation_order = [
                skill_id for skill_id in curated["library_variant_ids"] if skill_id in set(oracle_ids)
            ]
            if set(presentation_order) != set(oracle_ids):
                raise OracleBoundManifestError(
                    f"oracle presentation order cannot be derived for {task_id} trial {trial_index}"
                )
            oracle_cell = {
                "actual_distractor_count": 0,
                "arm_id": "oracle-only",
                "cell_id": f"{task_id}__t{trial_index}__oracle-only",
                "library_size": len(presentation_order),
                "library_snapshot_sha256": sha256_json(
                    [variants[skill_id] for skill_id in presentation_order]
                ),
                "library_variant_ids": presentation_order,
                "reference_skill_variants": list(curated["reference_skill_variants"]),
                "empirical_oracle_skill_variants": list(oracle_ids),
                "requested_distractor_count": "empirical-oracle-only",
                "task_id": task_id,
                "task_instruction_sha256": curated["task_instruction_sha256"],
                "trial_index": trial_index,
                "trial_seed": curated["trial_seed"],
                "verifier_contract_sha256": curated["verifier_contract_sha256"],
            }
            cells.append(oracle_cell)
            for arm_id in expected_base_arm_order:
                expanded = dict(by_arm[arm_id])
                expanded["empirical_oracle_skill_variants"] = list(oracle_ids)
                cells.append(expanded)

    derived = {
        "schema_version": 2,
        "experiment_id": f"{base_manifest['experiment_id']}-oracle-bound-v1",
        "created": created or date.today().isoformat(),
        "source": base_manifest.get("source"),
        "commit": base_manifest.get("commit"),
        "license": base_manifest.get("license"),
        "base_seed": base_manifest.get("base_seed"),
        "trial_indices": list(base_manifest.get("trial_indices", [])),
        "distractor_counts": list(base_manifest.get("distractor_counts", [])),
        "task_count": base_manifest.get("task_count"),
        "skill_pool_count": base_manifest.get("skill_pool_count"),
        "arm_count_per_trial": 6,
        "expected_cells": len(task_ids) * len(base_manifest["trial_indices"]) * 6,
        "frozen_inputs": {
            **dict(base_manifest.get("frozen_inputs", {})),
            "base_library_scale_manifest_sha256": base_manifest_sha256,
            "empirical_oracle_manifest_sha256": empirical_oracle_manifest_sha256,
        },
        "design": {
            **dict(base_manifest.get("design", {})),
            "reference_arm": "empirical oracle-only skill set derived before scale evaluation",
            "oracle_binding": "oracle-only is a subset of the always-present curated candidate scope",
            "paired_expansion": "oracle-only, curated, +10, +50, +100, full for every task and trial",
        },
        "evidence_contract": {
            **dict(base_manifest.get("evidence_contract", {})),
            "empirical_oracle_bound": True,
            "headline_shadowing_claim_eligible": False,
            "headline_blocker": "requires complete scored execution and actual invocation evidence for all derived cells",
            "more_skills_decomposition_schedule_eligible": True,
        },
        "oracle_estimation_contract": dict(oracle_estimation_contract),
        "base_manifest": {
            "experiment_id": base_manifest.get("experiment_id"),
            "sha256": base_manifest_sha256,
        },
        "empirical_oracle_manifest": {
            "sha256": empirical_oracle_manifest_sha256,
        },
        "task_contracts": task_contracts,
        "cells": cells,
    }
    if len(cells) != derived["expected_cells"] or len({cell["cell_id"] for cell in cells}) != len(cells):
        raise OracleBoundManifestError("derived oracle-bound cell coverage is invalid")
    return derived


def build_oracle_bound_manifest_from_files(
    *,
    base_manifest_path: Path,
    empirical_oracle_path: Path,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
    created: str | None = None,
) -> dict[str, Any]:
    try:
        base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OracleBoundManifestError("cannot read base library-scale manifest") from exc
    try:
        validate_library_scale_manifest(base_manifest, index_path=index_path, skills_root=skills_root)
    except LibraryScaleManifestError as exc:
        raise OracleBoundManifestError(str(exc)) from exc
    try:
        mapping, oracle_contract = load_empirical_oracle_mapping(
            empirical_oracle_path,
            manifest=base_manifest,
            manifest_path=base_manifest_path,
        )
    except LibraryScaleAggregationError as exc:
        raise OracleBoundManifestError(str(exc)) from exc
    return build_oracle_bound_manifest(
        base_manifest=base_manifest,
        empirical_oracle_by_task=mapping,
        oracle_estimation_contract=oracle_contract,
        base_manifest_sha256=sha256_file(base_manifest_path),
        empirical_oracle_manifest_sha256=sha256_file(empirical_oracle_path),
        index_path=index_path,
        skills_root=skills_root,
        created=created,
    )


def validate_oracle_bound_manifest(
    manifest: Mapping[str, Any],
    *,
    base_manifest_path: Path,
    empirical_oracle_path: Path,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> None:
    if not isinstance(manifest, dict):
        raise OracleBoundManifestError("oracle-bound manifest must be a JSON object")
    expected = build_oracle_bound_manifest_from_files(
        base_manifest_path=base_manifest_path,
        empirical_oracle_path=empirical_oracle_path,
        index_path=index_path,
        skills_root=skills_root,
        created=manifest.get("created"),
    )
    if manifest != expected:
        raise OracleBoundManifestError("oracle-bound manifest does not reproduce from frozen evidence")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--empirical-oracle", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)

    if args.verify is not None:
        payload = json.loads(args.verify.read_text(encoding="utf-8"))
        validate_oracle_bound_manifest(
            payload,
            base_manifest_path=args.base_manifest,
            empirical_oracle_path=args.empirical_oracle,
            index_path=args.index,
            skills_root=args.skills_root,
        )
        print(f"verified -> {args.verify}")
        print(f"manifest_sha256={sha256_file(args.verify)}")
        return 0

    payload = build_oracle_bound_manifest_from_files(
        base_manifest_path=args.base_manifest,
        empirical_oracle_path=args.empirical_oracle,
        index_path=args.index,
        skills_root=args.skills_root,
    )
    write_json_atomic(args.output, payload)
    print(f"task_count={payload['task_count']}")
    print(f"arm_count_per_trial={payload['arm_count_per_trial']}")
    print(f"expected_cells={payload['expected_cells']}")
    print(f"saved -> {args.output}")
    print(f"manifest_sha256={sha256_file(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
