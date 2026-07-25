"""Revalidate the retained requested-GPT-5.6 repair chain end to end."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence

from experiments.mvp.run_live_model_skill_repair import (
    SKILL_ID,
    _baseline_skill,
    _profile,
    frozen_cases,
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
from src.merlin_harness.models import SkillArtifact
from src.merlin_harness.skill_repair import (
    RepairCase,
    RepairCaseResult,
    RepairDiagnosis,
    SkillRepairError,
    run_skill_repair,
    skill_artifact_sha256,
    skill_library_snapshot_sha256,
)


SHA256_RE = re.compile(r"[0-9a-f]{64}")
EXPECTED_GATE_NAMES = (
    "repair_eligibility",
    "verifier_trust",
    "target_repair",
    "held_out_non_regression",
    "library_regression_non_regression",
    "copy_on_write_isolation",
)
EXPECTED_CHECK_EVIDENCE = {
    "repair_schema_and_boundary": "adopted model-authored COW repair with full-benchmark/native claims disabled",
    "exact_gate_denominator": "6/6 ordered repair promotion gates passed",
    "raw_trace_hash": "retained Codex JSONL matches the safe repair binding",
    "provider_events": "event denominator matches and no provider tool item occurred",
    "requested_model_contract": "requested model/effort and unresolved provider-ID boundary match",
    "target_only_prompt_hash": "reconstructed target-only prompt matches and contains no hidden case data",
    "response_hash": "raw final model message matches the repair response hash",
    "response_to_quarantine": "raw model response exactly reconstructs the immutable quarantine records",
    "script_only_mutation": "SKILL.md and agents/openai.yaml are unchanged while scripts/run.py changed",
    "quarantine_integrity": "safe quarantine semantic hash and every candidate file revalidate",
    "fresh_six_phase_execution": "fresh baseline 0/1,0/1,1/1 and candidate 1/1,1/1,1/1 phases reproduced",
    "repair_replay": "fresh phase outcomes reproduce the exact stored repair decision and 6/6 gates",
    "copy_on_write_binding": "resolved v2 is active, manifest-bound, and the original v1 artifact remains unchanged",
}
SOURCE_HASH_KEYS = {
    "repair_evidence_file_sha256",
    "resolved_library_file_sha256",
    "quarantine_manifest_file_sha256",
    "quarantine_manifest_semantic_sha256",
    "quarantine_report_file_sha256",
    "repair_raw_trace_sha256",
    "repair_response_sha256",
}


class ModelAuthoredRepairChainAuditError(ValueError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_file(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ModelAuthoredRepairChainAuditError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ModelAuthoredRepairChainAuditError(f"{label} is missing") from exc
    if not resolved.is_file():
        raise ModelAuthoredRepairChainAuditError(f"{label} must be a regular file")
    return resolved


def _require_dir(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ModelAuthoredRepairChainAuditError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ModelAuthoredRepairChainAuditError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise ModelAuthoredRepairChainAuditError(f"{label} must be a directory")
    return resolved


def _load(path: Path, *, label: str) -> dict[str, Any]:
    path = _require_file(path, label=label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ModelAuthoredRepairChainAuditError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ModelAuthoredRepairChainAuditError(f"{label} must be an object")
    return value


def _check(checks: list[dict[str, Any]], name: str, passed: bool) -> None:
    if not passed:
        raise ModelAuthoredRepairChainAuditError(f"repair chain check failed: {name}")
    checks.append(
        {"name": name, "passed": True, "evidence": EXPECTED_CHECK_EVIDENCE[name]}
    )


def _manifest_semantic_sha256(manifest: dict[str, Any]) -> str:
    body = {
        key: value
        for key, value in manifest.items()
        if key not in {"schema_version", "manifest_sha256"}
    }
    return content_sha256(body)


def _records_from_response(
    raw_response: str, *, expected_skill_id: str = SKILL_ID
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ModelAuthoredRepairChainAuditError("repair response is not JSON") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("candidate_skill_id") != expected_skill_id
        or not isinstance(files, list)
    ):
        raise ModelAuthoredRepairChainAuditError("repair response identity drifted")
    records = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise ModelAuthoredRepairChainAuditError("repair response file schema drifted")
        encoded = item["content"].encode("utf-8")
        records.append(
            {"path": item["path"], "bytes": len(encoded), "sha256": _sha256_bytes(encoded)}
        )
    return sorted(records, key=lambda item: item["path"])


def _repair_result(value: dict[str, Any]) -> dict[str, Any]:
    result = value.get("repair_result")
    if not isinstance(result, dict):
        raise ModelAuthoredRepairChainAuditError("repair result is missing")
    return result


class _FixedReviser:
    def __init__(self, candidate: SkillArtifact) -> None:
        self.candidate = candidate

    def propose(self, original, diagnosis, target_feedback, max_candidates):
        del original, diagnosis, target_feedback, max_candidates
        return (copy.deepcopy(self.candidate),)


class _ReplayEvaluator:
    def __init__(
        self,
        rows: dict[tuple[int, str], tuple[RepairCaseResult, ...]],
        *,
        skill_id: str = SKILL_ID,
    ) -> None:
        self.rows = rows
        self.skill_id = skill_id

    def evaluate_skill(self, skill, cases):
        return self.rows[(skill.version, cases[0].split)]

    def evaluate_library(self, skills, cases):
        target = next(skill for skill in skills if skill.id == self.skill_id)
        return self.rows[(target.version, cases[0].split)]


def _case_results(execution: Any, cases: tuple[RepairCase, ...]) -> tuple[RepairCaseResult, ...]:
    by_id = {item.case_id: item for item in execution.cases}
    return tuple(
        RepairCaseResult(
            case_id=case.case_id,
            verifier_id=case.verifier_id,
            passed=by_id[case.case_id].passed,
            score=float(by_id[case.case_id].passed),
            evidence=(
                "macos-confined exact-file verifier; "
                f"return_code={by_id[case.case_id].return_code}; "
                f"workspace_manifest_sha256={by_id[case.case_id].workspace_manifest_sha256}"
            ),
        )
        for case in cases
    )


def audit_model_authored_repair_chain(
    *,
    evidence_root: Path,
    raw_trace_path: Path,
    output_path: Path,
    process_runner: ProcessRunner | None = None,
) -> dict[str, Any]:
    evidence_root = _require_dir(evidence_root, label="repair evidence root")
    raw_trace_path = _require_file(raw_trace_path, label="repair raw trace")
    output_path = output_path.expanduser().resolve(strict=False)
    if output_path.exists() or output_path.is_symlink():
        raise ModelAuthoredRepairChainAuditError("audit output must be new")

    evidence_path = _require_file(
        evidence_root / "model_authored_skill_repair_evidence.json",
        label="repair evidence",
    )
    resolved_path = _require_file(
        evidence_root / "resolved_library.json", label="resolved library"
    )
    manifest_path = _require_file(
        evidence_root / "quarantine/quarantine_manifest.json",
        label="repair quarantine manifest",
    )
    quarantine_report_path = _require_file(
        evidence_root / "quarantine/quarantine_report.json",
        label="repair quarantine report",
    )
    evidence = _load(evidence_path, label="repair evidence")
    manifest = _load(manifest_path, label="repair quarantine manifest")
    quarantine_report = _load(quarantine_report_path, label="repair quarantine report")
    repair = _repair_result(evidence)
    checks: list[dict[str, Any]] = []

    boundary = evidence.get("evidence_boundary")
    _check(
        checks,
        "repair_schema_and_boundary",
        evidence.get("schema_version") == 1
        and evidence.get("campaign_id") == "live-gpt56-model-authored-repair-v1"
        and evidence.get("adopted") is True
        and evidence.get("lifecycle_action") == "adopt"
        and isinstance(boundary, dict)
        and boundary.get("actual_codex_provider_run") is True
        and boundary.get("model_authored_repair") is True
        and boundary.get("copy_on_write_promoted") is True
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

    model = evidence.get("model_repair")
    if not isinstance(model, dict):
        raise ModelAuthoredRepairChainAuditError("model repair binding is missing")
    raw_bytes = raw_trace_path.read_bytes()
    _check(
        checks,
        "raw_trace_hash",
        _sha256_bytes(raw_bytes) == model.get("raw_trace_sha256"),
    )
    try:
        raw_text = raw_bytes.decode("utf-8")
        summary = parse_codex_exec_jsonl(raw_text)
        item_types = _provider_item_types(raw_text)
    except (UnicodeError, CodexCliAdapterError, ModelCandidateGeneratorError) as exc:
        raise ModelAuthoredRepairChainAuditError("repair provider JSONL is invalid") from exc
    _check(
        checks,
        "provider_events",
        summary.event_count == 4 and item_types == ("agent_message",),
    )
    _check(
        checks,
        "requested_model_contract",
        model.get("requested_model_id") == "gpt-5.6-terra"
        and model.get("effort") == "high"
        and list(summary.reported_model_ids) == model.get("provider_reported_model_ids") == []
        and model.get("model_evidence_level") == "requested_cli_contract_only",
    )

    baseline_manifest = _load(
        Path(__file__).resolve().parent
        / "results/model_authored_skill_live_v1/quarantine/quarantine_manifest.json",
        label="baseline quarantine manifest",
    )
    baseline_manifest_sha256 = baseline_manifest["manifest_sha256"]
    baseline_root = Path(__file__).resolve().parent / "results/model_authored_skill_live_v1/quarantine"
    original_files = _read_immutable_bundle(
        baseline_root,
        expected_manifest_sha256=baseline_manifest_sha256,
        expected_skill_id=SKILL_ID,
    )
    original = _baseline_skill(baseline_manifest_sha256)
    target_feedback = tuple(
        RepairCaseResult(**item) for item in repair["baseline_target_results"]
    )
    diagnosis = RepairDiagnosis(
        skill_id=SKILL_ID,
        failure_kind="skill_local",
        trace_ids=("live-repair-target-failure-001",),
        failed_target_case_ids=("target-marker-spacing",),
        verifier_feedback=(
            "TODO markers may contain horizontal whitespace between the TODO token and colon; preserve item text and order.",
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
    hidden_case = next(item for item in frozen_cases() if item.case.split == "held_out")
    _check(
        checks,
        "target_only_prompt_hash",
        _sha256_bytes(prompt.encode("utf-8")) == model.get("prompt_sha256")
        and hidden_case.case.case_id not in prompt
        and all(content not in prompt for _path, content in hidden_case.input_files)
        and all(content not in prompt for _path, content in hidden_case.expected_files),
    )

    raw_response = (summary.final_message or "").strip()
    _check(
        checks,
        "response_hash",
        bool(raw_response)
        and _sha256_bytes(raw_response.encode("utf-8")) == model.get("response_sha256"),
    )
    try:
        envelope = parse_model_candidate_response(
            raw_response=raw_response,
            generator_backend=manifest["generator_backend"],
            generator_model=manifest["generator_model"],
            generator_effort=manifest["generator_effort"],
            generator_prompt_sha256=manifest["generator_prompt_sha256"],
            generator_provider_reported_model_ids=tuple(
                manifest["generator_provider_reported_model_ids"]
            ),
            generator_cli_version=manifest["generator_cli_version"],
            generator_raw_trace_sha256=manifest["generator_raw_trace_sha256"],
            generator_thread_id=manifest["generator_thread_id"],
            generator_turn_id=manifest["generator_turn_id"],
        )
    except ModelCandidateQuarantineError as exc:
        raise ModelAuthoredRepairChainAuditError("raw repair response is not quarantine-valid") from exc
    _check(
        checks,
        "response_to_quarantine",
        envelope.generator_response_sha256 == model.get("response_sha256")
        and _records_from_response(raw_response) == manifest.get("files"),
    )
    safe_files = {
        item.path: item.content for item in envelope.files
    }
    _check(
        checks,
        "script_only_mutation",
        safe_files.get("SKILL.md") == original_files.get("SKILL.md")
        and safe_files.get("agents/openai.yaml") == original_files.get("agents/openai.yaml")
        and safe_files.get("scripts/run.py") != original_files.get("scripts/run.py"),
    )
    candidate_root = evidence_root / "quarantine/candidate" / SKILL_ID
    files_ok = all(
        (candidate_root / record["path"]).is_file()
        and len((candidate_root / record["path"]).read_bytes()) == record["bytes"]
        and _sha256_bytes((candidate_root / record["path"]).read_bytes()) == record["sha256"]
        for record in manifest["files"]
    )
    _check(
        checks,
        "quarantine_integrity",
        _manifest_semantic_sha256(manifest) == manifest.get("manifest_sha256")
        == model.get("quarantine_manifest_sha256")
        == quarantine_report.get("manifest_sha256")
        and files_ok,
    )

    case_specs = frozen_cases()
    grouped = {
        split: tuple(item for item in case_specs if item.case.split == split)
        for split in ("target", "held_out", "library_regression")
    }
    rows: dict[tuple[int, str], tuple[RepairCaseResult, ...]] = {}
    fresh_counts: dict[str, list[int]] = {}
    roots = {1: baseline_root, 2: evidence_root / "quarantine"}
    manifests = {1: baseline_manifest_sha256, 2: manifest["manifest_sha256"]}
    with tempfile.TemporaryDirectory(prefix="merlin-repair-audit-") as temporary:
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
                    sum(item.passed for item in execution.cases), len(execution.cases)
                ]
    _check(
        checks,
        "fresh_six_phase_execution",
        fresh_counts
        == {
            "v1_target": [0, 1],
            "v1_held_out": [0, 1],
            "v1_library_regression": [1, 1],
            "v2_target": [1, 1],
            "v2_held_out": [1, 1],
            "v2_library_regression": [1, 1],
        },
    )

    resolved_payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(resolved_payload, list) or len(resolved_payload) != 1:
        raise ModelAuthoredRepairChainAuditError("resolved library schema drifted")
    candidate = FileSkillLibrary._from_dict(resolved_payload[0])
    profiles = {item.case.verifier_id: _profile(item) for item in case_specs}
    replay = run_skill_repair(
        diagnosis=diagnosis,
        library=(original,),
        target_cases=tuple(item.case for item in grouped["target"]),
        held_out_cases=tuple(item.case for item in grouped["held_out"]),
        regression_cases=tuple(item.case for item in grouped["library_regression"]),
        evaluator=_ReplayEvaluator(rows),
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
        == manifest["manifest_sha256"]
        and skill_artifact_sha256(original) == original_hash,
    )

    source_hashes = {
        "repair_evidence_file_sha256": _sha256_bytes(evidence_path.read_bytes()),
        "resolved_library_file_sha256": _sha256_bytes(resolved_path.read_bytes()),
        "quarantine_manifest_file_sha256": _sha256_bytes(manifest_path.read_bytes()),
        "quarantine_manifest_semantic_sha256": manifest["manifest_sha256"],
        "quarantine_report_file_sha256": _sha256_bytes(quarantine_report_path.read_bytes()),
        "repair_raw_trace_sha256": _sha256_bytes(raw_bytes),
        "repair_response_sha256": _sha256_bytes(raw_response.encode("utf-8")),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "requested-gpt56-model-authored-repair-chain-v1",
        "status": "passed",
        "skill_id": SKILL_ID,
        "source_hashes": source_hashes,
        "provider_contract": {
            "backend": "openai-codex-cli",
            "requested_model_id": "gpt-5.6-terra",
            "requested_effort": "high",
            "provider_reported_model_ids": list(summary.reported_model_ids),
            "model_evidence_level": model["model_evidence_level"],
            "event_count": summary.event_count,
            "item_types": list(item_types),
            "thread_id_sha256": _sha256_bytes((summary.thread_id or "").encode("utf-8")),
        },
        "fresh_revalidation": {
            **fresh_counts,
            "promotion_gates_passed": [6, 6],
            "selected_version": 2,
        },
        "checks": checks,
        "claim_boundary": {
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
    validate_model_authored_repair_chain_audit(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def validate_model_authored_repair_chain_audit(report: dict[str, Any]) -> None:
    expected_top = {
        "schema_version",
        "audit_id",
        "audit_sha256",
        "status",
        "skill_id",
        "source_hashes",
        "provider_contract",
        "fresh_revalidation",
        "checks",
        "claim_boundary",
    }
    if set(report) != expected_top:
        raise ModelAuthoredRepairChainAuditError("audit schema drifted")
    if (
        report.get("schema_version") != 1
        or report.get("audit_id") != "requested-gpt56-model-authored-repair-chain-v1"
        or report.get("status") != "passed"
        or report.get("skill_id") != SKILL_ID
    ):
        raise ModelAuthoredRepairChainAuditError("audit identity/status drifted")
    unhashed = dict(report)
    recorded = unhashed.pop("audit_sha256", None)
    if not isinstance(recorded, str) or content_sha256(unhashed) != recorded:
        raise ModelAuthoredRepairChainAuditError("audit content hash is invalid")
    hashes = report.get("source_hashes")
    if (
        not isinstance(hashes, dict)
        or set(hashes) != SOURCE_HASH_KEYS
        or any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in hashes.values())
    ):
        raise ModelAuthoredRepairChainAuditError("audit source hashes drifted")
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
        raise ModelAuthoredRepairChainAuditError("provider contract drifted")
    fresh = report.get("fresh_revalidation")
    expected_fresh = {
        "v1_target": [0, 1],
        "v1_held_out": [0, 1],
        "v1_library_regression": [1, 1],
        "v2_target": [1, 1],
        "v2_held_out": [1, 1],
        "v2_library_regression": [1, 1],
        "promotion_gates_passed": [6, 6],
        "selected_version": 2,
    }
    if fresh != expected_fresh:
        raise ModelAuthoredRepairChainAuditError("fresh denominator drifted")
    expected_checks = [
        {"name": name, "passed": True, "evidence": evidence}
        for name, evidence in EXPECTED_CHECK_EVIDENCE.items()
    ]
    if report.get("checks") != expected_checks:
        raise ModelAuthoredRepairChainAuditError("ordered audit checks drifted")
    if report.get("claim_boundary") != {
        "raw_provider_text_included": False,
        "provider_thread_id_included": False,
        "absolute_local_paths_included": False,
        "requested_model_is_provider_resolved_model": False,
        "provider_native_skill_invocation": False,
        "full_benchmark_result": False,
        "audit_is_new_model_execution": False,
        "fresh_candidate_isolated_reexecution": True,
    }:
        raise ModelAuthoredRepairChainAuditError("claim boundary drifted")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--raw-trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_model_authored_repair_chain(
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
    print("Merlin model-authored repair chain audit")
    print(f"status={report['status']}")
    print(f"checks={sum(item['passed'] for item in report['checks'])}/{len(report['checks'])}")
    print(f"audit_sha256={report['audit_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
