"""Materialize one schedule-only empirical-oracle estimation cell safely."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

from experiments.skillsbench.assemble_empirical_oracle_evidence import result_pointer_for_cell
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


_VARIANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,159}$")


class EmpiricalOracleEstimationMaterializationError(ValueError):
    """Raised when a schedule-only estimation cell cannot be staged safely."""


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmpiricalOracleEstimationMaterializationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise EmpiricalOracleEstimationMaterializationError(f"{label} must be a JSON object")
    return value


def _resolve_regular_file(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise EmpiricalOracleEstimationMaterializationError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise EmpiricalOracleEstimationMaterializationError(f"{label} is missing") from exc
    if not resolved.is_file():
        raise EmpiricalOracleEstimationMaterializationError(f"{label} must be a regular file")
    return resolved


def _find_cell(manifest: dict[str, Any], cell_id: str) -> dict[str, Any]:
    if not isinstance(cell_id, str) or not cell_id:
        raise EmpiricalOracleEstimationMaterializationError("cell_id must be a non-empty string")
    matches = [cell for cell in manifest.get("cells", []) if cell.get("cell_id") == cell_id]
    if len(matches) != 1:
        raise EmpiricalOracleEstimationMaterializationError(
            f"cell id must resolve exactly once: {cell_id}"
        )
    return matches[0]


def _task_contract_for_cell(manifest: dict[str, Any], cell: dict[str, Any]) -> dict[str, Any]:
    task_id = cell.get("task_id")
    matches = [
        task
        for task in manifest.get("task_contracts", [])
        if isinstance(task, dict) and task.get("task_id") == task_id
    ]
    if len(matches) != 1:
        raise EmpiricalOracleEstimationMaterializationError(
            f"cell task contract must resolve exactly once: {task_id}"
        )
    return matches[0]


def _validated_cell_skill_records(cell: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    condition_kind = cell.get("condition_kind")
    variant_ids = cell.get("skill_variant_ids")
    byte_hashes = cell.get("skill_variant_byte_hashes")
    if (
        condition_kind not in {"no-skill", "single-skill"}
        or not isinstance(variant_ids, list)
        or not isinstance(byte_hashes, list)
        or any(not isinstance(variant, str) or not _VARIANT_RE.fullmatch(variant) for variant in variant_ids)
        or len(variant_ids) != len(set(variant_ids))
    ):
        raise EmpiricalOracleEstimationMaterializationError("cell skill condition is invalid")
    if condition_kind == "no-skill" and (variant_ids or byte_hashes):
        raise EmpiricalOracleEstimationMaterializationError("no-skill cell must have no skill bundle")
    if condition_kind == "single-skill" and len(variant_ids) != 1:
        raise EmpiricalOracleEstimationMaterializationError(
            "single-skill cell must have exactly one skill bundle"
        )
    expected_records: list[dict[str, str]] = []
    for record in byte_hashes:
        if (
            not isinstance(record, dict)
            or set(record) != {"variant", "tree_sha256"}
            or not isinstance(record["variant"], str)
            or not _VARIANT_RE.fullmatch(record["variant"])
            or not isinstance(record["tree_sha256"], str)
            or len(record["tree_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in record["tree_sha256"])
        ):
            raise EmpiricalOracleEstimationMaterializationError("cell skill byte hash record is invalid")
        expected_records.append(
            {"variant": record["variant"], "tree_sha256": record["tree_sha256"]}
        )
    if [record["variant"] for record in expected_records] != variant_ids:
        raise EmpiricalOracleEstimationMaterializationError(
            "cell skill byte hash records do not match the selected skill order"
        )
    return list(variant_ids), expected_records


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def materialize_empirical_oracle_estimation_cell(
    *,
    manifest_path: Path,
    cell_id: str,
    output_root: Path,
    base_manifest_path: Path = DEFAULT_BASE_MANIFEST,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    """Stage exactly the zero or one portable skill bundle selected by one cell."""

    manifest_path = _resolve_regular_file(manifest_path, label="estimation manifest")
    base_manifest_path = _resolve_regular_file(base_manifest_path, label="base library-scale manifest")
    manifest = _load_json_object(manifest_path, label="estimation manifest")
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
        raise EmpiricalOracleEstimationMaterializationError(str(exc)) from exc
    cell = _find_cell(manifest, cell_id)
    task = _task_contract_for_cell(manifest, cell)
    variant_ids, expected_variant_records = _validated_cell_skill_records(cell)

    if cell.get("task_instruction_sha256") != task.get("task_instruction_sha256"):
        raise EmpiricalOracleEstimationMaterializationError("cell task instruction contract mismatch")
    if cell.get("task_tree_sha256") != task.get("task_tree_sha256"):
        raise EmpiricalOracleEstimationMaterializationError("cell task tree contract mismatch")
    if cell.get("verifier_contract_sha256") != task.get("verifier_contract_sha256"):
        raise EmpiricalOracleEstimationMaterializationError("cell verifier contract mismatch")
    if cell.get("verifier_tree_sha256") != task.get("verifier_tree_sha256"):
        raise EmpiricalOracleEstimationMaterializationError("cell verifier tree contract mismatch")
    if cell.get("runtime_contract_sha256") != sha256_json(manifest["estimation_contract"]):
        raise EmpiricalOracleEstimationMaterializationError("cell runtime contract hash mismatch")

    task_root = tasks_root / cell["task_id"]
    verifier_root = task_root / "verifier"
    source_task_tree_sha256 = tree_sha256(task_root)
    source_verifier_tree_sha256 = tree_sha256(verifier_root)
    if source_task_tree_sha256 != cell["task_tree_sha256"]:
        raise EmpiricalOracleEstimationMaterializationError("source task tree hash drifted")
    if source_verifier_tree_sha256 != cell["verifier_tree_sha256"]:
        raise EmpiricalOracleEstimationMaterializationError("source verifier tree hash drifted")

    output_root = output_root.expanduser()
    if output_root.exists() or output_root.is_symlink():
        raise EmpiricalOracleEstimationMaterializationError("output root must not already exist")
    output_root = output_root.resolve(strict=False)
    if output_root.exists():
        raise EmpiricalOracleEstimationMaterializationError("output root must not already exist")
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise EmpiricalOracleEstimationMaterializationError("skills root must be a regular directory")
    resolved_skills_root = skills_root.resolve(strict=True)

    output_root.mkdir(parents=True, exist_ok=False)
    staged_skills = output_root / "skills"
    staged_skills.mkdir()
    variant_records: list[dict[str, Any]] = []
    try:
        for ordinal, (variant, expected) in enumerate(
            zip(variant_ids, expected_variant_records, strict=True),
            start=1,
        ):
            source = resolved_skills_root / variant
            if source.is_symlink() or not source.is_dir():
                raise EmpiricalOracleEstimationMaterializationError(
                    f"source skill package is missing or unsafe: {variant}"
                )
            source_tree_sha256 = tree_sha256(source)
            if source_tree_sha256 != expected["tree_sha256"]:
                raise EmpiricalOracleEstimationMaterializationError(
                    f"source skill tree hash drifted: {variant}"
                )
            destination = staged_skills / variant
            shutil.copytree(source, destination, symlinks=False)
            staged_tree_sha256 = tree_sha256(destination)
            if staged_tree_sha256 != source_tree_sha256:
                raise EmpiricalOracleEstimationMaterializationError(
                    f"staged skill tree hash mismatch: {variant}"
                )
            variant_records.append(
                {
                    "ordinal": ordinal,
                    "variant": variant,
                    "expected_cell_tree_sha256": expected["tree_sha256"],
                    "source_tree_sha256": source_tree_sha256,
                    "staged_tree_sha256": staged_tree_sha256,
                }
            )

        cell_byte_snapshot_sha256 = sha256_json(expected_variant_records)
        source_byte_snapshot_sha256 = sha256_json(
            [
                {"variant": record["variant"], "tree_sha256": record["source_tree_sha256"]}
                for record in variant_records
            ]
        )
        staged_byte_snapshot_sha256 = sha256_json(
            [
                {"variant": record["variant"], "tree_sha256": record["staged_tree_sha256"]}
                for record in variant_records
            ]
        )
        if not (
            cell_byte_snapshot_sha256
            == source_byte_snapshot_sha256
            == staged_byte_snapshot_sha256
        ):
            raise EmpiricalOracleEstimationMaterializationError(
                "cell, source, and staged skill byte snapshots do not match"
            )
        contract = {
            "schema_version": 1,
            "manifest_schema_version": manifest["schema_version"],
            "experiment_id": manifest["experiment_id"],
            "estimation_manifest_path": manifest_path.name,
            "estimation_manifest_sha256": sha256_file(manifest_path),
            "estimation_cell_sha256": sha256_json(cell),
            "cell_contract_sha256": sha256_json(cell),
            "cell_id": cell["cell_id"],
            "task_id": cell["task_id"],
            "condition_id": cell["condition_id"],
            "condition_kind": cell["condition_kind"],
            "skill_variant_ids": variant_ids,
            "manifest_skill_variant_byte_hashes": expected_variant_records,
            "trial_index": cell["trial_index"],
            "trial_seed": cell["trial_seed"],
            "estimation_contract": manifest["estimation_contract"],
            "runtime_contract_sha256": cell["runtime_contract_sha256"],
            "task_instruction_sha256": cell["task_instruction_sha256"],
            "manifest_task_tree_sha256": cell["task_tree_sha256"],
            "source_task_tree_sha256": source_task_tree_sha256,
            "verifier_contract_sha256": cell["verifier_contract_sha256"],
            "manifest_verifier_tree_sha256": cell["verifier_tree_sha256"],
            "source_verifier_tree_sha256": source_verifier_tree_sha256,
            "expected_result_pointer": result_pointer_for_cell(cell["cell_id"]),
            "staged_skill_root": "skills",
            "variant_records": variant_records,
            "cell_byte_snapshot_sha256": cell_byte_snapshot_sha256,
            "source_byte_snapshot_sha256": source_byte_snapshot_sha256,
            "staged_byte_snapshot_sha256": staged_byte_snapshot_sha256,
            "source_cell_staged_bytes_match": True,
            "execution_status": "not_run",
            "evidence_boundary": {
                "materialization_is_model_execution": False,
                "materialization_is_actual_invocation": False,
                "materialization_is_result": False,
                "empirical_oracle_membership_estimated": False,
                "expected_result_pointer_is_not_a_result": True,
            },
        }
        _write_json_atomic(output_root / "cell-contract.json", contract)
        return contract
    except Exception:
        # output_root was required to be new, so this removes only this call's
        # partial bundle and never targets an existing user directory.
        shutil.rmtree(output_root, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cell-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    args = parser.parse_args(argv)

    contract = materialize_empirical_oracle_estimation_cell(
        manifest_path=args.manifest,
        cell_id=args.cell_id,
        output_root=args.output,
        base_manifest_path=args.base_manifest,
        index_path=args.index,
        corpus_provenance_path=args.corpus_provenance,
        tasks_root=args.tasks_root,
        skills_root=args.skills_root,
    )
    print(f"cell_id={contract['cell_id']}")
    print(f"condition_kind={contract['condition_kind']}")
    print(f"skill_count={len(contract['skill_variant_ids'])}")
    print(f"expected_result_pointer={contract['expected_result_pointer']}")
    print(f"saved -> {args.output / 'cell-contract.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
