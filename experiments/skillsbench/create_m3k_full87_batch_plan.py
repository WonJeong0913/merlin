"""Create a frozen post-pilot plan for all 522 M3-K full-87 trajectories.

The plan is schedule and evidence control, not model execution.  It reopens the
six-cell admission against the same bound manifest and evidence root, marks
those exact trajectories as already sealed, and orders only the remaining 516
for later one-cell-at-a-time execution under DESKTOP host admission.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    validate_bound_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import (
    sha256_file,
    sha256_json,
)
from experiments.skillsbench.create_m3k_pilot_manifest import (
    M3KPilotManifestError,
    validate_pilot_manifest,
)
from experiments.skillsbench.m3k_external_evidence import (
    M3KExternalEvidenceError,
    validate_m3k_external_evidence_subset,
)
from experiments.skillsbench.validate_m3k_pilot_evidence import (
    M3KPilotEvidenceError,
    validate_m3k_pilot_report,
)
from src.merlin_harness.management import content_sha256


class M3KBatchPlanError(ValueError):
    """Raised when a post-pilot full-87 plan cannot be frozen or reopened."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KBatchPlanError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M3KBatchPlanError(f"{label} is missing or invalid") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise M3KBatchPlanError(f"{label} must be a regular JSON object")
    return resolved, value


def _pilot_evidence_summary(
    *,
    bound_manifest_path: Path,
    pilot: dict[str, Any],
    evidence_root: Path,
) -> tuple[list[str], str]:
    trajectories = pilot.get("trajectories")
    if not isinstance(trajectories, list) or len(trajectories) != 6:
        raise M3KBatchPlanError("pilot must contain exactly six trajectories")
    trajectory_ids = [item.get("trajectory_id") for item in trajectories]
    if any(not isinstance(item, str) or not item for item in trajectory_ids):
        raise M3KBatchPlanError("pilot trajectory IDs are invalid")
    try:
        validated = validate_m3k_external_evidence_subset(
            bound_manifest_path=bound_manifest_path,
            evidence_root=evidence_root,
            trajectory_ids=trajectory_ids,
            allow_additional_trajectories=True,
        )
    except M3KExternalEvidenceError as exc:
        raise M3KBatchPlanError(str(exc)) from exc
    if validated.get("unique_execution_pack_count") != 6:
        raise M3KBatchPlanError("pilot must contain six unique execution packs")
    records = [validated["records"][trajectory_id] for trajectory_id in trajectory_ids]
    return trajectory_ids, content_sha256(records)


