"""Validate the first sealed M3-K pilot cell before opening cells two to six.

This is an execution-order gate, not a performance gate.  It proves that the
exact ordinal-1 trajectory in the frozen pilot has complete, replayable
external evidence.  It does not claim that the six-cell pilot, full-87 run, or
harness-promotion comparison is complete.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    validate_bound_manifest,
)
from experiments.skillsbench.create_m3k_pilot_manifest import (
    M3KPilotManifestError,
    validate_pilot_manifest,
)
from experiments.skillsbench.m3k_external_evidence import (
    M3KExternalEvidenceError,
    requested_model_contract,
    sha256_file,
    validate_m3k_external_evidence_subset,
)
from src.merlin_harness.management import content_sha256


class M3KFirstCellEvidenceError(ValueError):
    """Raised when ordinal-1 evidence cannot open the rest of the pilot."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KFirstCellEvidenceError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M3KFirstCellEvidenceError(f"{label} is missing or invalid") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise M3KFirstCellEvidenceError(f"{label} must be a regular JSON object")
    return resolved, value


def _build_first_cell_report(
    *,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
    evidence_root: Path,
) -> dict[str, Any]:
    bound_path, bound = _load_json(bound_manifest_path, label="bound M3-K manifest")
    pilot_path, pilot = _load_json(pilot_manifest_path, label="M3-K pilot manifest")
    try:
        validate_bound_manifest(bound)
        validate_pilot_manifest(pilot, bound_manifest=bound)
    except (M3KProposalBindingError, M3KPilotManifestError) as exc:
        raise M3KFirstCellEvidenceError(str(exc)) from exc
    if bound.get("execution_gate", {}).get("execution_allowed") is not True:
        raise M3KFirstCellEvidenceError("first-cell evidence requires execution_allowed=true")
    source = pilot.get("source_full87")
    if not isinstance(source, dict) or source.get(
        "bound_manifest_file_sha256"
    ) != sha256_file(bound_path):
        raise M3KFirstCellEvidenceError("pilot bound-manifest file hash drifted")
    trajectories = pilot.get("trajectories")
    if not isinstance(trajectories, list) or len(trajectories) != 6:
        raise M3KFirstCellEvidenceError("pilot must name exactly six trajectories")
    first = trajectories[0]
    if not isinstance(first, dict):
        raise M3KFirstCellEvidenceError("pilot ordinal-1 trajectory is missing")
    trajectory_id = first.get("trajectory_id")
    if not isinstance(trajectory_id, str) or not trajectory_id:
        raise M3KFirstCellEvidenceError("pilot ordinal-1 trajectory ID is invalid")
    try:
        validated = validate_m3k_external_evidence_subset(
            bound_manifest_path=bound_path,
            evidence_root=evidence_root,
            trajectory_ids=[trajectory_id],
            allow_additional_trajectories=True,
        )
    except M3KExternalEvidenceError as exc:
        raise M3KFirstCellEvidenceError(str(exc)) from exc
    record = validated["records"][trajectory_id]
    if record.get("actual_invocation_evidence_complete") is not True:
        raise M3KFirstCellEvidenceError(
            "ordinal-1 requires complete actual-invocation evidence"
        )
    report = {
        "schema_version": 1,
        "validation_id": f"{pilot['pilot_id']}:first-cell-admission",
        "status": "passed",
        "source": {
            "bound_manifest_sha256": bound["manifest_sha256"],
            "bound_manifest_file_sha256": sha256_file(bound_path),
            "pilot_manifest_sha256": pilot["pilot_manifest_sha256"],
            "pilot_manifest_file_sha256": sha256_file(pilot_path),
            "evaluation_contract_sha256": bound["evaluation_contract_sha256"],
        },
        "requested_model_contract": requested_model_contract(bound),
        "first_cell": {
            "ordinal": 1,
            "trajectory_id": trajectory_id,
            "variant_role": record["variant_role"],
            "task_id": record["task_id"],
            "actual_invocation_evidence_complete": True,
            "raw_provider_trace_sha256": record["raw_provider_trace_sha256"],
            "runtime_audit_sha256": record["runtime_audit_sha256"],
            "execution_pack_sha256": record["execution_pack_sha256"],
        },
        "execution_order_gate": {
            "ordinal_1_replayed": True,
            "ordinals_2_through_6_allowed": True,
            "six_cell_completion": False,
            "contract_expansion_to_522_allowed": False,
            "promotion_decision_allowed": False,
        },
        "claim_boundary": {
            "validation_is_model_execution": False,
            "first_cell_is_six_cell_result": False,
            "first_cell_is_full87_result": False,
            "first_cell_is_library_scale_shadowing_result": False,
            "provider_resolved_model_identity_claimed": False,
            "provider_native_skill_invocation_claimed": False,
            "candidate_promotion_claimed": False,
        },
    }
    report["report_sha256"] = content_sha256(report)
    return report


def validate_m3k_first_cell_report(
    *,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
    evidence_root: Path,
    report_path: Path,
) -> dict[str, Any]:
    _resolved, stored = _load_json(report_path, label="M3-K first-cell report")
    stored_hash = stored.get("report_sha256")
    unhashed = dict(stored)
    unhashed.pop("report_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise M3KFirstCellEvidenceError("first-cell report hash mismatch")
    expected = _build_first_cell_report(
        bound_manifest_path=bound_manifest_path,
        pilot_manifest_path=pilot_manifest_path,
        evidence_root=evidence_root,
    )
    if stored != expected:
        raise M3KFirstCellEvidenceError("first-cell report drifted from sealed evidence")
    return stored


def validate_m3k_first_cell_evidence(
    *,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
    evidence_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise M3KFirstCellEvidenceError("first-cell report output must be new-only")
    report = _build_first_cell_report(
        bound_manifest_path=bound_manifest_path,
        pilot_manifest_path=pilot_manifest_path,
        evidence_root=evidence_root,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise M3KFirstCellEvidenceError(
            "first-cell report output must be new-only"
        ) from exc
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bound-manifest", type=Path, required=True)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.report is not None:
            report = validate_m3k_first_cell_report(
                bound_manifest_path=args.bound_manifest,
                pilot_manifest_path=args.pilot_manifest,
                evidence_root=args.evidence_root,
                report_path=args.report,
            )
        else:
            report = validate_m3k_first_cell_evidence(
                bound_manifest_path=args.bound_manifest,
                pilot_manifest_path=args.pilot_manifest,
                evidence_root=args.evidence_root,
                output_path=args.output,
            )
    except M3KFirstCellEvidenceError as exc:
        parser.error(str(exc))
    print("Merlin M3-K first-cell evidence gate")
    print("status=revalidated" if args.report is not None else "status=passed")
    print("ordinal_1_replayed=true")
    print("ordinals_2_through_6_allowed=true")
    print("six_cell_completion=false")
    print(f"report_sha256={report['report_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
