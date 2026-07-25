"""Create a hash-bound, schedule-only empirical-oracle estimation manifest.

This is deliberately not an empirical-oracle result.  It schedules, for every
SkillsBench task, paired no-skill and one-curated-skill conditions that a
separate executor must later score with raw trace evidence.  The resulting
evidence can be consumed by the empirical-oracle loader in
``aggregate_library_scale_results.py`` only after results are collected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_CORPUS_PROVENANCE,
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    DEFAULT_TASKS_ROOT,
    LibraryScaleManifestError,
    _load_index,
    indexed_variant_snapshot_records,
    sha256_file,
    sha256_json,
    tree_sha256,
    validate_library_scale_manifest,
    write_json_atomic,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_MANIFEST = ROOT / "library-scale-manifest.json"
DEFAULT_OUTPUT = ROOT / "empirical-oracle-estimation-manifest.json"
DEFAULT_BASE_SEED = 20260719
DEFAULT_REPEATS = 3
DEFAULT_TAU = 0.1


class EmpiricalOracleEstimationManifestError(ValueError):
    """Raised when the empirical-oracle estimation schedule is unsafe or inconsistent."""


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmpiricalOracleEstimationManifestError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise EmpiricalOracleEstimationManifestError(f"{label} must be a JSON object")
    return value


def _require_nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EmpiricalOracleEstimationManifestError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_parameters(
    *,
    model_id: Any,
    backend: Any,
    harness_mode: Any,
    tau: Any,
    repeats: Any,
    base_seed: Any,
) -> tuple[str, str, str, float, int, int]:
    normalized_model_id = _require_nonempty_string(model_id, label="model_id")
    normalized_backend = _require_nonempty_string(backend, label="backend")
    normalized_harness_mode = _require_nonempty_string(harness_mode, label="harness_mode")
    if isinstance(tau, bool) or not isinstance(tau, (int, float)) or not 0.0 <= tau <= 1.0:
        raise EmpiricalOracleEstimationManifestError("tau must be numeric in [0,1]")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 3:
        raise EmpiricalOracleEstimationManifestError("repeats must be an integer of at least 3")
    if isinstance(base_seed, bool) or not isinstance(base_seed, int) or base_seed < 0:
        raise EmpiricalOracleEstimationManifestError("base_seed must be a non-negative integer")
    return (
        normalized_model_id,
        normalized_backend,
        normalized_harness_mode,
        float(tau),
        repeats,
        base_seed,
    )


def _stable_trial_seed(*, base_seed: int, task_id: str, trial_index: int) -> int:
    """Derive one paired, task-local trial seed without using runtime randomness."""

    digest = hashlib.sha256(
        f"empirical-oracle-estimation-v1:{base_seed}:{task_id}:{trial_index}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _load_and_validate_base_manifest(
    base_manifest_path: Path,
    *,
    index_path: Path,
    corpus_provenance_path: Path,
    tasks_root: Path,
    skills_root: Path,
) -> dict[str, Any]:
    base_manifest = _load_json_object(base_manifest_path, label="library-scale manifest")
    if base_manifest.get("schema_version") != 1:
        raise EmpiricalOracleEstimationManifestError(
            "empirical-oracle estimation requires a canonical schema-1 library-scale manifest"
        )
    try:
        validate_library_scale_manifest(
            base_manifest,
            index_path=index_path,
            corpus_provenance_path=corpus_provenance_path,
            tasks_root=tasks_root,
            skills_root=skills_root,
        )
    except LibraryScaleManifestError as exc:
        raise EmpiricalOracleEstimationManifestError(str(exc)) from exc
    if base_manifest.get("task_count") != 87:
        raise EmpiricalOracleEstimationManifestError(
            "canonical library-scale manifest must schedule all 87 tasks"
        )
    return base_manifest


def build_empirical_oracle_estimation_manifest(
    *,
    base_manifest_path: Path = DEFAULT_BASE_MANIFEST,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
    model_id: str,
    backend: str,
    harness_mode: str,
    tau: float = DEFAULT_TAU,
    repeats: int = DEFAULT_REPEATS,
    base_seed: int = DEFAULT_BASE_SEED,
    created: str | None = None,
) -> dict[str, Any]:
    """Build the complete no-skill plus curated-single-skill schedule.

    Every condition is only a future execution request.  The manifest contains
    no rewards, invocation events, raw traces, or derived oracle membership.
    """

    (
        model_id,
        backend,
        harness_mode,
        tau,
        repeats,
        base_seed,
    ) = _validate_parameters(
        model_id=model_id,
        backend=backend,
        harness_mode=harness_mode,
        tau=tau,
        repeats=repeats,
        base_seed=base_seed,
    )
    base_manifest = _load_and_validate_base_manifest(
        base_manifest_path,
        index_path=index_path,
        corpus_provenance_path=corpus_provenance_path,
        tasks_root=tasks_root,
        skills_root=skills_root,
    )
    index = _load_index(index_path)
    variant_order, indexed_variants = indexed_variant_snapshot_records(index, skills_root)
    base_skill_pool_sha256 = base_manifest.get("frozen_inputs", {}).get("skill_pool_sha256")
    candidate_pool_sha256 = sha256_json(
        [indexed_variants[variant] for variant in variant_order]
    )
    if candidate_pool_sha256 != base_skill_pool_sha256:
        raise EmpiricalOracleEstimationManifestError(
            "canonical library-scale manifest candidate pool hash does not match indexed skill bytes"
        )

    estimation_contract = {
        "model_id": model_id,
        "backend": backend,
        "harness_mode": harness_mode,
        "tau": tau,
        "repeats": repeats,
        "candidate_pool_sha256": candidate_pool_sha256,
    }
    runtime_contract_sha256 = sha256_json(estimation_contract)

    base_task_contracts = base_manifest.get("task_contracts")
    if not isinstance(base_task_contracts, list):
        raise EmpiricalOracleEstimationManifestError("base manifest task contracts are missing")
    base_by_task: dict[str, dict[str, Any]] = {}
    for task in base_task_contracts:
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not task_id or task_id in base_by_task:
            raise EmpiricalOracleEstimationManifestError("base manifest task contracts are invalid")
        base_by_task[task_id] = task
    if len(base_by_task) != 87:
        raise EmpiricalOracleEstimationManifestError("base manifest does not contain 87 unique task contracts")

    task_contracts: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    for task_id in sorted(base_by_task):
        source = base_by_task[task_id]
        reference = source.get("reference_skill_variants")
        if (
            not isinstance(reference, list)
            or not reference
            or any(not isinstance(variant, str) or not variant for variant in reference)
            or len(reference) != len(set(reference))
        ):
            raise EmpiricalOracleEstimationManifestError(
                f"base manifest curated candidate scope is invalid: {task_id}"
            )
        unknown = set(reference) - set(indexed_variants)
        if unknown:
            raise EmpiricalOracleEstimationManifestError(
                f"base manifest references unknown curated variants for {task_id}"
            )
        task_root = tasks_root / task_id
        verifier_root = task_root / "verifier"
        task_md = task_root / "task.md"
        if not task_md.is_file() or not verifier_root.is_dir():
            raise EmpiricalOracleEstimationManifestError(f"task or verifier is missing: {task_id}")
        task_instruction_sha256 = sha256_file(task_md)
        if task_instruction_sha256 != source.get("task_instruction_sha256"):
            raise EmpiricalOracleEstimationManifestError(
                f"base manifest task instruction hash drifted: {task_id}"
            )
        verifier_contract_sha256 = source.get("verifier_contract_sha256")
        if not isinstance(verifier_contract_sha256, str) or len(verifier_contract_sha256) != 64:
            raise EmpiricalOracleEstimationManifestError(
                f"base manifest verifier contract is invalid: {task_id}"
            )
        variant_records = [
            {
                "variant": variant,
                "tree_sha256": tree_sha256(skills_root / variant),
            }
            for variant in reference
        ]
        task_contracts.append(
            {
                "task_id": task_id,
                "category": source.get("category"),
                "difficulty": source.get("difficulty"),
                "task_instruction_sha256": task_instruction_sha256,
                "task_tree_sha256": tree_sha256(task_root),
                "verifier_contract_sha256": verifier_contract_sha256,
                "verifier_tree_sha256": tree_sha256(verifier_root),
                "reference_skill_variants": list(reference),
                "candidate_variant_byte_hashes": variant_records,
            }
        )

        conditions = [("no-skill", "no-skill", [])]
        conditions.extend(
            ("single-skill", f"single-skill:{variant}", [variant]) for variant in reference
        )
        for trial_index in range(1, repeats + 1):
            trial_seed = _stable_trial_seed(
                base_seed=base_seed,
                task_id=task_id,
                trial_index=trial_index,
            )
            for condition_kind, condition_id, skill_variant_ids in conditions:
                skill_variant_byte_hashes = [
                    record
                    for record in variant_records
                    if record["variant"] in set(skill_variant_ids)
                ]
                cells.append(
                    {
                        "cell_id": f"{task_id}__{condition_id}__t{trial_index}",
                        "task_id": task_id,
                        "condition_id": condition_id,
                        "condition_kind": condition_kind,
                        "skill_variant_ids": list(skill_variant_ids),
                        "skill_variant_byte_hashes": skill_variant_byte_hashes,
                        "trial_index": trial_index,
                        "trial_seed": trial_seed,
                        "task_instruction_sha256": task_instruction_sha256,
                        "task_tree_sha256": task_contracts[-1]["task_tree_sha256"],
                        "verifier_contract_sha256": verifier_contract_sha256,
                        "verifier_tree_sha256": task_contracts[-1]["verifier_tree_sha256"],
                        "runtime_contract_sha256": runtime_contract_sha256,
                    }
                )

    condition_count = sum(1 + len(task["reference_skill_variants"]) for task in task_contracts)
    expected_counts = {
        "task_count": len(task_contracts),
        "no_skill_conditions": len(task_contracts),
        "single_skill_conditions": condition_count - len(task_contracts),
        "condition_count": condition_count,
        "repeats": repeats,
        "cell_count": condition_count * repeats,
    }
    if expected_counts != {
        "task_count": 87,
        "no_skill_conditions": 87,
        "single_skill_conditions": 232,
        "condition_count": 319,
        "repeats": repeats,
        "cell_count": 319 * repeats,
    }:
        raise EmpiricalOracleEstimationManifestError(
            "canonical curated candidate scope no longer produces the expected 319 conditions"
        )
    if len(cells) != expected_counts["cell_count"] or len({cell["cell_id"] for cell in cells}) != len(cells):
        raise EmpiricalOracleEstimationManifestError("empirical-oracle estimation cell coverage is invalid")

    task_pool_sha256 = sha256_json(
        [
            {
                "task_id": task["task_id"],
                "task_tree_sha256": task["task_tree_sha256"],
                "verifier_tree_sha256": task["verifier_tree_sha256"],
            }
            for task in task_contracts
        ]
    )

    return {
        "schema_version": 1,
        "experiment_id": "skillsbench-full87-empirical-oracle-estimation-v1",
        "created": created or date.today().isoformat(),
        "library_scale_manifest_sha256": sha256_file(base_manifest_path),
        "schedule_only": True,
        "base_seed": base_seed,
        "estimation_contract": estimation_contract,
        "frozen_inputs": {
            "library_scale_manifest_sha256": sha256_file(base_manifest_path),
            "skills_index_sha256": sha256_file(index_path),
            "corpus_provenance_sha256": sha256_file(corpus_provenance_path),
            "task_pool_sha256": task_pool_sha256,
            "skill_pool_sha256": candidate_pool_sha256,
        },
        "design": {
            "candidate_scope": "task_curated_bundle",
            "conditions": "one no-skill condition plus one isolated single-skill condition per curated variant",
            "repeat_pairing": "all conditions for one task and trial share the same stable task-local seed",
            "trial_seed_derivation": "sha256(empirical-oracle-estimation-v1:base_seed:task_id:trial_index) first eight bytes as unsigned integer",
        },
        "evidence_contract": {
            "actual_model_results_present": False,
            "actual_invocation_evidence_present": False,
            "empirical_oracle_membership_estimated": False,
            "schedule_only_not_result": True,
            "future_results_require_hashed_raw_traces": True,
        },
        "task_contracts": task_contracts,
        "cells": cells,
        "expected_counts": expected_counts,
    }


def validate_empirical_oracle_estimation_manifest(
    manifest: dict[str, Any],
    *,
    base_manifest_path: Path = DEFAULT_BASE_MANIFEST,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> None:
    """Fail closed unless the manifest exactly recomputes from frozen inputs."""

    if not isinstance(manifest, dict):
        raise EmpiricalOracleEstimationManifestError("manifest must be a JSON object")
    contract = manifest.get("estimation_contract")
    if not isinstance(contract, dict) or set(contract) != {
        "model_id",
        "backend",
        "harness_mode",
        "tau",
        "repeats",
        "candidate_pool_sha256",
    }:
        raise EmpiricalOracleEstimationManifestError("estimation_contract must have the exact six-key schema")
    expected = build_empirical_oracle_estimation_manifest(
        base_manifest_path=base_manifest_path,
        index_path=index_path,
        corpus_provenance_path=corpus_provenance_path,
        tasks_root=tasks_root,
        skills_root=skills_root,
        model_id=contract["model_id"],
        backend=contract["backend"],
        harness_mode=contract["harness_mode"],
        tau=contract["tau"],
        repeats=contract["repeats"],
        base_seed=manifest.get("base_seed"),
        created=manifest.get("created"),
    )
    if manifest != expected:
        raise EmpiricalOracleEstimationManifestError(
            "manifest does not reproduce from the canonical frozen inputs"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-id")
    parser.add_argument("--backend")
    parser.add_argument("--harness-mode")
    parser.add_argument("--tau", type=float, default=DEFAULT_TAU)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)

    if args.verify is not None:
        payload = _load_json_object(args.verify, label="empirical-oracle estimation manifest")
        validate_empirical_oracle_estimation_manifest(
            payload,
            base_manifest_path=args.base_manifest,
            index_path=args.index,
            corpus_provenance_path=args.corpus_provenance,
            tasks_root=args.tasks_root,
            skills_root=args.skills_root,
        )
        print(f"verified -> {args.verify}")
        print(f"manifest_sha256={sha256_file(args.verify)}")
        return 0

    try:
        payload = build_empirical_oracle_estimation_manifest(
            base_manifest_path=args.base_manifest,
            index_path=args.index,
            corpus_provenance_path=args.corpus_provenance,
            tasks_root=args.tasks_root,
            skills_root=args.skills_root,
            model_id=args.model_id,
            backend=args.backend,
            harness_mode=args.harness_mode,
            tau=args.tau,
            repeats=args.repeats,
            base_seed=args.base_seed,
        )
    except EmpiricalOracleEstimationManifestError as exc:
        parser.error(str(exc))
    write_json_atomic(args.output, payload)
    print(f"task_count={payload['expected_counts']['task_count']}")
    print(f"condition_count={payload['expected_counts']['condition_count']}")
    print(f"cell_count={payload['expected_counts']['cell_count']}")
    print(f"saved -> {args.output}")
    print(f"manifest_sha256={sha256_file(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
