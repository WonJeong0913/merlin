"""Bind a bounded harness proposal and strict executor capability to M3-K."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from experiments.skillsbench.create_m3k_evaluation_manifest import (
    M3KManifestError,
    validate_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import (
    LibraryScaleManifestError,
    sha256_file,
    sha256_json,
    validate_library_scale_manifest,
)
from src.merlin_harness.harness import (
    HarnessEvolutionProposal,
    HarnessVariantSpec,
    build_runtime_from_variant,
)
from src.merlin_harness.management import content_sha256
from experiments.skillsbench.probe_codex_mcp_capability import (
    NATIVE_TOOL_FEATURES_TO_DISABLE,
)
from experiments.skillsbench.m3k_policy_proposal import (
    BUNDLE_CONTRACT,
    M3KPolicyProposalError,
    validate_canonical_bundle,
)


class M3KProposalBindingError(ValueError):
    pass


REQUIRED_CAPABILITY_CHECKS = (
    "container_runtime_ready",
    "ephemeral_json_read_only_controls_available",
    "mcp_server_ready",
    "model_exec_tool_call_observed",
    "native_tool_allowlist_available",
    "native_tool_denylist_available",
    "per_run_mcp_config_available",
    "rules_suppression_available",
    "strict_mcp_config_available",
    "user_config_suppression_available",
)
V3_REQUIRED_CAPABILITY_CHECKS = (
    "mcp_server_ready",
    "per_run_mcp_config_available",
    "strict_config_available",
    "user_config_suppression_available",
    "rules_suppression_available",
    "ephemeral_json_controls_available",
    "all_tool_bearing_features_disabled",
    "boundary_canary_model_mcp_exec_observed",
    "boundary_canary_single_exec_tool_surface",
    "boundary_canary_forbidden_native_items_absent",
    "boundary_canary_requested_model_contract_match",
    "inspected_container_runtime",
)
LIBRARY_ARM_ID = "full-209"
LIBRARY_TRAJECTORY_FIELDS = (
    "library_arm_id",
    "library_size",
    "library_snapshot_sha256",
    "library_variant_ids",
    "library_order_sha256",
    "library_trial_seed",
)
_SHA256_ALPHABET = frozenset("0123456789abcdef")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_ALPHABET for character in value)
    ):
        raise M3KProposalBindingError(f"{label} must be a lowercase SHA-256")
    return value


def _load_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KProposalBindingError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise M3KProposalBindingError(f"{label} must be a JSON object")
    return value, raw


def _variant(value: Any, *, label: str) -> HarnessVariantSpec:
    if not isinstance(value, dict) or set(value) != {
        "id",
        "parent_id",
        "summary",
        "processor_manifest",
        "policy",
        "metadata",
    }:
        raise M3KProposalBindingError(f"{label} schema is invalid")
    if not isinstance(value["processor_manifest"], dict):
        raise M3KProposalBindingError(f"{label}.processor_manifest must be an object")
    if not isinstance(value["policy"], dict) or not isinstance(value["metadata"], dict):
        raise M3KProposalBindingError(f"{label} policy/metadata must be objects")
    return HarnessVariantSpec(
        id=value["id"],
        parent_id=value["parent_id"],
        summary=value["summary"],
        processor_manifest=value["processor_manifest"],
        policy=value["policy"],
        metadata=value["metadata"],
    )


def load_proposal_bundle(payload: dict[str, Any]) -> tuple[HarnessVariantSpec, HarnessEvolutionProposal]:
    schema_version = payload.get("schema_version")
    schema_keys = {
        1: {"schema_version", "parent_variant", "proposal"},
        2: {
            "schema_version",
            "bundle_contract",
            "parent_variant",
            "proposal",
            "construction_evidence",
            "candidate_hypothesis",
            "claim_boundary",
        },
    }
    if schema_version not in schema_keys or set(payload) != schema_keys[schema_version]:
        raise M3KProposalBindingError("proposal bundle schema is invalid")
    parent = _variant(payload["parent_variant"], label="parent_variant")
    proposal_value = payload["proposal"]
    if not isinstance(proposal_value, dict) or set(proposal_value) != {
        "id",
        "parent_variant_id",
        "candidate",
        "rationale",
        "changed_hooks",
        "evidence_trace_ids",
    }:
        raise M3KProposalBindingError("proposal schema is invalid")
    candidate = _variant(proposal_value["candidate"], label="proposal.candidate")
    proposal = HarnessEvolutionProposal(
        id=proposal_value["id"],
        parent_variant_id=proposal_value["parent_variant_id"],
        candidate=candidate,
        rationale=proposal_value["rationale"],
        changed_hooks=proposal_value["changed_hooks"],
        evidence_trace_ids=proposal_value["evidence_trace_ids"],
    )
    if parent.id != proposal.parent_variant_id or candidate.parent_id != parent.id:
        raise M3KProposalBindingError("proposal parent lineage is invalid")
    if candidate.id == parent.id:
        raise M3KProposalBindingError("candidate variant must have a new ID")
    if not proposal.id or not proposal.rationale or not proposal.changed_hooks or not proposal.evidence_trace_ids:
        raise M3KProposalBindingError("proposal audit fields must be non-empty")
    try:
        build_runtime_from_variant(parent)
        build_runtime_from_variant(candidate)
    except (KeyError, TypeError, ValueError) as exc:
        raise M3KProposalBindingError(f"proposal variant is not reconstructable: {exc}") from exc
    return parent, proposal


def proposal_bundle(parent: HarnessVariantSpec, proposal: HarnessEvolutionProposal) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "parent_variant": asdict(parent),
        "proposal": {
            "id": proposal.id,
            "parent_variant_id": proposal.parent_variant_id,
            "candidate": asdict(proposal.candidate),
            "rationale": proposal.rationale,
            "changed_hooks": list(proposal.changed_hooks),
            "evidence_trace_ids": list(proposal.evidence_trace_ids),
        },
    }


def validate_executor_capability(payload: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2, 3} or payload.get("diagnostic") != "codex_mcp_capability":
        raise M3KProposalBindingError("executor capability schema is unsupported")
    if schema_version == 3:
        return _validate_v3_executor_capability(payload)
    readiness = payload.get("readiness")
    direct = payload.get("direct_mcp_server")
    container = payload.get("container_runtime")
    if not isinstance(readiness, dict) or not isinstance(direct, dict) or not isinstance(container, dict):
        raise M3KProposalBindingError("executor capability sections are missing")
    checks = readiness.get("checks")
    if not isinstance(checks, dict):
        raise M3KProposalBindingError("executor capability checks are missing")
    failed = [name for name in REQUIRED_CAPABILITY_CHECKS if checks.get(name) is not True]
    if direct.get("tool_names") != ["exec"] or direct.get("tool_count") != 1:
        failed.append("single_exec_tool_surface")
    if direct.get("boundary_override_arguments_exposed") is not False:
        failed.append("no_boundary_override_arguments")
    if readiness.get("strict_benchmark_bridge_eligible") is not True:
        failed.append("strict_benchmark_bridge_eligible")
    if readiness.get("six_cell_execution_allowed") is not True:
        failed.append("six_cell_execution_allowed")
    if readiness.get("this_probe_is_model_execution") is not False:
        failed.append("capability_probe_not_model_execution")
    if readiness.get("this_probe_is_benchmark_result") is not False:
        failed.append("capability_probe_not_benchmark_result")
    if readiness.get("handshake_only_is_benchmark_evidence") is not False:
        failed.append("handshake_not_benchmark_evidence")
    if container.get("container_id_provided") is not True or container.get("container_inspect_passed") is not True:
        failed.append("inspected_container")
    failed = list(dict.fromkeys(failed))
    safe_summary = {
        "diagnostic": payload["diagnostic"],
        "strict_benchmark_bridge_eligible": readiness.get("strict_benchmark_bridge_eligible"),
        "six_cell_execution_allowed": readiness.get("six_cell_execution_allowed"),
        "required_checks": {name: checks.get(name) for name in REQUIRED_CAPABILITY_CHECKS},
        "tool_names": direct.get("tool_names"),
        "container_id_provided": container.get("container_id_provided"),
        "container_inspect_passed": container.get("container_inspect_passed"),
    }
    return not failed, failed, safe_summary


def _validate_v3_executor_capability(
    payload: dict[str, Any],
) -> tuple[bool, list[str], dict[str, Any]]:
    """Validate the observed-control capability used by the current runner.

    Schema v3 deliberately does not require CLI flags that the current Codex
    executable does not expose.  It requires the controls the runner actually
    applies and a live non-benchmark one-MCP canary.  This gate opens one pilot
    cell only; later cells require evidence from that first cell.
    """

    stored_hash = payload.get("capability_sha256")
    unhashed = dict(payload)
    unhashed.pop("capability_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise M3KProposalBindingError("executor capability semantic hash mismatch")
    readiness = payload.get("readiness")
    direct = payload.get("direct_mcp_server")
    suppression = payload.get("native_tool_feature_suppression")
    canary = payload.get("boundary_canary")
    container = payload.get("container_runtime")
    boundary = payload.get("claim_boundary")
    requested = payload.get("requested_model_contract")
    sources = payload.get("sources")
    if not all(
        isinstance(section, dict)
        for section in (
            readiness,
            direct,
            suppression,
            canary,
            container,
            boundary,
            requested,
            sources,
        )
    ):
        raise M3KProposalBindingError("executor capability v3 sections are missing")
    checks = readiness.get("checks")
    if not isinstance(checks, dict):
        raise M3KProposalBindingError("executor capability v3 checks are missing")
    failed = [name for name in V3_REQUIRED_CAPABILITY_CHECKS if checks.get(name) is not True]
    if set(checks) != set(V3_REQUIRED_CAPABILITY_CHECKS):
        failed.append("exact_v3_required_check_set")
    if (
        direct.get("tool_names") != ["exec"]
        or direct.get("tool_count") != 1
        or direct.get("tool_argument_names") != ["command", "timeout_sec"]
    ):
        failed.append("single_exec_tool_surface")
    if direct.get("boundary_override_arguments_exposed") is not False:
        failed.append("no_boundary_override_arguments")
    disabled = list(NATIVE_TOOL_FEATURES_TO_DISABLE)
    if (
        suppression.get("disabled_tool_features") != disabled
        or suppression.get("disabled_tool_features_sha256") != content_sha256(disabled)
        or suppression.get("all_requested_features_disabled") is not True
    ):
        failed.append("exact_tool_feature_suppression")
    if (
        canary.get("requested_model_id") != requested.get("model_id")
        or not isinstance(canary.get("requested_effort"), str)
        or not canary.get("requested_effort")
        or canary.get("mcp_tool_count") != 1
        or canary.get("mcp_exec_call_count") != 1
        or canary.get("forbidden_native_tool_item_types") != []
        or canary.get("all_requested_features_disabled") is not True
        or canary.get("this_is_model_execution") is not True
        or canary.get("this_is_benchmark_execution") is not False
    ):
        failed.append("live_boundary_canary_contract")
    reported = canary.get("provider_reported_model_ids")
    if (
        not isinstance(reported, list)
        or any(not isinstance(item, str) or not item for item in reported)
        or len(reported) != len(set(reported))
        or (reported and requested.get("model_id") not in reported)
    ):
        failed.append("provider_model_boundary")
    if (
        container.get("container_id_provided") is not True
        or container.get("container_inspect_passed") is not True
        or container.get("container_running") is not True
    ):
        failed.append("inspected_container")
    for name, digest in sources.items():
        try:
            _require_sha256(digest, label=f"executor capability source {name}")
        except M3KProposalBindingError:
            failed.append("source_hashes")
            break
    if set(sources) != {
        "preflight_file_sha256",
        "boundary_canary_file_sha256",
        "container_inspect_file_sha256",
    }:
        failed.append("exact_source_hash_set")
    if readiness.get("strict_benchmark_bridge_eligible") is not True:
        failed.append("strict_benchmark_bridge_eligible")
    if readiness.get("one_cell_execution_allowed") is not True:
        failed.append("one_cell_execution_allowed")
    if readiness.get("six_cell_execution_allowed") is not False:
        failed.append("six_cell_must_require_first_cell_evidence")
    if readiness.get("additional_pilot_cells_require_validated_first_cell") is not True:
        failed.append("additional_cells_require_first_cell")
    if readiness.get("failed_required_checks") != []:
        failed.append("failed_required_checks_must_be_empty")
    if readiness.get("this_report_is_model_execution") is not False:
        failed.append("capability_composition_not_model_execution")
    if readiness.get("boundary_canary_is_model_execution") is not True:
        failed.append("boundary_canary_model_execution_explicit")
    if readiness.get("boundary_canary_is_benchmark_result") is not False:
        failed.append("boundary_canary_not_benchmark_result")
    if readiness.get("handshake_only_is_benchmark_evidence") is not False:
        failed.append("handshake_not_benchmark_evidence")
    if (
        boundary.get("capability_composition_is_model_execution") is not False
        or boundary.get("capability_is_benchmark_result") is not False
        or boundary.get("provider_native_skill_invocation_claimed") is not False
        or boundary.get("first_cell_utility_claimed") is not False
        or boundary.get("six_cell_completion_claimed") is not False
    ):
        failed.append("safe_claim_boundary")
    failed = list(dict.fromkeys(failed))
    safe_summary = {
        "diagnostic": payload["diagnostic"],
        "schema_version": 3,
        "strict_benchmark_bridge_eligible": readiness.get(
            "strict_benchmark_bridge_eligible"
        ),
        "one_cell_execution_allowed": readiness.get("one_cell_execution_allowed"),
        "six_cell_execution_allowed": readiness.get("six_cell_execution_allowed"),
        "additional_pilot_cells_require_validated_first_cell": readiness.get(
            "additional_pilot_cells_require_validated_first_cell"
        ),
        "required_checks": {
            name: checks.get(name) for name in V3_REQUIRED_CAPABILITY_CHECKS
        },
        "tool_names": direct.get("tool_names"),
        "container_id_provided": container.get("container_id_provided"),
        "container_inspect_passed": container.get("container_inspect_passed"),
        "boundary_canary_diagnostic_sha256": canary.get("diagnostic_sha256"),
    }
    return not failed, failed, safe_summary


def _library_cell_projection(cell: dict[str, Any]) -> dict[str, Any]:
    """Return the exact full-library fields bound to one M3-K trajectory."""

    variants = cell.get("library_variant_ids")
    if not isinstance(variants, list) or any(not isinstance(value, str) or not value for value in variants):
        raise M3KProposalBindingError("library full-209 cell has an invalid variant order")
    if len(variants) != len(set(variants)):
        raise M3KProposalBindingError("library full-209 cell has duplicate variants")
    library_size = cell.get("library_size")
    if isinstance(library_size, bool) or not isinstance(library_size, int) or library_size != len(variants):
        raise M3KProposalBindingError("library full-209 cell size/order is inconsistent")
    if cell.get("arm_id") != LIBRARY_ARM_ID:
        raise M3KProposalBindingError("library binding must use the full-209 arm")
    trial_index = cell.get("trial_index")
    trial_seed = cell.get("trial_seed")
    task_id = cell.get("task_id")
    if (
        not isinstance(task_id, str)
        or not task_id
        or isinstance(trial_index, bool)
        or not isinstance(trial_index, int)
        or trial_index < 1
        or isinstance(trial_seed, bool)
        or not isinstance(trial_seed, int)
    ):
        raise M3KProposalBindingError("library full-209 cell identity is invalid")
    return {
        "task_id": task_id,
        "trial_index": trial_index,
        "task_instruction_sha256": _require_sha256(
            cell.get("task_instruction_sha256"),
            label="library task_instruction_sha256",
        ),
        "verifier_contract_sha256": _require_sha256(
            cell.get("verifier_contract_sha256"),
            label="library verifier_contract_sha256",
        ),
        "library_arm_id": cell["arm_id"],
        "library_size": library_size,
        "library_snapshot_sha256": _require_sha256(
            cell.get("library_snapshot_sha256"),
            label="library_snapshot_sha256",
        ),
        "library_variant_ids": list(variants),
        "library_order_sha256": sha256_json(variants),
        "library_trial_seed": trial_seed,
    }


def _full_library_cells(library_scale_manifest: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    """Index the only permitted (full-209) library cell for every task/repeat."""

    try:
        validate_library_scale_manifest(library_scale_manifest)
    except LibraryScaleManifestError as exc:
        raise M3KProposalBindingError(f"canonical library-scale manifest is invalid: {exc}") from exc

    task_count = library_scale_manifest.get("task_count")
    skill_pool_count = library_scale_manifest.get("skill_pool_count")
    trial_indices = library_scale_manifest.get("trial_indices")
    expected_cells = library_scale_manifest.get("expected_cells")
    if (
        task_count != 87
        or skill_pool_count != 209
        or trial_indices != [1, 2, 3]
        or expected_cells != 1305
    ):
        raise M3KProposalBindingError("canonical library-scale denominator drifted")

    full_cells: dict[tuple[str, int], dict[str, Any]] = {}
    cells = library_scale_manifest.get("cells")
    if not isinstance(cells, list):
        raise M3KProposalBindingError("canonical library-scale cells are missing")
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("arm_id") != LIBRARY_ARM_ID:
            continue
        projection = _library_cell_projection(cell)
        if projection["library_size"] != skill_pool_count:
            raise M3KProposalBindingError("full-209 library cell count drifted")
        key = (projection["task_id"], projection["trial_index"])
        if key in full_cells:
            raise M3KProposalBindingError("duplicate full-209 library cell")
        full_cells[key] = projection
    if len(full_cells) != task_count * len(trial_indices):
        raise M3KProposalBindingError("canonical full-209 library coverage is incomplete")
    return full_cells


def _scheduled_pairs(
    paired_cells: Any,
    *,
    full_cells: dict[tuple[str, int], dict[str, Any]],
) -> dict[tuple[str, int], dict[str, dict[str, Any]]]:
    """Validate the M3-K two-variant schedule against full-209 cell identities."""

    if not isinstance(paired_cells, list) or len(paired_cells) != 522:
        raise M3KProposalBindingError("M3-K paired cells must contain exactly 522 trajectories")
    pairs: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    trajectory_ids: set[str] = set()
    for scheduled in paired_cells:
        if not isinstance(scheduled, dict):
            raise M3KProposalBindingError("M3-K scheduled trajectory is invalid")
        task_id = scheduled.get("task_id")
        trial_index = scheduled.get("trial_index")
        role = scheduled.get("variant_role")
        trajectory_id = scheduled.get("trajectory_id")
        if (
            not isinstance(task_id, str)
            or not task_id
            or isinstance(trial_index, bool)
            or not isinstance(trial_index, int)
            or trial_index < 1
            or role not in {"parent", "candidate"}
            or not isinstance(trajectory_id, str)
            or not trajectory_id
        ):
            raise M3KProposalBindingError("M3-K scheduled trajectory identity is invalid")
        if trajectory_id in trajectory_ids:
            raise M3KProposalBindingError("duplicate M3-K trajectory ID")
        trajectory_ids.add(trajectory_id)
        key = (task_id, trial_index)
        source = full_cells.get(key)
        if source is None:
            raise M3KProposalBindingError("M3-K trajectory has no matching full-209 library cell")
        pair = pairs.setdefault(key, {})
        if role in pair:
            raise M3KProposalBindingError("duplicate M3-K parent/candidate library pairing")
        if scheduled.get("task_instruction_sha256") != source["task_instruction_sha256"]:
            raise M3KProposalBindingError("M3-K task instruction binding drifted")
        if scheduled.get("verifier_id") != source["verifier_contract_sha256"]:
            raise M3KProposalBindingError("M3-K verifier binding drifted")
        pair[role] = scheduled
    if set(pairs) != set(full_cells):
        raise M3KProposalBindingError("M3-K schedule/full-209 cell coverage differs")
    if any(set(pair) != {"parent", "candidate"} for pair in pairs.values()):
        raise M3KProposalBindingError("every full-209 cell requires parent and candidate trajectories")
    return pairs


def _library_binding_payload(
    *,
    library_scale_manifest: dict[str, Any],
    library_scale_file_sha256: str,
    full_cells: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    projected_cells = [
        full_cells[key]
        for key in sorted(full_cells)
    ]
    binding = {
        "source_manifest_semantic_sha256": sha256_json(library_scale_manifest),
        "source_manifest_file_sha256": _require_sha256(
            library_scale_file_sha256,
            label="library_scale_file_sha256",
        ),
        "arm_id": LIBRARY_ARM_ID,
        "counts": {
            "source_task_count": library_scale_manifest["task_count"],
            "source_expected_cell_count": library_scale_manifest["expected_cells"],
            "source_skill_pool_count": library_scale_manifest["skill_pool_count"],
            "full_arm_cell_count": len(full_cells),
            "paired_trajectory_count": len(full_cells) * 2,
        },
        "full_arm_cells_sha256": sha256_json(projected_cells),
    }
    binding["content_sha256"] = sha256_json(binding)
    return binding


def _enrich_paired_cells(
    *,
    paired_cells: list[dict[str, Any]],
    pairs: dict[tuple[str, int], dict[str, dict[str, Any]]],
    full_cells: dict[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for scheduled in paired_cells:
        key = (scheduled["task_id"], scheduled["trial_index"])
        source = full_cells[key]
        item = dict(scheduled)
        item.update({field: source[field] for field in LIBRARY_TRAJECTORY_FIELDS})
        enriched.append(item)
    for key, pair in pairs.items():
        parent = next(item for item in enriched if item["trajectory_id"] == pair["parent"]["trajectory_id"])
        candidate = next(item for item in enriched if item["trajectory_id"] == pair["candidate"]["trajectory_id"])
        if any(parent[field] != candidate[field] for field in LIBRARY_TRAJECTORY_FIELDS):
            raise M3KProposalBindingError(f"parent/candidate library binding differs for {key[0]} trial {key[1]}")
    return enriched


def bind_manifest(
    *,
    schedule: dict[str, Any],
    schedule_file_sha256: str,
    library_scale_manifest: dict[str, Any],
    library_scale_file_sha256: str,
    bundle: dict[str, Any],
    bundle_file_sha256: str,
    capability: dict[str, Any] | None,
    capability_file_sha256: str | None,
) -> dict[str, Any]:
    try:
        validate_manifest(schedule)
    except M3KManifestError as exc:
        raise M3KProposalBindingError(str(exc)) from exc
    full_library_cells = _full_library_cells(library_scale_manifest)
    _require_sha256(schedule_file_sha256, label="schedule_file_sha256")
    _require_sha256(library_scale_file_sha256, label="library_scale_file_sha256")
    evaluation_contract = schedule.get("evaluation_contract")
    if not isinstance(evaluation_contract, dict):
        raise M3KProposalBindingError("M3-K evaluation contract is missing")
    if evaluation_contract.get("task_contract_source_sha256") != library_scale_file_sha256:
        raise M3KProposalBindingError("M3-K schedule is not bound to this canonical library-scale file")
    pairs = _scheduled_pairs(schedule.get("paired_cells"), full_cells=full_library_cells)
    enriched_paired_cells = _enrich_paired_cells(
        paired_cells=schedule["paired_cells"],
        pairs=pairs,
        full_cells=full_library_cells,
    )
    library_binding = _library_binding_payload(
        library_scale_manifest=library_scale_manifest,
        library_scale_file_sha256=library_scale_file_sha256,
        full_cells=full_library_cells,
    )
    try:
        validate_canonical_bundle(bundle)
    except M3KPolicyProposalError as exc:
        raise M3KProposalBindingError(
            f"M3-K execution requires the canonical pre-registered proposal: {exc}"
        ) from exc
    parent, proposal = load_proposal_bundle(bundle)
    parent_sha256 = content_sha256(parent)
    candidate_sha256 = content_sha256(proposal.candidate)
    proposal_sha256 = content_sha256(proposal)
    capability_ready = False
    capability_failures = ["strict_tool_controlled_container_executor_evidence_not_supplied"]
    capability_summary = None
    if capability is not None:
        capability_ready, capability_failures, capability_summary = validate_executor_capability(capability)
    reasons = [] if capability_ready else capability_failures
    payload = {
        **{key: value for key, value in schedule.items() if key != "manifest_sha256"},
        "schema_version": 2,
        "status": "ready" if capability_ready else "proposal_bound_not_ready",
        "source_schedule": {
            "manifest_sha256": schedule["manifest_sha256"],
            "file_sha256": schedule_file_sha256,
        },
        "library_binding": library_binding,
        "paired_cells": enriched_paired_cells,
        "proposal_binding": {
            "bundle_contract": BUNDLE_CONTRACT,
            "construction_evidence_sha256": content_sha256(
                bundle["construction_evidence"]
            ),
            "proposal_id": proposal.id,
            "proposal_sha256": proposal_sha256,
            "proposal_bundle_sha256": content_sha256(bundle),
            "proposal_bundle_file_sha256": bundle_file_sha256,
            "parent_variant_id": parent.id,
            "parent_variant_sha256": parent_sha256,
            "candidate_variant_id": proposal.candidate.id,
            "candidate_variant_sha256": candidate_sha256,
            "binding_status": "bound",
            "bundle": bundle,
        },
        "executor_capability": {
            "provided": capability is not None,
            "file_sha256": capability_file_sha256,
            "eligible": capability_ready,
            "failed_required_checks": reasons,
            "safe_summary": capability_summary,
        },
        "execution_gate": {
            "execution_allowed": capability_ready,
            "reasons": reasons,
            "required_result_contract": schedule["execution_gate"]["required_result_contract"],
        },
        "claim_boundary": {
            "schedule_only": True,
            "model_execution": False,
            "trajectory_results": False,
            "full87_result": False,
            "provider_native_invocation": False,
        },
    }
    payload["manifest_sha256"] = content_sha256(payload)
    validate_bound_manifest(payload)
    return payload


def _bound_library_projection(scheduled: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in LIBRARY_TRAJECTORY_FIELDS if field not in scheduled]
    if missing:
        raise M3KProposalBindingError(f"bound M3-K trajectory is missing library fields: {', '.join(missing)}")
    variants = scheduled.get("library_variant_ids")
    library_size = scheduled.get("library_size")
    if (
        not isinstance(variants, list)
        or any(not isinstance(value, str) or not value for value in variants)
        or len(variants) != len(set(variants))
        or isinstance(library_size, bool)
        or not isinstance(library_size, int)
        or library_size != len(variants)
    ):
        raise M3KProposalBindingError("bound M3-K library order/count is invalid")
    if scheduled.get("library_arm_id") != LIBRARY_ARM_ID:
        raise M3KProposalBindingError("bound M3-K trajectory must use the full-209 library arm")
    task_id = scheduled.get("task_id")
    trial_index = scheduled.get("trial_index")
    trial_seed = scheduled.get("library_trial_seed")
    if (
        not isinstance(task_id, str)
        or not task_id
        or isinstance(trial_index, bool)
        or not isinstance(trial_index, int)
        or trial_index < 1
        or isinstance(trial_seed, bool)
        or not isinstance(trial_seed, int)
    ):
        raise M3KProposalBindingError("bound M3-K library cell identity is invalid")
    expected_order = sha256_json(variants)
    if scheduled.get("library_order_sha256") != expected_order:
        raise M3KProposalBindingError("bound M3-K library order hash drifted")
    return {
        "task_id": task_id,
        "trial_index": trial_index,
        "task_instruction_sha256": _require_sha256(
            scheduled.get("task_instruction_sha256"),
            label="bound task_instruction_sha256",
        ),
        "verifier_contract_sha256": _require_sha256(
            scheduled.get("verifier_id"),
            label="bound verifier_id",
        ),
        "library_arm_id": scheduled["library_arm_id"],
        "library_size": library_size,
        "library_snapshot_sha256": _require_sha256(
            scheduled.get("library_snapshot_sha256"),
            label="bound library_snapshot_sha256",
        ),
        "library_variant_ids": list(variants),
        "library_order_sha256": expected_order,
        "library_trial_seed": trial_seed,
    }


def _validate_library_binding(payload: dict[str, Any]) -> None:
    binding = payload.get("library_binding")
    if not isinstance(binding, dict):
        raise M3KProposalBindingError("bound M3-K library binding is missing")
    expected_keys = {
        "source_manifest_semantic_sha256",
        "source_manifest_file_sha256",
        "arm_id",
        "counts",
        "full_arm_cells_sha256",
        "content_sha256",
    }
    if set(binding) != expected_keys:
        raise M3KProposalBindingError("bound M3-K library binding schema is invalid")
    if binding.get("arm_id") != LIBRARY_ARM_ID:
        raise M3KProposalBindingError("bound M3-K library binding arm drifted")
    for key in (
        "source_manifest_semantic_sha256",
        "source_manifest_file_sha256",
        "full_arm_cells_sha256",
        "content_sha256",
    ):
        _require_sha256(binding.get(key), label=f"library_binding.{key}")
    counts = binding.get("counts")
    expected_counts = {
        "source_task_count": 87,
        "source_expected_cell_count": 1305,
        "source_skill_pool_count": 209,
        "full_arm_cell_count": 261,
        "paired_trajectory_count": 522,
    }
    if counts != expected_counts:
        raise M3KProposalBindingError("bound M3-K library binding counts drifted")
    binding_without_content = dict(binding)
    binding_without_content.pop("content_sha256")
    if binding["content_sha256"] != sha256_json(binding_without_content):
        raise M3KProposalBindingError("bound M3-K library binding content hash drifted")

    contract = payload.get("evaluation_contract")
    if not isinstance(contract, dict) or contract.get("task_contract_source_sha256") != binding[
        "source_manifest_file_sha256"
    ]:
        raise M3KProposalBindingError("bound M3-K library file hash differs from the evaluation contract")
    paired_cells = payload.get("paired_cells")
    if not isinstance(paired_cells, list) or len(paired_cells) != counts["paired_trajectory_count"]:
        raise M3KProposalBindingError("bound M3-K library trajectory count drifted")

    pairs: dict[tuple[str, int], dict[str, tuple[dict[str, Any], dict[str, Any]]]] = {}
    trajectory_ids: set[str] = set()
    for scheduled in paired_cells:
        if not isinstance(scheduled, dict):
            raise M3KProposalBindingError("bound M3-K library trajectory is invalid")
        role = scheduled.get("variant_role")
        trajectory_id = scheduled.get("trajectory_id")
        pair_id = scheduled.get("pair_id")
        cell_id = scheduled.get("cell_id")
        if (
            role not in {"parent", "candidate"}
            or not isinstance(trajectory_id, str)
            or not trajectory_id
            or not isinstance(pair_id, str)
            or not pair_id
            or not isinstance(cell_id, str)
            or not cell_id
        ):
            raise M3KProposalBindingError("bound M3-K library trajectory identity is invalid")
        if trajectory_id in trajectory_ids:
            raise M3KProposalBindingError("bound M3-K library trajectory ID is duplicated")
        trajectory_ids.add(trajectory_id)
        projection = _bound_library_projection(scheduled)
        if projection["library_size"] != counts["source_skill_pool_count"]:
            raise M3KProposalBindingError("bound M3-K full-209 library size drifted")
        key = (projection["task_id"], projection["trial_index"])
        pair = pairs.setdefault(key, {})
        if role in pair:
            raise M3KProposalBindingError("bound M3-K library cell has a duplicate parent/candidate trajectory")
        pair[role] = (scheduled, projection)
    if len(pairs) != counts["full_arm_cell_count"]:
        raise M3KProposalBindingError("bound M3-K library cells are missing or duplicated")
    if any(set(pair) != {"parent", "candidate"} for pair in pairs.values()):
        raise M3KProposalBindingError("bound M3-K library cell lacks a parent/candidate trajectory")

    full_cells: list[dict[str, Any]] = []
    for key in sorted(pairs):
        parent, parent_projection = pairs[key]["parent"]
        candidate, candidate_projection = pairs[key]["candidate"]
        if parent["pair_id"] != candidate["pair_id"] or parent["cell_id"] != candidate["cell_id"]:
            raise M3KProposalBindingError("bound M3-K parent/candidate pair identity differs")
        if parent_projection != candidate_projection:
            raise M3KProposalBindingError("bound M3-K parent/candidate library binding differs")
        full_cells.append(parent_projection)
    if binding["full_arm_cells_sha256"] != sha256_json(full_cells):
        raise M3KProposalBindingError("bound M3-K full-209 library cell hash drifted")


def validate_bound_manifest(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 2 or payload.get("status") not in {"ready", "proposal_bound_not_ready"}:
        raise M3KProposalBindingError("bound M3-K manifest schema/status is invalid")
    stored_hash = payload.get("manifest_sha256")
    unhashed = dict(payload)
    unhashed.pop("manifest_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise M3KProposalBindingError("bound M3-K manifest hash mismatch")
    source = payload.get("source_schedule")
    binding = payload.get("proposal_binding")
    capability = payload.get("executor_capability")
    gate = payload.get("execution_gate")
    if not all(isinstance(item, dict) for item in (source, binding, capability, gate)):
        raise M3KProposalBindingError("bound M3-K manifest sections are missing")
    bundle = binding.get("bundle")
    if not isinstance(bundle, dict):
        raise M3KProposalBindingError("bound proposal bundle is missing")
    try:
        validate_canonical_bundle(bundle)
    except M3KPolicyProposalError as exc:
        raise M3KProposalBindingError(
            f"bound canonical M3-K proposal is invalid: {exc}"
        ) from exc
    parent, proposal = load_proposal_bundle(bundle)
    expected = {
        "bundle_contract": BUNDLE_CONTRACT,
        "construction_evidence_sha256": content_sha256(
            bundle["construction_evidence"]
        ),
        "proposal_id": proposal.id,
        "proposal_sha256": content_sha256(proposal),
        "proposal_bundle_sha256": content_sha256(bundle),
        "parent_variant_id": parent.id,
        "parent_variant_sha256": content_sha256(parent),
        "candidate_variant_id": proposal.candidate.id,
        "candidate_variant_sha256": content_sha256(proposal.candidate),
        "binding_status": "bound",
    }
    for key, value in expected.items():
        if binding.get(key) != value:
            raise M3KProposalBindingError(f"bound proposal {key} drifted")
    allowed = gate.get("execution_allowed")
    if allowed is not capability.get("eligible") or allowed is not (payload.get("status") == "ready"):
        raise M3KProposalBindingError("bound M3-K execution gate/status is inconsistent")
    if allowed and gate.get("reasons") != []:
        raise M3KProposalBindingError("ready M3-K manifest cannot retain gate failures")
    if not allowed and not gate.get("reasons"):
        raise M3KProposalBindingError("blocked M3-K manifest must explain its gate failures")
    if payload.get("summary", {}).get("expected_trajectories") != 522 or len(payload.get("paired_cells", [])) != 522:
        raise M3KProposalBindingError("bound M3-K denominator drifted")
    _require_sha256(source.get("manifest_sha256"), label="source_schedule.manifest_sha256")
    _require_sha256(source.get("file_sha256"), label="source_schedule.file_sha256")
    _validate_library_binding(payload)
    boundary = payload.get("claim_boundary", {})
    if any(boundary.get(key) is not False for key in ("model_execution", "trajectory_results", "full87_result", "provider_native_invocation")):
        raise M3KProposalBindingError("bound not-run M3-K claim boundary is unsafe")


def write_bound_manifest(
    *,
    schedule_path: Path,
    library_scale_manifest_path: Path,
    proposal_bundle_path: Path,
    output: Path,
    executor_capability_path: Path | None = None,
) -> dict[str, Any]:
    schedule, schedule_bytes = _load_json(schedule_path, label="M3-K schedule")
    library_scale_manifest, library_scale_bytes = _load_json(
        library_scale_manifest_path,
        label="canonical library-scale manifest",
    )
    library_scale_file_sha256 = sha256_file(library_scale_manifest_path)
    if library_scale_file_sha256 != _sha256_bytes(library_scale_bytes):
        raise M3KProposalBindingError("canonical library-scale manifest changed while being read")
    bundle, bundle_bytes = _load_json(proposal_bundle_path, label="proposal bundle")
    capability = None
    capability_bytes = None
    if executor_capability_path is not None:
        capability, capability_bytes = _load_json(executor_capability_path, label="executor capability")
    output = output.expanduser().resolve(strict=False)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite bound M3-K manifest: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = bind_manifest(
        schedule=schedule,
        schedule_file_sha256=_sha256_bytes(schedule_bytes),
        library_scale_manifest=library_scale_manifest,
        library_scale_file_sha256=library_scale_file_sha256,
        bundle=bundle,
        bundle_file_sha256=_sha256_bytes(bundle_bytes),
        capability=capability,
        capability_file_sha256=(_sha256_bytes(capability_bytes) if capability_bytes is not None else None),
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--library-scale-manifest", type=Path, required=True)
    parser.add_argument("--proposal-bundle", type=Path, required=True)
    parser.add_argument("--executor-capability", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = write_bound_manifest(
        schedule_path=args.schedule,
        library_scale_manifest_path=args.library_scale_manifest,
        proposal_bundle_path=args.proposal_bundle,
        executor_capability_path=args.executor_capability,
        output=args.output,
    )
    print("Merlin M3-K proposal binding")
    print(f"status={payload['status']}")
    print(f"execution_allowed={str(payload['execution_gate']['execution_allowed']).lower()}")
    print(f"proposal={payload['proposal_binding']['proposal_id']}")
    print(f"manifest_sha256={payload['manifest_sha256']}")
    print(f"saved -> {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