def build_m3k_full87_batch_plan(
    *,
    bound_manifest_path: Path,
    library_scale_manifest_path: Path,
    pilot_manifest_path: Path,
    pilot_report_path: Path,
    evidence_root: Path,
) -> dict[str, Any]:
    """Reconstruct the exact 522-cell post-pilot execution plan."""

    bound_path, bound = _load_json(bound_manifest_path, label="bound M3-K manifest")
    library_path, library = _load_json(
        library_scale_manifest_path, label="library-scale manifest"
    )
    pilot_path, pilot = _load_json(pilot_manifest_path, label="M3-K pilot manifest")
    report_path, _stored_report = _load_json(
        pilot_report_path, label="M3-K pilot report"
    )
    try:
        validate_bound_manifest(bound)
        validate_pilot_manifest(pilot, bound_manifest=bound)
        report = validate_m3k_pilot_report(
            bound_manifest_path=bound_path,
            pilot_manifest_path=pilot_path,
            evidence_root=evidence_root,
            report_path=report_path,
            allow_additional_trajectories=True,
        )
    except (M3KProposalBindingError, M3KPilotManifestError, M3KPilotEvidenceError) as exc:
        raise M3KBatchPlanError(str(exc)) from exc
    if bound.get("execution_gate", {}).get("execution_allowed") is not True:
        raise M3KBatchPlanError("batch planning requires execution_allowed=true")
    gate = report.get("scale_gate")
    if (
        not isinstance(gate, dict)
        or gate.get("strict_executor_contract_passed") is not True
        or gate.get("contract_expansion_to_522_allowed") is not True
        or gate.get("promotion_decision_allowed") is not False
    ):
        raise M3KBatchPlanError("six-cell report does not authorize contract expansion")

    library_binding = bound.get("library_binding")
    if not isinstance(library_binding, dict):
        raise M3KBatchPlanError("bound manifest library binding is missing")
    if sha256_file(library_path) != library_binding.get("source_manifest_file_sha256"):
        raise M3KBatchPlanError("library-scale file hash drifted")
    if sha256_json(library) != library_binding.get("source_manifest_semantic_sha256"):
        raise M3KBatchPlanError("library-scale semantic hash drifted")

    pilot_ids, pilot_evidence_sha256 = _pilot_evidence_summary(
        bound_manifest_path=bound_path,
        pilot=pilot,
        evidence_root=evidence_root,
    )
    pilot_set = set(pilot_ids)
    scheduled = bound.get("paired_cells")
    if not isinstance(scheduled, list) or len(scheduled) != 522:
        raise M3KBatchPlanError("bound manifest must schedule exactly 522 trajectories")
    trajectory_ids = [item.get("trajectory_id") for item in scheduled if isinstance(item, dict)]
    if len(trajectory_ids) != 522 or len(set(trajectory_ids)) != 522:
        raise M3KBatchPlanError("bound trajectory IDs must be complete and unique")
    if not pilot_set.issubset(trajectory_ids):
        raise M3KBatchPlanError("pilot trajectories are not a subset of the full schedule")

    dependencies = {
        "bound_manifest_sha256": bound["manifest_sha256"],
        "bound_manifest_file_sha256": sha256_file(bound_path),
        "library_scale_manifest_sha256": sha256_json(library),
        "library_scale_manifest_file_sha256": sha256_file(library_path),
        "pilot_manifest_sha256": pilot["pilot_manifest_sha256"],
        "pilot_manifest_file_sha256": sha256_file(pilot_path),
        "pilot_report_sha256": report["report_sha256"],
        "pilot_report_file_sha256": sha256_file(report_path),
        "pilot_evidence_records_sha256": pilot_evidence_sha256,
    }
    cells: list[dict[str, Any]] = []
    for ordinal, cell in enumerate(scheduled, start=1):
        if not isinstance(cell, dict):
            raise M3KBatchPlanError("bound schedule contains a non-object cell")
        trajectory_id = cell["trajectory_id"]
        cells.append(
            {
                "ordinal": ordinal,
                "trajectory_id": trajectory_id,
                "pair_id": cell["pair_id"],
                "cell_id": cell["cell_id"],
                "variant_role": cell["variant_role"],
                "task_id": cell["task_id"],
                "split": cell["split"],
                "trial_index": cell["trial_index"],
                "verifier_id": cell["verifier_id"],
                "task_instruction_sha256": cell["task_instruction_sha256"],
                "work_key": content_sha256({"trajectory_id": trajectory_id}),
                "initial_status": (
                    "sealed_pilot_evidence" if trajectory_id in pilot_set else "pending"
                ),
            }
        )
    plan = {
        "schema_version": 1,
        "plan_kind": "m3k-full87-post-pilot-522",
        "batch_id": f"m3k-full87-{content_sha256(dependencies)[:20]}",
        "dependencies": dependencies,
        "counts": {
            "scheduled_trajectories": 522,
            "sealed_pilot_trajectories": 6,
            "pending_trajectories": 516,
            "task_count": 87,
            "trials_per_variant": 3,
            "variant_roles": ["parent", "candidate"],
        },
        "cells": cells,
        "execution_policy": {
            "order": "bound_manifest_paired_cells_order",
            "concurrency": 1,
            "materialization": "new-only-per-trajectory",
            "raw_root": "new-only-per-trajectory",
            "evidence_root": "same-root-as-six-cell-pilot",
            "desktop_host_admission_required": True,
            "execution_pack_required": True,
            "automatic_cleanup": False,
            "resume_from_unverified_files": False,
        },
        "claim_boundary": {
            "plan_is_model_execution": False,
            "plan_is_benchmark_result": False,
            "pilot_is_policy_promotion": False,
            "full87_completion_claimed": False,
            "library_scale_shadowing_claimed": False,
        },
    }
    plan["plan_sha256"] = content_sha256(plan)
    return plan


def write_m3k_full87_batch_plan(*, output_path: Path, **kwargs: Any) -> dict[str, Any]:
    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise M3KBatchPlanError("batch plan output must be new-only")
    plan = build_m3k_full87_batch_plan(**kwargs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise M3KBatchPlanError("batch plan output must be new-only") from exc
    return plan


def validate_m3k_full87_batch_plan(*, plan_path: Path, **kwargs: Any) -> dict[str, Any]:
    _path, stored = _load_json(plan_path, label="M3-K full87 batch plan")
    stored_hash = stored.get("plan_sha256")
    unhashed = dict(stored)
    unhashed.pop("plan_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise M3KBatchPlanError("batch plan hash mismatch")
    expected = build_m3k_full87_batch_plan(**kwargs)
    if stored != expected:
        raise M3KBatchPlanError("batch plan drifted from frozen dependencies")
    return stored


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bound-manifest", type=Path, required=True)
    parser.add_argument("--library-scale-manifest", type=Path, required=True)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--pilot-report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--plan", type=Path)
    args = parser.parse_args(argv)
    kwargs = {
        "bound_manifest_path": args.bound_manifest,
        "library_scale_manifest_path": args.library_scale_manifest,
        "pilot_manifest_path": args.pilot_manifest,
        "pilot_report_path": args.pilot_report,
        "evidence_root": args.evidence_root,
    }
    try:
        if args.plan is not None:
            plan = validate_m3k_full87_batch_plan(plan_path=args.plan, **kwargs)
        else:
            plan = write_m3k_full87_batch_plan(output_path=args.output, **kwargs)
    except M3KBatchPlanError as exc:
        parser.error(str(exc))
    print("Merlin M3-K full87 batch plan")
    print("status=revalidated" if args.plan is not None else "status=created")
    print(f"batch_id={plan['batch_id']}")
    print("scheduled=522")
    print("sealed_pilot=6")
    print("pending=516")
    print("model_execution=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
