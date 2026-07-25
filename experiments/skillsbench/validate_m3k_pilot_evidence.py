"""Validate exactly six sealed M3-K pilot trajectories before scale-up.

This validator never runs a model and never promotes a harness candidate.  It
proves only that one held-in task has all three frozen parent/candidate pairs
recorded under the strict external-executor evidence contract.  Passing may
authorize operator-controlled expansion to the frozen 522-trajectory schedule;
it is not a full-87 result or a policy-quality conclusion.
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


class M3KPilotEvidenceError(ValueError):
    """Raised when the six-cell pilot cannot authorize contract expansion."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KPilotEvidenceError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M3KPilotEvidenceError(f"{label} is missing or invalid") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise M3KPilotEvidenceError(f"{label} must be a regular JSON object")
    return resolved, value


def _role_observation(records: list[dict[str, Any]], role: str) -> dict[str, Any]:
    selected = [record for record in records if record["variant_role"] == role]
    if len(selected) != 3 or {record["trial_index"] for record in selected} != {1, 2, 3}:
        raise M3KPilotEvidenceError(f"pilot {role} denominator must be exactly three trials")
    return {
        "recorded_trials": 3,
        "verifier_passed": sum(record["verifier_passed"] for record in selected),
        "mean_verifier_score": sum(float(record["verifier_score"]) for record in selected) / 3,
        "mean_cost": sum(float(record["cost"]) for record in selected) / 3,
    }


def _build_m3k_pilot_report(
    *,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
    evidence_root: Path,
    allow_additional_trajectories: bool = False,
) -> dict[str, Any]:
    """Reconstruct the exact six-cell admission report from sealed evidence."""

    bound_path, bound = _load_json(bound_manifest_path, label="bound M3-K manifest")
    pilot_path, pilot = _load_json(pilot_manifest_path, label="M3-K pilot manifest")
    try:
        validate_bound_manifest(bound)
        validate_pilot_manifest(pilot, bound_manifest=bound)
    except (M3KProposalBindingError, M3KPilotManifestError) as exc:
        raise M3KPilotEvidenceError(str(exc)) from exc
    if bound.get("execution_gate", {}).get("execution_allowed") is not True:
        raise M3KPilotEvidenceError("pilot evidence requires execution_allowed=true")
    source = pilot.get("source_full87", {})
    if source.get("bound_manifest_file_sha256") != sha256_file(bound_path):
        raise M3KPilotEvidenceError("pilot bound-manifest file hash drifted")

    trajectories = pilot.get("trajectories")
    if not isinstance(trajectories, list) or len(trajectories) != 6:
        raise M3KPilotEvidenceError("pilot must name exactly six trajectories")
    trajectory_ids = [item.get("trajectory_id") for item in trajectories]
    try:
        validated = validate_m3k_external_evidence_subset(
            bound_manifest_path=bound_path,
            evidence_root=evidence_root,
            trajectory_ids=trajectory_ids,
            allow_additional_trajectories=allow_additional_trajectories,
        )
    except M3KExternalEvidenceError as exc:
        raise M3KPilotEvidenceError(str(exc)) from exc
    records = [validated["records"][trajectory_id] for trajectory_id in trajectory_ids]
    if any(record["actual_invocation_evidence_complete"] is not True for record in records):
        raise M3KPilotEvidenceError(
            "all pilot trajectories require complete actual-invocation evidence"
        )
    if len({record["pair_id"] for record in records}) != 3:
        raise M3KPilotEvidenceError("pilot must preserve three parent/candidate pairs")

    report = {
        "schema_version": 1,
        "validation_id": pilot["pilot_id"],
        "status": "passed",
        "source": {
            "bound_manifest_sha256": bound["manifest_sha256"],
            "bound_manifest_file_sha256": sha256_file(bound_path),
            "pilot_manifest_sha256": pilot["pilot_manifest_sha256"],
            "pilot_manifest_file_sha256": sha256_file(pilot_path),
            "evaluation_contract_sha256": bound["evaluation_contract_sha256"],
        },
        "requested_model_contract": requested_model_contract(bound),
        "coverage": {
            "task_id": pilot["task_id"],
            "split": "held_in",
            "expected_trajectories": 6,
            "recorded_trajectories": len(records),
            "unique_raw_provider_traces": validated[
                "unique_raw_provider_trace_count"
            ],
            "unique_runtime_audits": validated["unique_runtime_audit_count"],
            "unique_execution_packs": validated["unique_execution_pack_count"],
            "complete": len(records) == 6,
        },
        "task_local_observation": {
            "parent": _role_observation(records, "parent"),
            "candidate": _role_observation(records, "candidate"),
            "policy_quality_gate_applied": False,
        },
        "scale_gate": {
            "strict_executor_contract_passed": True,
            "contract_expansion_to_522_allowed": True,
            "promotion_decision_allowed": False,
        },
        "claim_boundary": {
            "validation_is_model_execution": False,
            "pilot_is_full87_result": False,
            "pilot_is_library_scale_shadowing_result": False,
            "provider_resolved_model_identity_claimed": False,
            "provider_native_skill_invocation_claimed": False,
            "candidate_promotion_claimed": False,
            "task_local_scores_are_policy_quality_conclusion": False,
        },
    }
    report["report_sha256"] = content_sha256(report)
    return report


