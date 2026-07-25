"""Complete the held-out phase for an immutable prior model campaign.

This command does not call a model.  It revalidates the raw trace hash,
quarantine manifest, candidate bytes, target-phase report, frozen contract, and
current live-library snapshot from a prior campaign.  It then executes only the
previously untouched held-out phase and resolves promotion versus COW rollback.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from experiments.mvp.run_live_model_skill_hidden_rollback import (
    CANDIDATE_ID,
    REPO_ROOT,
    _artifact,
    _canonical_json,
    _proposal,
    _verifier_profile,
    campaign_contract,
    frozen_cases,
    resolve_lifecycle_outcome,
)
from src.merlin_harness.governed_provisioning import GovernedProvisioner, active_library_snapshot
from src.merlin_harness.isolated_candidate_runner import (
    CandidateExecutionCase,
    IsolatedCandidateRunnerError,
    run_quarantined_candidate_phase,
)
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.models import LifecycleStatus, ValidationResult
from src.merlin_harness.verifier_trust import assess_verifier_trust


MVP_ROOT = REPO_ROOT / "experiments" / "mvp"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"evidence must be a JSON object: {path.name}")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_candidate_manifest(quarantine_root: Path, manifest: dict[str, Any]) -> None:
    candidate_root = quarantine_root / "candidate" / CANDIDATE_ID
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ValueError("quarantine manifest has no bounded file records")
    observed: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("quarantine file record is malformed")
        relative = record.get("path")
        expected_hash = record.get("sha256")
        expected_bytes = record.get("bytes")
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise ValueError("quarantine file record fields are malformed")
        path = candidate_root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"quarantined candidate file missing or linked: {relative}")
        data = path.read_bytes()
        if len(data) != expected_bytes or hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError(f"quarantined candidate bytes drifted: {relative}")
        observed.add(relative)
    actual = {
        path.relative_to(candidate_root).as_posix()
        for path in candidate_root.rglob("*")
        if path.is_file()
    }
    if observed != actual:
        raise ValueError("quarantined candidate file set drifted")


def audit_completion(*, evidence_root: Path, prior_evidence_root: Path) -> dict[str, Any]:
    """Audit the safe, packageable rollback chain without private raw files."""

    evidence_path = evidence_root / "model_authored_hidden_completion_evidence.json"
    prior_path = prior_evidence_root / "model_authored_hidden_rollback_evidence.json"
    evidence = _read_json(evidence_path)
    prior = _read_json(prior_path)
    boundary = evidence.get("evidence_boundary", {})
    observations = evidence.get("failure_observations", {})
    phases = evidence.get("isolated_execution", {})
    target = phases.get("target_phase", {}) if isinstance(phases, dict) else {}
    hidden = phases.get("held_out_phase", {}) if isinstance(phases, dict) else {}
    gates = evidence.get("gates", [])
    by_name = {
        gate.get("name"): gate
        for gate in gates
        if isinstance(gate, dict) and isinstance(gate.get("name"), str)
    }
    checks = {
        "schema_and_contract": (
            evidence.get("schema_version") == 1
            and evidence.get("campaign_contract") == campaign_contract()
            and evidence.get("campaign_contract_sha256")
            == hashlib.sha256(
                _canonical_json(campaign_contract()).encode("utf-8")
            ).hexdigest()
        ),
        "prior_evidence_binding": (
            evidence.get("prior_evidence", {}).get("sha256") == _sha256_file(prior_path)
            and evidence.get("prior_evidence", {}).get("retained_outcome")
            == prior.get("lifecycle_action")
            == "reject"
        ),
        "same_model_candidate_binding": (
            evidence.get("generator") == prior.get("generator")
            and evidence.get("quarantine_binding", {}).get("manifest_sha256")
            == prior.get("quarantine", {}).get("manifest_sha256")
            and evidence.get("quarantine_binding", {}).get("raw_trace_sha256_reverified")
            is True
            and evidence.get("quarantine_binding", {}).get("target_report_reverified")
            is True
        ),
        "target_phase_exact": (
            target == prior.get("isolated_execution", {}).get("target_phase")
            and target.get("all_passed") is True
            and len(target.get("cases", [])) == 2
        ),
        "held_out_phase_exact": (
            hidden.get("all_passed") is True
            and len(hidden.get("cases", [])) == 1
            and hidden.get("phase") == "held_out"
        ),
        "observed_route_shadowing": (
            observations.get("hidden_case_passed") is True
            and observations.get("negative_route_passed") is False
            and observations.get("shadowed_negative_case_ids") == ["negative-line-count"]
            and by_name.get("G3_trigger", {}).get("passed") is False
            and by_name.get("G5_hidden_regression", {}).get("passed") is False
        ),
        "cow_rollback_resolution": (
            evidence.get("lifecycle_action") == "rollback"
            and evidence.get("adopted") is False
            and evidence.get("original_library_snapshot_sha256")
            == evidence.get("resolved_library_snapshot_sha256")
            and CANDIDATE_ID not in evidence.get("resolved_library_statuses", {})
            and by_name.get("G6_cow_resolution", {}).get("passed") is True
        ),
        "claim_boundary": (
            boundary.get("actual_codex_provider_run") is True
            and boundary.get("new_provider_run_during_completion") is False
            and boundary.get("model_authored_candidate") is True
            and boundary.get("isolated_held_out_execution") is True
            and boundary.get("target_verifier_passed") is True
            and boundary.get("hidden_held_out_verifier_passed") is True
            and boundary.get("negative_routing_verifier_passed") is False
            and boundary.get("copy_on_write_promoted") is False
            and boundary.get("copy_on_write_rolled_back") is True
            and boundary.get("live_library_mutated") is False
            and boundary.get("provider_native_skill_invocation") is False
            and boundary.get("full_benchmark_claim") is False
        ),
        "safe_path_boundary": (
            "/private/tmp/" not in evidence_path.read_text(encoding="utf-8")
            and str(REPO_ROOT) not in evidence_path.read_text(encoding="utf-8")
        ),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "model-authored-hidden-completion-audit-v1",
        "passed": all(checks.values()),
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "evidence_sha256": _sha256_file(evidence_path),
        "prior_evidence_sha256": _sha256_file(prior_path),
        "claim_boundary": {
            "private_raw_trace_required_for_provider_reverification": True,
            "safe_chain_tamper_detection": True,
            "provider_resolved_model_identity_claim": False,
            "provider_native_skill_invocation_claim": False,
            "benchmark_generalization_claim": False,
        },
    }
    if not report["passed"]:
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise ValueError(f"safe completion audit failed: {failed}")
    return report


def complete_campaign(
    *, raw_root: Path, prior_evidence_root: Path, output_root: Path
) -> dict[str, Any]:
    raw_root = raw_root.expanduser().resolve()
    prior_evidence_root = prior_evidence_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve(strict=False)
    if raw_root.is_relative_to(REPO_ROOT):
        raise ValueError("raw provider/sandbox root must stay outside the repository")
    if output_root.exists():
        raise ValueError("completion output root must be new")
    if (raw_root / "execution-held-out").exists():
        raise ValueError("held-out phase already exists; refusing replay")

    prior_path = prior_evidence_root / "model_authored_hidden_rollback_evidence.json"
    prior = _read_json(prior_path)
    contract = campaign_contract()
    if prior.get("campaign_contract") != contract:
        raise ValueError("prior evidence differs from the current frozen campaign contract")
    if prior.get("lifecycle_action") != "reject":
        raise ValueError("completion expects the retained pre-hidden reject record")
    if prior.get("candidate_skill_id") != CANDIDATE_ID:
        raise ValueError("prior candidate identity drifted")

    generator = prior.get("generator")
    if not isinstance(generator, dict):
        raise ValueError("prior generator evidence is missing")
    raw_trace = raw_root / "generator" / str(generator.get("raw_trace_pointer"))
    if not raw_trace.is_file() or _sha256_file(raw_trace) != generator.get("raw_trace_sha256"):
        raise ValueError("raw provider trace hash differs from retained evidence")

    raw_manifest_path = raw_root / "quarantine" / "quarantine_manifest.json"
    raw_manifest = _read_json(raw_manifest_path)
    prior_quarantine = prior.get("quarantine")
    if not isinstance(prior_quarantine, dict):
        raise ValueError("prior quarantine evidence is missing")
    if raw_manifest.get("manifest_sha256") != prior_quarantine.get("manifest_sha256"):
        raise ValueError("raw quarantine manifest differs from retained evidence")
    _validate_candidate_manifest(raw_root / "quarantine", raw_manifest)

    target_report = _read_json(raw_root / "execution-target" / "isolated_target_report.json")
    prior_execution = prior.get("isolated_execution")
    if not isinstance(prior_execution, dict) or target_report != prior_execution.get("target_phase"):
        raise ValueError("raw target-phase report differs from retained evidence")
    target_cases = target_report.get("cases")
    if not isinstance(target_cases, list) or not target_cases or not all(
        isinstance(item, dict) and item.get("passed") is True for item in target_cases
    ):
        raise ValueError("prior campaign did not pass every target case")

    existing = tuple(FileSkillLibrary(MVP_ROOT / "skills").list())
    live_snapshot = active_library_snapshot(existing)[1]
    if live_snapshot != prior.get("original_library_snapshot_sha256"):
        raise ValueError("live library snapshot changed since the prior campaign")

    heldout_cases = tuple(case for case in frozen_cases() if case.split == "held_out")
    heldout_phase = run_quarantined_candidate_phase(
        quarantine_root=raw_root / "quarantine",
        expected_manifest_sha256=str(raw_manifest["manifest_sha256"]),
        phase="held_out",
        cases=tuple(
            CandidateExecutionCase(case.id, "held_out", case.input_files, case.expected_files)
            for case in heldout_cases
        ),
        output_root=raw_root / "execution-held-out",
    )
    heldout_trust = tuple(
        check
        for case in heldout_cases
        for check in assess_verifier_trust(_verifier_profile(case), purpose="promotion")
    )
    hidden_passed = heldout_phase.all_passed and all(item.passed for item in heldout_trust)

    proposal = _proposal(
        existing=existing,
        prompt_sha256=str(generator["prompt_sha256"]),
        model_id=str(generator["requested_model_id"]),
        effort=str(generator["effort"]),
    )
    candidate = _artifact(proposal, str(raw_manifest["manifest_sha256"]))
    candidate.status = LifecycleStatus.ACTIVE
    decisions = {
        case.id: GovernedProvisioner(exposure_budget=1).decide(
            case.prompt, (*existing, candidate)
        )
        for case in frozen_cases()
    }
    negative_cases = tuple(case for case in frozen_cases() if case.split == "negative")
    negative_passed = all(
        decisions[case.id].primary_id != CANDIDATE_ID for case in negative_cases
    )
    outcome = resolve_lifecycle_outcome(
        pre_hidden_passed=True,
        hidden_passed=hidden_passed,
        negative_passed=negative_passed,
    )

    if outcome == "adopt":
        resolved = (*copy.deepcopy(existing), candidate)
        resolved_snapshot = active_library_snapshot(resolved)[1]
        cow_passed = resolved_snapshot != live_snapshot
    else:
        resolved = copy.deepcopy(existing)
        resolved_snapshot = active_library_snapshot(resolved)[1]
        cow_passed = resolved_snapshot == live_snapshot

    gates = [
        ValidationResult(**gate)
        for gate in prior.get("gates", [])
        if isinstance(gate, dict) and gate.get("name") not in {"G5_hidden_regression", "G6_cow_resolution"}
    ]
    gates.extend(
        (
            ValidationResult(
                name="G5_hidden_regression",
                passed=hidden_passed and negative_passed,
                evidence=(
                    f"hidden={sum(item.passed for item in heldout_phase.cases)}/{len(heldout_phase.cases)}; "
                    f"negative_routes={sum(decisions[c.id].primary_id != CANDIDATE_ID for c in negative_cases)}/{len(negative_cases)}; "
                    "same immutable candidate and live snapshot reverified"
                ),
            ),
            ValidationResult(
                name="G6_cow_resolution",
                passed=cow_passed,
                evidence=(
                    "all gates passed; new active candidate exists only in COW snapshot"
                    if outcome == "adopt"
                    else "post-target hidden/routing failure; candidate absent and original library snapshot retained"
                ),
            ),
        )
    )

    output_root.mkdir(parents=True)
    result: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": prior["campaign_id"],
        "completion_id": "same-candidate-held-out-completion-v1",
        "campaign_contract": contract,
        "campaign_contract_sha256": prior["campaign_contract_sha256"],
        "candidate_skill_id": CANDIDATE_ID,
        "adopted": outcome == "adopt",
        "lifecycle_action": outcome,
        "prior_evidence": {
            "relative_path": "../model_authored_hidden_rollback_live_v1/model_authored_hidden_rollback_evidence.json",
            "sha256": _sha256_file(prior_path),
            "retained_outcome": prior["lifecycle_action"],
        },
        "generator": generator,
        "quarantine_binding": {
            "manifest_sha256": raw_manifest["manifest_sha256"],
            "candidate_files_reverified": len(raw_manifest["files"]),
            "raw_trace_sha256_reverified": True,
            "target_report_reverified": True,
        },
        "isolated_execution": {
            "target_phase": target_report,
            "held_out_phase": heldout_phase.to_dict(),
        },
        "gates": [asdict(item) for item in gates],
        "original_library_snapshot_sha256": live_snapshot,
        "resolved_library_snapshot_sha256": resolved_snapshot,
        "resolved_library_statuses": {skill.id: skill.status.value for skill in resolved},
        "failure_observations": {
            "hidden_case_passed": hidden_passed,
            "negative_route_passed": negative_passed,
            "shadowed_negative_case_ids": [
                case.id for case in negative_cases if decisions[case.id].primary_id == CANDIDATE_ID
            ],
        },
        "evidence_boundary": {
            "actual_codex_provider_run": True,
            "new_provider_run_during_completion": False,
            "requested_model_id": generator["requested_model_id"],
            "provider_reported_model_ids": generator["provider_reported_model_ids"],
            "model_evidence_level": generator["model_evidence_level"],
            "model_authored_candidate": True,
            "raw_provider_trace_hash_reverified": True,
            "quarantine_manifest_and_candidate_bytes_reverified": True,
            "isolated_target_execution_reverified": True,
            "isolated_held_out_execution": True,
            "target_verifier_passed": True,
            "hidden_held_out_verifier_passed": hidden_passed,
            "negative_routing_verifier_passed": negative_passed,
            "copy_on_write_promoted": outcome == "adopt",
            "copy_on_write_rolled_back": outcome == "rollback" and cow_passed,
            "live_library_mutated": False,
            "provider_native_skill_invocation": False,
            "full_benchmark_claim": False,
        },
    }
    (output_root / "model_authored_hidden_completion_evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "resolved_library.json").write_text(
        json.dumps([skill.to_dict() for skill in resolved], ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    audit = audit_completion(
        evidence_root=output_root,
        prior_evidence_root=prior_evidence_root,
    )
    (output_root / "model_authored_hidden_completion_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--prior-evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-outcome", choices=("adopt", "rollback", "any"), default="rollback")
    args = parser.parse_args(argv)
    try:
        result = complete_campaign(
            raw_root=args.raw_root,
            prior_evidence_root=args.prior_evidence_root,
            output_root=args.output,
        )
    except (IsolatedCandidateRunnerError, OSError, ValueError) as exc:
        parser.error(str(exc))
    outcome = str(result["lifecycle_action"])
    observations = result["failure_observations"]
    print("Merlin same-candidate held-out completion")
    print(f"outcome={outcome}")
    print(f"hidden_passed={str(observations['hidden_case_passed']).lower()}")
    print(f"negative_route_passed={str(observations['negative_route_passed']).lower()}")
    print(f"safe_evidence={args.output.expanduser().resolve()}")
    return 0 if args.require_outcome in {"any", outcome} else 2


if __name__ == "__main__":
    raise SystemExit(main())
