"""Revalidate the complete model-authored skill lifecycle from retained raw evidence.

The audit reads the retained provider authoring JSONL and promoted-chat session,
reconstructs the candidate/quarantine/promotion chain, freshly executes the
immutable candidate on the frozen target and hidden cases, and emits a safe,
hash-only report. Raw provider text, commands, thread IDs, paths, and workspace
contents are never copied into the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from experiments.mvp.package_promoted_chat_smoke import package_promoted_chat_smoke
from experiments.mvp.run_chat import load_verified_promotion_overlay
from experiments.mvp.run_live_model_skill_creation import (
    CANDIDATE_ID,
    MVP_ROOT,
    frozen_cases,
    generator_prompt,
)
from src.merlin_harness.codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl
from src.merlin_harness.governed_provisioning import GovernedProvisioner
from src.merlin_harness.isolated_candidate_runner import (
    CandidateExecutionCase,
    IsolatedCandidateRunnerError,
    run_quarantined_candidate,
)
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.model_candidate_generator import (
    ModelCandidateGeneratorError,
    _provider_item_types,
)
from src.merlin_harness.model_candidate_quarantine import (
    ModelCandidateQuarantineError,
    parse_model_candidate_response,
)
from src.merlin_harness.models import LifecycleStatus
from src.merlin_harness.management import content_sha256


EXPECTED_GATE_NAMES = (
    "G0_need",
    "Q_Q0_provenance",
    "Q_Q1_paths",
    "Q_Q2_size",
    "Q_Q3_static_python",
    "Q_Q4_execution_block",
    "G1_format",
    "G2_safety",
    "G3_trigger",
    "G4_target",
    "G5_hidden_regression",
    "G6_adoption",
)

EXPECTED_CHECK_EVIDENCE = {
    "promotion_schema": "schema_version=1",
    "promotion_boundary": (
        "adopted COW candidate with provider-native/full-benchmark claims disabled"
    ),
    "exact_gate_denominator": "12/12 ordered quarantine and G0-G6 gates passed",
    "authoring_raw_trace_hash": "retained authoring JSONL matches the safe generator record",
    "authoring_provider_events": "event denominator matches and no provider tool item occurred",
    "authoring_model_contract": (
        "requested-model contract and provider-reported-ID boundary match the raw run"
    ),
    "authoring_prompt_hash": (
        "current frozen target-only authoring prompt matches the recorded prompt hash"
    ),
    "authoring_response_hash": (
        "raw agent message matches the recorded candidate response hash"
    ),
    "response_to_quarantine": (
        "raw model response file bytes exactly match the quarantine manifest"
    ),
    "quarantine_manifest_integrity": (
        "quarantine, execution, report, and promotion share one semantic manifest hash"
    ),
    "quarantine_files": "all three quarantined candidate files match their records",
    "fresh_isolated_target_hidden": (
        "fresh immutable rerun passed target 2/2 and hidden 1/1 with "
        "network/expected-output denial"
    ),
    "promotion_overlay_and_negative_routes": (
        "hash-bound overlay stages one active candidate and preserves 2/2 negative routes"
    ),
    "promoted_chat_reproduction": (
        "retained raw chat trace, routing metadata, output, and verifier reproduce "
        "the packaged summary"
    ),
    "promotion_to_chat_binding": (
        "recorded chat is bound to the exact promotion/quarantine and one successful "
        "script execution"
    ),
}

SOURCE_HASH_KEYS = {
    "promotion_evidence_file_sha256",
    "provisional_library_file_sha256",
    "quarantine_manifest_file_sha256",
    "quarantine_manifest_semantic_sha256",
    "quarantine_report_file_sha256",
    "authoring_raw_trace_sha256",
    "authoring_response_sha256",
    "packaged_promoted_chat_file_sha256",
    "promoted_chat_raw_trace_sha256",
}

SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ModelAuthoredSkillChainAuditError(ValueError):
    """Raised when any link in the retained evidence chain fails closed."""


def _require_exact_keys(value: Any, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ModelAuthoredSkillChainAuditError(f"{label} schema drifted")
    return value


def validate_model_authored_skill_chain_audit(report: dict[str, Any]) -> None:
    """Fail closed on a packaged hash-only v1 chain-audit report."""

    top = _require_exact_keys(
        report,
        {
            "schema_version",
            "audit_id",
            "audit_sha256",
            "status",
            "candidate_skill_id",
            "source_hashes",
            "provider_contract",
            "fresh_revalidation",
            "checks",
            "claim_boundary",
        },
        label="audit",
    )
    if (
        top["schema_version"] != 1
        or top["audit_id"] != "requested-gpt56-model-authored-skill-chain-v1"
        or top["status"] != "passed"
        or top["candidate_skill_id"] != CANDIDATE_ID
        or not isinstance(top["audit_sha256"], str)
        or not SHA256_RE.fullmatch(top["audit_sha256"])
    ):
        raise ModelAuthoredSkillChainAuditError("audit identity or status drifted")

    unhashed = dict(top)
    recorded_hash = unhashed.pop("audit_sha256")
    if content_sha256(unhashed) != recorded_hash:
        raise ModelAuthoredSkillChainAuditError("audit content hash is invalid")

    hashes = _require_exact_keys(top["source_hashes"], SOURCE_HASH_KEYS, label="source hashes")
    if not all(isinstance(value, str) and SHA256_RE.fullmatch(value) for value in hashes.values()):
        raise ModelAuthoredSkillChainAuditError("source hash is invalid")

    provider = _require_exact_keys(
        top["provider_contract"],
        {
            "backend",
            "cli_version",
            "requested_model_id",
            "requested_effort",
            "provider_reported_model_ids",
            "model_evidence_level",
            "authoring_event_count",
            "authoring_item_types",
            "authoring_thread_id_sha256",
        },
        label="provider contract",
    )
    if (
        provider["backend"] != "openai-codex-cli"
        or not isinstance(provider["cli_version"], str)
        or not provider["cli_version"].startswith("codex-cli ")
        or provider["requested_model_id"] != "gpt-5.6-terra"
        or provider["requested_effort"] != "high"
        or provider["provider_reported_model_ids"] != []
        or provider["model_evidence_level"] != "requested_cli_contract_only"
        or provider["authoring_event_count"] != 4
        or provider["authoring_item_types"] != ["agent_message"]
        or not isinstance(provider["authoring_thread_id_sha256"], str)
        or not SHA256_RE.fullmatch(provider["authoring_thread_id_sha256"])
    ):
        raise ModelAuthoredSkillChainAuditError("provider contract drifted")

    fresh = _require_exact_keys(
        top["fresh_revalidation"],
        {
            "target_passed",
            "hidden_held_out_passed",
            "negative_routes_passed",
            "promotion_gates_passed",
            "promoted_chat_script_executions",
            "promoted_chat_verifier_passed",
        },
        label="fresh revalidation",
    )
    if fresh != {
        "target_passed": [2, 2],
        "hidden_held_out_passed": [1, 1],
        "negative_routes_passed": [2, 2],
        "promotion_gates_passed": [12, 12],
        "promoted_chat_script_executions": 1,
        "promoted_chat_verifier_passed": True,
    }:
        raise ModelAuthoredSkillChainAuditError("fresh revalidation denominator drifted")

    checks = top["checks"]
    expected_checks = [
        {"name": name, "passed": True, "evidence": evidence}
        for name, evidence in EXPECTED_CHECK_EVIDENCE.items()
    ]
    if checks != expected_checks:
        raise ModelAuthoredSkillChainAuditError("ordered chain checks drifted")

    claim = _require_exact_keys(
        top["claim_boundary"],
        {
            "raw_provider_text_included",
            "raw_command_text_included",
            "provider_thread_id_included",
            "absolute_local_paths_included",
            "requested_model_is_provider_resolved_model",
            "provider_native_skill_invocation",
            "full_benchmark_result",
            "audit_is_new_model_execution",
            "fresh_candidate_isolated_reexecution",
        },
        label="claim boundary",
    )
    if claim != {
        "raw_provider_text_included": False,
        "raw_command_text_included": False,
        "provider_thread_id_included": False,
        "absolute_local_paths_included": False,
        "requested_model_is_provider_resolved_model": False,
        "provider_native_skill_invocation": False,
        "full_benchmark_result": False,
        "audit_is_new_model_execution": False,
        "fresh_candidate_isolated_reexecution": True,
    }:
        raise ModelAuthoredSkillChainAuditError("claim boundary drifted")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _regular_file(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ModelAuthoredSkillChainAuditError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ModelAuthoredSkillChainAuditError(f"{label} is missing") from exc
    if not resolved.is_file():
        raise ModelAuthoredSkillChainAuditError(f"{label} must be a regular file")
    return resolved


def _regular_directory(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ModelAuthoredSkillChainAuditError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ModelAuthoredSkillChainAuditError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise ModelAuthoredSkillChainAuditError(f"{label} must be a regular directory")
    return resolved


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    file_path = _regular_file(path, label=label)
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ModelAuthoredSkillChainAuditError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ModelAuthoredSkillChainAuditError(f"{label} must be a JSON object")
    return value


def _check(checks: list[dict[str, Any]], name: str, condition: bool, evidence: str) -> None:
    if not condition:
        raise ModelAuthoredSkillChainAuditError(f"{name} failed: {evidence}")
    checks.append({"name": name, "passed": True, "evidence": evidence})


def _manifest_body_sha256(manifest: dict[str, Any]) -> str:
    body = {
        key: value
        for key, value in manifest.items()
        if key not in {"schema_version", "manifest_sha256"}
    }
    return content_sha256(body)


def _candidate_records_from_response(raw_response: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ModelAuthoredSkillChainAuditError("authoring response is not strict JSON") from exc
    if not isinstance(payload, dict) or payload.get("candidate_skill_id") != CANDIDATE_ID:
        raise ModelAuthoredSkillChainAuditError("authoring response candidate identity drifted")
    files = payload.get("files")
    if not isinstance(files, list):
        raise ModelAuthoredSkillChainAuditError("authoring response file list is invalid")
    records: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise ModelAuthoredSkillChainAuditError("authoring response file schema drifted")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ModelAuthoredSkillChainAuditError("authoring response file type drifted")
        encoded = content.encode("utf-8")
        records.append({"path": path, "bytes": len(encoded), "sha256": _sha256_bytes(encoded)})
    return sorted(records, key=lambda item: item["path"])


def audit_model_authored_skill_chain(
    *,
    evidence_root: Path,
    authoring_raw_trace_path: Path,
    promoted_chat_workspace: Path,
    promoted_chat_session_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Audit retained authoring and promoted-use artifacts and write one safe report."""

    evidence_root = _regular_directory(evidence_root, label="model-authored evidence root")
    authoring_raw = _regular_file(authoring_raw_trace_path, label="authoring raw trace")
    chat_workspace = _regular_directory(promoted_chat_workspace, label="promoted chat workspace")
    chat_session = _regular_directory(promoted_chat_session_root, label="promoted chat session")
    if not chat_session.is_relative_to(chat_workspace):
        raise ModelAuthoredSkillChainAuditError("promoted chat session must stay inside its workspace")
    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise ModelAuthoredSkillChainAuditError("audit output must be new and not a symlink")

    promotion_path = _regular_file(
        evidence_root / "model_authored_skill_evidence.json",
        label="promotion evidence",
    )
    provisional_path = _regular_file(
        evidence_root / "provisional_library.json",
        label="provisional library",
    )
    quarantine_manifest_path = _regular_file(
        evidence_root / "quarantine/quarantine_manifest.json",
        label="quarantine manifest",
    )
    quarantine_report_path = _regular_file(
        evidence_root / "quarantine/quarantine_report.json",
        label="quarantine report",
    )
    packaged_chat_path = _regular_file(
        evidence_root / "promoted_chat_smoke.json",
        label="packaged promoted-chat evidence",
    )
    promotion = _load_json(promotion_path, label="promotion evidence")
    quarantine_manifest = _load_json(quarantine_manifest_path, label="quarantine manifest")
    quarantine_report = _load_json(quarantine_report_path, label="quarantine report")
    packaged_chat = _load_json(packaged_chat_path, label="packaged promoted-chat evidence")
    checks: list[dict[str, Any]] = []

    boundary = promotion.get("evidence_boundary")
    _check(checks, "promotion_schema", promotion.get("schema_version") == 1, "schema_version=1")
    _check(
        checks,
        "promotion_boundary",
        isinstance(boundary, dict)
        and promotion.get("adopted") is True
        and promotion.get("lifecycle_action") == "adopt"
        and boundary.get("actual_codex_provider_run") is True
        and boundary.get("model_authored_candidate") is True
        and boundary.get("copy_on_write_promoted") is True
        and boundary.get("live_library_mutated") is False
        and boundary.get("provider_native_skill_invocation") is False
        and boundary.get("full_benchmark_claim") is False,
        "adopted COW candidate with provider-native/full-benchmark claims disabled",
    )
    gates = promotion.get("gates")
    gate_names = tuple(item.get("name") for item in gates) if isinstance(gates, list) else ()
    _check(
        checks,
        "exact_gate_denominator",
        gate_names == EXPECTED_GATE_NAMES
        and all(isinstance(item, dict) and item.get("passed") is True for item in gates),
        "12/12 ordered quarantine and G0-G6 gates passed",
    )

    generator = promotion.get("generator")
    if not isinstance(generator, dict):
        raise ModelAuthoredSkillChainAuditError("promotion generator record is missing")
    raw_bytes = authoring_raw.read_bytes()
    _check(
        checks,
        "authoring_raw_trace_hash",
        _sha256_bytes(raw_bytes) == generator.get("raw_trace_sha256"),
        "retained authoring JSONL matches the safe generator record",
    )
    try:
        raw_text = raw_bytes.decode("utf-8")
        summary = parse_codex_exec_jsonl(raw_text)
        item_types = _provider_item_types(raw_text)
    except (UnicodeError, CodexCliAdapterError, ModelCandidateGeneratorError) as exc:
        raise ModelAuthoredSkillChainAuditError(str(exc)) from exc
    raw_response = summary.final_message
    if not isinstance(raw_response, str) or not raw_response:
        raise ModelAuthoredSkillChainAuditError("authoring raw trace has no final model response")
    _check(
        checks,
        "authoring_provider_events",
        summary.event_count == generator.get("event_count")
        and list(item_types) == generator.get("item_types")
        and not (set(item_types) - {"agent_message", "reasoning"}),
        "event denominator matches and no provider tool item occurred",
    )
    _check(
        checks,
        "authoring_model_contract",
        generator.get("requested_model_id") == boundary.get("requested_model_id")
        and generator.get("provider_reported_model_ids") == boundary.get("provider_reported_model_ids")
        and generator.get("model_evidence_level") == boundary.get("model_evidence_level")
        and isinstance(summary.thread_id, str)
        and bool(summary.thread_id)
        and generator.get("thread_id") == summary.thread_id,
        "requested-model contract and provider-reported-ID boundary match the raw run",
    )
    _check(
        checks,
        "authoring_prompt_hash",
        _sha256_bytes(generator_prompt().encode("utf-8")) == generator.get("prompt_sha256"),
        "current frozen target-only authoring prompt matches the recorded prompt hash",
    )
    _check(
        checks,
        "authoring_response_hash",
        _sha256_bytes(raw_response.encode("utf-8")) == generator.get("response_sha256"),
        "raw agent message matches the recorded candidate response hash",
    )

    try:
        envelope = parse_model_candidate_response(
            raw_response=raw_response,
            generator_backend=quarantine_manifest.get("generator_backend"),
            generator_model=quarantine_manifest.get("generator_model"),
            generator_effort=quarantine_manifest.get("generator_effort"),
            generator_prompt_sha256=quarantine_manifest.get("generator_prompt_sha256"),
            generator_provider_reported_model_ids=tuple(
                quarantine_manifest.get("generator_provider_reported_model_ids", [])
            ),
            generator_cli_version=quarantine_manifest.get("generator_cli_version"),
            generator_raw_trace_sha256=quarantine_manifest.get("generator_raw_trace_sha256"),
            generator_thread_id=quarantine_manifest.get("generator_thread_id"),
            generator_turn_id=quarantine_manifest.get("generator_turn_id"),
        )
    except ModelCandidateQuarantineError as exc:
        raise ModelAuthoredSkillChainAuditError(str(exc)) from exc
    _check(
        checks,
        "response_to_quarantine",
        envelope.generator_response_sha256 == quarantine_manifest.get("generator_response_sha256")
        and _candidate_records_from_response(raw_response) == quarantine_manifest.get("files"),
        "raw model response file bytes exactly match the quarantine manifest",
    )
    _check(
        checks,
        "quarantine_manifest_integrity",
        quarantine_manifest.get("manifest_sha256") == _manifest_body_sha256(quarantine_manifest)
        and quarantine_manifest.get("manifest_sha256")
        == promotion.get("quarantine", {}).get("manifest_sha256")
        == promotion.get("isolated_execution", {}).get("quarantine_manifest_sha256")
        == quarantine_report.get("manifest_sha256"),
        "quarantine, execution, report, and promotion share one semantic manifest hash",
    )
    candidate_root = evidence_root / "quarantine/candidate" / CANDIDATE_ID
    for record in quarantine_manifest["files"]:
        file_path = _regular_file(candidate_root / record["path"], label="quarantined candidate file")
        payload = file_path.read_bytes()
        if len(payload) != record["bytes"] or _sha256_bytes(payload) != record["sha256"]:
            raise ModelAuthoredSkillChainAuditError("quarantined candidate file bytes drifted")
    _check(checks, "quarantine_files", True, "all three quarantined candidate files match their records")

    positive_cases = tuple(
        CandidateExecutionCase(
            case_id=case.id,
            split=case.split,
            input_files=case.input_files,
            expected_files=case.expected_files,
        )
        for case in frozen_cases()
        if case.should_trigger
    )
    try:
        with tempfile.TemporaryDirectory(prefix="merlin-chain-audit-") as temporary:
            rerun = run_quarantined_candidate(
                quarantine_root=evidence_root / "quarantine",
                expected_manifest_sha256=quarantine_manifest["manifest_sha256"],
                cases=positive_cases,
                output_root=Path(temporary) / "isolated-rerun",
            )
    except (OSError, IsolatedCandidateRunnerError) as exc:
        raise ModelAuthoredSkillChainAuditError(str(exc)) from exc
    target_rows = [item for item in rerun.cases if item.split == "target"]
    hidden_rows = [item for item in rerun.cases if item.split == "held_out"]
    _check(
        checks,
        "fresh_isolated_target_hidden",
        len(target_rows) == 2
        and len(hidden_rows) == 1
        and all(item.passed for item in (*target_rows, *hidden_rows))
        and rerun.evidence_boundary["network_allowed"] is False
        and rerun.evidence_boundary["expected_outputs_visible_to_candidate"] is False,
        "fresh immutable rerun passed target 2/2 and hidden 1/1 with network/expected-output denial",
    )

    with tempfile.TemporaryDirectory(prefix="merlin-overlay-audit-") as temporary:
        overlay, overlay_summary = load_verified_promotion_overlay(
            base_library=FileSkillLibrary(MVP_ROOT / "skills"),
            evidence_path=promotion_path,
            overlay_root=Path(temporary) / "library-overlay",
        )
        overlay_skills = tuple(overlay.list())
        candidate = next((item for item in overlay_skills if item.id == CANDIDATE_ID), None)
        negative_cases = [item for item in frozen_cases() if not item.should_trigger]
        negative_passed = all(
            GovernedProvisioner(exposure_budget=1).decide(case.prompt, overlay_skills).primary_id
            != CANDIDATE_ID
            for case in negative_cases
        )
        _check(
            checks,
            "promotion_overlay_and_negative_routes",
            candidate is not None
            and candidate.status is LifecycleStatus.ACTIVE
            and overlay_summary.get("candidate_bundle_manifest_sha256")
            == quarantine_manifest["manifest_sha256"]
            and negative_passed,
            "hash-bound overlay stages one active candidate and preserves 2/2 negative routes",
        )

    with tempfile.TemporaryDirectory(prefix="merlin-chat-audit-") as temporary:
        regenerated_chat = package_promoted_chat_smoke(
            workspace=chat_workspace,
            session_root=chat_session,
            promotion_evidence_path=promotion_path,
            output_path=Path(temporary) / "promoted-chat-safe.json",
        )
    _check(
        checks,
        "promoted_chat_reproduction",
        regenerated_chat == packaged_chat,
        "retained raw chat trace, routing metadata, output, and verifier reproduce the packaged summary",
    )
    _check(
        checks,
        "promotion_to_chat_binding",
        packaged_chat.get("promotion_evidence_sha256") == _sha256_bytes(promotion_path.read_bytes())
        and packaged_chat.get("quarantine_manifest_sha256") == quarantine_manifest["manifest_sha256"]
        and packaged_chat.get("trace_observation", {}).get(
            "successful_promoted_script_execution_count"
        )
        == 1
        and packaged_chat.get("verifier", {}).get("passed") is True,
        "recorded chat is bound to the exact promotion/quarantine and one successful script execution",
    )

    report = {
        "schema_version": 1,
        "audit_id": "requested-gpt56-model-authored-skill-chain-v1",
        "status": "passed",
        "candidate_skill_id": CANDIDATE_ID,
        "source_hashes": {
            "promotion_evidence_file_sha256": _sha256_bytes(promotion_path.read_bytes()),
            "provisional_library_file_sha256": _sha256_bytes(provisional_path.read_bytes()),
            "quarantine_manifest_file_sha256": _sha256_bytes(quarantine_manifest_path.read_bytes()),
            "quarantine_manifest_semantic_sha256": quarantine_manifest["manifest_sha256"],
            "quarantine_report_file_sha256": _sha256_bytes(quarantine_report_path.read_bytes()),
            "authoring_raw_trace_sha256": _sha256_bytes(raw_bytes),
            "authoring_response_sha256": generator["response_sha256"],
            "packaged_promoted_chat_file_sha256": _sha256_bytes(packaged_chat_path.read_bytes()),
            "promoted_chat_raw_trace_sha256": packaged_chat["provider"]["raw_trace_sha256"],
        },
        "provider_contract": {
            "backend": quarantine_manifest["generator_backend"],
            "cli_version": quarantine_manifest["generator_cli_version"],
            "requested_model_id": generator["requested_model_id"],
            "requested_effort": generator["effort"],
            "provider_reported_model_ids": generator["provider_reported_model_ids"],
            "model_evidence_level": generator["model_evidence_level"],
            "authoring_event_count": summary.event_count,
            "authoring_item_types": list(item_types),
            "authoring_thread_id_sha256": _sha256_bytes(summary.thread_id.encode("utf-8")),
        },
        "fresh_revalidation": {
            "target_passed": [sum(item.passed for item in target_rows), len(target_rows)],
            "hidden_held_out_passed": [
                sum(item.passed for item in hidden_rows),
                len(hidden_rows),
            ],
            "negative_routes_passed": [2, 2],
            "promotion_gates_passed": [12, 12],
            "promoted_chat_script_executions": 1,
            "promoted_chat_verifier_passed": True,
        },
        "checks": checks,
        "claim_boundary": {
            "raw_provider_text_included": False,
            "raw_command_text_included": False,
            "provider_thread_id_included": False,
            "absolute_local_paths_included": False,
            "requested_model_is_provider_resolved_model": bool(
                generator["provider_reported_model_ids"]
            ),
            "provider_native_skill_invocation": False,
            "full_benchmark_result": False,
            "audit_is_new_model_execution": False,
            "fresh_candidate_isolated_reexecution": True,
        },
    }
    report["audit_sha256"] = content_sha256(report)
    validate_model_authored_skill_chain_audit(report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise ModelAuthoredSkillChainAuditError("audit output must be new") from exc
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--authoring-raw-trace", type=Path, required=True)
    parser.add_argument("--promoted-chat-workspace", type=Path, required=True)
    parser.add_argument("--promoted-chat-session-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_model_authored_skill_chain(
            evidence_root=args.evidence_root,
            authoring_raw_trace_path=args.authoring_raw_trace,
            promoted_chat_workspace=args.promoted_chat_workspace,
            promoted_chat_session_root=args.promoted_chat_session_root,
            output_path=args.output,
        )
    except ModelAuthoredSkillChainAuditError as exc:
        parser.error(str(exc))
    print("Merlin model-authored skill evidence-chain audit")
    print(f"status={report['status']}")
    print(f"checks={len(report['checks'])}/{len(report['checks'])}")
    print("target=2/2 hidden=1/1 negative=2/2 promoted_chat=passed")
    print(f"audit_sha256={report['audit_sha256']}")
    print(f"saved -> {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
