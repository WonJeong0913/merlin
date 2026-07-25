"""Compose a fail-closed one-cell executor capability from observed evidence.

The model-free preflight and the live non-benchmark boundary canary intentionally
remain separate artifacts.  This module binds both of them, plus one currently
running inspected Docker container, into the schema-v3 capability consumed by
M3-K.  Passing this gate authorizes exactly the first pilot cell.  It never
authorizes the remaining five cells or represents the canary as benchmark
utility evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from experiments.skillsbench.probe_codex_mcp_capability import (
    NATIVE_TOOL_FEATURES_TO_DISABLE,
)
from src.merlin_harness.management import content_sha256


class ExecutorCapabilityCompositionError(ValueError):
    """Raised when source evidence cannot authorize a first pilot cell."""


_SHA256_ALPHABET = frozenset("0123456789abcdef")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_ALPHABET for character in value)
    ):
        raise ExecutorCapabilityCompositionError(f"{label} must be a lowercase SHA-256")
    return value


def _load_json(path: Path, *, label: str) -> tuple[Path, bytes, Any]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ExecutorCapabilityCompositionError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        raw = resolved.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutorCapabilityCompositionError(f"cannot read {label}") from exc
    if not resolved.is_file():
        raise ExecutorCapabilityCompositionError(f"{label} must be a regular file")
    return resolved, raw, value


def _validate_preflight(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 2
        or value.get("diagnostic") != "codex_mcp_capability"
    ):
        raise ExecutorCapabilityCompositionError("preflight schema is unsupported")
    cli = value.get("codex_cli")
    direct = value.get("direct_mcp_server")
    suppression = value.get("native_tool_feature_suppression")
    if not all(isinstance(section, dict) for section in (cli, direct, suppression)):
        raise ExecutorCapabilityCompositionError("preflight sections are missing")
    flags = cli.get("capability_flags")
    if not isinstance(flags, dict):
        raise ExecutorCapabilityCompositionError("preflight CLI flags are missing")
    required_flags = (
        "per_run_config_override",
        "strict_config",
        "ignore_user_config",
        "ignore_rules",
        "ephemeral",
        "json_events",
    )
    failed = [name for name in required_flags if flags.get(name) is not True]
    if failed:
        raise ExecutorCapabilityCompositionError(
            "preflight lacks required CLI controls: " + ",".join(failed)
        )
    if (
        direct.get("passed") is not True
        or direct.get("tool_count") != 1
        or direct.get("tool_names") != ["exec"]
        or direct.get("tool_argument_names") != ["command", "timeout_sec"]
        or direct.get("boundary_override_arguments_exposed") is not False
        or direct.get("tools_call_performed") is not False
    ):
        raise ExecutorCapabilityCompositionError("preflight MCP surface is not one fixed exec tool")
    if (
        suppression.get("all_requested_features_disabled") is not True
        or suppression.get("requested_disabled_features")
        != list(NATIVE_TOOL_FEATURES_TO_DISABLE)
        or suppression.get("observed_disabled_features")
        != list(NATIVE_TOOL_FEATURES_TO_DISABLE)
    ):
        raise ExecutorCapabilityCompositionError("preflight feature suppression is incomplete")
    return {
        "version": cli.get("version"),
        "version_sha256": _require_sha256(
            cli.get("version_sha256"), label="preflight Codex version"
        ),
        "capability_flags": {name: True for name in required_flags},
        "tool_count": 1,
        "tool_names": ["exec"],
        "tool_argument_names": ["command", "timeout_sec"],
        "boundary_override_arguments_exposed": False,
        "disabled_tool_features": list(NATIVE_TOOL_FEATURES_TO_DISABLE),
        "disabled_tool_features_sha256": content_sha256(
            list(NATIVE_TOOL_FEATURES_TO_DISABLE)
        ),
        "features_list_sha256": _require_sha256(
            suppression.get("features_list_sha256"),
            label="preflight feature listing",
        ),
    }


def _validate_canary(
    value: Any, *, expected_model: str, expected_effort: str
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("diagnostic") != "codex_mcp_only_boundary_canary"
        or value.get("status") != "passed"
    ):
        raise ExecutorCapabilityCompositionError("boundary canary schema/status is invalid")
    stored_hash = value.get("diagnostic_sha256")
    unhashed = dict(value)
    unhashed.pop("diagnostic_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise ExecutorCapabilityCompositionError("boundary canary semantic hash mismatch")
    if (
        value.get("requested_model_id") != expected_model
        or value.get("requested_effort") != expected_effort
    ):
        raise ExecutorCapabilityCompositionError("boundary canary model contract differs")
    suppression = value.get("feature_suppression")
    runtime = value.get("runtime_observation")
    boundary = value.get("claim_boundary")
    sources = value.get("source_hashes")
    if not all(
        isinstance(section, dict)
        for section in (suppression, runtime, boundary, sources)
    ):
        raise ExecutorCapabilityCompositionError("boundary canary sections are missing")
    if (
        suppression.get("requested_count") != len(NATIVE_TOOL_FEATURES_TO_DISABLE)
        or suppression.get("observed_disabled_count")
        != len(NATIVE_TOOL_FEATURES_TO_DISABLE)
        or suppression.get("all_requested_features_disabled") is not True
    ):
        raise ExecutorCapabilityCompositionError("boundary canary feature suppression failed")
    if (
        runtime.get("mcp_initialize_observed") is not True
        or runtime.get("mcp_tools_list_observed") is not True
        or runtime.get("mcp_tool_count") != 1
        or runtime.get("mcp_exec_call_count") != 1
        or runtime.get("forbidden_native_tool_item_types") != []
    ):
        raise ExecutorCapabilityCompositionError("boundary canary runtime observation failed")
    if (
        boundary.get("this_is_model_execution") is not True
        or boundary.get("this_is_benchmark_execution") is not False
        or boundary.get("this_is_task_utility_evidence") is not False
        or boundary.get("native_tool_execution_observed") is not False
        or boundary.get("external_fixed_container_is_required_boundary") is not True
        or boundary.get("six_cell_execution_allowed") is not False
    ):
        raise ExecutorCapabilityCompositionError("boundary canary claim boundary is unsafe")
    for name, digest in sources.items():
        _require_sha256(digest, label=f"boundary canary source {name}")
    reported = value.get("provider_reported_model_ids")
    if (
        not isinstance(reported, list)
        or any(not isinstance(item, str) or not item for item in reported)
        or len(reported) != len(set(reported))
    ):
        raise ExecutorCapabilityCompositionError("provider-reported model IDs are invalid")
    if reported and expected_model not in reported:
        raise ExecutorCapabilityCompositionError("provider-reported model differs from request")
    return {
        "diagnostic_sha256": stored_hash,
        "requested_model_id": expected_model,
        "requested_effort": expected_effort,
        "provider_reported_model_ids": list(reported),
        "model_evidence_level": value.get("model_evidence_level"),
        "mcp_tool_count": 1,
        "mcp_exec_call_count": 1,
        "forbidden_native_tool_item_types": [],
        "all_requested_features_disabled": True,
        "this_is_model_execution": True,
        "this_is_benchmark_execution": False,
    }


def _validate_container_inspect(value: Any) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ExecutorCapabilityCompositionError("container inspect must contain one row")
    row = value[0]
    container_id = row.get("Id")
    image_id = row.get("Image")
    state = row.get("State")
    if not isinstance(container_id, str) or not container_id:
        raise ExecutorCapabilityCompositionError("container inspect has no container ID")
    if not isinstance(image_id, str) or not image_id:
        raise ExecutorCapabilityCompositionError("container inspect has no image ID")
    if not isinstance(state, dict) or state.get("Running") is not True:
        raise ExecutorCapabilityCompositionError("inspected container is not running")
    return {
        "container_id_sha256": _sha256(container_id.encode("utf-8")),
        "image_id_sha256": _sha256(image_id.encode("utf-8")),
        "container_id_provided": True,
        "container_inspect_passed": True,
        "container_running": True,
    }


def compose_executor_capability(
    *,
    preflight_path: Path,
    boundary_canary_path: Path,
    container_inspect_path: Path,
    requested_model_id: str,
    requested_effort: str,
) -> dict[str, Any]:
    """Revalidate three source artifacts and return schema-v3 capability."""

    _preflight_file, preflight_raw, preflight = _load_json(
        preflight_path, label="model-free capability preflight"
    )
    _canary_file, canary_raw, canary = _load_json(
        boundary_canary_path, label="live boundary canary"
    )
    _container_file, container_raw, container = _load_json(
        container_inspect_path, label="container inspect"
    )
    preflight_summary = _validate_preflight(preflight)
    canary_summary = _validate_canary(
        canary,
        expected_model=requested_model_id,
        expected_effort=requested_effort,
    )
    container_summary = _validate_container_inspect(container)
    checks = {
        "mcp_server_ready": True,
        "per_run_mcp_config_available": True,
        "strict_config_available": True,
        "user_config_suppression_available": True,
        "rules_suppression_available": True,
        "ephemeral_json_controls_available": True,
        "all_tool_bearing_features_disabled": True,
        "boundary_canary_model_mcp_exec_observed": True,
        "boundary_canary_single_exec_tool_surface": True,
        "boundary_canary_forbidden_native_items_absent": True,
        "boundary_canary_requested_model_contract_match": True,
        "inspected_container_runtime": True,
    }
    report = {
        "schema_version": 3,
        "diagnostic": "codex_mcp_capability",
        "scope": "one-cell executor admission composed from preflight, non-benchmark model canary, and inspected container",
        "sources": {
            "preflight_file_sha256": _sha256(preflight_raw),
            "boundary_canary_file_sha256": _sha256(canary_raw),
            "container_inspect_file_sha256": _sha256(container_raw),
        },
        "requested_model_contract": {
            "model_id": requested_model_id,
            "effort": requested_effort,
        },
        "codex_cli": preflight_summary,
        "direct_mcp_server": {
            "tool_count": preflight_summary["tool_count"],
            "tool_names": preflight_summary["tool_names"],
            "tool_argument_names": preflight_summary["tool_argument_names"],
            "boundary_override_arguments_exposed": False,
        },
        "native_tool_feature_suppression": {
            "disabled_tool_features": preflight_summary["disabled_tool_features"],
            "disabled_tool_features_sha256": preflight_summary[
                "disabled_tool_features_sha256"
            ],
            "all_requested_features_disabled": True,
        },
        "boundary_canary": canary_summary,
        "container_runtime": container_summary,
        "readiness": {
            "checks": checks,
            "strict_benchmark_bridge_eligible": True,
            "one_cell_execution_allowed": True,
            "six_cell_execution_allowed": False,
            "additional_pilot_cells_require_validated_first_cell": True,
            "failed_required_checks": [],
            "this_report_is_model_execution": False,
            "boundary_canary_is_model_execution": True,
            "boundary_canary_is_benchmark_result": False,
            "handshake_only_is_benchmark_evidence": False,
        },
        "claim_boundary": {
            "capability_composition_is_model_execution": False,
            "capability_is_benchmark_result": False,
            "provider_resolved_model_identity_claimed": bool(
                canary_summary["provider_reported_model_ids"]
            ),
            "provider_native_skill_invocation_claimed": False,
            "first_cell_utility_claimed": False,
            "six_cell_completion_claimed": False,
        },
    }
    report["capability_sha256"] = content_sha256(report)
    return report


def write_executor_capability(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise ExecutorCapabilityCompositionError("capability output must be new-only")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise ExecutorCapabilityCompositionError("capability output must be new-only") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--boundary-canary", type=Path, required=True)
    parser.add_argument("--container-inspect", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = compose_executor_capability(
            preflight_path=args.preflight,
            boundary_canary_path=args.boundary_canary,
            container_inspect_path=args.container_inspect,
            requested_model_id=args.model,
            requested_effort=args.effort,
        )
        write_executor_capability(args.output, report)
    except (ExecutorCapabilityCompositionError, OSError) as exc:
        parser.error(str(exc))
    print("Merlin Codex MCP executor capability")
    print("one_cell_execution_allowed=true")
    print("six_cell_execution_allowed=false")
    print(f"capability_sha256={report['capability_sha256']}")
    print(f"saved -> {args.output.expanduser().resolve(strict=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
