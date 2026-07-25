"""Validate one task-complete empirical-oracle pilot before 957-cell expansion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiments.skillsbench.assemble_empirical_oracle_evidence import (
    EmpiricalOracleAssemblyError,
    result_pointer_for_cell,
    validate_empirical_oracle_cell_result,
)
from experiments.skillsbench.create_empirical_oracle_estimation_manifest import (
    DEFAULT_BASE_MANIFEST,
    EmpiricalOracleEstimationManifestError,
    validate_empirical_oracle_estimation_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_CORPUS_PROVENANCE,
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    DEFAULT_TASKS_ROOT,
    sha256_file,
)


class EmpiricalOraclePilotError(ValueError):
    """Raised when a task-local pilot is incomplete or contract-invalid."""


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmpiricalOraclePilotError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise EmpiricalOraclePilotError(f"{label} must be a JSON object")
    return value


def _mean(values: list[float]) -> float:
    if not values:
        raise EmpiricalOraclePilotError("pilot mean requires at least one reward")
    return sum(values) / len(values)


def validate_empirical_oracle_task_pilot(
    *,
    estimation_manifest_path: Path,
    results_root: Path,
    task_id: str,
    base_manifest_path: Path = DEFAULT_BASE_MANIFEST,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    """Return a bounded report only when one task's paired pilot is complete."""

    for path, label in (
        (estimation_manifest_path, "estimation manifest"),
        (base_manifest_path, "base manifest"),
    ):
        if path.expanduser().is_symlink():
            raise EmpiricalOraclePilotError(f"{label} must not be a symlink")
    if results_root.expanduser().is_symlink():
        raise EmpiricalOraclePilotError("results_root must not be a symlink")
    estimation_manifest_path = estimation_manifest_path.expanduser().resolve(strict=True)
    base_manifest_path = base_manifest_path.expanduser().resolve(strict=True)
    results_root = results_root.expanduser().resolve(strict=True)
    if not results_root.is_dir():
        raise EmpiricalOraclePilotError("results_root must be a regular directory")
    if not isinstance(task_id, str) or not task_id:
        raise EmpiricalOraclePilotError("task_id must be a non-empty string")

    estimation = _load_json_object(estimation_manifest_path, label="estimation manifest")
    try:
        validate_empirical_oracle_estimation_manifest(
            estimation,
            base_manifest_path=base_manifest_path,
            index_path=index_path,
            corpus_provenance_path=corpus_provenance_path,
            tasks_root=tasks_root,
            skills_root=skills_root,
        )
    except EmpiricalOracleEstimationManifestError as exc:
        raise EmpiricalOraclePilotError(str(exc)) from exc

    task_matches = [task for task in estimation["task_contracts"] if task["task_id"] == task_id]
    if len(task_matches) != 1:
        raise EmpiricalOraclePilotError(f"task_id must resolve exactly once: {task_id}")
    task = task_matches[0]
    cells = [cell for cell in estimation["cells"] if cell["task_id"] == task_id]
    repeats = estimation["estimation_contract"]["repeats"]
    expected_cell_count = (1 + len(task["reference_skill_variants"])) * repeats
    if len(cells) != expected_cell_count:
        raise EmpiricalOraclePilotError("pilot cell denominator drifted from its task contract")

    expected_pointers = {result_pointer_for_cell(cell["cell_id"]) for cell in cells}
    result_dir = results_root / "cells"
    if not result_dir.is_dir() or result_dir.is_symlink():
        raise EmpiricalOraclePilotError("results_root/cells must be a regular directory")
    actual_pointers = {
        path.relative_to(results_root).as_posix()
        for path in result_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_pointers != expected_pointers:
        raise EmpiricalOraclePilotError(
            "pilot result coverage must exactly match the selected task "
            f"(missing={len(expected_pointers - actual_pointers)}, "
            f"unexpected={len(actual_pointers - expected_pointers)})"
        )

    estimation_sha256 = sha256_file(estimation_manifest_path)
    rewards_by_condition: dict[str, list[tuple[int, float]]] = {}
    native_complete_count = 0
    raw_pointers: set[str] = set()
    raw_hashes: set[str] = set()
    for cell in cells:
        result_path = results_root / result_pointer_for_cell(cell["cell_id"])
        result = _load_json_object(result_path, label=f"pilot result {cell['cell_id']}")
        try:
            reward, _raw_path, raw_sha256, native_complete = (
                validate_empirical_oracle_cell_result(
                    result,
                    cell=cell,
                    estimation_manifest_sha256=estimation_sha256,
                    results_root=results_root,
                )
            )
        except EmpiricalOracleAssemblyError as exc:
            raise EmpiricalOraclePilotError(str(exc)) from exc
        raw_pointer = result["raw_trace_pointer"]
        if raw_pointer in raw_pointers or raw_sha256 in raw_hashes:
            raise EmpiricalOraclePilotError("pilot raw trace evidence is reused")
        raw_pointers.add(raw_pointer)
        raw_hashes.add(raw_sha256)
        native_complete_count += int(native_complete)
        rewards_by_condition.setdefault(cell["condition_id"], []).append(
            (cell["trial_index"], reward)
        )

    expected_condition_ids = ["no-skill"] + [
        f"single-skill:{variant}" for variant in task["reference_skill_variants"]
    ]
    if list(rewards_by_condition) != expected_condition_ids:
        raise EmpiricalOraclePilotError("pilot condition order or coverage drifted")
    ordered_rewards: dict[str, list[float]] = {}
    for condition_id in expected_condition_ids:
        trials = sorted(rewards_by_condition[condition_id])
        if [index for index, _reward in trials] != list(range(1, repeats + 1)):
            raise EmpiricalOraclePilotError(f"pilot trial coverage drifted: {condition_id}")
        ordered_rewards[condition_id] = [reward for _index, reward in trials]

    no_skill_mean = _mean(ordered_rewards["no-skill"])
    tau = float(estimation["estimation_contract"]["tau"])
    candidates: list[dict[str, Any]] = []
    uplift_set: list[str] = []
    for variant in task["reference_skill_variants"]:
        condition_id = f"single-skill:{variant}"
        rewards = ordered_rewards[condition_id]
        mean_reward = _mean(rewards)
        uplift = mean_reward - no_skill_mean
        eligible = uplift >= tau
        candidates.append(
            {
                "skill_variant_id": variant,
                "trial_rewards": rewards,
                "mean_reward": mean_reward,
                "mean_uplift_vs_no_skill": uplift,
                "meets_tau": eligible,
            }
        )
        if eligible:
            uplift_set.append(variant)

    return {
        "schema_version": 1,
        "report_kind": "task_complete_empirical_oracle_pilot",
        "estimation_manifest_sha256": estimation_sha256,
        "base_manifest_sha256": sha256_file(base_manifest_path),
        "task_id": task_id,
        "estimation_contract": estimation["estimation_contract"],
        "task_contract": task,
        "expected_cell_count": expected_cell_count,
        "validated_cell_count": len(cells),
        "condition_count": len(expected_condition_ids),
        "repeats": repeats,
        "no_skill": {
            "trial_rewards": ordered_rewards["no-skill"],
            "mean_reward": no_skill_mean,
        },
        "candidates": candidates,
        "task_local_uplift_set": uplift_set,
        "native_invocation_evidence": {
            "complete_cell_count": native_complete_count,
            "incomplete_cell_count": len(cells) - native_complete_count,
            "all_cells_complete": native_complete_count == len(cells),
        },
        "expansion_contract_gate": {
            "accepted": True,
            "checks": {
                "task_denominator_complete": True,
                "all_results_scored": True,
                "condition_bytes_match": True,
                "verifier_runtime_contracts_match": True,
                "raw_traces_unique_and_hash_valid": True,
            },
        },
        "evidence_boundary": {
            "task_local_pilot_only": True,
            "is_full_87_empirical_oracle": False,
            "is_957_cell_result": False,
            "is_library_scale_shadowing_result": False,
            "task_local_uplift_is_not_full_oracle_evidence": True,
            "prompt_exposure_is_not_provider_native_invocation": True,
        },
    }


def _write_report_new(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser()
    if path.exists() or path.is_symlink():
        raise EmpiricalOraclePilotError("output report must not already exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimation-manifest", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    args = parser.parse_args(argv)
    report = validate_empirical_oracle_task_pilot(
        estimation_manifest_path=args.estimation_manifest,
        results_root=args.results_root,
        task_id=args.task_id,
        base_manifest_path=args.base_manifest,
        index_path=args.index,
        corpus_provenance_path=args.corpus_provenance,
        tasks_root=args.tasks_root,
        skills_root=args.skills_root,
    )
    _write_report_new(args.output, report)
    print(f"task_id={report['task_id']}")
    print(f"validated_cells={report['validated_cell_count']}")
    print(f"expansion_contract_gate={report['expansion_contract_gate']['accepted']}")
    print(f"saved -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
