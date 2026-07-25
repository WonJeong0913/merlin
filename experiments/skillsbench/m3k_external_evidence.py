"""Seal and replay externally executed M3-K trajectories without trusting them.

This is deliberately a research-only evidence boundary.  It never starts a
provider run.  The recorder accepts one already-completed external trajectory,
checks it against a ready (schema v2) M3-K manifest, rebuilds and validates a
deterministic execution pack containing the MCP, container, config, admission,
and verifier originals, copies that pack plus the provider trace and runtime
audit into a new-only root, and writes a normalized record.  The
assembler requires all 522 scheduled trajectories, revalidates every copied
artifact, then feeds only :class:`M3KTrajectoryResult` instances into the
existing M3-K evaluator.  Promotion and rollback therefore remain decisions of
``run_m3k_policy_evaluation`` rather than caller-supplied conclusions.

The runtime audit proves a requested-model and isolation contract.  It is not
evidence of a provider-resolved model identity or provider-native skill API.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    load_proposal_bundle,
    validate_bound_manifest,
)
from experiments.skillsbench.probe_codex_mcp_capability import (
    NATIVE_TOOL_FEATURES_TO_DISABLE,
)
from experiments.skillsbench.harness_policy_evaluation import (
    M3KCell,
    M3KContractError,
    M3KEvaluationContract,
    M3KPromotionCriteria,
    M3KTrajectoryResult,
    M3KVariantLineage,
    M3KVariantExecutor,
    M3KSplit,
    M3KTaskContract,
    VariantRole,
    run_m3k_policy_evaluation,
)


class M3KExternalEvidenceError(ValueError):
    """Raised when external M3-K evidence cannot support a replayed result."""


_SHA256_ALPHABET = frozenset("0123456789abcdef")
_ROOT_BUCKETS = ("trajectories", "raw", "runtime-audits", "execution-packs")

EXECUTION_ARTIFACT_NAMES = (
    "allowed-skill-ids.json",
    "codex.jsonl",
    "codex.stderr.txt",
    "container-inspect.json",
    "desktop-admission-start.json",
    "docker-build.stderr.txt",
    "docker-build.stdout.txt",
    "executor-capability.json",
    "feature-suppression.json",
    "image-inspect.json",
    "mcp-audit.jsonl",
    "provisioning.json",
    "run-config.json",
    "source-snapshot-manifest.json",
    "verifier-result.json",
    "verifier.stderr.txt",
    "verifier.stdout.txt",
)
EXECUTION_EVENT_KEYS = frozenset(
    {
        "schema_version",
        "trajectory_id",
        "raw_artifact_hashes",
        "mcp_exec_call_count",
        "invoked_skill_ids",
        "provider_reported_model_ids",
        "forbidden_native_item_types",
    }
)

# This is the externally supplied, signed-or-otherwise-transported statement
# about one execution.  Artifact paths/hashes are added only by this recorder.
ATTESTATION_KEYS = frozenset(
    {
        "schema_version",
        "bound_manifest_sha256",
        "bound_manifest_file_sha256",
        "trajectory_id",
        "pair_id",
        "cell_id",
        "variant_role",
        "variant_id",
        "variant_sha256",
        "proposal_id",
        "proposal_sha256",
        "evaluation_contract_sha256",
        "task_id",
        "split",
        "trial_index",
        "verifier_id",
        "task_instruction_sha256",
        "library_arm_id",
        "library_size",
        "library_snapshot_sha256",
        "library_order_sha256",
        "actual_invocation_evidence_complete",
        "invoked_skill_ids",
        "oracle_skill_ids",
        "verifier_passed",
        "verifier_score",
        "cost",
    }
)
RECORD_KEYS = ATTESTATION_KEYS | frozenset(
    {
        "raw_provider_trace_pointer",
        "raw_provider_trace_sha256",
        "runtime_audit_pointer",
        "runtime_audit_sha256",
        "execution_pack_pointer",
        "execution_pack_sha256",
        "execution_event_sha256",
    }
)
RUNTIME_AUDIT_KEYS = frozenset(
    {
        "schema_version",
        "bound_manifest_sha256",
        "executor_capability_file_sha256",
        "trajectory_id",
        "raw_provider_trace_sha256",
        "requested_model_contract",
        "tool_feature_suppression_enforced",
        "feature_suppression_sha256",
        "strict_config_enforced",
        "user_config_suppressed",
        "rules_suppressed",
        "per_run_mcp_isolation",
        "host_native_tool_event_observed",
        "exec_tool_call_observed",
        "inspected_container_id",
        "inspected_container_sha256",
        "inspected_image_id",
        "inspected_image_sha256",
        "run_config_sha256",
        "audit_event_sha256",
    }
)


def sha256_file(path: Path) -> str:
    """Hash a regular artifact without interpreting provider/audit contents."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: Any, *, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_ALPHABET for character in value)
    ):
        raise M3KExternalEvidenceError(f"{label} must be a lowercase SHA-256")


