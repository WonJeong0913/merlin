"""Materialize one frozen library-scale cell into a new immutable run bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_CORPUS_PROVENANCE,
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    DEFAULT_TASKS_ROOT,
    LibraryScaleManifestError,
    sha256_file,
    sha256_json,
    tree_sha256,
    validate_library_scale_manifest,
)


_VARIANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,159}$")
_RUNTIME_DIRS = frozenset({"__pycache__", ".pytest_cache"})
_RUNTIME_NAMES = frozenset({".DS_Store"})
_RUNTIME_SUFFIXES = frozenset({".pyc", ".pyo"})
_ROOT_ENTRIES = frozenset(
    {"skills", "task-visible", "verifier-hidden", "cell-contract.json"}
)


class LibraryScaleMaterializationError(ValueError):
    """Raised when a frozen cell cannot be materialized safely."""


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryScaleMaterializationError(f"cannot read manifest: {path}") from exc
    if not isinstance(value, dict):
        raise LibraryScaleMaterializationError("manifest must be a JSON object")
    return value


def _find_cell(manifest: dict[str, Any], cell_id: str) -> dict[str, Any]:
    matches = [cell for cell in manifest.get("cells", []) if cell.get("cell_id") == cell_id]
    if len(matches) != 1:
        raise LibraryScaleMaterializationError(f"cell id must resolve exactly once: {cell_id}")
    return matches[0]


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_source_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise LibraryScaleMaterializationError(f"{label} is missing or unsafe: {path}")
    for member in path.rglob("*"):
        if member.is_symlink():
            raise LibraryScaleMaterializationError(f"{label} contains a symlink: {member}")
    return path


def _is_runtime_artifact(relative: Path) -> bool:
    return (
        any(part in _RUNTIME_DIRS for part in relative.parts)
        or relative.name in _RUNTIME_NAMES
        or relative.suffix in _RUNTIME_SUFFIXES
    )


def _filtered_records(
    source: Path, *, excluded_top_level: frozenset[str] = frozenset()
) -> list[dict[str, str]]:
    _safe_source_directory(source, label="source tree")
    records: list[dict[str, str]] = []
    for member in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if not member.is_file():
            continue
        relative = member.relative_to(source)
        if (
            _is_runtime_artifact(relative)
            or (relative.parts and relative.parts[0] in excluded_top_level)
        ):
            continue
        records.append({"path": relative.as_posix(), "sha256": sha256_file(member)})
    return records


def _copy_filtered_tree(
    source: Path,
    destination: Path,
    *,
    label: str,
    excluded_top_level: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    records = _filtered_records(source, excluded_top_level=excluded_top_level)
    destination.mkdir(parents=True, exist_ok=False)
    for record in records:
        relative = Path(record["path"])
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    staged = _filtered_records(destination)
    if staged != records:
        raise LibraryScaleMaterializationError(f"{label} staged byte records drifted")
    return {
        "regular_file_count": len(records),
        "records_sha256": sha256_json(records),
        "excluded_runtime_artifacts": [
            "__pycache__",
            ".pytest_cache",
            "*.pyc",
            "*.pyo",
            ".DS_Store",
        ],
    }


def _validate_tree_contract(root: Path, expected: Any, *, label: str) -> None:
    if not isinstance(expected, dict):
        raise LibraryScaleMaterializationError(f"{label} contract is missing")
    records = _filtered_records(root)
    if (
        expected.get("regular_file_count") != len(records)
        or expected.get("records_sha256") != sha256_json(records)
    ):
        raise LibraryScaleMaterializationError(f"{label} bytes drifted")


def validate_materialized_library_scale_cell(
    bundle_root: Path, *, expected_cell_id: str | None = None
) -> dict[str, Any]:
    """Reopen one staged cell and verify every retained execution input."""

    expanded = bundle_root.expanduser()
    if expanded.is_symlink():
        raise LibraryScaleMaterializationError("materialized root must not be a symlink")
    try:
        root = expanded.resolve(strict=True)
    except OSError as exc:
        raise LibraryScaleMaterializationError("materialized root is missing") from exc
    if not root.is_dir() or {item.name for item in root.iterdir()} != set(_ROOT_ENTRIES):
        raise LibraryScaleMaterializationError("materialized root entries drifted")
    for member in root.rglob("*"):
        if member.is_symlink():
            raise LibraryScaleMaterializationError("materialized cell contains a symlink")
    try:
        contract = json.loads((root / "cell-contract.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryScaleMaterializationError("cell contract is unreadable") from exc
    if not isinstance(contract, dict):
        raise LibraryScaleMaterializationError("cell contract must be an object")
    stored_hash = contract.get("cell_contract_sha256")
    unhashed = dict(contract)
    unhashed.pop("cell_contract_sha256", None)
    if stored_hash != sha256_json(unhashed):
        raise LibraryScaleMaterializationError("cell contract hash mismatch")
    if expected_cell_id is not None and contract.get("cell_id") != expected_cell_id:
        raise LibraryScaleMaterializationError("materialized cell identity differs")
    presentation = contract.get("presentation_order")
    records = contract.get("variant_records")
    if not isinstance(presentation, list) or not isinstance(records, list):
        raise LibraryScaleMaterializationError("materialized skill contract is incomplete")
    if len(presentation) != contract.get("library_size") or len(records) != len(presentation):
        raise LibraryScaleMaterializationError("materialized skill denominator drifted")
    skills = root / "skills"
    if {item.name for item in skills.iterdir()} != set(presentation):
        raise LibraryScaleMaterializationError("materialized skill membership drifted")
    for ordinal, (variant, record) in enumerate(zip(presentation, records, strict=True), start=1):
        if (
            not isinstance(record, dict)
            or record.get("ordinal") != ordinal
            or record.get("variant") != variant
            or tree_sha256(skills / variant) != record.get("staged_tree_sha256")
            or record.get("source_tree_sha256") != record.get("staged_tree_sha256")
        ):
            raise LibraryScaleMaterializationError("materialized skill bytes/order drifted")
    task_visible = root / "task-visible"
    task_md = task_visible / "task.md"
    if sha256_file(task_md) != contract.get("task_instruction_sha256"):
        raise LibraryScaleMaterializationError("staged task instruction drifted")
    environment_contract = contract.get("task_environment")
    environment = task_visible / "environment"
    if environment_contract is None:
        if environment.exists():
            raise LibraryScaleMaterializationError("unexpected task environment")
    else:
        _validate_tree_contract(environment, environment_contract, label="task environment")
        skills_placeholder = environment / "skills"
        if not skills_placeholder.is_dir() or any(skills_placeholder.iterdir()):
            raise LibraryScaleMaterializationError(
                "task environment skill placeholder must remain empty"
            )
    _validate_tree_contract(
        root / "verifier-hidden",
        contract.get("hidden_verifier"),
        label="hidden verifier",
    )
    return contract


def materialize_library_scale_cell(
    *,
    manifest_path: Path,
    cell_id: str,
    output_root: Path,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
    index_path: Path = DEFAULT_INDEX,
    base_manifest_path: Path | None = None,
    empirical_oracle_path: Path | None = None,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
) -> dict[str, Any]:
    """Copy one exact cell library into a new output directory and re-hash it."""

    manifest = _load_manifest(manifest_path)
    schema_version = manifest.get("schema_version")
    if schema_version == 1:
        try:
            validate_library_scale_manifest(
                manifest,
                index_path=index_path,
                skills_root=skills_root,
                tasks_root=tasks_root,
                corpus_provenance_path=corpus_provenance_path,
            )
        except LibraryScaleManifestError as exc:
            raise LibraryScaleMaterializationError(str(exc)) from exc
    elif schema_version == 2:
        if base_manifest_path is None or empirical_oracle_path is None:
            raise LibraryScaleMaterializationError(
                "schema 2 materialization requires --base-manifest and --empirical-oracle"
            )
        try:
            from experiments.skillsbench.bind_empirical_oracle_manifest import (
                OracleBoundManifestError,
                validate_oracle_bound_manifest,
            )

            validate_oracle_bound_manifest(
                manifest,
                base_manifest_path=base_manifest_path,
                empirical_oracle_path=empirical_oracle_path,
                index_path=index_path,
                skills_root=skills_root,
            )
        except (OracleBoundManifestError, OSError, json.JSONDecodeError) as exc:
            raise LibraryScaleMaterializationError(str(exc)) from exc
    else:
        raise LibraryScaleMaterializationError("manifest schema version must be 1 or 2")
    cell = _find_cell(manifest, cell_id)
    if output_root.exists():
        raise LibraryScaleMaterializationError("output root must not already exist")
    if output_root.is_symlink():
        raise LibraryScaleMaterializationError("output root must not be a symlink")

    variant_ids = cell.get("library_variant_ids")
    if not isinstance(variant_ids, list) or any(
        not isinstance(variant, str) or not _VARIANT_RE.fullmatch(variant)
        for variant in variant_ids
    ):
        raise LibraryScaleMaterializationError("cell contains an unsafe skill variant id")

    output_root.mkdir(parents=True, exist_ok=False)
    staged_skills = output_root / "skills"
    staged_skills.mkdir()
    variant_records: list[dict[str, Any]] = []
    try:
        for ordinal, variant in enumerate(variant_ids, start=1):
            source = skills_root / variant
            if not source.is_dir() or source.is_symlink():
                raise LibraryScaleMaterializationError(f"source skill package is missing or unsafe: {variant}")
            source_sha = tree_sha256(source)
            destination = staged_skills / variant
            shutil.copytree(source, destination, symlinks=False)
            staged_sha = tree_sha256(destination)
            if staged_sha != source_sha:
                raise LibraryScaleMaterializationError(f"staged skill hash mismatch: {variant}")
            variant_records.append(
                {
                    "ordinal": ordinal,
                    "variant": variant,
                    "source_tree_sha256": source_sha,
                    "staged_tree_sha256": staged_sha,
                }
            )

        byte_snapshot_sha256 = sha256_json(variant_records)
        source_task_root = _safe_source_directory(
            tasks_root / cell["task_id"], label="task package"
        )
        task_md = source_task_root / "task.md"
        if task_md.is_symlink() or not task_md.is_file():
            raise LibraryScaleMaterializationError("task instruction is missing or unsafe")
        if sha256_file(task_md) != cell["task_instruction_sha256"]:
            raise LibraryScaleMaterializationError("task instruction bytes drifted")
        visible = output_root / "task-visible"
        visible.mkdir()
        shutil.copyfile(task_md, visible / "task.md")
        environment = source_task_root / "environment"
        environment_contract = None
        if environment.exists():
            environment_contract = _copy_filtered_tree(
                environment,
                visible / "environment",
                label="task environment",
                excluded_top_level=frozenset({"skills"}),
            )
            (visible / "environment" / "skills").mkdir(exist_ok=True)
        verifier_contract = _copy_filtered_tree(
            source_task_root / "verifier",
            output_root / "verifier-hidden",
            label="hidden verifier",
        )
        provenance = _load_manifest(corpus_provenance_path)
        task_corpus_source = {
            "corpus_provenance_file_sha256": sha256_file(corpus_provenance_path),
            "upstream_commit": provenance.get("upstream_commit"),
            "regular_blob_count": provenance.get("regular_blob_count"),
            "expected_manifest_sha256": provenance.get("expected_manifest_sha256"),
            "local_manifest_sha256": provenance.get("local_manifest_sha256"),
            "tasks_root_path_sha256": hashlib.sha256(
                str(tasks_root.expanduser().resolve(strict=True)).encode("utf-8")
            ).hexdigest(),
            "runtime_admission_must_match": True,
        }
        if (
            not re.fullmatch(r"[0-9a-f]{40}", str(task_corpus_source["upstream_commit"] or ""))
            or task_corpus_source["expected_manifest_sha256"]
            != task_corpus_source["local_manifest_sha256"]
        ):
            raise LibraryScaleMaterializationError("task corpus provenance is invalid")
        derived_dependencies = None
        empirical_oracle_skill_variants = None
        if schema_version == 2:
            assert base_manifest_path is not None
            assert empirical_oracle_path is not None
            derived_dependencies = {
                "base_manifest_path": base_manifest_path.name,
                "base_manifest_file_sha256": sha256_file(base_manifest_path),
                "empirical_oracle_path": empirical_oracle_path.name,
                "empirical_oracle_file_sha256": sha256_file(empirical_oracle_path),
            }
            empirical_oracle_skill_variants = list(
                cell["empirical_oracle_skill_variants"]
            )
        contract = {
            "schema_version": 1,
            "manifest_schema_version": schema_version,
            "experiment_id": manifest["experiment_id"],
            "manifest_path": manifest_path.name,
            "manifest_file_sha256": sha256_file(manifest_path),
            "cell_id": cell["cell_id"],
            "task_id": cell["task_id"],
            "trial_index": cell["trial_index"],
            "trial_seed": cell["trial_seed"],
            "arm_id": cell["arm_id"],
            "library_size": cell["library_size"],
            "manifest_library_snapshot_sha256": cell["library_snapshot_sha256"],
            "materialized_byte_snapshot_sha256": byte_snapshot_sha256,
            "task_instruction_sha256": cell["task_instruction_sha256"],
            "verifier_contract_sha256": cell["verifier_contract_sha256"],
            "reference_skill_variants": cell["reference_skill_variants"],
            "empirical_oracle_skill_variants": empirical_oracle_skill_variants,
            "derived_manifest_dependencies": derived_dependencies,
            "presentation_order": list(variant_ids),
            "staged_skill_root": "skills",
            "task_visible_root": "task-visible",
            "task_environment": environment_contract,
            "task_environment_source_skills_excluded": True,
            "hidden_verifier_root": "verifier-hidden",
            "hidden_verifier": verifier_contract,
            "oracle_copied": False,
            "task_corpus_source": task_corpus_source,
            "variant_records": variant_records,
            "source_and_staged_bytes_match": True,
            "execution_status": "not_run",
            "evidence_boundary": {
                "materialization_is_model_execution": False,
                "materialization_is_actual_invocation": False,
                "runtime_must_preserve_presentation_order": True,
                "runtime_must_emit_raw_trace_hash": True,
                "runtime_must_hash_the_staged_verifier": True,
                "runtime_must_not_expose_verifier_or_oracle": True,
            },
        }
        contract["cell_contract_sha256"] = sha256_json(contract)
        _write_json_atomic(output_root / "cell-contract.json", contract)
        validate_materialized_library_scale_cell(
            output_root, expected_cell_id=cell["cell_id"]
        )
        return contract
    except Exception:
        # The caller asked for a new output root, so a failed partial bundle is
        # safe to remove. Existing user data is never targeted by this path.
        shutil.rmtree(output_root, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cell-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--base-manifest", type=Path)
    parser.add_argument("--empirical-oracle", type=Path)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument(
        "--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE
    )
    args = parser.parse_args(argv)

    contract = materialize_library_scale_cell(
        manifest_path=args.manifest,
        cell_id=args.cell_id,
        output_root=args.output,
        skills_root=args.skills_root,
        index_path=args.index,
        base_manifest_path=args.base_manifest,
        empirical_oracle_path=args.empirical_oracle,
        tasks_root=args.tasks_root,
        corpus_provenance_path=args.corpus_provenance,
    )
    print(f"cell_id={contract['cell_id']}")
    print(f"library_size={contract['library_size']}")
    print(f"materialized_byte_snapshot_sha256={contract['materialized_byte_snapshot_sha256']}")
    print(f"saved -> {args.output / 'cell-contract.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
