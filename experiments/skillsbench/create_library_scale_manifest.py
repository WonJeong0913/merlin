"""Create an auditable 87-task repeated library-scale experiment manifest.

The manifest expands every task's upstream curated bundle with deterministic,
nested distractor sets drawn from the full vendored SkillsBench pool.  It is a
scheduling and contamination-control artifact.  The curated bundle is *not*
renamed to an empirical oracle set, and the manifest alone is not shadowing
evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_INDEX = ROOT / "skills-index.json"
DEFAULT_CORPUS_PROVENANCE = ROOT / "corpus-provenance.json"
TASKS_ROOT_ENV = "MERLIN_SKILLSBENCH_TASKS_ROOT"
_configured_tasks_root = os.environ.get(TASKS_ROOT_ENV)
if _configured_tasks_root:
    DEFAULT_TASKS_ROOT = Path(_configured_tasks_root).expanduser()
    if not DEFAULT_TASKS_ROOT.is_absolute():
        raise ValueError(f"{TASKS_ROOT_ENV} must be an absolute path")
else:
    DEFAULT_TASKS_ROOT = ROOT / "tasks"
DEFAULT_SKILLS_ROOT = ROOT / "skills"
DEFAULT_OUTPUT = ROOT / "library-scale-manifest.json"
DEFAULT_BASE_SEED = 20260719
DEFAULT_TRIAL_INDICES = (1, 2, 3)
DEFAULT_DISTRACTOR_COUNTS: tuple[int | None, ...] = (0, 10, 50, 100, None)


class LibraryScaleManifestError(ValueError):
    """Raised when the frozen library-scale contract is inconsistent."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def tree_sha256(root: Path) -> str:
    """Hash an immutable regular-file tree including relative paths."""

    if not root.is_dir():
        raise LibraryScaleManifestError(f"required tree is missing: {root}")
    records: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise LibraryScaleManifestError(f"symlinks are not allowed in frozen trees: {path}")
        if path.is_file():
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                }
            )
    return sha256_json(records)


def _stable_key(*parts: object) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _normalize_trial_indices(values: Iterable[int]) -> tuple[int, ...]:
    result = tuple(values)
    if not result or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in result):
        raise LibraryScaleManifestError("trial indices must be positive integers")
    if len(result) != len(set(result)) or tuple(sorted(result)) != result:
        raise LibraryScaleManifestError("trial indices must be unique and sorted")
    return result


def _normalize_distractor_counts(values: Iterable[int | None]) -> tuple[int | None, ...]:
    result = tuple(values)
    if not result or result[0] != 0 or result[-1] is not None:
        raise LibraryScaleManifestError("distractor counts must start at 0 and end at full")
    numeric = result[:-1]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in numeric):
        raise LibraryScaleManifestError("distractor counts must be non-negative integers plus final full")
    if len(numeric) != len(set(numeric)) or tuple(sorted(numeric)) != numeric:
        raise LibraryScaleManifestError("numeric distractor counts must be unique and sorted")
    return result


