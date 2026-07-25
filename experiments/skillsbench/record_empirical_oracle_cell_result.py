"""Record one externally executed empirical-oracle estimation cell safely.

The recorder does not run an executor or infer invocation.  It validates a
pre-materialized cell, copies one externally produced raw trace into a portable
new-only result root, and writes exactly the result schema consumed by the
empirical-oracle assembler.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from experiments.skillsbench.assemble_empirical_oracle_evidence import (
    CONDITION_KEYS,
    RESULT_KEYS,
    result_pointer_for_cell,
    validate_condition_application,
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
    tree_sha256,
)


class EmpiricalOracleResultRecordingError(ValueError):
    """Raised when external cell evidence cannot be recorded safely."""


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmpiricalOracleResultRecordingError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise EmpiricalOracleResultRecordingError(f"{label} must be a JSON object")
    return value


def _resolve_regular_file(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise EmpiricalOracleResultRecordingError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise EmpiricalOracleResultRecordingError(f"{label} is missing") from exc
    if not resolved.is_file():
        raise EmpiricalOracleResultRecordingError(f"{label} must be a regular file")
    return resolved


def _find_cell(manifest: dict[str, Any], cell_id: Any) -> dict[str, Any]:
    if not isinstance(cell_id, str) or not cell_id:
        raise EmpiricalOracleResultRecordingError("cell contract cell_id must be a non-empty string")
    matches = [cell for cell in manifest.get("cells", []) if cell.get("cell_id") == cell_id]
    if len(matches) != 1:
        raise EmpiricalOracleResultRecordingError(
            f"cell id must resolve exactly once in the estimation manifest: {cell_id}"
        )
    return matches[0]


def _require_equal(contract: dict[str, Any], key: str, expected: Any) -> None:
    if contract.get(key) != expected:
        raise EmpiricalOracleResultRecordingError(f"cell contract {key} drifted from the estimation cell")


def _validate_materialized_cell_contract(
    contract: dict[str, Any],
    *,
    contract_path: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    cell: dict[str, Any],
) -> None:
    if contract.get("schema_version") != 1:
        raise EmpiricalOracleResultRecordingError("cell contract schema version is unsupported")
    _require_equal(contract, "experiment_id", manifest["experiment_id"])
    _require_equal(contract, "estimation_manifest_sha256", sha256_file(manifest_path))
    _require_equal(contract, "estimation_cell_sha256", sha256_json(cell))
    _require_equal(contract, "cell_contract_sha256", sha256_json(cell))
    for key in (
        "cell_id",
        "task_id",
        "condition_id",
        "condition_kind",
        "skill_variant_ids",
        "trial_index",
        "trial_seed",
        "runtime_contract_sha256",
        "verifier_contract_sha256",
    ):
        _require_equal(contract, key, cell[key])
    _require_equal(
        contract,
        "manifest_skill_variant_byte_hashes",
        cell["skill_variant_byte_hashes"],
    )
    _require_equal(contract, "estimation_contract", manifest["estimation_contract"])
    _require_equal(contract, "expected_result_pointer", result_pointer_for_cell(cell["cell_id"]))
    if contract.get("execution_status") != "not_run":
        raise EmpiricalOracleResultRecordingError("cell contract must remain an unexecuted materialization")
    boundary = contract.get("evidence_boundary")
    if not isinstance(boundary, dict) or any(
        boundary.get(key) is not False
        for key in (
            "materialization_is_model_execution",
            "materialization_is_actual_invocation",
            "materialization_is_result",
            "empirical_oracle_membership_estimated",
        )
    ):
        raise EmpiricalOracleResultRecordingError("cell contract materialization boundary is unsafe")

    for contract_key, cell_key in (
        ("manifest_task_tree_sha256", "task_tree_sha256"),
        ("source_task_tree_sha256", "task_tree_sha256"),
        ("manifest_verifier_tree_sha256", "verifier_tree_sha256"),
        ("source_verifier_tree_sha256", "verifier_tree_sha256"),
    ):
        _require_equal(contract, contract_key, cell[cell_key])

    staged_root_name = contract.get("staged_skill_root")
    if staged_root_name != "skills":
        raise EmpiricalOracleResultRecordingError("cell contract staged_skill_root must be skills")
    staged_root = contract_path.parent / staged_root_name
    if staged_root.is_symlink() or not staged_root.is_dir():
        raise EmpiricalOracleResultRecordingError("materialized staged skills root is missing or unsafe")
    variant_records = contract.get("variant_records")
    if not isinstance(variant_records, list) or len(variant_records) != len(cell["skill_variant_ids"]):
        raise EmpiricalOracleResultRecordingError("cell contract variant records are invalid")
    expected_variant_names = set(cell["skill_variant_ids"])
    observed_variant_names: set[str] = set()
    for ordinal, record in enumerate(variant_records, start=1):
        if not isinstance(record, dict) or record.get("ordinal") != ordinal:
            raise EmpiricalOracleResultRecordingError("cell contract variant record order is invalid")
        variant = record.get("variant")
        if variant not in expected_variant_names or variant in observed_variant_names:
            raise EmpiricalOracleResultRecordingError("cell contract variant record identity is invalid")
        expected_tree = cell["skill_variant_byte_hashes"][ordinal - 1]["tree_sha256"]
        if (
            record.get("expected_cell_tree_sha256") != expected_tree
            or record.get("source_tree_sha256") != expected_tree
            or record.get("staged_tree_sha256") != expected_tree
        ):
            raise EmpiricalOracleResultRecordingError("cell contract variant byte hashes drifted")
        staged_variant = staged_root / variant
        if staged_variant.is_symlink() or not staged_variant.is_dir() or tree_sha256(staged_variant) != expected_tree:
            raise EmpiricalOracleResultRecordingError("materialized staged skill bytes drifted")
        observed_variant_names.add(variant)
    root_entries = list(staged_root.iterdir())
    if any(entry.is_symlink() or not entry.is_dir() for entry in root_entries):
        raise EmpiricalOracleResultRecordingError("materialized staged skills root has unsafe entries")
    if {entry.name for entry in root_entries} != observed_variant_names:
        raise EmpiricalOracleResultRecordingError("materialized staged skill set drifted")


def _load_and_validate_condition_evidence(path: Path, *, cell: dict[str, Any]) -> dict[str, Any]:
    evidence_path = _resolve_regular_file(path, label="runtime condition evidence")
    evidence = _load_json_object(evidence_path, label="runtime condition evidence")
    if set(evidence) != CONDITION_KEYS:
        raise EmpiricalOracleResultRecordingError("runtime condition evidence schema is invalid")
    try:
        validate_condition_application(evidence, cell=cell)
    except Exception as exc:
        raise EmpiricalOracleResultRecordingError(str(exc)) from exc
    return evidence


def _validate_reward(reward: Any) -> float:
    if isinstance(reward, bool) or not isinstance(reward, (int, float)) or not 0.0 <= reward <= 1.0:
        raise EmpiricalOracleResultRecordingError("reward must be numeric in [0,1]")
    return float(reward)


def _ensure_new_result_paths(
    results_root: Path,
    *,
    result_path: Path,
    raw_path: Path,
    raw_trace_sha256: str,
) -> None:
    if results_root.is_symlink():
        raise EmpiricalOracleResultRecordingError("results_root must not be a symlink")
    if results_root.exists() and not results_root.is_dir():
        raise EmpiricalOracleResultRecordingError("results_root must be a directory when it exists")
    if result_path.exists() or result_path.is_symlink():
        raise EmpiricalOracleResultRecordingError("result destination already exists")
    if raw_path.exists() or raw_path.is_symlink():
        raise EmpiricalOracleResultRecordingError("raw destination already exists")
    raw_root = results_root / "raw"
    if raw_root.exists():
        if raw_root.is_symlink() or not raw_root.is_dir():
            raise EmpiricalOracleResultRecordingError("results raw root is unsafe")
        for existing in raw_root.iterdir():
            if existing.is_symlink() or not existing.is_file():
                raise EmpiricalOracleResultRecordingError("results raw root contains an unsafe entry")
            if sha256_file(existing) == raw_trace_sha256:
                raise EmpiricalOracleResultRecordingError("raw trace bytes are already recorded for another cell")


def _mkdir_new_or_existing(path: Path, *, created_dirs: list[Path]) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise EmpiricalOracleResultRecordingError(f"unsafe output directory: {path}")
        return
    path.mkdir(parents=False, exist_ok=False)
    created_dirs.append(path)


def record_empirical_oracle_cell_result(
    *,
    estimation_manifest_path: Path,
    cell_contract_path: Path,
    raw_trace_path: Path,
    reward: float,
    condition_evidence_path: Path,
    results_root: Path,
    base_manifest_path: Path = DEFAULT_BASE_MANIFEST,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    """Copy one raw trace and record one assembler-compatible scored result."""

    estimation_manifest_path = _resolve_regular_file(
        estimation_manifest_path,
        label="estimation manifest",
    )
    base_manifest_path = _resolve_regular_file(
        base_manifest_path,
        label="base library-scale manifest",
    )
    cell_contract_path = _resolve_regular_file(cell_contract_path, label="cell contract")
    raw_trace_path = _resolve_regular_file(raw_trace_path, label="external raw trace")
    manifest = _load_json_object(estimation_manifest_path, label="estimation manifest")
    try:
        validate_empirical_oracle_estimation_manifest(
            manifest,
            base_manifest_path=base_manifest_path,
            index_path=index_path,
            corpus_provenance_path=corpus_provenance_path,
            tasks_root=tasks_root,
            skills_root=skills_root,
        )
    except EmpiricalOracleEstimationManifestError as exc:
        raise EmpiricalOracleResultRecordingError(str(exc)) from exc
    contract = _load_json_object(cell_contract_path, label="cell contract")
    cell = _find_cell(manifest, contract.get("cell_id"))
    _validate_materialized_cell_contract(
        contract,
        contract_path=cell_contract_path,
        manifest=manifest,
        manifest_path=estimation_manifest_path,
        cell=cell,
    )
    condition_evidence = _load_and_validate_condition_evidence(
        condition_evidence_path,
        cell=cell,
    )
    normalized_reward = _validate_reward(reward)
    raw_trace_sha256 = sha256_file(raw_trace_path)

    results_root = results_root.expanduser()
    if results_root.is_symlink():
        raise EmpiricalOracleResultRecordingError("results_root must not be a symlink")
    results_root = results_root.resolve(strict=False)
    pointer = result_pointer_for_cell(cell["cell_id"])
    result_path = results_root / pointer
    raw_pointer = f"raw/{sha256_json(cell['cell_id'])}.jsonl"
    portable_raw_path = results_root / raw_pointer
    _ensure_new_result_paths(
        results_root,
        result_path=result_path,
        raw_path=portable_raw_path,
        raw_trace_sha256=raw_trace_sha256,
    )

    created_dirs: list[Path] = []
    created_raw = False
    created_result = False
    temporary_result: Path | None = None
    try:
        _mkdir_new_or_existing(results_root, created_dirs=created_dirs)
        _mkdir_new_or_existing(results_root / "raw", created_dirs=created_dirs)
        _mkdir_new_or_existing(result_path.parent, created_dirs=created_dirs)
        shutil.copyfile(raw_trace_path, portable_raw_path)
        created_raw = True
        if sha256_file(portable_raw_path) != raw_trace_sha256:
            raise EmpiricalOracleResultRecordingError("copied raw trace hash mismatch")
        result = {
            "schema_version": 1,
            "estimation_manifest_sha256": sha256_file(estimation_manifest_path),
            "cell_id": cell["cell_id"],
            "cell_contract_sha256": sha256_json(cell),
            "terminal_status": "scored",
            "reward": normalized_reward,
            "verifier_contract_sha256": cell["verifier_contract_sha256"],
            "runtime_contract_sha256": cell["runtime_contract_sha256"],
            "condition_application": condition_evidence,
            "raw_trace_pointer": raw_pointer,
            "raw_trace_sha256": raw_trace_sha256,
        }
        if set(result) != RESULT_KEYS:
            raise EmpiricalOracleResultRecordingError("assembler result schema drifted")
        temporary_result = result_path.with_suffix(result_path.suffix + ".tmp")
        temporary_result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_result.replace(result_path)
        created_result = True
        return result
    except Exception:
        if (
            temporary_result is not None
            and temporary_result.exists()
            and not temporary_result.is_symlink()
        ):
            temporary_result.unlink()
        if created_result and result_path.exists() and not result_path.is_symlink():
            result_path.unlink()
        if created_raw and portable_raw_path.exists() and not portable_raw_path.is_symlink():
            portable_raw_path.unlink()
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cell-contract", type=Path, required=True)
    parser.add_argument("--raw-trace", type=Path, required=True)
    parser.add_argument("--reward", type=float, required=True)
    parser.add_argument("--condition-evidence", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    args = parser.parse_args(argv)

    result = record_empirical_oracle_cell_result(
        estimation_manifest_path=args.manifest,
        cell_contract_path=args.cell_contract,
        raw_trace_path=args.raw_trace,
        reward=args.reward,
        condition_evidence_path=args.condition_evidence,
        results_root=args.results_root,
        base_manifest_path=args.base_manifest,
        index_path=args.index,
        corpus_provenance_path=args.corpus_provenance,
        tasks_root=args.tasks_root,
        skills_root=args.skills_root,
    )
    print(f"cell_id={result['cell_id']}")
    print(f"result_pointer={result_pointer_for_cell(result['cell_id'])}")
    print(f"raw_trace_pointer={result['raw_trace_pointer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