def validate_m3k_pilot_report(
    *,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
    evidence_root: Path,
    report_path: Path,
    allow_additional_trajectories: bool = False,
) -> dict[str, Any]:
    """Reopen one report and require exact reconstruction from current evidence.

    A caller cannot make a semantic mutation acceptable merely by recomputing
    ``report_sha256``: the stored object must equal the deterministic report
    rebuilt from the bound manifest, pilot manifest, and all six raw/audit
    evidence chains.
    """

    _resolved, stored = _load_json(report_path, label="M3-K pilot report")
    stored_hash = stored.get("report_sha256")
    unhashed = dict(stored)
    unhashed.pop("report_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise M3KPilotEvidenceError("pilot report hash mismatch")
    expected = _build_m3k_pilot_report(
        bound_manifest_path=bound_manifest_path,
        pilot_manifest_path=pilot_manifest_path,
        evidence_root=evidence_root,
        allow_additional_trajectories=allow_additional_trajectories,
    )
    if stored != expected:
        raise M3KPilotEvidenceError("pilot report drifted from sealed evidence")
    return stored


def validate_m3k_pilot_evidence(
    *,
    bound_manifest_path: Path,
    pilot_manifest_path: Path,
    evidence_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Write one new-only, hash-bound six-cell executor admission report."""

    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise M3KPilotEvidenceError("pilot report output must be new-only")
    report = _build_m3k_pilot_report(
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
        raise M3KPilotEvidenceError("pilot report output must be new-only") from exc
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
            report = validate_m3k_pilot_report(
                bound_manifest_path=args.bound_manifest,
                pilot_manifest_path=args.pilot_manifest,
                evidence_root=args.evidence_root,
                report_path=args.report,
            )
        else:
            report = validate_m3k_pilot_evidence(
                bound_manifest_path=args.bound_manifest,
                pilot_manifest_path=args.pilot_manifest,
                evidence_root=args.evidence_root,
                output_path=args.output,
            )
    except M3KPilotEvidenceError as exc:
        parser.error(str(exc))
    print("Merlin M3-K six-cell evidence gate")
    print("status=revalidated" if args.report is not None else "status=passed")
    print("coverage=6/6")
    print("contract_expansion_to_522_allowed=true")
    print("promotion_decision_allowed=false")
    print(f"report_sha256={report['report_sha256']}")
    target = args.report if args.report is not None else args.output
    print(f"{'validated' if args.report is not None else 'saved'} -> {target.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
