"""Derive immutable M3-K full-87 progress snapshots from sealed evidence.

The snapshot is not a mutable job database.  Every status is reconstructed
from the frozen 522-cell plan and fully revalidated trajectory evidence, so a
stale checkpoint cannot skip work or turn partial files into completion.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from experiments.skillsbench.create_library_scale_manifest import sha256_file
from experiments.skillsbench.create_m3k_full87_batch_plan import (
    M3KBatchPlanError,
    validate_m3k_full87_batch_plan,
)
from experiments.skillsbench.m3k_external_evidence import (
    M3KExternalEvidenceError,
    record_pointer_for_trajectory,
    validate_m3k_external_evidence_subset,
)
from src.merlin_harness.management import content_sha256


class M3KFull87ProgressError(ValueError):
    """Raised when progress cannot be reconstructed from sealed evidence."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KFull87ProgressError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M3KFull87ProgressError(f"{label} is missing or invalid") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise M3KFull87ProgressError(f"{label} must be a regular JSON object")
    return resolved, value


def _recorded_trajectory_ids(
    *, evidence_root: Path, cells: list[dict[str, Any]]
) -> list[str]:
    expanded = evidence_root.expanduser()
    if expanded.is_symlink():
        raise M3KFull87ProgressError("evidence_root must not be a symlink")
    try:
        root = expanded.resolve(strict=True)
    except OSError as exc:
        raise M3KFull87ProgressError("evidence_root is missing") from exc
    if not root.is_dir():
        raise M3KFull87ProgressError("evidence_root must be a directory")
    bucket = root / "trajectories"
    if bucket.is_symlink() or not bucket.is_dir():
        raise M3KFull87ProgressError("trajectory record bucket is missing or unsafe")

    pointer_to_id = {
        record_pointer_for_trajectory(cell["trajectory_id"]): cell["trajectory_id"]
        for cell in cells
    }
    actual: set[str] = set()
    for entry in bucket.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise M3KFull87ProgressError("trajectory record bucket contains an unsafe entry")
        pointer = f"trajectories/{entry.name}"
        if pointer not in pointer_to_id:
            raise M3KFull87ProgressError("evidence contains an unscheduled trajectory record")
        actual.add(pointer)
    return [
        cell["trajectory_id"]
        for cell in cells
        if record_pointer_for_trajectory(cell["trajectory_id"]) in actual
    ]


