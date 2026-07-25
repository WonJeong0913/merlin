"""Prepare or revalidate the exact six-cell M3-K external operator bundle.

The bundle materializes every trajectory in one frozen held-in pilot, preserves
the pilot's exact order, and byte-revalidates each full-209 cell before an
eligible executor receives it.  It is an atomic, new-only handoff and never
executes a model or creates a benchmark result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    validate_bound_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_CORPUS_PROVENANCE,
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    DEFAULT_TASKS_ROOT,
    sha256_file,
)
from experiments.skillsbench.create_m3k_pilot_manifest import (
    M3KPilotManifestError,
    validate_pilot_manifest,
)
from experiments.skillsbench.materialize_m3k_external_cell import (
    DEFAULT_LIBRARY_SCALE,
    M3KMaterializationError,
    materialize_m3k_external_cell,
    validate_materialized_m3k_cell,
)
from src.merlin_harness.management import content_sha256


class M3KPilotOperatorBundleError(ValueError):
    """Raised when a six-cell operator bundle cannot be trusted."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KPilotOperatorBundleError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KPilotOperatorBundleError(f"cannot read {label}") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise M3KPilotOperatorBundleError(f"{label} must be a regular JSON object")
    return resolved, value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _cell_directory_name(ordinal: int, trajectory: dict[str, Any]) -> str:
    trajectory_id = trajectory.get("trajectory_id")
    role = trajectory.get("variant_role")
    trial = trajectory.get("trial_index")
    if not isinstance(trajectory_id, str) or role not in {"parent", "candidate"}:
        raise M3KPilotOperatorBundleError("pilot trajectory identity is malformed")
    if not isinstance(trial, int) or trial not in {1, 2, 3}:
        raise M3KPilotOperatorBundleError("pilot trajectory trial is malformed")
    suffix = hashlib.sha256(trajectory_id.encode("utf-8")).hexdigest()[:12]
    return f"{ordinal:02d}-t{trial}-{role}-{suffix}"


