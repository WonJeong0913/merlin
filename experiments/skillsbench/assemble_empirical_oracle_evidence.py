"""Assemble 957 scored cells into portable empirical-oracle evidence.

The assembler never runs a model and never trusts declared oracle membership.
It validates every result against the exact estimation cell, checks a unique
raw trace, copies the immutable traces into a new evidence root, recomputes
per-task uplift, and finally round-trips the output through the existing
empirical-oracle loader.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from experiments.skillsbench.aggregate_library_scale_results import (
    LibraryScaleAggregationError,
    load_empirical_oracle_mapping,
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
    sha256_json,
)


class EmpiricalOracleAssemblyError(ValueError):
    """Raised when scored estimation evidence is missing, reused, or inconsistent."""


RESULT_KEYS = {
    "schema_version",
    "estimation_manifest_sha256",
    "cell_id",
    "cell_contract_sha256",
    "terminal_status",
    "reward",
    "verifier_contract_sha256",
    "runtime_contract_sha256",
    "condition_application",
    "raw_trace_pointer",
    "raw_trace_sha256",
}
CONDITION_KEYS = {
    "expected_skill_variant_ids",
    "prompt_exposed_skill_variant_ids",
    "skill_variant_byte_hashes",
    "provider_native_invocation_evidence_complete",
    "provider_native_invoked_skill_variant_ids",
}


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmpiricalOracleAssemblyError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise EmpiricalOracleAssemblyError(f"{label} must be a JSON object")
    return value


def result_pointer_for_cell(cell_id: str) -> str:
    """Return the bounded portable result path for one potentially long cell ID."""

    if not isinstance(cell_id, str) or not cell_id:
        raise EmpiricalOracleAssemblyError("cell_id must be a non-empty string")
    return f"cells/{sha256_json(cell_id)}.json"


def _safe_hashed_file(
    root: Path,
    pointer: Any,
    expected_sha256: Any,
    *,
    label: str,
) -> Path:
    if not isinstance(pointer, str) or not pointer or Path(pointer).is_absolute():
        raise EmpiricalOracleAssemblyError(f"{label} pointer must be a non-empty relative path")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_sha256)
    ):
        raise EmpiricalOracleAssemblyError(f"{label} sha256 must be lowercase SHA-256")
    unresolved = root / pointer
    if unresolved.is_symlink():
        raise EmpiricalOracleAssemblyError(f"{label} pointer must not be a symlink")
    target = unresolved.resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise EmpiricalOracleAssemblyError(f"{label} pointer escapes its root") from exc
    if not target.is_file() or sha256_file(target) != expected_sha256:
        raise EmpiricalOracleAssemblyError(f"{label} is missing or hash-invalid")
    return target


def validate_condition_application(value: Any, *, cell: dict[str, Any]) -> None:
    if not isinstance(value, dict) or set(value) != CONDITION_KEYS:
        raise EmpiricalOracleAssemblyError(
            f"condition application schema is invalid: {cell['cell_id']}"
        )
    expected = cell["skill_variant_ids"]
    if value["expected_skill_variant_ids"] != expected:
        raise EmpiricalOracleAssemblyError(
            f"condition expected skill IDs drifted: {cell['cell_id']}"
        )
    if value["prompt_exposed_skill_variant_ids"] != expected:
        raise EmpiricalOracleAssemblyError(
            f"condition prompt exposure is incomplete: {cell['cell_id']}"
        )
    if value["skill_variant_byte_hashes"] != cell["skill_variant_byte_hashes"]:
        raise EmpiricalOracleAssemblyError(
            f"condition skill byte hashes drifted: {cell['cell_id']}"
        )
    complete = value["provider_native_invocation_evidence_complete"]
    invoked = value["provider_native_invoked_skill_variant_ids"]
    if not isinstance(complete, bool):
        raise EmpiricalOracleAssemblyError(
            f"native invocation completeness must be boolean: {cell['cell_id']}"
        )
    if complete:
        if (
            not isinstance(invoked, list)
            or any(not isinstance(item, str) or not item for item in invoked)
            or len(invoked) != len(set(invoked))
            or not set(invoked).issubset(set(expected))
        ):
            raise EmpiricalOracleAssemblyError(
                f"native invocation IDs are invalid: {cell['cell_id']}"
            )
    elif invoked is not None:
        raise EmpiricalOracleAssemblyError(
            f"incomplete native invocation evidence must use null IDs: {cell['cell_id']}"
        )


def validate_empirical_oracle_cell_result(
    result: dict[str, Any],
    *,
    cell: dict[str, Any],
    estimation_manifest_sha256: str,
    results_root: Path,
) -> tuple[float, Path, str, bool]:
    """Validate one normalized result against its frozen cell and raw trace."""

    if set(result) != RESULT_KEYS or result.get("schema_version") != 1:
        raise EmpiricalOracleAssemblyError(f"result schema is invalid: {cell['cell_id']}")
    if result["estimation_manifest_sha256"] != estimation_manifest_sha256:
        raise EmpiricalOracleAssemblyError(
            f"result is bound to another estimation manifest: {cell['cell_id']}"
        )
    if result["cell_id"] != cell["cell_id"]:
        raise EmpiricalOracleAssemblyError(f"result cell identity mismatch: {cell['cell_id']}")
    if result["cell_contract_sha256"] != sha256_json(cell):
        raise EmpiricalOracleAssemblyError(f"result cell contract mismatch: {cell['cell_id']}")
    if result["terminal_status"] != "scored":
        raise EmpiricalOracleAssemblyError(f"result is not scored: {cell['cell_id']}")
    reward = result["reward"]
    if isinstance(reward, bool) or not isinstance(reward, (int, float)) or not 0.0 <= reward <= 1.0:
        raise EmpiricalOracleAssemblyError(f"reward must be numeric in [0,1]: {cell['cell_id']}")
    if result["verifier_contract_sha256"] != cell["verifier_contract_sha256"]:
        raise EmpiricalOracleAssemblyError(f"result verifier drifted: {cell['cell_id']}")
    if result["runtime_contract_sha256"] != cell["runtime_contract_sha256"]:
        raise EmpiricalOracleAssemblyError(f"result runtime contract drifted: {cell['cell_id']}")
    validate_condition_application(result["condition_application"], cell=cell)
    raw = _safe_hashed_file(
        results_root,
        result["raw_trace_pointer"],
        result["raw_trace_sha256"],
        label=f"raw trace {cell['cell_id']}",
    )
    return (
        float(reward),
        raw,
        result["raw_trace_sha256"],
        result["condition_application"]["provider_native_invocation_evidence_complete"],
    )


def _write_json_new(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise EmpiricalOracleAssemblyError(f"refusing to overwrite evidence file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def assemble_empirical_oracle_evidence(
    *,
    estimation_manifest_path: Path,
    base_manifest_path: Path,
    results_root: Path,
    output_root: Path,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> Path:
    """Validate a complete 957-cell run and create a portable evidence bundle."""

    estimation_manifest_path = estimation_manifest_path.expanduser()
    base_manifest_path = base_manifest_path.expanduser()
    results_root = results_root.expanduser()
    output_root = output_root.expanduser()
    if estimation_manifest_path.is_symlink() or base_manifest_path.is_symlink():
        raise EmpiricalOracleAssemblyError("manifest inputs must not be symlinks")
    if results_root.is_symlink():
        raise EmpiricalOracleAssemblyError("results_root must not be a symlink")
    if output_root.is_symlink():
        raise EmpiricalOracleAssemblyError("output_root must not be a symlink")
    estimation_manifest_path = estimation_manifest_path.resolve(strict=True)
    base_manifest_path = base_manifest_path.resolve(strict=True)
    results_root = results_root.resolve(strict=True)
    output_root = output_root.resolve(strict=False)
    if not results_root.is_dir():
        raise EmpiricalOracleAssemblyError("results_root must be a regular directory")
    if output_root.exists():
        raise EmpiricalOracleAssemblyError("output_root must not already exist")

    estimation = _load_json_object(
        estimation_manifest_path,
        label="empirical-oracle estimation manifest",
    )
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
        raise EmpiricalOracleAssemblyError(str(exc)) from exc
    if estimation.get("schedule_only") is not True:
        raise EmpiricalOracleAssemblyError("estimation input must remain a schedule-only manifest")

    cells = estimation["cells"]
    expected_result_pointers = {result_pointer_for_cell(cell["cell_id"]) for cell in cells}
    result_dir = results_root / "cells"
    if not result_dir.is_dir() or result_dir.is_symlink():
        raise EmpiricalOracleAssemblyError("results_root/cells must be a regular directory")
    actual_result_pointers = {
        path.relative_to(results_root).as_posix()
        for path in result_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_result_pointers != expected_result_pointers:
        missing = len(expected_result_pointers - actual_result_pointers)
        unexpected = len(actual_result_pointers - expected_result_pointers)
        raise EmpiricalOracleAssemblyError(
            f"result coverage must exactly match all scheduled cells (missing={missing}, unexpected={unexpected})"
        )

    estimation_sha256 = sha256_file(estimation_manifest_path)
    validated: dict[str, dict[str, Any]] = {}
    seen_raw_pointers: set[str] = set()
    seen_raw_hashes: set[str] = set()
    for cell in cells:
        pointer = result_pointer_for_cell(cell["cell_id"])
        result_path = _safe_hashed_file(
            results_root,
            pointer,
            sha256_file(results_root / pointer),
            label=f"cell result {cell['cell_id']}",
        )
        result = _load_json_object(result_path, label=f"cell result {cell['cell_id']}")
        reward, raw_path, raw_sha256, native_complete = validate_empirical_oracle_cell_result(
            result,
            cell=cell,
            estimation_manifest_sha256=estimation_sha256,
            results_root=results_root,
        )
        raw_pointer = result["raw_trace_pointer"]
        if raw_pointer in seen_raw_pointers or raw_sha256 in seen_raw_hashes:
            raise EmpiricalOracleAssemblyError("raw trace evidence is reused across estimation cells")
        seen_raw_pointers.add(raw_pointer)
        seen_raw_hashes.add(raw_sha256)
        validated[cell["cell_id"]] = {
            "cell": cell,
            "reward": reward,
            "raw_path": raw_path,
            "raw_sha256": raw_sha256,
            "native_complete": native_complete,
        }

    base_manifest = _load_json_object(base_manifest_path, label="library-scale manifest")
    contract = estimation["estimation_contract"]
    runtime_contract_sha256 = sha256_json(contract)
    tasks: list[dict[str, Any]] = []
    try:
        output_root.mkdir(parents=True, exist_ok=False)
        copied_trials: dict[str, dict[str, Any]] = {}
        for cell in cells:
            record = validated[cell["cell_id"]]
            portable_pointer = f"raw/{sha256_json(cell['cell_id'])}.jsonl"
            portable_raw = output_root / portable_pointer
            portable_raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(record["raw_path"], portable_raw)
            if sha256_file(portable_raw) != record["raw_sha256"]:
                raise EmpiricalOracleAssemblyError(
                    f"copied raw trace hash drifted: {cell['cell_id']}"
                )
            copied_trials[cell["cell_id"]] = {
                "trial_index": cell["trial_index"],
                "reward": record["reward"],
                "verifier_contract_sha256": cell["verifier_contract_sha256"],
                "runtime_contract_sha256": runtime_contract_sha256,
                "raw_trace_pointer": portable_pointer,
                "raw_trace_sha256": record["raw_sha256"],
            }

        by_task = {task["task_id"]: task for task in estimation["task_contracts"]}
        cells_by_task: dict[str, list[dict[str, Any]]] = {task_id: [] for task_id in by_task}
        for cell in cells:
            cells_by_task[cell["task_id"]].append(cell)
        repeats = contract["repeats"]
        tau = float(contract["tau"])
        for task_id in sorted(by_task):
            task = by_task[task_id]
            task_cells = cells_by_task[task_id]
            no_skill_cells = sorted(
                (cell for cell in task_cells if cell["condition_kind"] == "no-skill"),
                key=lambda cell: cell["trial_index"],
            )
            no_skill_trials = [copied_trials[cell["cell_id"]] for cell in no_skill_cells]
            no_skill_mean = sum(trial["reward"] for trial in no_skill_trials) / repeats
            candidate_trials: list[dict[str, Any]] = []
            derived: list[str] = []
            for variant in task["reference_skill_variants"]:
                variant_cells = sorted(
                    (
                        cell
                        for cell in task_cells
                        if cell["skill_variant_ids"] == [variant]
                    ),
                    key=lambda cell: cell["trial_index"],
                )
                trials = [copied_trials[cell["cell_id"]] for cell in variant_cells]
                if len(trials) != repeats:
                    raise EmpiricalOracleAssemblyError(
                        f"candidate trial coverage drifted after validation: {task_id}/{variant}"
                    )
                candidate_trials.append({"skill_variant_id": variant, "trials": trials})
                if sum(trial["reward"] for trial in trials) / repeats - no_skill_mean >= tau:
                    derived.append(variant)
            task_evidence = {
                "schema_version": 1,
                "task_id": task_id,
                "estimation_contract_sha256": runtime_contract_sha256,
                "verifier_contract_sha256": task["verifier_contract_sha256"],
                "no_skill_trials": no_skill_trials,
                "candidate_trials": candidate_trials,
            }
            task_pointer = f"tasks/{task_id}.json"
            task_path = output_root / task_pointer
            _write_json_new(task_path, task_evidence)
            tasks.append(
                {
                    "task_id": task_id,
                    "skill_variant_ids": derived,
                    "evidence_pointer": task_pointer,
                    "evidence_sha256": sha256_file(task_path),
                }
            )

        empirical_manifest = {
            "schema_version": 1,
            "experiment_id": base_manifest["experiment_id"],
            "library_scale_manifest_sha256": sha256_file(base_manifest_path),
            "oracle_candidate_scope": "task_curated_bundle",
            "estimation_evidence_complete": True,
            "estimation_contract": contract,
            "tasks": tasks,
        }
        output_path = output_root / "empirical-oracle.json"
        _write_json_new(output_path, empirical_manifest)
        try:
            mapping, loaded_contract = load_empirical_oracle_mapping(
                output_path,
                manifest=base_manifest,
                manifest_path=base_manifest_path,
            )
        except LibraryScaleAggregationError as exc:
            raise EmpiricalOracleAssemblyError(
                f"assembled evidence failed loader round-trip: {exc}"
            ) from exc
        if loaded_contract != contract or len(mapping) != estimation["expected_counts"]["task_count"]:
            raise EmpiricalOracleAssemblyError("assembled evidence round-trip changed its contract")
        return output_path
    except Exception:
        if output_root.exists():
            shutil.rmtree(output_root)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimation-manifest", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    args = parser.parse_args(argv)
    output = assemble_empirical_oracle_evidence(
        estimation_manifest_path=args.estimation_manifest,
        base_manifest_path=args.base_manifest,
        results_root=args.results_root,
        output_root=args.output_root,
        index_path=args.index,
        corpus_provenance_path=args.corpus_provenance,
        tasks_root=args.tasks_root,
        skills_root=args.skills_root,
    )
    print("task_count=87")
    print("condition_count=319")
    print("cell_count=957")
    print(f"saved -> {output}")
    print(f"manifest_sha256={sha256_file(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
