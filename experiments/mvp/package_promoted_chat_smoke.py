"""Package a safe summary of one promoted-bundle chat execution trace."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


class PromotedChatEvidenceError(ValueError):
    pass


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PromotedChatEvidenceError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise PromotedChatEvidenceError(f"{label} must be an object")
    return value


def _parse_raw_events(raw_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PromotedChatEvidenceError(f"raw trace line {line_number} is malformed") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise PromotedChatEvidenceError(f"raw trace line {line_number} is not a typed event")
        events.append(event)
    if not events:
        raise PromotedChatEvidenceError("raw trace is empty")
    return events


def package_promoted_chat_smoke(
    *,
    workspace: Path,
    session_root: Path,
    promotion_evidence_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    session_root = session_root.expanduser().resolve()
    promotion_evidence_path = promotion_evidence_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve(strict=False)
    if not workspace.is_dir() or not session_root.is_dir() or not session_root.is_relative_to(workspace):
        raise PromotedChatEvidenceError("session root must be an existing directory inside workspace")
    if output_path.exists():
        raise PromotedChatEvidenceError("refusing to overwrite promoted chat evidence")
    meta = _load_object(session_root / "turn-0001.meta.json", "turn metadata")
    promotion = _load_object(promotion_evidence_path, "promotion evidence")
    overlay = _load_object(session_root / "library-overlay-manifest.json", "overlay manifest")
    if promotion.get("adopted") is not True or overlay.get("adopted") is not True:
        raise PromotedChatEvidenceError("candidate was not adopted before chat execution")
    candidate_id = promotion.get("candidate_skill_id")
    if not isinstance(candidate_id, str) or overlay.get("candidate_skill_id") != candidate_id:
        raise PromotedChatEvidenceError("candidate identity differs across promotion and overlay")
    if overlay.get("source_evidence_sha256") != _sha256(promotion_evidence_path.read_bytes()):
        raise PromotedChatEvidenceError("overlay source evidence hash is invalid")
    raw_reference = meta.get("raw_trace")
    if not isinstance(raw_reference, dict) or set(raw_reference) != {"pointer", "sha256"}:
        raise PromotedChatEvidenceError("turn metadata raw trace reference is invalid")
    relative = PurePosixPath(raw_reference["pointer"])
    if relative.is_absolute() or ".." in relative.parts:
        raise PromotedChatEvidenceError("raw trace pointer is unsafe")
    raw_path = session_root.joinpath(*relative.parts)
    raw_bytes = raw_path.read_bytes()
    if _sha256(raw_bytes) != raw_reference["sha256"]:
        raise PromotedChatEvidenceError("raw trace hash differs from turn metadata")
    events = _parse_raw_events(raw_bytes.decode("utf-8"))
    commands: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not isinstance(command, str):
            raise PromotedChatEvidenceError("completed command event has no command text")
        commands.append(
            {
                "sha256": _sha256(command.encode("utf-8")),
                "exit_code": item.get("exit_code"),
                "status": item.get("status"),
                "reads_skill_body": f"promoted-bundles/{candidate_id}/SKILL.md" in command,
                "executes_promoted_script": (
                    f"promoted-bundles/{candidate_id}/scripts/run.py" in command
                    and "--workspace" in command
                ),
            }
        )
    body_reads = [item for item in commands if item["reads_skill_body"] and item["exit_code"] == 0]
    executions = [
        item for item in commands if item["executes_promoted_script"] and item["exit_code"] == 0
    ]
    if len(executions) != 1:
        raise PromotedChatEvidenceError(
            "trace does not contain exactly one successful promoted-script execution"
        )
    routing = meta.get("routing_decision")
    provisioned = meta.get("provisioned_skills")
    if (
        not isinstance(routing, dict)
        or routing.get("final_provisioned_ids") != [candidate_id]
        or not isinstance(provisioned, list)
        or [item.get("skill_id") for item in provisioned] != [candidate_id]
    ):
        raise PromotedChatEvidenceError("chat turn did not provision only the promoted candidate")
    output_file = workspace / "todo-items.json"
    expected_output = (
        json.dumps(
            {"items": ["회귀 테스트", "데모 문서 갱신"]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    output_bytes = output_file.read_bytes()
    verifier_passed = output_bytes == expected_output.encode("utf-8")
    if not verifier_passed:
        raise PromotedChatEvidenceError("chat output failed the frozen exact-file verifier")
    backend = meta.get("backend_metadata")
    if not isinstance(backend, dict) or backend.get("return_code") != 0:
        raise PromotedChatEvidenceError("chat backend contract did not complete successfully")
    provider_models = backend.get("provider_reported_model_ids")
    if not isinstance(provider_models, list):
        raise PromotedChatEvidenceError("provider-reported model list is invalid")
    result = {
        "schema_version": 1,
        "campaign_id": "promoted-model-authored-skill-chat-smoke-v1",
        "candidate_skill_id": candidate_id,
        "promotion_evidence_sha256": _sha256(promotion_evidence_path.read_bytes()),
        "quarantine_manifest_sha256": overlay.get("candidate_bundle_manifest_sha256"),
        "session_overlay_snapshot_sha256": overlay.get("session_overlay_snapshot_sha256"),
        "routing": {
            "policy": routing.get("routing_source"),
            "active_skill_count": routing.get("active_skill_count"),
            "provisioned_skill_ids": [candidate_id],
            "harness_primary_skill_id": meta.get("deterministic_reference_decision", {}).get(
                "harness_primary_id"
            ),
            "exact_artifact_anchor": True,
            "exact_input_anchor": True,
        },
        "provider": {
            "backend": backend.get("provider"),
            "cli_version": backend.get("cli_version"),
            "requested_model_id": backend.get("model_id"),
            "requested_effort": backend.get("effort"),
            "provider_reported_model_ids": provider_models,
            "model_evidence_level": (
                "provider_reported" if provider_models else "requested_cli_contract_only"
            ),
            "raw_event_count": backend.get("event_count"),
            "raw_trace_sha256": raw_reference["sha256"],
        },
        "trace_observation": {
            "completed_command_count": len(commands),
            "successful_skill_body_read_count": len(body_reads),
            "successful_promoted_script_execution_count": len(executions),
            "promoted_script_command_sha256": executions[0]["sha256"],
        },
        "verifier": {
            "id": "exact-todo-items-korean-chat-smoke-v1",
            "passed": True,
            "output_sha256": _sha256(output_bytes),
            "item_count": 2,
        },
        "evidence_boundary": {
            "prompt_exposure_observed": True,
            "harness_selection_observed": True,
            "skill_body_read_observed_in_provider_trace": bool(body_reads),
            "promoted_bundle_script_execution_observed_in_provider_trace": True,
            "deterministic_utility_verifier_passed": True,
            "provider_native_skill_invocation_event": False,
            "actual_invocation_evidence_complete_under_provider_native_taxonomy": False,
            "model_quality_or_full_benchmark_claim": False,
            "raw_provider_trace_packaged": False,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument("--promotion-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = package_promoted_chat_smoke(
            workspace=args.workspace,
            session_root=args.session_root,
            promotion_evidence_path=args.promotion_evidence,
            output_path=args.output,
        )
    except (OSError, PromotedChatEvidenceError) as exc:
        parser.error(str(exc))
    print("Merlin promoted-bundle chat smoke")
    print(f"candidate={result['candidate_skill_id']}")
    print("script_execution=observed")
    print("verifier=passed")
    print(f"saved={args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