def _safe_cell_pointer(root: Path, pointer: Any) -> Path:
    if not isinstance(pointer, str) or not pointer:
        raise M3KPilotOperatorBundleError("operator cell pointer is malformed")
    pure = PurePosixPath(pointer)
    if pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
        raise M3KPilotOperatorBundleError("operator cell pointer escapes the bundle")
    candidate = root / Path(*pure.parts)
    if candidate.is_symlink():
        raise M3KPilotOperatorBundleError("operator cell pointer is a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise M3KPilotOperatorBundleError("operator cell pointer escapes or is missing") from exc
    if not resolved.is_dir():
        raise M3KPilotOperatorBundleError("operator cell pointer must name a directory")
    return resolved


def _source_contracts(
    *,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    bound_path, bound = _load_json(bound_manifest_path, label="bound M3-K manifest")
    pilot_path, pilot = _load_json(pilot_manifest_path, label="M3-K pilot manifest")
    try:
        validate_bound_manifest(bound)
        validate_pilot_manifest(pilot, bound_manifest=bound)
    except (M3KProposalBindingError, M3KPilotManifestError) as exc:
        raise M3KPilotOperatorBundleError(str(exc)) from exc
    source = pilot.get("source_full87")
    if not isinstance(source, dict) or source.get("bound_manifest_file_sha256") != sha256_file(bound_path):
        raise M3KPilotOperatorBundleError("pilot bound-manifest file identity drifted")
    return bound_path, bound, pilot_path, pilot


def prepare_m3k_pilot_operator_bundle(
    *,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
    library_scale_manifest_path: Path,
    output_root: Path,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    """Atomically materialize and seal all six pilot cells in frozen order."""

    bound_path, bound, pilot_path, pilot = _source_contracts(
        bound_manifest_path=bound_manifest_path,
        pilot_manifest_path=pilot_manifest_path,
    )
    library_path = library_scale_manifest_path.expanduser()
    if library_path.is_symlink() or not library_path.is_file():
        raise M3KPilotOperatorBundleError("library-scale manifest must be a regular file")
    library_path = library_path.resolve(strict=True)
    destination = output_root.expanduser()
    if destination.exists() or destination.is_symlink():
        raise M3KPilotOperatorBundleError("operator bundle output must be new-only")
    destination = destination.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.partial-", dir=destination.parent)
    )
    try:
        cells_root = temporary / "cells"
        cells_root.mkdir()
        entries: list[dict[str, Any]] = []
        trajectories = pilot.get("trajectories")
        if not isinstance(trajectories, list) or len(trajectories) != 6:
            raise M3KPilotOperatorBundleError("pilot must contain exactly six trajectories")
        for ordinal, trajectory in enumerate(trajectories, start=1):
            if not isinstance(trajectory, dict):
                raise M3KPilotOperatorBundleError("pilot trajectory is malformed")
            directory_name = _cell_directory_name(ordinal, trajectory)
            cell_root = cells_root / directory_name
            contract = materialize_m3k_external_cell(
                bound_manifest_path=bound_path,
                library_scale_manifest_path=library_path,
                trajectory_id=trajectory["trajectory_id"],
                output_root=cell_root,
                index_path=index_path,
                corpus_provenance_path=corpus_provenance_path,
                tasks_root=tasks_root,
                skills_root=skills_root,
            )
            entries.append(
                {
                    "ordinal": ordinal,
                    "trajectory_id": trajectory["trajectory_id"],
                    "pair_id": trajectory["pair_id"],
                    "variant_role": trajectory["variant_role"],
                    "trial_index": trajectory["trial_index"],
                    "cell_pointer": f"cells/{directory_name}",
                    "execution_contract_sha256": contract["execution_contract_sha256"],
                    "execution_contract_file_sha256": sha256_file(
                        cell_root / "execution-contract.json"
                    ),
                }
            )
        payload = {
            "schema_version": 1,
            "status": "not_run",
            "scope": "six_cell_pilot_operator_bundle",
            "source": {
                "bound_manifest_sha256": bound["manifest_sha256"],
                "bound_manifest_file_sha256": sha256_file(bound_path),
                "pilot_manifest_sha256": pilot["pilot_manifest_sha256"],
                "pilot_manifest_file_sha256": sha256_file(pilot_path),
                "library_scale_manifest_file_sha256": sha256_file(library_path),
            },
            "expected_trajectories": 6,
            "cells": entries,
            "claim_boundary": {
                "bundle_is_model_execution": False,
                "bundle_is_actual_invocation": False,
                "bundle_is_benchmark_result": False,
                "bundle_can_promote_candidate": False,
                "all_cells_require_fresh_pre_execution_revalidation": True,
            },
        }
        payload["operator_bundle_sha256"] = content_sha256(payload)
        _write_json(temporary / "operator-manifest.json", payload)
        _write_json(
            temporary / "progress.template.json",
            {
                "schema_version": 1,
                "operator_bundle_sha256": payload["operator_bundle_sha256"],
                "status": "not_run",
                "completed_trajectory_ids": [],
                "templates_are_results": False,
            },
        )
        validated = validate_m3k_pilot_operator_bundle(
            bundle_root=temporary,
            bound_manifest_path=bound_path,
            pilot_manifest_path=pilot_path,
            library_scale_manifest_path=library_path,
        )
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return validated


def validate_m3k_pilot_operator_bundle(
    *,
    bundle_root: Path,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
    library_scale_manifest_path: Path,
) -> dict[str, Any]:
    """Re-open the operator manifest and all six materialized cell trees."""

    bound_path, bound, pilot_path, pilot = _source_contracts(
        bound_manifest_path=bound_manifest_path,
        pilot_manifest_path=pilot_manifest_path,
    )
    library_path = library_scale_manifest_path.expanduser()
    if library_path.is_symlink() or not library_path.is_file():
        raise M3KPilotOperatorBundleError("library-scale manifest must be a regular file")
    library_path = library_path.resolve(strict=True)
    expanded = bundle_root.expanduser()
    if expanded.is_symlink():
        raise M3KPilotOperatorBundleError("operator bundle root must not be a symlink")
    try:
        root = expanded.resolve(strict=True)
    except OSError as exc:
        raise M3KPilotOperatorBundleError("operator bundle root is missing") from exc
    if not root.is_dir() or {item.name for item in root.iterdir()} != {
        "cells",
        "operator-manifest.json",
        "progress.template.json",
    }:
        raise M3KPilotOperatorBundleError("operator bundle root entries drifted")
    for member in root.rglob("*"):
        if member.is_symlink():
            raise M3KPilotOperatorBundleError("operator bundle contains a symlink")
    _, manifest = _load_json(root / "operator-manifest.json", label="operator manifest")
    stored_hash = manifest.get("operator_bundle_sha256")
    unhashed = dict(manifest)
    unhashed.pop("operator_bundle_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise M3KPilotOperatorBundleError("operator bundle hash mismatch")
    if manifest.get("schema_version") != 1 or manifest.get("status") != "not_run":
        raise M3KPilotOperatorBundleError("operator bundle schema/status is invalid")
    if manifest.get("scope") != "six_cell_pilot_operator_bundle":
        raise M3KPilotOperatorBundleError("operator bundle scope drifted")
    source = manifest.get("source")
    expected_source = {
        "bound_manifest_sha256": bound["manifest_sha256"],
        "bound_manifest_file_sha256": sha256_file(bound_path),
        "pilot_manifest_sha256": pilot["pilot_manifest_sha256"],
        "pilot_manifest_file_sha256": sha256_file(pilot_path),
        "library_scale_manifest_file_sha256": sha256_file(library_path),
    }
    if source != expected_source:
        raise M3KPilotOperatorBundleError("operator bundle source binding drifted")
    boundary = manifest.get("claim_boundary")
    if not isinstance(boundary, dict) or any(
        boundary.get(key) is not False
        for key in (
            "bundle_is_model_execution",
            "bundle_is_actual_invocation",
            "bundle_is_benchmark_result",
            "bundle_can_promote_candidate",
        )
    ):
        raise M3KPilotOperatorBundleError("operator bundle claim boundary is unsafe")
    if boundary.get("all_cells_require_fresh_pre_execution_revalidation") is not True:
        raise M3KPilotOperatorBundleError("operator bundle pre-execution gate is disabled")
    cells = manifest.get("cells")
    trajectories = pilot.get("trajectories")
    if (
        manifest.get("expected_trajectories") != 6
        or not isinstance(cells, list)
        or len(cells) != 6
        or not isinstance(trajectories, list)
        or len(trajectories) != 6
    ):
        raise M3KPilotOperatorBundleError("operator bundle denominator drifted")
    seen_pointers: set[str] = set()
    seen_trajectories: set[str] = set()
    for ordinal, (entry, trajectory) in enumerate(zip(cells, trajectories, strict=True), start=1):
        if not isinstance(entry, dict) or not isinstance(trajectory, dict):
            raise M3KPilotOperatorBundleError("operator cell entry is malformed")
        expected_identity = {
            "ordinal": ordinal,
            "trajectory_id": trajectory["trajectory_id"],
            "pair_id": trajectory["pair_id"],
            "variant_role": trajectory["variant_role"],
            "trial_index": trajectory["trial_index"],
        }
        if any(entry.get(key) != value for key, value in expected_identity.items()):
            raise M3KPilotOperatorBundleError("operator cell order/identity drifted")
        pointer = entry.get("cell_pointer")
        if pointer in seen_pointers or trajectory["trajectory_id"] in seen_trajectories:
            raise M3KPilotOperatorBundleError("operator cell identity is duplicated")
        seen_pointers.add(pointer)
        seen_trajectories.add(trajectory["trajectory_id"])
        cell_root = _safe_cell_pointer(root, pointer)
        contract = validate_materialized_m3k_cell(
            cell_root,
            expected_contract_sha256=entry.get("execution_contract_sha256"),
        )
        if contract.get("trajectory", {}).get("trajectory_id") != trajectory["trajectory_id"]:
            raise M3KPilotOperatorBundleError("operator cell contract trajectory drifted")
        if sha256_file(cell_root / "execution-contract.json") != entry.get(
            "execution_contract_file_sha256"
        ):
            raise M3KPilotOperatorBundleError("operator cell contract file bytes drifted")
    cells_root = root / "cells"
    if {f"cells/{entry.name}" for entry in cells_root.iterdir()} != seen_pointers:
        raise M3KPilotOperatorBundleError("operator cells directory membership drifted")
    _, progress = _load_json(root / "progress.template.json", label="progress template")
    if progress != {
        "schema_version": 1,
        "operator_bundle_sha256": stored_hash,
        "status": "not_run",
        "completed_trajectory_ids": [],
        "templates_are_results": False,
    }:
        raise M3KPilotOperatorBundleError("operator progress template is unsafe or drifted")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "validate"):
        command = subparsers.add_parser(name)
        command.add_argument("--bound-manifest", type=Path, required=True)
        command.add_argument("--pilot-manifest", type=Path, required=True)
        command.add_argument(
            "--library-scale-manifest",
            type=Path,
            default=DEFAULT_LIBRARY_SCALE,
        )
        command.add_argument("--bundle", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--index", type=Path, default=DEFAULT_INDEX)
            command.add_argument(
                "--corpus-provenance",
                type=Path,
                default=DEFAULT_CORPUS_PROVENANCE,
            )
            command.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
            command.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        manifest = prepare_m3k_pilot_operator_bundle(
            bound_manifest_path=args.bound_manifest,
            pilot_manifest_path=args.pilot_manifest,
            library_scale_manifest_path=args.library_scale_manifest,
            output_root=args.bundle,
            index_path=args.index,
            corpus_provenance_path=args.corpus_provenance,
            tasks_root=args.tasks_root,
            skills_root=args.skills_root,
        )
        verb = "saved"
    else:
        manifest = validate_m3k_pilot_operator_bundle(
            bundle_root=args.bundle,
            bound_manifest_path=args.bound_manifest,
            pilot_manifest_path=args.pilot_manifest,
            library_scale_manifest_path=args.library_scale_manifest,
        )
        verb = "verified"
    print("Merlin M3-K six-cell operator bundle")
    print("expected_trajectories=6")
    print("execution_status=not_run")
    print(f"operator_bundle_sha256={manifest['operator_bundle_sha256']}")
    print(f"{verb} -> {args.bundle.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
