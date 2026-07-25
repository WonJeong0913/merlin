"""Reconstruct library-scale progress only from revalidated cell evidence.

This is an immutable snapshot, not a mutable job database. A cell advances
only when its staged bytes, normalized trace, raw-trace hash, invocation
evidence, verifier result, and frozen manifest binding all revalidate through
the ordinary library-scale aggregator.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from experiments.skillsbench.aggregate_library_scale_results import (
    LibraryScaleAggregationError,
    aggregate_library_scale_run,
)
from experiments.skillsbench.create_library_scale_batch_plan import (
    LibraryScaleBatchPlanError,
    validate_library_scale_batch_plan,
)
from experiments.skillsbench.derive_library_scale_trial1_plan import (
    LibraryScaleTrial1PlanError,
    PLAN_KIND as TRIAL1_PLAN_KIND,
    validate_library_scale_trial1_plan,
)
from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    sha256_file,
)
from src.merlin_harness.management import content_sha256
from src.merlin_harness.traces import FileTraceStore


class LibraryScaleProgressError(ValueError):
    """Raised when progress cannot be derived from safe evidence."""


def _validate_execution_plan(
    *,
    plan_path: Path,
    source_plan_path: Path | None,
    manifest_path: Path,
    base_manifest_path: Path | None,
    empirical_oracle_path: Path | None,
    index_path: Path,
    skills_root: Path,
) -> dict[str, Any]:
    try:
        raw = json.loads(plan_path.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryScaleProgressError("batch plan is missing or invalid") from exc
    if not isinstance(raw, dict):
        raise LibraryScaleProgressError("batch plan must be a JSON object")
    try:
        if raw.get("plan_kind") == TRIAL1_PLAN_KIND:
            if source_plan_path is None:
                raise LibraryScaleProgressError(
                    "derived trial-1 progress requires source_plan_path"
                )
            return validate_library_scale_trial1_plan(
                plan_path=plan_path,
                source_plan_path=source_plan_path,
                manifest_path=manifest_path,
                base_manifest_path=base_manifest_path,
                empirical_oracle_path=empirical_oracle_path,
                index_path=index_path,
                skills_root=skills_root,
            )
        return validate_library_scale_batch_plan(
            plan_path=plan_path,
            manifest_path=manifest_path,
            base_manifest_path=base_manifest_path,
            empirical_oracle_path=empirical_oracle_path,
            index_path=index_path,
            skills_root=skills_root,
        )
    except (LibraryScaleBatchPlanError, LibraryScaleTrial1PlanError) as exc:
        raise LibraryScaleProgressError(str(exc)) from exc


def _safe_directory(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise LibraryScaleProgressError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise LibraryScaleProgressError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise LibraryScaleProgressError(f"{label} must be a directory")
    return resolved


def build_library_scale_progress(
    *,
    plan_path: Path,
    source_plan_path: Path | None = None,
    manifest_path: Path,
    cell_root: Path,
    trace_root: Path,
    base_manifest_path: Path | None = None,
    empirical_oracle_path: Path | None = None,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    plan = _validate_execution_plan(
        plan_path=plan_path,
        source_plan_path=source_plan_path,
        manifest_path=manifest_path,
        base_manifest_path=base_manifest_path,
        empirical_oracle_path=empirical_oracle_path,
        index_path=index_path,
        skills_root=skills_root,
    )
    cells = plan.get("cells")
    if not isinstance(cells, list) or not cells:
        raise LibraryScaleProgressError("batch plan has no cells")
    cell_directory = _safe_directory(cell_root, label="cell root")
    trace_directory = _safe_directory(trace_root, label="trace root")
    trace_files: list[Path] = []
    for entry in trace_directory.iterdir():
        if entry.is_symlink() or not entry.is_file() or entry.suffix != ".json":
            raise LibraryScaleProgressError("trace root contains an unsafe entry")
        trace_files.append(entry)
    try:
        aggregate = aggregate_library_scale_run(
            manifest_path=manifest_path,
            cell_root=cell_directory,
            trace_root=trace_directory,
            empirical_oracle_path=empirical_oracle_path,
            base_manifest_path=base_manifest_path,
            index_path=index_path,
            skills_root=skills_root,
        )
        traces = FileTraceStore(trace_directory).list()
    except (LibraryScaleAggregationError, OSError, ValueError) as exc:
        raise LibraryScaleProgressError(str(exc)) from exc

    scheduled = {cell["cell_id"] for cell in cells}
    trace_ids = [trace.id for trace in traces]
    if len(trace_ids) != len(set(trace_ids)) or set(trace_ids) - scheduled:
        raise LibraryScaleProgressError("trace root contains duplicate or unscheduled evidence")
    if {path.stem for path in trace_files} != set(trace_ids):
        raise LibraryScaleProgressError("trace filename does not match its immutable trace ID")
    materialized: set[str] = set()
    for entry in cell_directory.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            raise LibraryScaleProgressError("cell root contains an unsafe entry")
        if entry.name not in scheduled:
            raise LibraryScaleProgressError("cell root contains an unscheduled materialization")
        materialized.add(entry.name)
    missing_materialization = set(trace_ids) - materialized
    if missing_materialization:
        raise LibraryScaleProgressError("validated trace has no materialized cell")
    sealed = set(trace_ids)
    partial = materialized - sealed

    states: list[dict[str, Any]] = []
    for cell in cells:
        cell_id = cell["cell_id"]
        if cell_id in sealed:
            status = "sealed_validated_trace"
            trace_sha256 = sha256_file(trace_directory / f"{cell_id}.json")
        elif cell_id in partial:
            status = "materialized_without_validated_trace"
            trace_sha256 = None
        else:
            status = "pending"
            trace_sha256 = None
        states.append(
            {
                "ordinal": cell["ordinal"],
                "cell_id": cell_id,
                "work_key": cell["work_key"],
                "status": status,
                "trace_file_sha256": trace_sha256,
            }
        )
    unfinished = [item for item in states if item["status"] != "sealed_validated_trace"]
    next_pending = None
    if unfinished:
        first = unfinished[0]
        next_pending = {
            "ordinal": first["ordinal"],
            "cell_id": first["cell_id"],
            "work_key": first["work_key"],
            "materialization_exists": first["status"]
            == "materialized_without_validated_trace",
            "operator_audit_required_before_retry": first["status"]
            == "materialized_without_validated_trace",
        }
    summary = aggregate["summary"]
    if summary.get("observed_cells") != len(sealed):
        raise LibraryScaleProgressError("aggregate observed-cell count drifted")
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "snapshot_kind": "full87-library-scale-evidence-derived-progress-v1",
        "batch_id": plan["batch_id"],
        "source": {
            "plan_sha256": plan["plan_sha256"],
            "plan_file_sha256": sha256_file(plan_path),
            "manifest_file_sha256": plan["dependencies"]["manifest_file_sha256"],
        },
        "status": (
            "all_evidence_sealed"
            if not unfinished
            else (
                "operator_audit_required"
                if partial
                else "awaiting_evidence"
            )
        ),
        "counts": {
            "scheduled_cells": len(cells),
            "sealed_validated_cells": len(sealed),
            "materialized_without_validated_trace": len(partial),
            "pending_unmaterialized_cells": len(cells) - len(sealed) - len(partial),
        },
        "validated_trace_records_sha256": content_sha256(
            [
                {
                    "cell_id": item["cell_id"],
                    "trace_file_sha256": item["trace_file_sha256"],
                }
                for item in states
                if item["status"] == "sealed_validated_trace"
            ]
        ),
        "aggregate_sha256": content_sha256(aggregate),
        "aggregate_boundary": {
            "full_denominator_observed": summary.get("full_denominator_observed"),
            "full_denominator_scored": summary.get("full_denominator_scored"),
            "actual_invocation_evidence_complete": summary.get(
                "actual_invocation_evidence_complete"
            ),
            "shadowing_status": summary.get("shadowing_summary", {}).get("status"),
            "shadowing_reason": summary.get("shadowing_summary", {}).get("reason"),
        },
        "next_pending": next_pending,
        "cells": states,
        "claim_boundary": {
            "snapshot_is_model_execution": False,
            "snapshot_is_benchmark_result": False,
            "partial_evidence_is_full87_completion": False,
            "partial_evidence_is_shadowing_curve": False,
            "provider_resolved_model_identity_claimed": False,
        },
    }
    snapshot["snapshot_sha256"] = content_sha256(snapshot)
    return snapshot


def write_library_scale_progress(*, output_path: Path, **kwargs: Any) -> dict[str, Any]:
    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise LibraryScaleProgressError("progress output must be new-only")
    snapshot = build_library_scale_progress(**kwargs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise LibraryScaleProgressError("progress output must be new-only") from exc
    return snapshot


def validate_library_scale_progress(*, progress_path: Path, **kwargs: Any) -> dict[str, Any]:
    expanded = progress_path.expanduser()
    if expanded.is_symlink():
        raise LibraryScaleProgressError("progress snapshot must not be a symlink")
    try:
        stored = json.loads(expanded.resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryScaleProgressError("progress snapshot is missing or invalid") from exc
    if not isinstance(stored, dict):
        raise LibraryScaleProgressError("progress snapshot must be a JSON object")
    stored_hash = stored.get("snapshot_sha256")
    unhashed = dict(stored)
    unhashed.pop("snapshot_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise LibraryScaleProgressError("progress snapshot hash mismatch")
    expected = build_library_scale_progress(**kwargs)
    if stored != expected:
        raise LibraryScaleProgressError("progress snapshot drifted from validated evidence")
    return stored


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source-plan", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cell-root", type=Path, required=True)
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path)
    parser.add_argument("--empirical-oracle", type=Path)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--progress", type=Path)
    args = parser.parse_args(argv)
    kwargs = {
        "plan_path": args.plan,
        "source_plan_path": args.source_plan,
        "manifest_path": args.manifest,
        "cell_root": args.cell_root,
        "trace_root": args.trace_root,
        "base_manifest_path": args.base_manifest,
        "empirical_oracle_path": args.empirical_oracle,
        "index_path": args.index,
        "skills_root": args.skills_root,
    }
    try:
        snapshot = (
            validate_library_scale_progress(progress_path=args.progress, **kwargs)
            if args.progress is not None
            else write_library_scale_progress(output_path=args.output, **kwargs)
        )
    except LibraryScaleProgressError as exc:
        parser.error(str(exc))
    print("Merlin full-87 library-scale evidence-derived progress")
    print("status=revalidated" if args.progress is not None else "status=created")
    print(
        f"sealed={snapshot['counts']['sealed_validated_cells']}/"
        f"{snapshot['counts']['scheduled_cells']}"
    )
    print(f"pending={snapshot['counts']['pending_unmaterialized_cells']}")
    print(f"partial={snapshot['counts']['materialized_without_validated_trace']}")
    print("model_execution=false")
    print("benchmark_result=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
