"""Aggregate byte- and trace-verified library-scale cell results."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    LibraryScaleManifestError,
    sha256_file,
    sha256_json,
    tree_sha256,
    validate_library_scale_manifest,
)
from experiments.skillsbench.clustered_library_scale_bootstrap import (
    ClusteredBootstrapError,
    build_library_scale_clustered_bootstrap,
)
from src.merlin_harness.library_scale_results import (
    LibraryScaleResultError,
    aggregate_library_scale_cells,
    validate_library_scale_cell_trace,
)
from src.merlin_harness.traces import FileTraceStore


class LibraryScaleAggregationError(ValueError):
    """Raised when file-backed aggregation evidence is incomplete or unsafe."""


_VARIANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,159}$")


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryScaleAggregationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise LibraryScaleAggregationError(f"{label} must be a JSON object")
    return value


def _safe_evidence_file(root: Path, pointer: Any, expected_sha256: Any, *, label: str) -> Path:
    if not isinstance(pointer, str) or not pointer or Path(pointer).is_absolute():
        raise LibraryScaleAggregationError(f"{label} pointer must be a non-empty relative path")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in expected_sha256
    ):
        raise LibraryScaleAggregationError(f"{label} sha256 must be lowercase SHA-256")
    unresolved = root / pointer
    if unresolved.is_symlink():
        raise LibraryScaleAggregationError(f"{label} pointer must not be a symlink")
    target = unresolved.resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise LibraryScaleAggregationError(f"{label} pointer escapes its evidence root") from exc
    if not target.is_file() or sha256_file(target) != expected_sha256:
        raise LibraryScaleAggregationError(f"{label} evidence is missing or hash-invalid")
    return target


def load_empirical_oracle_mapping(
    path: Path,
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
) -> tuple[dict[str, tuple[str, ...]], dict[str, Any]]:
    """Load a separately evidenced curated-scope empirical oracle estimate."""

    payload = _load_json_object(path, label="empirical oracle manifest")
    required = {
        "schema_version",
        "experiment_id",
        "library_scale_manifest_sha256",
        "oracle_candidate_scope",
        "estimation_evidence_complete",
        "estimation_contract",
        "tasks",
    }
    if set(payload) != required:
        raise LibraryScaleAggregationError("empirical oracle manifest keys do not match schema")
    if payload["schema_version"] != 1 or payload["experiment_id"] != manifest.get("experiment_id"):
        raise LibraryScaleAggregationError("empirical oracle manifest identity mismatch")
    if payload["library_scale_manifest_sha256"] != sha256_file(manifest_path):
        raise LibraryScaleAggregationError("empirical oracle manifest is bound to another scale manifest")
    if payload["oracle_candidate_scope"] != "task_curated_bundle":
        raise LibraryScaleAggregationError("empirical oracle candidate scope must be task_curated_bundle")
    if payload["estimation_evidence_complete"] is not True:
        raise LibraryScaleAggregationError("empirical oracle estimation evidence is incomplete")

    contract = payload["estimation_contract"]
    if not isinstance(contract, dict) or set(contract) != {
        "model_id",
        "backend",
        "harness_mode",
        "tau",
        "repeats",
        "candidate_pool_sha256",
    }:
        raise LibraryScaleAggregationError("empirical oracle estimation contract is invalid")
    for field in ("model_id", "backend", "harness_mode"):
        if not isinstance(contract[field], str) or not contract[field].strip():
            raise LibraryScaleAggregationError(f"empirical oracle {field} must be non-empty")
    tau = contract["tau"]
    if isinstance(tau, bool) or not isinstance(tau, (int, float)) or not 0.0 <= tau <= 1.0:
        raise LibraryScaleAggregationError("empirical oracle tau must be numeric in [0,1]")
    repeats = contract["repeats"]
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 3:
        raise LibraryScaleAggregationError("empirical oracle repeats must be at least 3")
    if contract["candidate_pool_sha256"] != manifest["frozen_inputs"]["skill_pool_sha256"]:
        raise LibraryScaleAggregationError("empirical oracle candidate pool hash mismatch")

    tasks = payload["tasks"]
    if not isinstance(tasks, list):
        raise LibraryScaleAggregationError("empirical oracle tasks must be a list")
    mapping: dict[str, tuple[str, ...]] = {}
    root = path.parent.resolve()
    task_contracts = {
        task["task_id"]: task
        for task in manifest.get("task_contracts", [])
        if isinstance(task, dict) and isinstance(task.get("task_id"), str)
    }
    runtime_contract_sha256 = sha256_json(contract)
    seen_raw_evidence: set[tuple[str, str]] = set()

    def validate_trials(
        value: Any,
        *,
        task_id: str,
        label: str,
        verifier_contract_sha256: str,
    ) -> list[float]:
        if not isinstance(value, list) or len(value) != repeats:
            raise LibraryScaleAggregationError(
                f"empirical oracle {task_id} {label} must contain exactly {repeats} trials"
            )
        rewards: list[float] = []
        seen_indices: set[int] = set()
        for trial in value:
            if not isinstance(trial, dict) or set(trial) != {
                "trial_index",
                "reward",
                "verifier_contract_sha256",
                "runtime_contract_sha256",
                "raw_trace_pointer",
                "raw_trace_sha256",
            }:
                raise LibraryScaleAggregationError(
                    f"empirical oracle {task_id} {label} trial schema is invalid"
                )
            trial_index = trial["trial_index"]
            reward = trial["reward"]
            if (
                isinstance(trial_index, bool)
                or not isinstance(trial_index, int)
                or not 1 <= trial_index <= repeats
                or trial_index in seen_indices
            ):
                raise LibraryScaleAggregationError(
                    f"empirical oracle {task_id} {label} trial indices are invalid"
                )
            if isinstance(reward, bool) or not isinstance(reward, (int, float)) or not 0.0 <= reward <= 1.0:
                raise LibraryScaleAggregationError(
                    f"empirical oracle {task_id} {label} reward must be numeric in [0,1]"
                )
            if trial["verifier_contract_sha256"] != verifier_contract_sha256:
                raise LibraryScaleAggregationError(
                    f"empirical oracle {task_id} {label} verifier contract mismatch"
                )
            if trial["runtime_contract_sha256"] != runtime_contract_sha256:
                raise LibraryScaleAggregationError(
                    f"empirical oracle {task_id} {label} runtime contract mismatch"
                )
            _safe_evidence_file(
                root,
                trial["raw_trace_pointer"],
                trial["raw_trace_sha256"],
                label=f"empirical oracle raw trace {task_id} {label} t{trial_index}",
            )
            raw_key = (trial["raw_trace_pointer"], trial["raw_trace_sha256"])
            if raw_key in seen_raw_evidence:
                raise LibraryScaleAggregationError("empirical oracle raw trace evidence is reused")
            seen_raw_evidence.add(raw_key)
            seen_indices.add(trial_index)
            rewards.append(float(reward))
        return rewards

    for record in tasks:
        if not isinstance(record, dict) or set(record) != {
            "task_id",
            "skill_variant_ids",
            "evidence_pointer",
            "evidence_sha256",
        }:
            raise LibraryScaleAggregationError("empirical oracle task record is invalid")
        task_id = record["task_id"]
        skill_ids = record["skill_variant_ids"]
        if not isinstance(task_id, str) or not isinstance(skill_ids, list) or any(
            not isinstance(skill_id, str) or not skill_id for skill_id in skill_ids
        ):
            raise LibraryScaleAggregationError("empirical oracle task id or skill list is invalid")
        if task_id in mapping or len(skill_ids) != len(set(skill_ids)):
            raise LibraryScaleAggregationError("empirical oracle tasks and skill IDs must be unique")
        evidence_path = _safe_evidence_file(
            root,
            record["evidence_pointer"],
            record["evidence_sha256"],
            label=f"empirical oracle {task_id}",
        )
        task_contract = task_contracts.get(task_id)
        if task_contract is None:
            raise LibraryScaleAggregationError(f"empirical oracle references unknown task: {task_id}")
        evidence = _load_json_object(evidence_path, label=f"empirical oracle task evidence {task_id}")
        if set(evidence) != {
            "schema_version",
            "task_id",
            "estimation_contract_sha256",
            "verifier_contract_sha256",
            "no_skill_trials",
            "candidate_trials",
        }:
            raise LibraryScaleAggregationError(f"empirical oracle task evidence schema is invalid: {task_id}")
        if evidence["schema_version"] != 1 or evidence["task_id"] != task_id:
            raise LibraryScaleAggregationError(f"empirical oracle task evidence identity mismatch: {task_id}")
        if evidence["estimation_contract_sha256"] != runtime_contract_sha256:
            raise LibraryScaleAggregationError(f"empirical oracle task contract mismatch: {task_id}")
        verifier_contract_sha256 = task_contract["verifier_contract_sha256"]
        if evidence["verifier_contract_sha256"] != verifier_contract_sha256:
            raise LibraryScaleAggregationError(f"empirical oracle verifier mismatch: {task_id}")
        no_skill_rewards = validate_trials(
            evidence["no_skill_trials"],
            task_id=task_id,
            label="no-skill",
            verifier_contract_sha256=verifier_contract_sha256,
        )
        candidates = evidence["candidate_trials"]
        if not isinstance(candidates, list):
            raise LibraryScaleAggregationError(f"empirical oracle candidate trials must be a list: {task_id}")
        reference_order = list(task_contract.get("reference_skill_variants", []))
        if [candidate.get("skill_variant_id") for candidate in candidates if isinstance(candidate, dict)] != reference_order:
            raise LibraryScaleAggregationError(
                f"empirical oracle candidates must exactly cover the curated task bundle: {task_id}"
            )
        no_skill_mean = sum(no_skill_rewards) / repeats
        derived: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {"skill_variant_id", "trials"}:
                raise LibraryScaleAggregationError(f"empirical oracle candidate schema is invalid: {task_id}")
            candidate_id = candidate["skill_variant_id"]
            rewards = validate_trials(
                candidate["trials"],
                task_id=task_id,
                label=f"skill-{candidate_id}",
                verifier_contract_sha256=verifier_contract_sha256,
            )
            if sum(rewards) / repeats - no_skill_mean >= float(tau):
                derived.append(candidate_id)
        if skill_ids != derived:
            raise LibraryScaleAggregationError(
                f"empirical oracle declared skills do not match recomputed uplift set: {task_id}"
            )
        mapping[task_id] = tuple(derived)
    expected_tasks = {cell["task_id"] for cell in manifest["cells"]}
    if set(mapping) != expected_tasks:
        raise LibraryScaleAggregationError("empirical oracle task coverage must match all 87 tasks")
    return mapping, contract


def _verify_materialized_bundle(contract_path: Path, contract: dict[str, Any]) -> None:
    cell_root = contract_path.parent.resolve()
    if contract.get("staged_skill_root") != "skills":
        raise LibraryScaleAggregationError("cell staged_skill_root must be the portable skills directory")
    skills_root = (cell_root / "skills").resolve()
    try:
        skills_root.relative_to(cell_root)
    except ValueError as exc:
        raise LibraryScaleAggregationError("staged skills root escapes its cell directory") from exc
    if not skills_root.is_dir() or skills_root.is_symlink():
        raise LibraryScaleAggregationError("staged skills root is missing or unsafe")
    records = contract.get("variant_records")
    if not isinstance(records, list):
        raise LibraryScaleAggregationError("cell variant records must be a list")
    expected_variants: set[str] = set()
    for record in records:
        variant = record.get("variant") if isinstance(record, dict) else None
        expected_sha = record.get("staged_tree_sha256") if isinstance(record, dict) else None
        if (
            not isinstance(variant, str)
            or not _VARIANT_RE.fullmatch(variant)
            or not isinstance(expected_sha, str)
        ):
            raise LibraryScaleAggregationError("cell variant record is invalid")
        variant_root = (skills_root / variant).resolve()
        try:
            variant_root.relative_to(skills_root)
        except ValueError as exc:
            raise LibraryScaleAggregationError("staged skill path escapes its cell") from exc
        if variant_root.is_symlink() or tree_sha256(variant_root) != expected_sha:
            raise LibraryScaleAggregationError(f"staged skill bytes drifted: {variant}")
        expected_variants.add(variant)
    root_entries = list(skills_root.iterdir())
    if any(path.is_symlink() or not path.is_dir() for path in root_entries):
        raise LibraryScaleAggregationError("staged skill root contains unexpected entries")
    observed_variants = {path.name for path in root_entries}
    if observed_variants != expected_variants:
        raise LibraryScaleAggregationError("staged skill directory set does not match cell contract")


def aggregate_library_scale_run(
    *,
    manifest_path: Path,
    cell_root: Path,
    trace_root: Path,
    empirical_oracle_path: Path | None = None,
    base_manifest_path: Path | None = None,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    manifest = _load_json_object(manifest_path, label="library-scale manifest")
    schema_version = manifest.get("schema_version")
    if schema_version == 1:
        try:
            validate_library_scale_manifest(
                manifest,
                index_path=index_path,
                skills_root=skills_root,
            )
        except LibraryScaleManifestError as exc:
            raise LibraryScaleAggregationError(str(exc)) from exc
        oracle_binding_manifest = manifest
        oracle_binding_manifest_path = manifest_path
    elif schema_version == 2:
        if base_manifest_path is None or empirical_oracle_path is None:
            raise LibraryScaleAggregationError(
                "schema 2 aggregation requires --base-manifest and --empirical-oracle"
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
            raise LibraryScaleAggregationError(str(exc)) from exc
        oracle_binding_manifest = _load_json_object(
            base_manifest_path,
            label="base library-scale manifest",
        )
        oracle_binding_manifest_path = base_manifest_path
    else:
        raise LibraryScaleAggregationError("manifest schema version must be 1 or 2")
    if not cell_root.is_dir() or cell_root.is_symlink():
        raise LibraryScaleAggregationError("cell root must be an existing non-symlink directory")
    if not trace_root.is_dir() or trace_root.is_symlink():
        raise LibraryScaleAggregationError("trace root must be an existing non-symlink directory")

    trace_store = FileTraceStore(trace_root)
    traces = trace_store.list()
    manifest_by_cell = {cell["cell_id"]: cell for cell in manifest["cells"]}
    validated = []
    manifest_sha = sha256_file(manifest_path)
    for trace in traces:
        if trace.id not in manifest_by_cell:
            raise LibraryScaleAggregationError(f"trace references unknown manifest cell: {trace.id}")
        cell_directory = cell_root / trace.id
        if cell_directory.is_symlink():
            raise LibraryScaleAggregationError(f"cell directory must not be a symlink: {trace.id}")
        resolved_cell_directory = cell_directory.resolve()
        try:
            resolved_cell_directory.relative_to(cell_root.resolve())
        except ValueError as exc:
            raise LibraryScaleAggregationError(f"cell directory escapes cell root: {trace.id}") from exc
        contract_path = resolved_cell_directory / "cell-contract.json"
        if contract_path.is_symlink():
            raise LibraryScaleAggregationError(f"cell contract must not be a symlink: {trace.id}")
        contract = _load_json_object(contract_path, label=f"cell contract {trace.id}")
        if contract.get("manifest_file_sha256") != manifest_sha:
            raise LibraryScaleAggregationError(f"cell contract manifest hash mismatch: {trace.id}")
        if schema_version == 2:
            dependencies = contract.get("derived_manifest_dependencies")
            if (
                contract.get("manifest_schema_version") != 2
                or not isinstance(dependencies, dict)
                or dependencies.get("base_manifest_file_sha256")
                != sha256_file(base_manifest_path)
                or dependencies.get("empirical_oracle_file_sha256")
                != sha256_file(empirical_oracle_path)
                or contract.get("empirical_oracle_skill_variants")
                != manifest_by_cell[trace.id].get(
                    "empirical_oracle_skill_variants"
                )
            ):
                raise LibraryScaleAggregationError(
                    f"cell contract derived-manifest dependency mismatch: {trace.id}"
                )
        _verify_materialized_bundle(contract_path, contract)
        try:
            validated.append(
                validate_library_scale_cell_trace(
                    manifest_cell=manifest_by_cell[trace.id],
                    materialization_contract=contract,
                    trace=trace,
                )
            )
        except LibraryScaleResultError as exc:
            raise LibraryScaleAggregationError(f"cell {trace.id}: {exc}") from exc

    oracle_mapping = None
    oracle_contract = None
    if empirical_oracle_path is not None:
        oracle_mapping, oracle_contract = load_empirical_oracle_mapping(
            empirical_oracle_path,
            manifest=oracle_binding_manifest,
            manifest_path=oracle_binding_manifest_path,
        )
        if validated:
            runtime = validated[0].runtime_key
            if (
                oracle_contract["backend"] != runtime[2]
                or oracle_contract["model_id"] != runtime[3]
                or oracle_contract["harness_mode"] != runtime[6]
            ):
                raise LibraryScaleAggregationError(
                    "empirical oracle model/backend/harness contract does not match the run"
                )

    summary = aggregate_library_scale_cells(
        manifest=manifest,
        cells=validated,
        empirical_oracle_by_task=oracle_mapping,
    )
    try:
        summary["shadowing_summary"]["clustered_bootstrap"] = (
            build_library_scale_clustered_bootstrap(
                manifest=manifest,
                cells=validated,
                normalized_oracles=oracle_mapping,
                aggregate_summary=summary,
            )
        )
    except ClusteredBootstrapError as exc:
        raise LibraryScaleAggregationError(
            f"clustered bootstrap contract failed: {exc}"
        ) from exc
    return {
        "schema_version": 1,
        "manifest": {
            "path": manifest_path.name,
            "sha256": manifest_sha,
            "schema_version": schema_version,
            "base_manifest": (
                None
                if base_manifest_path is None
                else {
                    "path": base_manifest_path.name,
                    "sha256": sha256_file(base_manifest_path),
                }
            ),
        },
        "trace_count": len(traces),
        "empirical_oracle": (
            None
            if empirical_oracle_path is None
            else {
                "path": empirical_oracle_path.name,
                "sha256": sha256_file(empirical_oracle_path),
                "estimation_contract": oracle_contract,
            }
        ),
        "summary": summary,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise LibraryScaleAggregationError("output already exists; pass --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cell-root", type=Path, required=True)
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--empirical-oracle", type=Path)
    parser.add_argument("--base-manifest", type=Path)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    output_resolved = args.output.resolve()
    for protected_root, label in (
        (args.cell_root.resolve(), "cell root"),
        (args.trace_root.resolve(), "trace root"),
    ):
        try:
            output_resolved.relative_to(protected_root)
        except ValueError:
            continue
        raise LibraryScaleAggregationError(f"output must be outside the {label}")

    payload = aggregate_library_scale_run(
        manifest_path=args.manifest,
        cell_root=args.cell_root,
        trace_root=args.trace_root,
        empirical_oracle_path=args.empirical_oracle,
        base_manifest_path=args.base_manifest,
        index_path=args.index,
        skills_root=args.skills_root,
    )
    _write_json_atomic(args.output, payload, overwrite=args.overwrite)
    print(f"observed_cells={payload['summary']['observed_cells']}")
    print(f"full_denominator_scored={payload['summary']['full_denominator_scored']}")
    print(f"shadowing_status={payload['summary']['shadowing_summary']['status']}")
    print(f"saved -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
