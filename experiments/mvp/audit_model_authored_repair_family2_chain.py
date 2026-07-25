"""Independently replay the second requested-GPT-5.6 repair family."""

from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence

from experiments.mvp.audit_model_authored_repair_chain import (
    EXPECTED_GATE_NAMES,
    ModelAuthoredRepairChainAuditError,
    _FixedReviser,
    _ReplayEvaluator,
    _case_results,
    _load,
    _manifest_semantic_sha256,
    _records_from_response,
    _require_dir,
    _require_file,
    _sha256_bytes,
)
from experiments.mvp.run_live_model_skill_repair_family2 import (
    CAMPAIGN_ID,
    SKILL_ID,
    _original_skill,
    _profile,
    frozen_cases,
    write_fixture_quarantine,
)
from src.merlin_harness.codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl
from src.merlin_harness.isolated_candidate_runner import (
    CandidateExecutionCase,
    IsolatedCandidateRunnerError,
    ProcessRunner,
    run_quarantined_candidate_phase,
)
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.management import content_sha256
from src.merlin_harness.model_candidate_generator import (
    ModelCandidateGeneratorError,
    _provider_item_types,
)
from src.merlin_harness.model_candidate_quarantine import (
    ModelCandidateQuarantineError,
    parse_model_candidate_response,
)
from src.merlin_harness.model_skill_reviser import (
    _read_immutable_bundle,
    build_model_repair_prompt,
)
from src.merlin_harness.skill_repair import (
    RepairCaseResult,
    RepairDiagnosis,
    SkillRepairError,
    run_skill_repair,
    skill_artifact_sha256,
    skill_library_snapshot_sha256,
)


SHA256_RE = re.compile(r"[0-9a-f]{64}")
AUDIT_ID = "requested-gpt56-model-authored-repair-family2-chain-v1"
EXPECTED_CHECK_EVIDENCE = {
    "repair_schema_and_boundary": "family-2 model-authored COW promotion with baseline and provider claims separated",
    "exact_gate_denominator": "6/6 ordered repair promotion gates passed",
    "deterministic_baseline_binding": "safe baseline exactly reconstructs the declared deterministic fixture",
    "raw_trace_hash": "retained Codex JSONL matches the safe family-2 binding",
    "provider_events": "event denominator matches and no provider tool item occurred",
    "requested_model_contract": "requested model/effort and unresolved provider-ID boundary match",
    "target_only_prompt_hash": "reconstructed prompt matches and excludes hidden and regression case content",
    "response_hash": "raw final model message matches the family-2 response hash",
    "response_to_quarantine": "raw model response exactly reconstructs the immutable candidate records",
    "script_only_mutation": "routing and interface files are unchanged while scripts/run.py changed",
    "candidate_quarantine_integrity": "candidate semantic manifest and every safe file revalidate",
    "fresh_six_phase_execution": "fresh baseline 0/1,1/1,1/1 and candidate 1/1,1/1,1/1 phases reproduced",
    "repair_replay": "fresh phase outcomes reproduce the stored family-2 decision and 6/6 gates",
    "copy_on_write_binding": "resolved v2 is active and the deterministic v1 baseline remains unchanged",
}
SOURCE_HASH_KEYS = {
    "repair_evidence_file_sha256",
    "resolved_library_file_sha256",
    "baseline_manifest_file_sha256",
    "baseline_manifest_semantic_sha256",
    "candidate_manifest_file_sha256",
    "candidate_manifest_semantic_sha256",
    "candidate_report_file_sha256",
    "repair_raw_trace_sha256",
    "repair_response_sha256",
}


def _check(checks: list[dict[str, Any]], name: str, passed: bool) -> None:
    if not passed:
        raise ModelAuthoredRepairChainAuditError(
            f"family-2 repair chain check failed: {name}"
        )
    checks.append(
        {"name": name, "passed": True, "evidence": EXPECTED_CHECK_EVIDENCE[name]}
    )