def _load_index(index_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryScaleManifestError(f"cannot read skills index: {index_path}") from exc
    if not isinstance(value, dict):
        raise LibraryScaleManifestError("skills index must be a JSON object")
    return value


def _load_corpus_provenance(path: Path, *, expected_commit: str | None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryScaleManifestError(f"cannot read corpus provenance: {path}") from exc
    if not isinstance(value, dict):
        raise LibraryScaleManifestError("corpus provenance must be a JSON object")
    expected = value.get("expected_manifest_sha256")
    local = value.get("local_manifest_sha256")
    if (
        value.get("regular_blobs_exact") is not True
        or not isinstance(expected, str)
        or len(expected) != 64
        or expected != local
    ):
        raise LibraryScaleManifestError("corpus provenance does not prove an exact regular-blob mirror")
    if expected_commit is not None and value.get("upstream_commit") != expected_commit:
        raise LibraryScaleManifestError("corpus provenance commit does not match the skills index")
    return value


def indexed_variant_snapshot_records(
    index: dict[str, Any],
    skills_root: Path,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Return the validated index-order records used by cell snapshot hashes."""
    entries = index.get("skills")
    if not isinstance(entries, list) or not entries:
        raise LibraryScaleManifestError("skills index has no skill records")
    order: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("variant"), str):
            raise LibraryScaleManifestError("skills index contains an invalid skill record")
        variant = entry["variant"]
        if variant in records:
            raise LibraryScaleManifestError(f"duplicate skill variant: {variant}")
        variant_root = skills_root / variant
        if not variant_root.is_dir() or not (variant_root / "SKILL.md").is_file():
            raise LibraryScaleManifestError(f"indexed skill package is missing: {variant}")
        records[variant] = {
            "variant": variant,
            "index_content_hash": entry.get("content_hash"),
            "size_bytes": entry.get("size_bytes"),
            "n_files": entry.get("n_files"),
        }
        order.append(variant)
    return order, records


def _task_records(index: dict[str, Any]) -> list[dict[str, Any]]:
    entries = index.get("tasks")
    if not isinstance(entries, list) or not entries:
        raise LibraryScaleManifestError("skills index has no task records")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        task_id = entry.get("id") if isinstance(entry, dict) else None
        curated = entry.get("curated_skill_variants") if isinstance(entry, dict) else None
        if not isinstance(task_id, str) or not task_id or not isinstance(curated, list):
            raise LibraryScaleManifestError("skills index contains an invalid task record")
        if task_id in seen:
            raise LibraryScaleManifestError(f"duplicate task id: {task_id}")
        if any(not isinstance(value, str) or not value for value in curated):
            raise LibraryScaleManifestError(f"task {task_id} has an invalid curated variant")
        if len(curated) != len(set(curated)):
            raise LibraryScaleManifestError(f"task {task_id} has duplicate curated variants")
        seen.add(task_id)
        result.append(entry)
    return sorted(result, key=lambda item: item["id"])


def _arm_id(count: int | None, pool_size: int) -> str:
    if count == 0:
        return "curated"
    if count is None:
        return f"full-{pool_size}"
    return f"plus-{count}"


def build_library_scale_manifest(
    *,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
    base_seed: int = DEFAULT_BASE_SEED,
    trial_indices: Iterable[int] = DEFAULT_TRIAL_INDICES,
    distractor_counts: Iterable[int | None] = DEFAULT_DISTRACTOR_COUNTS,
    created: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic full-denominator library-scale contract."""

    if isinstance(base_seed, bool) or not isinstance(base_seed, int) or base_seed < 0:
        raise LibraryScaleManifestError("base seed must be a non-negative integer")
    trials = _normalize_trial_indices(trial_indices)
    counts = _normalize_distractor_counts(distractor_counts)
    index = _load_index(index_path)
    corpus_provenance = _load_corpus_provenance(
        corpus_provenance_path,
        expected_commit=index.get("commit"),
    )
    corpus_manifest_sha256 = corpus_provenance["local_manifest_sha256"]
    variant_order, variants = indexed_variant_snapshot_records(index, skills_root)
    tasks = _task_records(index)
    variant_ids = set(variant_order)

    task_contracts: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task["id"]
        task_root = tasks_root / task_id
        task_md = task_root / "task.md"
        verifier_root = task_root / "verifier"
        if not task_md.is_file() or not verifier_root.is_dir():
            raise LibraryScaleManifestError(f"task instruction is missing: {task_id}")
        task_instruction_sha256 = sha256_file(task_md)
        verifier_contract_sha256 = sha256_json(
            {
                "corpus_manifest_sha256": corpus_manifest_sha256,
                "path": f"{task_id}/verifier",
            }
        )
        reference = tuple(task["curated_skill_variants"])
        unknown = set(reference) - variant_ids
        if unknown:
            raise LibraryScaleManifestError(
                f"task {task_id} references unknown curated variants: {', '.join(sorted(unknown))}"
            )
        task_contracts.append(
            {
                "task_id": task_id,
                "category": task.get("category"),
                "difficulty": task.get("difficulty"),
                "task_instruction_sha256": task_instruction_sha256,
                "verifier_contract_sha256": verifier_contract_sha256,
                "reference_skill_variants": list(reference),
                "reference_semantics": "upstream_curated_bundle_not_empirical_oracle",
            }
        )

        distractor_pool = [variant for variant in variant_order if variant not in set(reference)]
        for trial_index in trials:
            trial_seed = base_seed + trial_index
            membership_order = sorted(
                distractor_pool,
                key=lambda variant: _stable_key("membership", trial_seed, task_id, variant),
            )
            prior_set: set[str] = set(reference)
            for count in counts:
                selected_distractors = (
                    membership_order
                    if count is None
                    else membership_order[: min(count, len(membership_order))]
                )
                library_set = set(reference) | set(selected_distractors)
                if not prior_set.issubset(library_set):
                    raise LibraryScaleManifestError("generated library arms are not nested")
                prior_set = library_set
                presentation_order = sorted(
                    library_set,
                    key=lambda variant: _stable_key("presentation", trial_seed, task_id, variant),
                )
                snapshot_records = [variants[variant] for variant in presentation_order]
                arm_id = _arm_id(count, len(variant_order))
                cells.append(
                    {
                        "cell_id": f"{task_id}__t{trial_index}__{arm_id}",
                        "task_id": task_id,
                        "trial_index": trial_index,
                        "trial_seed": trial_seed,
                        "arm_id": arm_id,
                        "requested_distractor_count": "full" if count is None else count,
                        "actual_distractor_count": len(selected_distractors),
                        "library_size": len(presentation_order),
                        "library_variant_ids": presentation_order,
                        "library_snapshot_sha256": sha256_json(snapshot_records),
                        "task_instruction_sha256": task_instruction_sha256,
                        "verifier_contract_sha256": verifier_contract_sha256,
                        "reference_skill_variants": list(reference),
                    }
                )

    manifest = {
        "schema_version": 1,
        "experiment_id": "skillsbench-full87-library-scale-v1",
        "created": created or date.today().isoformat(),
        "source": index.get("source"),
        "commit": index.get("commit"),
        "license": index.get("license"),
        "base_seed": base_seed,
        "trial_indices": list(trials),
        "distractor_counts": ["full" if value is None else value for value in counts],
        "task_count": len(tasks),
        "skill_pool_count": len(variant_order),
        "arm_count_per_trial": len(counts),
        "expected_cells": len(tasks) * len(trials) * len(counts),
        "frozen_inputs": {
            "skills_index_sha256": sha256_file(index_path),
            "corpus_provenance_sha256": sha256_file(corpus_provenance_path),
            "corpus_regular_blob_manifest_sha256": corpus_manifest_sha256,
            "skill_pool_sha256": sha256_json([variants[variant] for variant in variant_order]),
        },
        "design": {
            "full_denominator": "all indexed tasks are scheduled in every trial and arm",
            "reference_bundle": "the upstream curated task bundle remains present in every arm",
            "nested_membership": "distractors are stable-hash prefixes within each task and trial",
            "presentation_order": "a separate stable hash avoids curated-first ordering bias",
            "repeat_pairing": "all arms for one task and trial share task, verifier, and trial seed",
        },
        "evidence_contract": {
            "curated_bundle_is_empirical_oracle": False,
            "selected_or_exposed_skill_ids_are_actual_invocations": False,
            "actual_invocation_evidence_required_for_shadowing_metrics": True,
            "raw_trace_hash_required": True,
            "same_staged_verifier_tree_hash_required_across_paired_arms": True,
            "numeric_noncompletion_and_infrastructure_policy_must_be_preregistered": True,
            "headline_shadowing_claim_eligible": False,
            "headline_blocker": "attach a separately estimated empirical oracle manifest and complete actual-invocation traces",
        },
        "task_contracts": task_contracts,
        "cells": cells,
    }
    if len({cell["cell_id"] for cell in cells}) != len(cells):
        raise LibraryScaleManifestError("generated cell IDs are not unique")
    return manifest


def validate_library_scale_manifest(
    manifest: dict[str, Any],
    *,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> None:
    """Fail closed unless a manifest exactly reproduces from frozen inputs."""

    if not isinstance(manifest, dict):
        raise LibraryScaleManifestError("manifest must be a JSON object")
    raw_counts = manifest.get("distractor_counts")
    if not isinstance(raw_counts, list):
        raise LibraryScaleManifestError("manifest distractor_counts must be a list")
    counts: list[int | None] = [None if value == "full" else value for value in raw_counts]
    expected = build_library_scale_manifest(
        index_path=index_path,
        corpus_provenance_path=corpus_provenance_path,
        tasks_root=tasks_root,
        skills_root=skills_root,
        base_seed=manifest.get("base_seed"),
        trial_indices=manifest.get("trial_indices", []),
        distractor_counts=counts,
        created=manifest.get("created"),
    )
    if manifest != expected:
        raise LibraryScaleManifestError("manifest does not reproduce from the frozen corpus")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the full-87 repeated library-scale manifest.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)

    if args.verify is not None:
        payload = json.loads(args.verify.read_text(encoding="utf-8"))
        validate_library_scale_manifest(
            payload,
            index_path=args.index,
            corpus_provenance_path=args.corpus_provenance,
            tasks_root=args.tasks_root,
            skills_root=args.skills_root,
        )
        print(f"verified -> {args.verify}")
        print(f"manifest_sha256={sha256_file(args.verify)}")
        return 0

    manifest = build_library_scale_manifest(
        index_path=args.index,
        corpus_provenance_path=args.corpus_provenance,
        tasks_root=args.tasks_root,
        skills_root=args.skills_root,
        base_seed=args.base_seed,
        trial_indices=range(1, args.trials + 1),
    )
    write_json_atomic(args.output, manifest)
    validate_library_scale_manifest(
        manifest,
        index_path=args.index,
        corpus_provenance_path=args.corpus_provenance,
        tasks_root=args.tasks_root,
        skills_root=args.skills_root,
    )
    print(f"task_count={manifest['task_count']}")
    print(f"skill_pool_count={manifest['skill_pool_count']}")
    print(f"expected_cells={manifest['expected_cells']}")
    print(f"saved -> {args.output}")
    print(f"manifest_sha256={sha256_file(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