def build_m3k_full87_progress(
    *,
    plan_path: Path,
    bound_manifest_path: Path,
    library_scale_manifest_path: Path,
    pilot_manifest_path: Path,
    pilot_report_path: Path,
    evidence_root: Path,
) -> dict[str, Any]:
    """Reconstruct the exact current completion frontier from evidence bytes."""

    try:
        plan = validate_m3k_full87_batch_plan(
            plan_path=plan_path,
            bound_manifest_path=bound_manifest_path,
            library_scale_manifest_path=library_scale_manifest_path,
            pilot_manifest_path=pilot_manifest_path,
            pilot_report_path=pilot_report_path,
            evidence_root=evidence_root,
        )
    except M3KBatchPlanError as exc:
        raise M3KFull87ProgressError(str(exc)) from exc
    cells = plan.get("cells")
    if not isinstance(cells, list) or len(cells) != 522:
        raise M3KFull87ProgressError("batch plan must contain exactly 522 cells")
    recorded_ids = _recorded_trajectory_ids(evidence_root=evidence_root, cells=cells)
    pilot_ids = [
        cell["trajectory_id"]
        for cell in cells
        if cell.get("initial_status") == "sealed_pilot_evidence"
    ]
    if len(pilot_ids) != 6 or not set(pilot_ids).issubset(recorded_ids):
        raise M3KFull87ProgressError("the six sealed pilot records are incomplete")
    try:
        validated = validate_m3k_external_evidence_subset(
            bound_manifest_path=bound_manifest_path,
            evidence_root=evidence_root,
            trajectory_ids=recorded_ids,
        )
    except M3KExternalEvidenceError as exc:
        raise M3KFull87ProgressError(str(exc)) from exc

    records = validated["records"]
    sealed = set(recorded_ids)
    cell_states: list[dict[str, Any]] = []
    for cell in cells:
        trajectory_id = cell["trajectory_id"]
        if trajectory_id not in sealed:
            status = "pending"
            record_sha256 = None
            execution_pack_sha256 = None
        else:
            status = (
                "sealed_pilot_evidence"
                if cell["initial_status"] == "sealed_pilot_evidence"
                else "sealed_execution_evidence"
            )
            record = records[trajectory_id]
            record_sha256 = content_sha256(record)
            execution_pack_sha256 = record["execution_pack_sha256"]
        cell_states.append(
            {
                "ordinal": cell["ordinal"],
                "trajectory_id": trajectory_id,
                "work_key": cell["work_key"],
                "status": status,
                "record_sha256": record_sha256,
                "execution_pack_sha256": execution_pack_sha256,
            }
        )
    pending = [item for item in cell_states if item["status"] == "pending"]
    next_pending = None
    if pending:
        next_pending = {
            "ordinal": pending[0]["ordinal"],
            "trajectory_id": pending[0]["trajectory_id"],
            "work_key": pending[0]["work_key"],
        }
    snapshot = {
        "schema_version": 1,
        "snapshot_kind": "m3k-full87-evidence-derived-progress",
        "batch_id": plan["batch_id"],
        "source": {
            "plan_sha256": plan["plan_sha256"],
            "plan_file_sha256": sha256_file(plan_path),
            "bound_manifest_sha256": plan["dependencies"]["bound_manifest_sha256"],
        },
        "status": "all_evidence_sealed" if not pending else "awaiting_evidence",
        "counts": {
            "scheduled_trajectories": 522,
            "sealed_trajectories": len(recorded_ids),
            "sealed_pilot_trajectories": 6,
            "sealed_expansion_trajectories": len(recorded_ids) - 6,
            "pending_trajectories": len(pending),
            "unique_raw_provider_traces": validated["unique_raw_provider_trace_count"],
            "unique_runtime_audits": validated["unique_runtime_audit_count"],
            "unique_execution_packs": validated["unique_execution_pack_count"],
        },
        "evidence_records_sha256": content_sha256(
            [records[trajectory_id] for trajectory_id in recorded_ids]
        ),
        "next_pending": next_pending,
        "cells": cell_states,
        "claim_boundary": {
            "snapshot_is_model_execution": False,
            "snapshot_is_benchmark_result": False,
            "all_evidence_is_policy_promotion": False,
            "provider_resolved_model_identity_claimed": False,
            "full87_completion_claimed": False,
        },
    }
    snapshot["snapshot_sha256"] = content_sha256(snapshot)
    return snapshot


def write_m3k_full87_progress(*, output_path: Path, **kwargs: Any) -> dict[str, Any]:
    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise M3KFull87ProgressError("progress output must be new-only")
    snapshot = build_m3k_full87_progress(**kwargs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise M3KFull87ProgressError("progress output must be new-only") from exc
    return snapshot


def validate_m3k_full87_progress(
    *, progress_path: Path, **kwargs: Any
) -> dict[str, Any]:
    _path, stored = _load_json(progress_path, label="M3-K full87 progress snapshot")
    stored_hash = stored.get("snapshot_sha256")
    unhashed = dict(stored)
    unhashed.pop("snapshot_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise M3KFull87ProgressError("progress snapshot hash mismatch")
    expected = build_m3k_full87_progress(**kwargs)
    if stored != expected:
        raise M3KFull87ProgressError("progress snapshot drifted from sealed evidence")
    return stored


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--bound-manifest", type=Path, required=True)
    parser.add_argument("--library-scale-manifest", type=Path, required=True)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--pilot-report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--progress", type=Path)
    args = parser.parse_args(argv)
    kwargs = {
        "plan_path": args.plan,
        "bound_manifest_path": args.bound_manifest,
        "library_scale_manifest_path": args.library_scale_manifest,
        "pilot_manifest_path": args.pilot_manifest,
        "pilot_report_path": args.pilot_report,
        "evidence_root": args.evidence_root,
    }
    try:
        if args.progress is not None:
            snapshot = validate_m3k_full87_progress(
                progress_path=args.progress, **kwargs
            )
        else:
            snapshot = write_m3k_full87_progress(
                output_path=args.output, **kwargs
            )
    except M3KFull87ProgressError as exc:
        parser.error(str(exc))
    print("Merlin M3-K full87 evidence-derived progress")
    print("status=revalidated" if args.progress is not None else "status=created")
    print(f"sealed={snapshot['counts']['sealed_trajectories']}/522")
    print(f"pending={snapshot['counts']['pending_trajectories']}")
    print("model_execution=false")
    print("benchmark_result=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