def audit_model_authored_repair_family2_chain(
    *,
    evidence_root: Path,
    raw_trace_path: Path,
    output_path: Path,
    process_runner: ProcessRunner | None = None,
) -> dict[str, Any]:
    evidence_root = _require_dir(evidence_root, label="family-2 evidence root")
    raw_trace_path = _require_file(raw_trace_path, label="family-2 raw trace")
    output_path = output_path.expanduser().resolve(strict=False)
    if output_path.exists() or output_path.is_symlink():
        raise ModelAuthoredRepairChainAuditError("family-2 audit output must be new")

    evidence_path = _require_file(
        evidence_root / "model_authored_skill_repair_family2_evidence.json",
        label="family-2 repair evidence",
    )
    resolved_path = _require_file(
        evidence_root / "resolved_library.json", label="family-2 resolved library"
    )
    baseline_root = _require_dir(
        evidence_root / "baseline_quarantine", label="family-2 safe baseline"
    )
    candidate_root = _require_dir(
        evidence_root / "candidate_quarantine", label="family-2 safe candidate"
    )
    baseline_manifest_path = _require_file(
        baseline_root / "quarantine_manifest.json", label="family-2 baseline manifest"
    )
    candidate_manifest_path = _require_file(
        candidate_root / "quarantine_manifest.json", label="family-2 candidate manifest"
    )
    candidate_report_path = _require_file(
        candidate_root / "quarantine_report.json", label="family-2 candidate report"
    )
    evidence = _load(evidence_path, label="family-2 repair evidence")
    baseline_manifest = _load(
        baseline_manifest_path, label="family-2 baseline manifest"
    )
    candidate_manifest = _load(
        candidate_manifest_path, label="family-2 candidate manifest"
    )
    candidate_report = _load(
        candidate_report_path, label="family-2 candidate report"
    )
    repair = evidence.get("repair_result")
    if not isinstance(repair, dict):
        raise ModelAuthoredRepairChainAuditError("family-2 repair result is missing")
    checks: list[dict[str, Any]] = []

    boundary = evidence.get("evidence_boundary")
    baseline_binding = evidence.get("baseline_bundle")
    _check(
        checks,
        "repair_schema_and_boundary",
        evidence.get("schema_version") == 1
        and evidence.get("campaign_id") == CAMPAIGN_ID
        and evidence.get("skill_id") == SKILL_ID
        and evidence.get("decision") == "promote"
        and evidence.get("adopted") is True
        and isinstance(baseline_binding, dict)
        and baseline_binding.get("authorship") == "deterministic_fixture"
        and baseline_binding.get("model_authorship_claim") is False
        and isinstance(boundary, dict)
        and boundary.get("actual_codex_provider_run") is True
        and boundary.get("baseline_model_authored") is False
        and boundary.get("candidate_model_authored_repair") is True
        and boundary.get("copy_on_write_promoted") is True
        and boundary.get("copy_on_write_rolled_back") is False
        and boundary.get("live_library_mutated") is False
        and boundary.get("provider_native_skill_invocation") is False
        and boundary.get("full_benchmark_claim") is False,
    )
    gates = repair.get("gates")
    _check(
        checks,
        "exact_gate_denominator",
        isinstance(gates, list)
        and tuple(item.get("name") for item in gates) == EXPECTED_GATE_NAMES
        and all(item.get("passed") is True for item in gates),
    )

    with tempfile.TemporaryDirectory(prefix="merlin-family2-baseline-") as temporary:
        regenerated_root = Path(temporary) / "baseline"
        regenerated_sha = write_fixture_quarantine(regenerated_root)
        regenerated_manifest = _load(
            regenerated_root / "quarantine_manifest.json",
            label="regenerated family-2 baseline",
        )
        regenerated_files = {
            path.relative_to(regenerated_root).as_posix(): path.read_bytes()
            for path in regenerated_root.rglob("*")
            if path.is_file()
        }
        safe_files = {
            path.relative_to(baseline_root).as_posix(): path.read_bytes()
            for path in baseline_root.rglob("*")
            if path.is_file()
        }
    _check(
        checks,
        "deterministic_baseline_binding",
        regenerated_sha == baseline_manifest.get("manifest_sha256")
        == baseline_binding.get("manifest_sha256")
        and regenerated_manifest == baseline_manifest
        and regenerated_files == safe_files,
    )

    model = evidence.get("model_repair")
    if not isinstance(model, dict):
        raise ModelAuthoredRepairChainAuditError("family-2 model binding is missing")
    raw_bytes = raw_trace_path.read_bytes()
    _check(
        checks,
        "raw_trace_hash",
        _sha256_bytes(raw_bytes) == model.get("raw_trace_sha256"),
    )
    try:
        raw_text = raw_bytes.decode("utf-8")
        provider = parse_codex_exec_jsonl(raw_text)
        item_types = _provider_item_types(raw_text)
    except (UnicodeError, CodexCliAdapterError, ModelCandidateGeneratorError) as exc:
        raise ModelAuthoredRepairChainAuditError(
            "family-2 provider JSONL is invalid"
        ) from exc
    _check(
        checks,
        "provider_events",
        provider.event_count == 4 and item_types == ("agent_message",),
    )
    _check(
        checks,
        "requested_model_contract",
        model.get("requested_model_id") == "gpt-5.6-terra"
        and model.get("effort") == "high"
        and list(provider.reported_model_ids)
        == model.get("provider_reported_model_ids")
        == []
        and model.get("model_evidence_level") == "requested_cli_contract_only",
    )

    baseline_sha = baseline_manifest["manifest_sha256"]
    original_files = _read_immutable_bundle(
        baseline_root,
        expected_manifest_sha256=baseline_sha,
        expected_skill_id=SKILL_ID,
    )
    original = _original_skill(baseline_sha)
    target_feedback = tuple(
        RepairCaseResult(**item) for item in repair["baseline_target_results"]
    )
    diagnosis = RepairDiagnosis(
        skill_id=SKILL_ID,
        failure_kind="skill_local",
        trace_ids=("family2-target-failure-001",),
        failed_target_case_ids=("target-horizontal-spacing",),
        verifier_feedback=(
            "Allow horizontal whitespace around the first key-value separator and "
            "ignore blank or comment-only lines; preserve existing first-value and "
            "literal-value behavior.",
        ),
        library_snapshot_sha256=skill_library_snapshot_sha256((original,)),
    )
    prompt = build_model_repair_prompt(
        original=original,
        original_files=original_files,
        diagnosis=diagnosis,
        target_feedback=target_feedback,
        next_version=2,
    )
    hidden_specs = tuple(
        item for item in frozen_cases() if item.case.split != "target"
    )
    _check(
        checks,
        "target_only_prompt_hash",
        _sha256_bytes(prompt.encode("utf-8")) == model.get("prompt_sha256")
        and all(item.case.case_id not in prompt for item in hidden_specs)
        and all(
            content not in prompt
            for item in hidden_specs
            for _path, content in item.input_files + item.expected_files
        ),
    )

    raw_response = (provider.final_message or "").strip()
    _check(
        checks,
        "response_hash",
        bool(raw_response)
        and _sha256_bytes(raw_response.encode("utf-8"))
        == model.get("response_sha256"),
    )
    try:
        envelope = parse_model_candidate_response(
            raw_response=raw_response,
            generator_backend=candidate_manifest["generator_backend"],
            generator_model=candidate_manifest["generator_model"],
            generator_effort=candidate_manifest["generator_effort"],
            generator_prompt_sha256=candidate_manifest["generator_prompt_sha256"],
            generator_provider_reported_model_ids=tuple(
                candidate_manifest["generator_provider_reported_model_ids"]
            ),
            generator_cli_version=candidate_manifest["generator_cli_version"],
            generator_raw_trace_sha256=candidate_manifest[
                "generator_raw_trace_sha256"
            ],
            generator_thread_id=candidate_manifest["generator_thread_id"],
            generator_turn_id=candidate_manifest["generator_turn_id"],
        )
    except ModelCandidateQuarantineError as exc:
        raise ModelAuthoredRepairChainAuditError(
            "family-2 raw response is not quarantine-valid"
        ) from exc
    _check(
        checks,
        "response_to_quarantine",
        envelope.generator_response_sha256 == model.get("response_sha256")
        and _records_from_response(raw_response, expected_skill_id=SKILL_ID)
        == candidate_manifest.get("files"),
    )
    proposed_files = {item.path: item.content for item in envelope.files}
    _check(
        checks,
        "script_only_mutation",
        proposed_files.get("SKILL.md") == original_files.get("SKILL.md")
        and proposed_files.get("agents/openai.yaml")
        == original_files.get("agents/openai.yaml")
        and proposed_files.get("scripts/run.py")
        != original_files.get("scripts/run.py"),
    )
    safe_candidate_root = candidate_root / "candidate" / SKILL_ID
    files_ok = all(
        (safe_candidate_root / record["path"]).is_file()
        and len((safe_candidate_root / record["path"]).read_bytes())
        == record["bytes"]
        and _sha256_bytes((safe_candidate_root / record["path"]).read_bytes())
        == record["sha256"]
        for record in candidate_manifest["files"]
    )
    _check(
        checks,
        "candidate_quarantine_integrity",
        _manifest_semantic_sha256(candidate_manifest)
        == candidate_manifest.get("manifest_sha256")
        == model.get("quarantine_manifest_sha256")
        == candidate_report.get("manifest_sha256")
        and files_ok,
    )

    case_specs = frozen_cases()
    grouped = {
        split: tuple(item for item in case_specs if item.case.split == split)
        for split in ("target", "held_out", "library_regression")
    }
    rows: dict[tuple[int, str], tuple[RepairCaseResult, ...]] = {}
    fresh_counts: dict[str, list[int]] = {}
    roots = {1: baseline_root, 2: candidate_root}
    manifests = {1: baseline_sha, 2: candidate_manifest["manifest_sha256"]}
    with tempfile.TemporaryDirectory(prefix="merlin-family2-audit-") as temporary:
        audit_root = Path(temporary)
        for version in (1, 2):
            for split in ("target", "held_out", "library_regression"):
                selected = grouped[split]
                kwargs: dict[str, Any] = {}
                if process_runner is not None:
                    kwargs["process_runner"] = process_runner
                execution = run_quarantined_candidate_phase(
                    quarantine_root=roots[version],
                    expected_manifest_sha256=manifests[version],
                    phase=split,
                    cases=tuple(
                        CandidateExecutionCase(
                            item.case.case_id,
                            item.case.split,
                            item.input_files,
                            item.expected_files,
                        )
                        for item in selected
                    ),
                    output_root=audit_root / f"v{version}-{split}",
                    **kwargs,
                )
                repair_cases = tuple(item.case for item in selected)
                rows[(version, split)] = _case_results(execution, repair_cases)
                fresh_counts[f"v{version}_{split}"] = [
                    sum(item.passed for item in execution.cases),
                    len(execution.cases),
                ]
    expected_fresh = {
        "v1_target": [0, 1],
        "v1_held_out": [1, 1],
        "v1_library_regression": [1, 1],
        "v2_target": [1, 1],
        "v2_held_out": [1, 1],
        "v2_library_regression": [1, 1],
    }
    _check(checks, "fresh_six_phase_execution", fresh_counts == expected_fresh)

    try:
        resolved_payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ModelAuthoredRepairChainAuditError(
            "family-2 resolved library is invalid"
        ) from exc
    if not isinstance(resolved_payload, list) or len(resolved_payload) != 1:
        raise ModelAuthoredRepairChainAuditError(
            "family-2 resolved library schema drifted"
        )
    candidate = FileSkillLibrary._from_dict(resolved_payload[0])
    profiles = {item.case.verifier_id: _profile(item) for item in case_specs}
    replay = run_skill_repair(
        diagnosis=diagnosis,
        library=(original,),
        target_cases=tuple(item.case for item in grouped["target"]),
        held_out_cases=tuple(item.case for item in grouped["held_out"]),
        regression_cases=tuple(item.case for item in grouped["library_regression"]),
        evaluator=_ReplayEvaluator(rows, skill_id=SKILL_ID),
        reviser=_FixedReviser(candidate),
        verifier_profiles=profiles,
        max_candidates=1,
    )
    _check(checks, "repair_replay", replay.to_dict() == repair)
    original_hash = skill_artifact_sha256(original)
    _check(
        checks,
        "copy_on_write_binding",
        replay.adopted
        and candidate.version == 2
        and candidate.status.value == "active"
        and candidate.metadata.get("repair_quarantine_manifest_sha256")
        == candidate_manifest["manifest_sha256"]
        and skill_artifact_sha256(original) == original_hash,
    )

    source_hashes = {
        "repair_evidence_file_sha256": _sha256_bytes(evidence_path.read_bytes()),
        "resolved_library_file_sha256": _sha256_bytes(resolved_path.read_bytes()),
        "baseline_manifest_file_sha256": _sha256_bytes(
            baseline_manifest_path.read_bytes()
        ),
        "baseline_manifest_semantic_sha256": baseline_sha,
        "candidate_manifest_file_sha256": _sha256_bytes(
            candidate_manifest_path.read_bytes()
        ),
        "candidate_manifest_semantic_sha256": candidate_manifest[
            "manifest_sha256"
        ],
        "candidate_report_file_sha256": _sha256_bytes(
            candidate_report_path.read_bytes()
        ),
        "repair_raw_trace_sha256": _sha256_bytes(raw_bytes),
        "repair_response_sha256": _sha256_bytes(raw_response.encode("utf-8")),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": AUDIT_ID,
        "status": "passed",
        "skill_id": SKILL_ID,
        "decision": "promote",
        "source_hashes": source_hashes,
        "provider_contract": {
            "backend": "openai-codex-cli",
            "requested_model_id": "gpt-5.6-terra",
            "requested_effort": "high",
            "provider_reported_model_ids": list(provider.reported_model_ids),
            "model_evidence_level": model["model_evidence_level"],
            "event_count": provider.event_count,
            "item_types": list(item_types),
            "thread_id_sha256": _sha256_bytes(
                (provider.thread_id or "").encode("utf-8")
            ),
        },
        "fresh_revalidation": {
            **fresh_counts,
            "promotion_gates_passed": [6, 6],
            "selected_version": 2,
        },
        "checks": checks,
        "claim_boundary": {
            "baseline_model_authorship_claimed": False,
            "raw_provider_text_included": False,
            "provider_thread_id_included": False,
            "absolute_local_paths_included": False,
            "requested_model_is_provider_resolved_model": False,
            "provider_native_skill_invocation": False,
            "full_benchmark_result": False,
            "audit_is_new_model_execution": False,
            "fresh_candidate_isolated_reexecution": True,
        },
    }
    report["audit_sha256"] = content_sha256(report)
    validate_model_authored_repair_family2_chain_audit(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def validate_model_authored_repair_family2_chain_audit(
    report: dict[str, Any],
) -> None:
    expected_top = {
        "schema_version",
        "audit_id",
        "audit_sha256",
        "status",
        "skill_id",
        "decision",
        "source_hashes",
        "provider_contract",
        "fresh_revalidation",
        "checks",
        "claim_boundary",
    }
    if set(report) != expected_top:
        raise ModelAuthoredRepairChainAuditError("family-2 audit schema drifted")
    if (
        report.get("schema_version") != 1
        or report.get("audit_id") != AUDIT_ID
        or report.get("status") != "passed"
        or report.get("skill_id") != SKILL_ID
        or report.get("decision") != "promote"
    ):
        raise ModelAuthoredRepairChainAuditError(
            "family-2 audit identity/status drifted"
        )
    unhashed = dict(report)
    recorded = unhashed.pop("audit_sha256", None)
    if not isinstance(recorded, str) or content_sha256(unhashed) != recorded:
        raise ModelAuthoredRepairChainAuditError(
            "family-2 audit content hash is invalid"
        )
    hashes = report.get("source_hashes")
    if (
        not isinstance(hashes, dict)
        or set(hashes) != SOURCE_HASH_KEYS
        or any(
            not isinstance(value, str) or not SHA256_RE.fullmatch(value)
            for value in hashes.values()
        )
    ):
        raise ModelAuthoredRepairChainAuditError(
            "family-2 audit source hashes drifted"
        )
    provider = report.get("provider_contract")
    if (
        not isinstance(provider, dict)
        or provider.get("backend") != "openai-codex-cli"
        or provider.get("requested_model_id") != "gpt-5.6-terra"
        or provider.get("requested_effort") != "high"
        or provider.get("provider_reported_model_ids") != []
        or provider.get("model_evidence_level") != "requested_cli_contract_only"
        or provider.get("event_count") != 4
        or provider.get("item_types") != ["agent_message"]
        or not SHA256_RE.fullmatch(str(provider.get("thread_id_sha256", "")))
    ):
        raise ModelAuthoredRepairChainAuditError(
            "family-2 provider contract drifted"
        )
    expected_fresh = {
        "v1_target": [0, 1],
        "v1_held_out": [1, 1],
        "v1_library_regression": [1, 1],
        "v2_target": [1, 1],
        "v2_held_out": [1, 1],
        "v2_library_regression": [1, 1],
        "promotion_gates_passed": [6, 6],
        "selected_version": 2,
    }
    if report.get("fresh_revalidation") != expected_fresh:
        raise ModelAuthoredRepairChainAuditError(
            "family-2 fresh denominator drifted"
        )
    expected_checks = [
        {"name": name, "passed": True, "evidence": evidence}
        for name, evidence in EXPECTED_CHECK_EVIDENCE.items()
    ]
    if report.get("checks") != expected_checks:
        raise ModelAuthoredRepairChainAuditError(
            "family-2 ordered checks drifted"
        )
    if report.get("claim_boundary") != {
        "baseline_model_authorship_claimed": False,
        "raw_provider_text_included": False,
        "provider_thread_id_included": False,
        "absolute_local_paths_included": False,
        "requested_model_is_provider_resolved_model": False,
        "provider_native_skill_invocation": False,
        "full_benchmark_result": False,
        "audit_is_new_model_execution": False,
        "fresh_candidate_isolated_reexecution": True,
    }:
        raise ModelAuthoredRepairChainAuditError(
            "family-2 claim boundary drifted"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--raw-trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_model_authored_repair_family2_chain(
            evidence_root=args.evidence_root,
            raw_trace_path=args.raw_trace,
            output_path=args.output,
        )
    except (
        CodexCliAdapterError,
        IsolatedCandidateRunnerError,
        ModelAuthoredRepairChainAuditError,
        ModelCandidateGeneratorError,
        ModelCandidateQuarantineError,
        OSError,
        SkillRepairError,
        ValueError,
    ) as exc:
        parser.error(str(exc))
    print("Merlin model-authored repair family-2 chain audit")
    print(f"status={report['status']}")
    print(
        f"checks={sum(item['passed'] for item in report['checks'])}/"
        f"{len(report['checks'])}"
    )
    print(f"audit_sha256={report['audit_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