def _require_nonempty(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise M3KExternalEvidenceError(f"{label} must be a non-empty string")


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KExternalEvidenceError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise M3KExternalEvidenceError(f"{label} must be a JSON object")
    return value


def _regular_file(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KExternalEvidenceError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise M3KExternalEvidenceError(f"{label} is missing") from exc
    if not resolved.is_file():
        raise M3KExternalEvidenceError(f"{label} must be a regular file")
    return resolved


def _regular_directory(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KExternalEvidenceError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise M3KExternalEvidenceError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise M3KExternalEvidenceError(f"{label} must be a regular directory")
    return resolved


def _new_root(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KExternalEvidenceError(f"{label} must not be a symlink")
    return expanded.resolve(strict=False)


def _relative_member(root: Path, pointer: Any, *, label: str) -> Path:
    if not isinstance(pointer, str) or not pointer:
        raise M3KExternalEvidenceError(f"{label} pointer must be a non-empty relative path")
    pure = PurePosixPath(pointer)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise M3KExternalEvidenceError(f"{label} pointer escapes its root")
    candidate = root / Path(*pure.parts)
    if candidate.is_symlink():
        raise M3KExternalEvidenceError(f"{label} pointer must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise M3KExternalEvidenceError(f"{label} pointer escapes its root or is missing") from exc
    if not resolved.is_file():
        raise M3KExternalEvidenceError(f"{label} pointer must name a regular file")
    return resolved


def _storage_key(trajectory_id: str) -> str:
    return hashlib.sha256(trajectory_id.encode("utf-8")).hexdigest()


def record_pointer_for_trajectory(trajectory_id: str) -> str:
    """Return the fixed, path-safe record location for a scheduled trajectory."""

    _require_nonempty(trajectory_id, label="trajectory_id")
    return f"trajectories/{_storage_key(trajectory_id)}.json"


def raw_pointer_for_sha256(raw_provider_trace_sha256: str) -> str:
    """Return the content-addressed raw-trace pointer used by new records."""

    _require_sha256(raw_provider_trace_sha256, label="raw_provider_trace_sha256")
    return f"raw/{raw_provider_trace_sha256}.bin"


def runtime_audit_pointer_for_sha256(runtime_audit_sha256: str) -> str:
    """Return the content-addressed runtime-audit pointer used by new records."""

    _require_sha256(runtime_audit_sha256, label="runtime_audit_sha256")
    return f"runtime-audits/{runtime_audit_sha256}.json"


def execution_pack_pointer_for_sha256(execution_pack_sha256: str) -> str:
    """Return the content-addressed deterministic execution-pack pointer."""

    _require_sha256(execution_pack_sha256, label="execution_pack_sha256")
    return f"execution-packs/{execution_pack_sha256}.tar"


def _ensure_root_layout(root: Path, *, allow_missing: bool) -> None:
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise M3KExternalEvidenceError("evidence_root must be a regular directory")
        names = {entry.name for entry in root.iterdir()}
        unexpected = names - set(_ROOT_BUCKETS)
        if unexpected:
            raise M3KExternalEvidenceError("evidence_root contains an unexpected entry")
        for name in names:
            bucket = root / name
            if bucket.is_symlink() or not bucket.is_dir():
                raise M3KExternalEvidenceError("evidence_root contains an unsafe bucket")
    elif not allow_missing:
        raise M3KExternalEvidenceError("evidence_root is missing")


def _new_destination(root: Path, pointer: str, *, label: str) -> Path:
    pure = PurePosixPath(pointer)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise M3KExternalEvidenceError(f"{label} destination escapes evidence_root")
    candidate = root / Path(*pure.parts)
    if candidate.exists() or candidate.is_symlink():
        raise M3KExternalEvidenceError(f"{label} destination already exists")
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise M3KExternalEvidenceError(f"{label} destination escapes evidence_root") from exc
    return candidate


def _copy_new(source: Path, destination: Path) -> None:
    """Copy without a destination overwrite race and verify bytes afterwards."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
        # os.link is create-only: unlike replace(), it can never overwrite an
        # already recorded evidence file.
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise M3KExternalEvidenceError(f"evidence destination already exists: {destination}") from exc
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def _write_json_new(destination: Path, payload: Mapping[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise M3KExternalEvidenceError(f"evidence destination already exists: {destination}") from exc
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def _json_from_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise M3KExternalEvidenceError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise M3KExternalEvidenceError(f"{label} must be a JSON object")
    return value


def _mcp_exec_summary(raw: bytes) -> tuple[int, list[str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise M3KExternalEvidenceError("MCP audit must be UTF-8 JSONL") from exc
    count = 0
    invoked: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise M3KExternalEvidenceError(
                f"MCP audit line {line_number} is malformed"
            ) from exc
        if not isinstance(event, dict):
            raise M3KExternalEvidenceError("MCP audit events must be objects")
        if event.get("method") != "tools/call" or event.get("tool_name") != "exec":
            continue
        count += 1
        skill_id = event.get("skill_id")
        if skill_id is not None:
            if not isinstance(skill_id, str) or not skill_id:
                raise M3KExternalEvidenceError("MCP audit skill_id is invalid")
            if skill_id not in invoked:
                invoked.append(skill_id)
    return count, invoked


def _forbidden_codex_item_types(raw: bytes) -> list[str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise M3KExternalEvidenceError("provider trace must be UTF-8 JSONL") from exc
    forbidden = {"command_execution", "file_change", "computer_use"}
    observed: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise M3KExternalEvidenceError(
                f"provider trace line {line_number} is malformed"
            ) from exc
        if not isinstance(event, dict):
            raise M3KExternalEvidenceError("provider trace events must be objects")
        if event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise M3KExternalEvidenceError("provider item event has no typed item")
        if item["type"] in forbidden:
            observed.add(item["type"])
    return sorted(observed)


def _validate_execution_payload(
    *,
    event_bytes: bytes,
    artifacts: Mapping[str, bytes],
    audit: Mapping[str, Any],
    attestation: Mapping[str, Any],
) -> dict[str, Any]:
    event = _json_from_bytes(event_bytes, label="execution event")
    if set(event) != EXECUTION_EVENT_KEYS or event.get("schema_version") != 1:
        raise M3KExternalEvidenceError("execution event schema is invalid")
    if event.get("trajectory_id") != attestation.get("trajectory_id"):
        raise M3KExternalEvidenceError("execution event trajectory_id drifted")
    hashes = event.get("raw_artifact_hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(EXECUTION_ARTIFACT_NAMES):
        raise M3KExternalEvidenceError("execution event artifact coverage is incomplete")
    if set(artifacts) != set(EXECUTION_ARTIFACT_NAMES):
        raise M3KExternalEvidenceError("execution pack artifact coverage is incomplete")
    for name in EXECUTION_ARTIFACT_NAMES:
        _require_sha256(hashes.get(name), label=f"execution artifact {name}")
        if hashlib.sha256(artifacts[name]).hexdigest() != hashes[name]:
            raise M3KExternalEvidenceError(f"execution artifact hash drifted: {name}")

    event_sha256 = hashlib.sha256(event_bytes).hexdigest()
    if audit.get("audit_event_sha256") != event_sha256:
        raise M3KExternalEvidenceError("runtime audit execution event hash drifted")
    cross_links = {
        "executor_capability_file_sha256": "executor-capability.json",
        "feature_suppression_sha256": "feature-suppression.json",
        "inspected_container_sha256": "container-inspect.json",
        "inspected_image_sha256": "image-inspect.json",
        "run_config_sha256": "run-config.json",
    }
    for audit_key, artifact_name in cross_links.items():
        if audit.get(audit_key) != hashes[artifact_name]:
            raise M3KExternalEvidenceError(
                f"runtime audit {audit_key} does not bind the execution pack"
            )

    feature_suppression = _json_from_bytes(
        artifacts["feature-suppression.json"], label="feature suppression"
    )
    expected_features = list(NATIVE_TOOL_FEATURES_TO_DISABLE)
    if (
        feature_suppression.get("provided") is not True
        or feature_suppression.get("requested_disabled_features") != expected_features
        or feature_suppression.get("observed_disabled_features") != expected_features
        or feature_suppression.get("all_requested_features_disabled") is not True
        or feature_suppression.get("feature_listing_is_runtime_tool_inventory_proof")
        is not False
        or feature_suppression.get("feature_listing_is_model_execution") is not False
    ):
        raise M3KExternalEvidenceError("execution feature suppression contract drifted")

    count, invoked = _mcp_exec_summary(artifacts["mcp-audit.jsonl"])
    if count < 1 or event.get("mcp_exec_call_count") != count:
        raise M3KExternalEvidenceError("execution event MCP call count drifted")
    if event.get("invoked_skill_ids") != invoked:
        raise M3KExternalEvidenceError("execution event invoked skill IDs drifted")
    if invoked != attestation.get("invoked_skill_ids"):
        raise M3KExternalEvidenceError("attestation invoked skill IDs drifted from MCP audit")
    reported = event.get("provider_reported_model_ids")
    if (
        not isinstance(reported, list)
        or any(not isinstance(item, str) or not item for item in reported)
        or len(set(reported)) != len(reported)
    ):
        raise M3KExternalEvidenceError("provider-reported model ID evidence is invalid")
    forbidden = _forbidden_codex_item_types(artifacts["codex.jsonl"])
    if event.get("forbidden_native_item_types") != forbidden or forbidden:
        raise M3KExternalEvidenceError("forbidden host-native tool evidence is present")

    allowed_ids = json.loads(artifacts["allowed-skill-ids.json"])
    if (
        not isinstance(allowed_ids, list)
        or any(not isinstance(item, str) or not item for item in allowed_ids)
        or len(set(allowed_ids)) != len(allowed_ids)
        or not set(invoked).issubset(allowed_ids)
    ):
        raise M3KExternalEvidenceError("allowed skill IDs do not cover MCP invocation")

    verifier = _json_from_bytes(
        artifacts["verifier-result.json"], label="verifier result"
    )
    required_verifier = {
        "schema_version",
        "exit_code",
        "reward",
        "passed",
        "stdout_sha256",
        "stderr_sha256",
        "hidden_verifier_tree_sha256",
    }
    if set(verifier) != required_verifier or verifier.get("schema_version") != 1:
        raise M3KExternalEvidenceError("verifier result schema is invalid")
    if (
        verifier.get("passed") is not attestation.get("verifier_passed")
        or verifier.get("reward") != attestation.get("verifier_score")
    ):
        raise M3KExternalEvidenceError("verifier result drifted from attestation")
    if verifier.get("stdout_sha256") != hashes["verifier.stdout.txt"]:
        raise M3KExternalEvidenceError("verifier stdout hash drifted")
    if verifier.get("stderr_sha256") != hashes["verifier.stderr.txt"]:
        raise M3KExternalEvidenceError("verifier stderr hash drifted")
    _require_sha256(
        verifier.get("hidden_verifier_tree_sha256"),
        label="hidden_verifier_tree_sha256",
    )

    run_config = _json_from_bytes(artifacts["run-config.json"], label="run config")
    if run_config.get("trajectory_id") != attestation.get("trajectory_id"):
        raise M3KExternalEvidenceError("run config trajectory_id drifted")
    if run_config.get("executor_capability_file_sha256") != hashes[
        "executor-capability.json"
    ]:
        raise M3KExternalEvidenceError("run config executor capability drifted")
    if run_config.get("container_id") != audit.get("inspected_container_id"):
        raise M3KExternalEvidenceError("run config container identity drifted")
    if run_config.get("image_id") != audit.get("inspected_image_id"):
        raise M3KExternalEvidenceError("run config image identity drifted")
    admission = run_config.get("desktop_admission")
    if not isinstance(admission, dict):
        raise M3KExternalEvidenceError("run config DESKTOP admission binding is missing")
    if admission.get("admission_start_sha256") != hashes[
        "desktop-admission-start.json"
    ]:
        raise M3KExternalEvidenceError("DESKTOP admission start hash drifted")
    if admission.get("source_snapshot_manifest_sha256") != hashes[
        "source-snapshot-manifest.json"
    ]:
        raise M3KExternalEvidenceError("source snapshot manifest hash drifted")
    return event


def _read_execution_sources(
    *,
    execution_event_path: Path,
    raw_artifact_root: Path,
    raw_provider_trace_path: Path,
    audit: Mapping[str, Any],
    attestation: Mapping[str, Any],
) -> tuple[bytes, dict[str, bytes], dict[str, Any]]:
    event_path = _regular_file(execution_event_path, label="execution event")
    artifact_root = _regular_directory(raw_artifact_root, label="raw artifact root")
    if event_path.parent != artifact_root:
        raise M3KExternalEvidenceError("execution event must be inside raw artifact root")
    artifacts: dict[str, bytes] = {}
    for name in EXECUTION_ARTIFACT_NAMES:
        artifact_path = _regular_file(artifact_root / name, label=f"execution artifact {name}")
        if artifact_path.parent != artifact_root:
            raise M3KExternalEvidenceError("execution artifact escapes raw artifact root")
        artifacts[name] = artifact_path.read_bytes()
    if _regular_file(raw_provider_trace_path, label="raw provider trace") != (
        artifact_root / "codex.jsonl"
    ):
        raise M3KExternalEvidenceError("raw provider trace must be the packed codex.jsonl")
    event_bytes = event_path.read_bytes()
    event = _validate_execution_payload(
        event_bytes=event_bytes,
        artifacts=artifacts,
        audit=audit,
        attestation=attestation,
    )
    return event_bytes, artifacts, event


def _write_deterministic_execution_pack(
    destination: Path, *, event_bytes: bytes, artifacts: Mapping[str, bytes]
) -> None:
    members = {"execution-event.json": event_bytes, **dict(artifacts)}
    with tarfile.open(destination, mode="x:", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(members):
            raw = members[name]
            info = tarfile.TarInfo(name=name)
            info.size = len(raw)
            info.mode = 0o600
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(raw))


def _read_and_validate_execution_pack(
    *,
    pack_path: Path,
    expected_pack_sha256: str,
    expected_event_sha256: str,
    raw_provider_trace_path: Path,
    audit: Mapping[str, Any],
    attestation: Mapping[str, Any],
) -> None:
    pack = _regular_file(pack_path, label="execution pack")
    if sha256_file(pack) != expected_pack_sha256:
        raise M3KExternalEvidenceError("execution pack is hash-invalid")
    expected_names = {"execution-event.json", *EXECUTION_ARTIFACT_NAMES}
    values: dict[str, bytes] = {}
    try:
        with tarfile.open(pack, mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != len(expected_names):
                raise M3KExternalEvidenceError("execution pack member coverage is invalid")
            for member in members:
                if member.name not in expected_names or not member.isfile():
                    raise M3KExternalEvidenceError("execution pack contains an unsafe member")
                handle = archive.extractfile(member)
                if handle is None or member.name in values:
                    raise M3KExternalEvidenceError("execution pack member is invalid")
                values[member.name] = handle.read()
    except (tarfile.TarError, OSError) as exc:
        raise M3KExternalEvidenceError("cannot read execution pack") from exc
    if set(values) != expected_names:
        raise M3KExternalEvidenceError("execution pack member coverage is invalid")
    event_bytes = values.pop("execution-event.json")
    if hashlib.sha256(event_bytes).hexdigest() != expected_event_sha256:
        raise M3KExternalEvidenceError("execution event is hash-invalid")
    if values["codex.jsonl"] != _regular_file(
        raw_provider_trace_path, label="raw provider trace"
    ).read_bytes():
        raise M3KExternalEvidenceError("execution pack provider trace bytes drifted")
    _validate_execution_payload(
        event_bytes=event_bytes,
        artifacts=values,
        audit=audit,
        attestation=attestation,
    )


def _load_bound_manifest(path: Path) -> tuple[Path, dict[str, Any], str]:
    manifest_path = _regular_file(path, label="bound M3-K manifest")
    manifest = _load_json_object(manifest_path, label="bound M3-K manifest")
    try:
        validate_bound_manifest(manifest)
    except M3KProposalBindingError as exc:
        raise M3KExternalEvidenceError(str(exc)) from exc
    if manifest.get("execution_gate", {}).get("execution_allowed") is not True:
        raise M3KExternalEvidenceError("bound M3-K manifest must have execution_allowed=true")
    return manifest_path, manifest, sha256_file(manifest_path)


def _scheduled_by_trajectory(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cells = manifest.get("paired_cells")
    if not isinstance(cells, list) or len(cells) != 522:
        raise M3KExternalEvidenceError("bound M3-K manifest must schedule exactly 522 trajectories")
    indexed: dict[str, dict[str, Any]] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise M3KExternalEvidenceError("bound M3-K schedule contains an invalid cell")
        trajectory_id = cell.get("trajectory_id")
        if not isinstance(trajectory_id, str) or not trajectory_id or trajectory_id in indexed:
            raise M3KExternalEvidenceError("bound M3-K trajectory IDs must be unique")
        if cell.get("variant_role") not in {role.value for role in VariantRole}:
            raise M3KExternalEvidenceError("bound M3-K trajectory variant role is invalid")
        indexed[trajectory_id] = cell
    return indexed


def _expected_attestation_fields(
    *,
    manifest: dict[str, Any],
    manifest_file_sha256: str,
    scheduled: dict[str, Any],
) -> dict[str, Any]:
    binding = manifest["proposal_binding"]
    role = scheduled["variant_role"]
    if role == VariantRole.PARENT.value:
        variant_id = binding["parent_variant_id"]
        variant_sha256 = binding["parent_variant_sha256"]
    else:
        variant_id = binding["candidate_variant_id"]
        variant_sha256 = binding["candidate_variant_sha256"]
    return {
        "bound_manifest_sha256": manifest["manifest_sha256"],
        "bound_manifest_file_sha256": manifest_file_sha256,
        "trajectory_id": scheduled["trajectory_id"],
        "pair_id": scheduled["pair_id"],
        "cell_id": scheduled["cell_id"],
        "variant_role": role,
        "variant_id": variant_id,
        "variant_sha256": variant_sha256,
        "proposal_id": binding["proposal_id"],
        "proposal_sha256": binding["proposal_sha256"],
        "evaluation_contract_sha256": manifest["evaluation_contract_sha256"],
        "task_id": scheduled["task_id"],
        "split": scheduled["split"],
        "trial_index": scheduled["trial_index"],
        "verifier_id": scheduled["verifier_id"],
        "task_instruction_sha256": scheduled["task_instruction_sha256"],
        "library_arm_id": scheduled["library_arm_id"],
        "library_size": scheduled["library_size"],
        "library_snapshot_sha256": scheduled["library_snapshot_sha256"],
        "library_order_sha256": scheduled["library_order_sha256"],
    }


def _skill_ids(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise M3KExternalEvidenceError(f"{label} must be a list of non-empty strings")
    if len(set(value)) != len(value):
        raise M3KExternalEvidenceError(f"{label} must not contain duplicate skill IDs")
    return tuple(value)


def _validate_attestation(
    attestation: dict[str, Any],
    *,
    expected: dict[str, Any],
) -> None:
    if set(attestation) != ATTESTATION_KEYS or attestation.get("schema_version") != 1:
        raise M3KExternalEvidenceError("external trajectory attestation schema is invalid")
    for key, value in expected.items():
        if attestation.get(key) != value:
            raise M3KExternalEvidenceError(f"external trajectory attestation {key} drifted")
    for key in (
        "bound_manifest_sha256",
        "bound_manifest_file_sha256",
        "variant_sha256",
        "proposal_sha256",
        "evaluation_contract_sha256",
        "verifier_id",
        "task_instruction_sha256",
        "library_snapshot_sha256",
        "library_order_sha256",
    ):
        _require_sha256(attestation[key], label=key)
    if attestation["library_arm_id"] != "full-209":
        raise M3KExternalEvidenceError("external trajectory must use the frozen full-209 arm")
    if attestation["library_size"] != 209:
        raise M3KExternalEvidenceError("external trajectory must expose exactly 209 skill variants")
    if not isinstance(attestation["actual_invocation_evidence_complete"], bool):
        raise M3KExternalEvidenceError("actual_invocation_evidence_complete must be boolean")
    invoked = _skill_ids(attestation["invoked_skill_ids"], label="invoked_skill_ids")
    _skill_ids(attestation["oracle_skill_ids"], label="oracle_skill_ids")
    if not attestation["actual_invocation_evidence_complete"] and invoked:
        raise M3KExternalEvidenceError(
            "incomplete actual invocation evidence must not claim invoked skill IDs"
        )
    if not isinstance(attestation["verifier_passed"], bool):
        raise M3KExternalEvidenceError("verifier_passed must be boolean")
    for key in ("verifier_score", "cost"):
        value = attestation[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise M3KExternalEvidenceError(f"{key} must be a finite number >= 0")
    if attestation["verifier_score"] > 1:
        raise M3KExternalEvidenceError("verifier_score must be in [0,1]")


def requested_model_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the requested execution contract a runtime audit must attest."""

    contract = manifest.get("evaluation_contract")
    if not isinstance(contract, dict):
        raise M3KExternalEvidenceError("bound M3-K evaluation contract is missing")
    return {
        "backend": contract.get("backend"),
        "model_id": contract.get("model_id"),
        "effort": contract.get("effort"),
        "tools": contract.get("tools"),
    }


def validate_runtime_audit(
    audit: dict[str, Any],
    *,
    manifest: dict[str, Any],
    trajectory_id: str,
    raw_provider_trace_sha256: str,
) -> None:
    """Validate only the strict requested-model/container isolation attestation."""

    if set(audit) != RUNTIME_AUDIT_KEYS or audit.get("schema_version") != 2:
        raise M3KExternalEvidenceError("runtime audit schema is invalid")
    if audit["bound_manifest_sha256"] != manifest.get("manifest_sha256"):
        raise M3KExternalEvidenceError("runtime audit bound manifest drifted")
    capability_sha256 = manifest.get("executor_capability", {}).get("file_sha256")
    if audit["executor_capability_file_sha256"] != capability_sha256:
        raise M3KExternalEvidenceError("runtime audit executor capability drifted")
    if audit["trajectory_id"] != trajectory_id:
        raise M3KExternalEvidenceError("runtime audit trajectory_id drifted")
    if audit["raw_provider_trace_sha256"] != raw_provider_trace_sha256:
        raise M3KExternalEvidenceError("runtime audit raw provider trace drifted")
    if audit["requested_model_contract"] != requested_model_contract(manifest):
        raise M3KExternalEvidenceError("runtime audit requested_model contract drifted")
    for key in (
        "tool_feature_suppression_enforced",
        "strict_config_enforced",
        "user_config_suppressed",
        "rules_suppressed",
        "per_run_mcp_isolation",
        "exec_tool_call_observed",
    ):
        if audit[key] is not True:
            raise M3KExternalEvidenceError(f"runtime audit requires {key}=true")
    if audit["host_native_tool_event_observed"] is not False:
        raise M3KExternalEvidenceError(
            "runtime audit requires host_native_tool_event_observed=false"
        )
    for key in ("inspected_container_id", "inspected_image_id"):
        _require_nonempty(audit[key], label=key)
    for key in (
        "bound_manifest_sha256",
        "executor_capability_file_sha256",
        "raw_provider_trace_sha256",
        "feature_suppression_sha256",
        "inspected_container_sha256",
        "inspected_image_sha256",
        "run_config_sha256",
        "audit_event_sha256",
    ):
        _require_sha256(audit[key], label=key)


def record_m3k_external_trajectory(
    *,
    bound_manifest_path: Path,
    attestation_path: Path,
    raw_provider_trace_path: Path,
    runtime_audit_path: Path,
    execution_event_path: Path,
    raw_artifact_root: Path,
    evidence_root: Path,
) -> dict[str, Any]:
    """Validate and seal one scheduled external trajectory into ``evidence_root``.

    The function intentionally accepts no executor callback.  An outside runtime
    has already run the cell; this layer only admits byte-hashed evidence whose
    manifest, variant, task, verifier, and isolation attestations match exactly.
    """

    manifest_path, manifest, manifest_file_sha256 = _load_bound_manifest(bound_manifest_path)
    scheduled = _scheduled_by_trajectory(manifest)
    attestation_file = _regular_file(attestation_path, label="external trajectory attestation")
    raw_source = _regular_file(raw_provider_trace_path, label="raw provider trace")
    audit_source = _regular_file(runtime_audit_path, label="runtime audit")
    attestation = _load_json_object(attestation_file, label="external trajectory attestation")
    trajectory_id = attestation.get("trajectory_id")
    if trajectory_id not in scheduled:
        raise M3KExternalEvidenceError("attestation trajectory_id is not scheduled by the bound manifest")
    _validate_attestation(
        attestation,
        expected=_expected_attestation_fields(
            manifest=manifest,
            manifest_file_sha256=manifest_file_sha256,
            scheduled=scheduled[trajectory_id],
        ),
    )
    raw_sha256 = sha256_file(raw_source)
    audit = _load_json_object(audit_source, label="runtime audit")
    validate_runtime_audit(
        audit,
        manifest=manifest,
        trajectory_id=trajectory_id,
        raw_provider_trace_sha256=raw_sha256,
    )
    audit_sha256 = sha256_file(audit_source)
    if raw_sha256 == audit_sha256:
        raise M3KExternalEvidenceError("raw provider trace and runtime audit must be distinct artifacts")

    root = _new_root(evidence_root, label="evidence_root")
    _ensure_root_layout(root, allow_missing=True)
    raw_pointer = raw_pointer_for_sha256(raw_sha256)
    audit_pointer = runtime_audit_pointer_for_sha256(audit_sha256)
    if root.exists() and (root / raw_pointer).exists():
        raise M3KExternalEvidenceError("raw provider trace evidence is already reused")
    if root.exists() and (root / audit_pointer).exists():
        raise M3KExternalEvidenceError("runtime audit evidence is already reused")
    event_bytes, artifact_bytes, _ = _read_execution_sources(
        execution_event_path=execution_event_path,
        raw_artifact_root=raw_artifact_root,
        raw_provider_trace_path=raw_source,
        audit=audit,
        attestation=attestation,
    )
    execution_event_sha256 = hashlib.sha256(event_bytes).hexdigest()

    with tempfile.TemporaryDirectory(prefix="merlin-m3k-pack-") as temporary:
        pack_source = Path(temporary) / "execution-pack.tar"
        _write_deterministic_execution_pack(
            pack_source,
            event_bytes=event_bytes,
            artifacts=artifact_bytes,
        )
        pack_sha256 = sha256_file(pack_source)
        pack_pointer = execution_pack_pointer_for_sha256(pack_sha256)
        if root.exists() and (root / pack_pointer).exists():
            raise M3KExternalEvidenceError("execution pack evidence is already reused")

        record_pointer = record_pointer_for_trajectory(trajectory_id)
        record_destination = _new_destination(root, record_pointer, label="trajectory record")
        raw_destination = _new_destination(root, raw_pointer, label="raw provider trace")
        audit_destination = _new_destination(root, audit_pointer, label="runtime audit")
        pack_destination = _new_destination(root, pack_pointer, label="execution pack")
        record = {
            **attestation,
            "raw_provider_trace_pointer": raw_pointer,
            "raw_provider_trace_sha256": raw_sha256,
            "runtime_audit_pointer": audit_pointer,
            "runtime_audit_sha256": audit_sha256,
            "execution_pack_pointer": pack_pointer,
            "execution_pack_sha256": pack_sha256,
            "execution_event_sha256": execution_event_sha256,
        }
        if set(record) != RECORD_KEYS:
            raise M3KExternalEvidenceError("normalized external trajectory record schema drifted")

        created: list[Path] = []
        try:
            _copy_new(raw_source, raw_destination)
            created.append(raw_destination)
            if sha256_file(raw_destination) != raw_sha256:
                raise M3KExternalEvidenceError("copied raw provider trace hash mismatch")
            _copy_new(audit_source, audit_destination)
            created.append(audit_destination)
            if sha256_file(audit_destination) != audit_sha256:
                raise M3KExternalEvidenceError("copied runtime audit hash mismatch")
            _copy_new(pack_source, pack_destination)
            created.append(pack_destination)
            _read_and_validate_execution_pack(
                pack_path=pack_destination,
                expected_pack_sha256=pack_sha256,
                expected_event_sha256=execution_event_sha256,
                raw_provider_trace_path=raw_destination,
                audit=audit,
                attestation=attestation,
            )
            _write_json_new(record_destination, record)
            created.append(record_destination)
        except Exception:
            for path in reversed(created):
                if path.exists() and not path.is_symlink():
                    path.unlink()
            raise
        return record


def _contract_from_manifest(manifest: dict[str, Any]) -> M3KEvaluationContract:
    value = manifest.get("evaluation_contract")
    if not isinstance(value, dict):
        raise M3KExternalEvidenceError("bound M3-K evaluation contract is missing")
    tasks_value = value.get("tasks")
    if not isinstance(tasks_value, list):
        raise M3KExternalEvidenceError("bound M3-K task contract list is invalid")
    try:
        tasks = tuple(
            M3KTaskContract(
                task_id=item["task_id"],
                split=M3KSplit(item["split"]),
                verifier_id=item["verifier_id"],
                task_instruction_sha256=item["task_instruction_sha256"],
            )
            for item in tasks_value
            if isinstance(item, dict)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise M3KExternalEvidenceError("bound M3-K task contracts are invalid") from exc
    if len(tasks) != len(tasks_value):
        raise M3KExternalEvidenceError("bound M3-K task contracts contain a non-object")
    try:
        contract = M3KEvaluationContract(
            experiment_id=value["experiment_id"],
            split_manifest_sha256=value["split_manifest_sha256"],
            task_contract_source_sha256=value["task_contract_source_sha256"],
            tasks=tasks,
            repeats=value["repeats"],
            base_agent_id=value["base_agent_id"],
            base_agent_version=value["base_agent_version"],
            backend=value["backend"],
            model_id=value["model_id"],
            effort=value["effort"],
            tools=tuple(value["tools"]),
            budget_id=value["budget_id"],
            held_out_visible_to_proposer=value["held_out_visible_to_proposer"],
            schema_version=value["schema_version"],
        )
    except (KeyError, TypeError) as exc:
        raise M3KExternalEvidenceError("bound M3-K evaluation contract fields are invalid") from exc
    if contract.contract_sha256 != manifest.get("evaluation_contract_sha256"):
        raise M3KExternalEvidenceError("bound M3-K evaluation contract hash drifted")
    return contract


def _assert_bucket_coverage(
    root: Path,
    *,
    expected_pointers: set[str],
    bucket_name: str,
    label: str,
) -> None:
    bucket = root / bucket_name
    if bucket.is_symlink() or not bucket.is_dir():
        raise M3KExternalEvidenceError(f"{label} bucket is missing or unsafe")
    actual: set[str] = set()
    for entry in bucket.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise M3KExternalEvidenceError(f"{label} bucket contains an unsafe entry")
        actual.add(f"{bucket_name}/{entry.name}")
    if actual != expected_pointers:
        missing = len(expected_pointers - actual)
        unexpected = len(actual - expected_pointers)
        raise M3KExternalEvidenceError(
            f"{label} coverage must exactly match {len(expected_pointers)} expected artifacts "
            f"(missing={missing}, unexpected={unexpected})"
        )


def _assert_bucket_contains(
    root: Path,
    *,
    expected_pointers: set[str],
    bucket_name: str,
    label: str,
) -> None:
    """Require a safe bucket to contain a selected immutable evidence subset."""

    bucket = root / bucket_name
    if bucket.is_symlink() or not bucket.is_dir():
        raise M3KExternalEvidenceError(f"{label} bucket is missing or unsafe")
    actual: set[str] = set()
    for entry in bucket.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise M3KExternalEvidenceError(f"{label} bucket contains an unsafe entry")
        actual.add(f"{bucket_name}/{entry.name}")
    missing = expected_pointers - actual
    if missing:
        raise M3KExternalEvidenceError(
            f"{label} bucket is missing {len(missing)} selected artifacts"
        )


def _validate_record(
    record: dict[str, Any],
    *,
    root: Path,
    manifest: dict[str, Any],
    manifest_file_sha256: str,
    scheduled: dict[str, Any],
) -> dict[str, Any]:
    if set(record) != RECORD_KEYS or record.get("schema_version") != 1:
        raise M3KExternalEvidenceError("external trajectory record schema is invalid")
    attestation = {key: record[key] for key in ATTESTATION_KEYS}
    _validate_attestation(
        attestation,
        expected=_expected_attestation_fields(
            manifest=manifest,
            manifest_file_sha256=manifest_file_sha256,
            scheduled=scheduled,
        ),
    )
    trajectory_id = scheduled["trajectory_id"]
    if record["raw_provider_trace_pointer"] != raw_pointer_for_sha256(
        record["raw_provider_trace_sha256"]
    ):
        raise M3KExternalEvidenceError("external trajectory raw provider trace pointer drifted")
    if record["runtime_audit_pointer"] != runtime_audit_pointer_for_sha256(
        record["runtime_audit_sha256"]
    ):
        raise M3KExternalEvidenceError("external trajectory runtime audit pointer drifted")
    if record["execution_pack_pointer"] != execution_pack_pointer_for_sha256(
        record["execution_pack_sha256"]
    ):
        raise M3KExternalEvidenceError("external trajectory execution pack pointer drifted")
    _require_sha256(record["raw_provider_trace_sha256"], label="raw_provider_trace_sha256")
    _require_sha256(record["runtime_audit_sha256"], label="runtime_audit_sha256")
    _require_sha256(record["execution_pack_sha256"], label="execution_pack_sha256")
    _require_sha256(record["execution_event_sha256"], label="execution_event_sha256")
    if record["raw_provider_trace_sha256"] == record["runtime_audit_sha256"]:
        raise M3KExternalEvidenceError("raw provider trace and runtime audit must be distinct artifacts")
    raw_path = _relative_member(
        root,
        record["raw_provider_trace_pointer"],
        label="raw provider trace",
    )
    audit_path = _relative_member(root, record["runtime_audit_pointer"], label="runtime audit")
    pack_path = _relative_member(root, record["execution_pack_pointer"], label="execution pack")
    if sha256_file(raw_path) != record["raw_provider_trace_sha256"]:
        raise M3KExternalEvidenceError("raw provider trace is hash-invalid")
    if sha256_file(audit_path) != record["runtime_audit_sha256"]:
        raise M3KExternalEvidenceError("runtime audit is hash-invalid")
    audit = _load_json_object(audit_path, label="runtime audit")
    validate_runtime_audit(
        audit,
        manifest=manifest,
        trajectory_id=trajectory_id,
        raw_provider_trace_sha256=record["raw_provider_trace_sha256"],
    )
    _read_and_validate_execution_pack(
        pack_path=pack_path,
        expected_pack_sha256=record["execution_pack_sha256"],
        expected_event_sha256=record["execution_event_sha256"],
        raw_provider_trace_path=raw_path,
        audit=audit,
        attestation=attestation,
    )
    return {
        "record": record,
        "raw_path": raw_path,
        "audit_path": audit_path,
        "pack_path": pack_path,
    }


def validate_m3k_external_evidence_subset(
    *,
    bound_manifest_path: Path,
    evidence_root: Path,
    trajectory_ids: Sequence[str],
    allow_additional_trajectories: bool = False,
) -> dict[str, Any]:
    """Revalidate one exact scheduled subset without making a policy decision.

    This is the shared admission boundary for small executor pilots.  The
    By default the evidence root must contain exactly the requested records and
    their unique raw/audit/pack artifacts.  A frozen post-pilot controller may
    set ``allow_additional_trajectories`` after expansion starts; selected
    records are still reopened byte-for-byte while safe regular-file additions
    in the same sealed buckets are tolerated.
    """

    _, manifest, manifest_file_sha256 = _load_bound_manifest(bound_manifest_path)
    scheduled_by_id = _scheduled_by_trajectory(manifest)
    requested = tuple(trajectory_ids)
    if not requested or any(not isinstance(item, str) or not item for item in requested):
        raise M3KExternalEvidenceError("trajectory subset must contain non-empty IDs")
    if len(set(requested)) != len(requested):
        raise M3KExternalEvidenceError("trajectory subset must not contain duplicate IDs")
    unknown = set(requested) - set(scheduled_by_id)
    if unknown:
        raise M3KExternalEvidenceError("trajectory subset contains an unscheduled ID")

    root = _regular_directory(evidence_root, label="evidence_root")
    _ensure_root_layout(root, allow_missing=False)
    if {entry.name for entry in root.iterdir()} != set(_ROOT_BUCKETS):
        raise M3KExternalEvidenceError(
            "evidence_root must contain exactly the M3-K evidence buckets"
        )
    expected_records = {record_pointer_for_trajectory(item) for item in requested}
    coverage_check = (
        _assert_bucket_contains
        if allow_additional_trajectories
        else _assert_bucket_coverage
    )
    coverage_check(
        root,
        expected_pointers=expected_records,
        bucket_name="trajectories",
        label="trajectory record",
    )

    validated: dict[str, dict[str, Any]] = {}
    raw_hashes: set[str] = set()
    audit_hashes: set[str] = set()
    pack_hashes: set[str] = set()
    for trajectory_id in requested:
        record_path = _relative_member(
            root,
            record_pointer_for_trajectory(trajectory_id),
            label="trajectory record",
        )
        record = _load_json_object(record_path, label="trajectory record")
        item = _validate_record(
            record,
            root=root,
            manifest=manifest,
            manifest_file_sha256=manifest_file_sha256,
            scheduled=scheduled_by_id[trajectory_id],
        )
        raw_sha256 = record["raw_provider_trace_sha256"]
        audit_sha256 = record["runtime_audit_sha256"]
        pack_sha256 = record["execution_pack_sha256"]
        if raw_sha256 in raw_hashes:
            raise M3KExternalEvidenceError(
                "raw provider trace evidence is reused across trajectories"
            )
        if audit_sha256 in audit_hashes:
            raise M3KExternalEvidenceError(
                "runtime audit evidence is reused across trajectories"
            )
        if pack_sha256 in pack_hashes:
            raise M3KExternalEvidenceError(
                "execution pack evidence is reused across trajectories"
            )
        raw_hashes.add(raw_sha256)
        audit_hashes.add(audit_sha256)
        pack_hashes.add(pack_sha256)
        validated[trajectory_id] = item

    coverage_check(
        root,
        expected_pointers={
            item["record"]["raw_provider_trace_pointer"] for item in validated.values()
        },
        bucket_name="raw",
        label="raw provider trace",
    )
    coverage_check(
        root,
        expected_pointers={
            item["record"]["runtime_audit_pointer"] for item in validated.values()
        },
        bucket_name="runtime-audits",
        label="runtime audit",
    )
    coverage_check(
        root,
        expected_pointers={
            item["record"]["execution_pack_pointer"] for item in validated.values()
        },
        bucket_name="execution-packs",
        label="execution pack",
    )
    return {
        "manifest": manifest,
        "manifest_file_sha256": manifest_file_sha256,
        "records": {key: value["record"] for key, value in validated.items()},
        "unique_raw_provider_trace_count": len(raw_hashes),
        "unique_runtime_audit_count": len(audit_hashes),
        "unique_execution_pack_count": len(pack_hashes),
    }


class _ExternalReplayExecutor(M3KVariantExecutor):
    """Adapt validated external records to the immutable core evaluator protocol."""

    def __init__(self, *, role: VariantRole, records: Mapping[str, dict[str, Any]]) -> None:
        self._role = role
        self._records = records

    def run(
        self,
        variant: Any,
        cells: tuple[M3KCell, ...],
        lineage: M3KVariantLineage,
    ) -> Sequence[M3KTrajectoryResult]:
        rows: list[M3KTrajectoryResult] = []
        for cell in cells:
            trajectory_id = f"{self._role.value}:{cell.cell_id}"
            record = self._records.get(trajectory_id)
            if record is None:
                raise M3KExternalEvidenceError("validated external replay is missing a scheduled trajectory")
            if record["variant_role"] != self._role.value:
                raise M3KExternalEvidenceError("validated external replay variant role drifted")
            rows.append(
                M3KTrajectoryResult(
                    cell_id=record["cell_id"],
                    task_id=record["task_id"],
                    split=M3KSplit(record["split"]),
                    trial_index=record["trial_index"],
                    verifier_id=record["verifier_id"],
                    task_instruction_sha256=record["task_instruction_sha256"],
                    variant_role=VariantRole(record["variant_role"]),
                    variant_id=record["variant_id"],
                    variant_sha256=record["variant_sha256"],
                    evaluation_contract_sha256=record["evaluation_contract_sha256"],
                    trace_id=record["trajectory_id"],
                    raw_trace_sha256=record["raw_provider_trace_sha256"],
                    verifier_passed=record["verifier_passed"],
                    verifier_score=float(record["verifier_score"]),
                    cost=float(record["cost"]),
                    actual_invocation_evidence_complete=record[
                        "actual_invocation_evidence_complete"
                    ],
                    invoked_skill_ids=tuple(record["invoked_skill_ids"]),
                    oracle_skill_ids=tuple(record["oracle_skill_ids"]),
                )
            )
        return tuple(rows)


def assemble_m3k_external_evidence(
    *,
    bound_manifest_path: Path,
    evidence_root: Path,
    output_root: Path,
    criteria: M3KPromotionCriteria | None = None,
) -> Path:
    """Require all 522 records and replay them through the core M3-K policy gate.

    ``output_root`` is new-only and receives a portable copy of the validated
    provider traces/audits plus a report.  The report records requested-model
    attestation only; assembling bytes never itself claims a live full-87 model
    result or a provider-resolved/native-skill identity.
    """

    _, manifest, manifest_file_sha256 = _load_bound_manifest(bound_manifest_path)
    scheduled_by_id = _scheduled_by_trajectory(manifest)
    root = _regular_directory(evidence_root, label="evidence_root")
    _ensure_root_layout(root, allow_missing=False)
    if {entry.name for entry in root.iterdir()} != set(_ROOT_BUCKETS):
        raise M3KExternalEvidenceError("evidence_root must contain exactly the M3-K evidence buckets")
    expected_records = {record_pointer_for_trajectory(item) for item in scheduled_by_id}
    _assert_bucket_coverage(
        root,
        expected_pointers=expected_records,
        bucket_name="trajectories",
        label="trajectory record",
    )

    validated: dict[str, dict[str, Any]] = {}
    raw_hashes: set[str] = set()
    audit_hashes: set[str] = set()
    pack_hashes: set[str] = set()
    for trajectory_id, scheduled in scheduled_by_id.items():
        record_path = _relative_member(
            root,
            record_pointer_for_trajectory(trajectory_id),
            label="trajectory record",
        )
        record = _load_json_object(record_path, label="trajectory record")
        item = _validate_record(
            record,
            root=root,
            manifest=manifest,
            manifest_file_sha256=manifest_file_sha256,
            scheduled=scheduled,
        )
        raw_sha256 = record["raw_provider_trace_sha256"]
        audit_sha256 = record["runtime_audit_sha256"]
        pack_sha256 = record["execution_pack_sha256"]
        if raw_sha256 in raw_hashes:
            raise M3KExternalEvidenceError("raw provider trace evidence is reused across trajectories")
        if audit_sha256 in audit_hashes:
            raise M3KExternalEvidenceError("runtime audit evidence is reused across trajectories")
        if pack_sha256 in pack_hashes:
            raise M3KExternalEvidenceError("execution pack evidence is reused across trajectories")
        raw_hashes.add(raw_sha256)
        audit_hashes.add(audit_sha256)
        pack_hashes.add(pack_sha256)
        validated[trajectory_id] = item

    expected_raw = {
        item["record"]["raw_provider_trace_pointer"] for item in validated.values()
    }
    expected_audits = {
        item["record"]["runtime_audit_pointer"] for item in validated.values()
    }
    expected_packs = {
        item["record"]["execution_pack_pointer"] for item in validated.values()
    }
    _assert_bucket_coverage(
        root,
        expected_pointers=expected_raw,
        bucket_name="raw",
        label="raw provider trace",
    )
    _assert_bucket_coverage(
        root,
        expected_pointers=expected_audits,
        bucket_name="runtime-audits",
        label="runtime audit",
    )
    _assert_bucket_coverage(
        root,
        expected_pointers=expected_packs,
        bucket_name="execution-packs",
        label="execution pack",
    )

    contract = _contract_from_manifest(manifest)
    try:
        parent, proposal = load_proposal_bundle(manifest["proposal_binding"]["bundle"])
        result = run_m3k_policy_evaluation(
            contract=contract,
            parent=parent,
            proposal=proposal,
            executor_factory=lambda role: _ExternalReplayExecutor(
                role=role,
                records={key: item["record"] for key, item in validated.items()},
            ),
            criteria=criteria,
        )
    except (M3KProposalBindingError, M3KContractError) as exc:
        raise M3KExternalEvidenceError(f"M3-K replay failed closed: {exc}") from exc

    destination_root = _new_root(output_root, label="output_root")
    if destination_root.exists():
        raise M3KExternalEvidenceError("output_root must be new-only")
    try:
        destination_root.mkdir(parents=True, exist_ok=False)
        for bucket in ("raw", "runtime-audits", "execution-packs"):
            (destination_root / bucket).mkdir(exist_ok=False)
        for trajectory_id in sorted(validated):
            item = validated[trajectory_id]
            record = item["record"]
            raw_destination = destination_root / f"raw/{record['raw_provider_trace_sha256']}.bin"
            audit_destination = destination_root / f"runtime-audits/{record['runtime_audit_sha256']}.json"
            pack_destination = destination_root / f"execution-packs/{record['execution_pack_sha256']}.tar"
            _copy_new(item["raw_path"], raw_destination)
            _copy_new(item["audit_path"], audit_destination)
            _copy_new(item["pack_path"], pack_destination)
            if sha256_file(raw_destination) != record["raw_provider_trace_sha256"]:
                raise M3KExternalEvidenceError("portable raw provider trace hash mismatch")
            if sha256_file(audit_destination) != record["runtime_audit_sha256"]:
                raise M3KExternalEvidenceError("portable runtime audit hash mismatch")
            if sha256_file(pack_destination) != record["execution_pack_sha256"]:
                raise M3KExternalEvidenceError("portable execution pack hash mismatch")
        report = {
            "schema_version": 1,
            "evidence_kind": "m3k_external_execution_assembly",
            "bound_manifest_sha256": manifest["manifest_sha256"],
            "bound_manifest_file_sha256": manifest_file_sha256,
            "requested_model_contract": requested_model_contract(manifest),
            "coverage": {
                "expected_trajectories": 522,
                "recorded_trajectories": len(validated),
                "unique_raw_provider_traces": len(raw_hashes),
                "unique_runtime_audits": len(audit_hashes),
                "unique_execution_packs": len(pack_hashes),
                "complete": len(validated) == 522,
            },
            "promotion_report": result.to_dict(),
            "claim_boundary": {
                "assembly_is_live_model_execution": False,
                "provider_resolved_model_identity_claimed": False,
                "provider_native_skill_invocation_claimed": False,
                "full87_result_claimed_by_assembly": False,
            },
        }
        report_path = destination_root / "m3k-external-promotion-report.json"
        _write_json_new(report_path, report)
        return report_path
    except Exception:
        if destination_root.exists() and not destination_root.is_symlink():
            shutil.rmtree(destination_root)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record", help="seal one external M3-K trajectory")
    record.add_argument("--bound-manifest", type=Path, required=True)
    record.add_argument("--attestation", type=Path, required=True)
    record.add_argument("--raw-provider-trace", type=Path, required=True)
    record.add_argument("--runtime-audit", type=Path, required=True)
    record.add_argument("--execution-event", type=Path, required=True)
    record.add_argument("--raw-artifact-root", type=Path, required=True)
    record.add_argument("--evidence-root", type=Path, required=True)
    assemble = subparsers.add_parser("assemble", help="replay all 522 sealed trajectories")
    assemble.add_argument("--bound-manifest", type=Path, required=True)
    assemble.add_argument("--evidence-root", type=Path, required=True)
    assemble.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "record":
        result = record_m3k_external_trajectory(
            bound_manifest_path=args.bound_manifest,
            attestation_path=args.attestation,
            raw_provider_trace_path=args.raw_provider_trace,
            runtime_audit_path=args.runtime_audit,
            execution_event_path=args.execution_event,
            raw_artifact_root=args.raw_artifact_root,
            evidence_root=args.evidence_root,
        )
        print(f"trajectory_id={result['trajectory_id']}")
        print(f"record_pointer={record_pointer_for_trajectory(result['trajectory_id'])}")
        return 0
    report = assemble_m3k_external_evidence(
        bound_manifest_path=args.bound_manifest,
        evidence_root=args.evidence_root,
        output_root=args.output_root,
    )
    print(f"report={report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
