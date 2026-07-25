"""Execute one sealed M3-K cell through Codex and a fixed Docker MCP.

This is the producer between an immutable pilot or post-pilot operator source
and ``m3k_external_evidence``. It revalidates the selected cell, reconstructs
the bound harness variant, provisions a bounded ordered skill view, starts a
fresh task container, exposes only that container through the one-tool MCP
bridge, then runs the hidden verifier after the model process has ended.

The runner is intentionally one-cell-at-a-time. Post-pilot mode accepts only
the next evidence-derived pending trajectory. It never promotes a harness
candidate and it never converts partial coverage into a full-87 claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    validate_executor_capability,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file
from experiments.skillsbench.m3k_external_evidence import (
    M3KExternalEvidenceError,
    record_m3k_external_trajectory,
    requested_model_contract,
)
from experiments.skillsbench.external_corpus_admission import (
    ExternalCorpusAdmissionError,
    validate_external_corpus_report,
)
from experiments.skillsbench.m3k_full87_progress import (
    M3KFull87ProgressError,
    validate_m3k_full87_progress,
)
from experiments.skillsbench.materialize_m3k_external_cell import (
    DEFAULT_LIBRARY_SCALE,
    validate_materialized_m3k_cell,
)
from experiments.skillsbench.prepare_m3k_pilot_operator_bundle import (
    validate_m3k_pilot_operator_bundle,
)
from experiments.skillsbench.validate_m3k_first_cell_evidence import (
    M3KFirstCellEvidenceError,
    validate_m3k_first_cell_report,
)
from experiments.skillsbench.probe_codex_mcp_capability import (
    DEFAULT_CODEX_CANDIDATES,
    DEFAULT_SERVER,
    NATIVE_TOOL_FEATURES_TO_DISABLE,
    codex_mcp_stdio_launch,
    detect_codex_executable,
    probe_codex_feature_suppression,
    summarize_recorded_audit,
)
from src.merlin_harness.codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl
from src.merlin_harness.harness import (
    HarnessEvent,
    HarnessVariantSpec,
    Hook,
    build_runtime_from_variant,
)
from src.merlin_harness.management import content_sha256
from src.merlin_harness.models import (
    LifecycleStatus,
    SkillArtifact,
    SkillStep,
    TaskSpec,
    VerifierSpec,
)
from src.merlin_harness.provisioning import LexicalProvisioner


MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._-]{0,255}$")
ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
MAX_RAW_BYTES = 16_000_000
DEFAULT_EXPOSURE_BUDGET = 10
MCP_SERVER_KEY = "merlin_harness_task"
FORBIDDEN_NATIVE_ITEM_TYPES = frozenset(
    {"command_execution", "file_change", "computer_use"}
)
LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class M3KCodexCellError(ValueError):
    """Raised when one external M3-K cell cannot produce strict evidence."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _write_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(value)
    except FileExistsError as exc:
        raise M3KCodexCellError(f"refusing to overwrite runtime artifact: {path.name}") from exc


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise M3KCodexCellError(f"{label} must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M3KCodexCellError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise M3KCodexCellError(f"{label} must be a JSON object")
    return value


def validate_executor_binding(
    *,
    bound_manifest_path: Path,
    executor_capability_path: Path,
    model: str,
    effort: str,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Bind this live run to the exact eligible capability and model contract."""

    bound = _load_json(bound_manifest_path, label="bound manifest")
    if executor_capability_path.is_symlink() or not executor_capability_path.is_file():
        raise M3KCodexCellError("executor capability must be a regular file")
    try:
        capability_bytes = executor_capability_path.read_bytes()
        capability = json.loads(capability_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KCodexCellError("cannot read executor capability") from exc
    if not isinstance(capability, dict):
        raise M3KCodexCellError("executor capability must be a JSON object")
    capability_binding = bound.get("executor_capability")
    gate = bound.get("execution_gate")
    if (
        bound.get("status") != "ready"
        or not isinstance(capability_binding, dict)
        or capability_binding.get("provided") is not True
        or capability_binding.get("eligible") is not True
        or not isinstance(gate, dict)
        or gate.get("execution_allowed") is not True
    ):
        raise M3KCodexCellError("bound manifest does not authorize live M3-K execution")
    expected_capability_sha256 = capability_binding.get("file_sha256")
    actual_capability_sha256 = _sha256_bytes(capability_bytes)
    if expected_capability_sha256 != actual_capability_sha256:
        raise M3KCodexCellError("executor capability file hash drifted from the bound manifest")
    try:
        eligible, failures, safe_summary = validate_executor_capability(capability)
    except M3KProposalBindingError as exc:
        raise M3KCodexCellError(f"executor capability is invalid: {exc}") from exc
    if not eligible or failures:
        raise M3KCodexCellError(
            "executor capability is no longer eligible: " + ",".join(failures)
        )
    try:
        requested = requested_model_contract(bound)
    except M3KExternalEvidenceError as exc:
        raise M3KCodexCellError(f"requested model contract is invalid: {exc}") from exc
    expected = {
        "backend": "strict-container-agent-executor-unbound",
        "model_id": model,
        "effort": effort,
        "tools": ["fixed-container-exec"],
    }
    if requested != expected:
        raise M3KCodexCellError("runtime arguments drifted from the bound model/tool contract")
    return bound, capability_bytes, safe_summary


def validate_admission_binding(
    *,
    admission_start_audit_path: Path,
    source_snapshot_manifest_path: Path,
    expected_start_sha256: str,
    expected_command_sha256: str,
) -> tuple[bytes, bytes, dict[str, Any]]:
    """Bind one live cell to the host lease and exact verified source snapshot."""

    if not LOWER_SHA256_RE.fullmatch(expected_start_sha256):
        raise M3KCodexCellError("admission start environment hash is invalid")
    if not LOWER_SHA256_RE.fullmatch(expected_command_sha256):
        raise M3KCodexCellError("admitted command environment hash is invalid")
    start_path = admission_start_audit_path.expanduser().resolve(strict=True)
    snapshot_path = source_snapshot_manifest_path.expanduser().resolve(strict=True)
    for path, label in (
        (start_path, "admission start audit"),
        (snapshot_path, "source snapshot manifest"),
    ):
        if path.is_symlink() or not path.is_file():
            raise M3KCodexCellError(f"{label} must be a regular file")
    try:
        start_bytes = start_path.read_bytes()
        snapshot_bytes = snapshot_path.read_bytes()
        start = json.loads(start_bytes)
        snapshot = json.loads(snapshot_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KCodexCellError("cannot read DESKTOP admission binding") from exc
    if not isinstance(start, dict) or not isinstance(snapshot, dict):
        raise M3KCodexCellError("DESKTOP admission binding must contain JSON objects")
    if _sha256_bytes(start_bytes) != expected_start_sha256:
        raise M3KCodexCellError("admission start audit drifted after lease acquisition")
    if (
        start.get("schema_version") != 1
        or start.get("diagnostic") != "desktop_host_admission"
        or start.get("command_recorded") is not False
        or start.get("command_sha256") != expected_command_sha256
    ):
        raise M3KCodexCellError("DESKTOP admission start contract is invalid")
    if not isinstance(start.get("started_unix"), (int, float)):
        raise M3KCodexCellError("DESKTOP admission start time is invalid")
    if not LOWER_SHA256_RE.fullmatch(str(start.get("global_lock_path_sha256", ""))):
        raise M3KCodexCellError("DESKTOP admission lock binding is invalid")
    legacy = start.get("legacy_runs")
    if not isinstance(legacy, list) or not legacy:
        raise M3KCodexCellError("DESKTOP admission must inspect a legacy run root")
    for run in legacy:
        if (
            not isinstance(run, dict)
            or run.get("pid_alive") is not False
            or run.get("lock_held") is not False
        ):
            raise M3KCodexCellError("DESKTOP legacy manager was not proven idle")
    docker = start.get("docker")
    if (
        not isinstance(docker, dict)
        or not isinstance(docker.get("running_container_count"), int)
        or not isinstance(docker.get("running_containers"), list)
    ):
        raise M3KCodexCellError("DESKTOP Docker admission record is invalid")
    source = start.get("source_snapshot")
    corpus = snapshot.get("external_pinned_corpus")
    if not isinstance(source, dict) or not isinstance(corpus, dict):
        raise M3KCodexCellError("DESKTOP source snapshot binding is missing")
    expected_source = {
        "manifest_file_sha256": _sha256_bytes(snapshot_bytes),
        "entries_sha256": snapshot.get("entries_sha256"),
        "entry_count": snapshot.get("entry_count"),
        "pinned_upstream_commit": corpus.get("upstream_commit"),
    }
    if source != expected_source:
        raise M3KCodexCellError("DESKTOP source snapshot drifted from admission")
    if (
        not LOWER_SHA256_RE.fullmatch(str(source.get("manifest_file_sha256", "")))
        or not LOWER_SHA256_RE.fullmatch(str(source.get("entries_sha256", "")))
        or not isinstance(source.get("entry_count"), int)
        or source["entry_count"] < 1
        or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("pinned_upstream_commit", "")))
    ):
        raise M3KCodexCellError("DESKTOP source snapshot fields are invalid")
    external_corpus = start.get("external_task_corpus")
    if not isinstance(external_corpus, dict):
        raise M3KCodexCellError("DESKTOP external task corpus admission is missing")
    try:
        validate_external_corpus_report(external_corpus)
    except ExternalCorpusAdmissionError as exc:
        raise M3KCodexCellError(
            f"DESKTOP external task corpus admission is invalid: {exc}"
        ) from exc
    if (
        external_corpus.get("source_snapshot_manifest_sha256")
        != expected_source["manifest_file_sha256"]
        or external_corpus.get("upstream_commit")
        != expected_source["pinned_upstream_commit"]
        or external_corpus.get("expected_manifest_sha256")
        != corpus.get("expected_manifest_sha256")
        or external_corpus.get("corpus_provenance_file_sha256")
        != corpus.get("corpus_provenance_file_sha256")
        or external_corpus.get("regular_blob_count")
        != corpus.get("regular_blob_count")
    ):
        raise M3KCodexCellError(
            "DESKTOP external task corpus drifted from source snapshot binding"
        )
    return start_bytes, snapshot_bytes, {
        "admission_start_sha256": expected_start_sha256,
        "admitted_command_sha256": expected_command_sha256,
        "source_snapshot_manifest_sha256": expected_source["manifest_file_sha256"],
        "source_snapshot_entries_sha256": expected_source["entries_sha256"],
        "source_snapshot_entry_count": expected_source["entry_count"],
        "pinned_upstream_commit": expected_source["pinned_upstream_commit"],
        "external_corpus_report_sha256": external_corpus["report_sha256"],
        "external_corpus_provenance_file_sha256": external_corpus[
            "corpus_provenance_file_sha256"
        ],
        "external_corpus_tasks_root_path_sha256": external_corpus[
            "tasks_root_path_sha256"
        ],
        "external_corpus_manifest_sha256": external_corpus[
            "local_manifest_sha256"
        ],
        "external_corpus_regular_blob_count": external_corpus[
            "regular_blob_count"
        ],
    }


def validate_materialized_corpus_binding(
    *, contract: Mapping[str, Any], admission_summary: Mapping[str, Any]
) -> None:
    """Require the staged task bytes and live external corpus to share provenance."""

    materialized_corpus = contract.get("task_corpus_source")
    if (
        not isinstance(materialized_corpus, dict)
        or materialized_corpus.get("upstream_commit")
        != admission_summary.get("pinned_upstream_commit")
        or materialized_corpus.get("corpus_provenance_file_sha256")
        != admission_summary.get("external_corpus_provenance_file_sha256")
        or materialized_corpus.get("expected_manifest_sha256")
        != admission_summary.get("external_corpus_manifest_sha256")
        or materialized_corpus.get("local_manifest_sha256")
        != admission_summary.get("external_corpus_manifest_sha256")
        or materialized_corpus.get("regular_blob_count")
        != admission_summary.get("external_corpus_regular_blob_count")
        or materialized_corpus.get("tasks_root_path_sha256")
        != admission_summary.get("external_corpus_tasks_root_path_sha256")
        or materialized_corpus.get("runtime_admission_must_match") is not True
    ):
        raise M3KCodexCellError(
            "materialized task corpus differs from live DESKTOP admission"
        )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _task_body(value: str) -> str:
    if not value.startswith("---"):
        return value.strip()
    parts = value.split("---", 2)
    return (parts[2] if len(parts) == 3 else value).strip()


def _frontmatter_scalar(text: str, key: str, default: str) -> str:
    if not text.startswith("---"):
        return default
    parts = text.split("---", 2)
    if len(parts) != 3:
        return default
    match = re.search(rf"(?m)^{re.escape(key)}:\s*['\"]?([^\n'\"]+)['\"]?\s*$", parts[1])
    return match.group(1).strip() if match else default


def _variant(payload: Mapping[str, Any]) -> HarnessVariantSpec:
    required = {"id", "parent_id", "summary", "processor_manifest", "policy", "metadata"}
    if set(payload) != required:
        raise M3KCodexCellError("harness variant schema drifted")
    return HarnessVariantSpec(
        id=payload["id"],
        parent_id=payload["parent_id"],
        summary=payload["summary"],
        processor_manifest=payload["processor_manifest"],
        policy=payload["policy"],
        metadata=payload["metadata"],
    )


def _skill_artifacts(cell_root: Path, presentation_order: Sequence[str]) -> list[SkillArtifact]:
    skills: list[SkillArtifact] = []
    for skill_id in presentation_order:
        if not isinstance(skill_id, str) or not SAFE_ID_RE.fullmatch(skill_id):
            raise M3KCodexCellError("staged skill ID is unsafe")
        skill_md = cell_root / "skills" / skill_id / "SKILL.md"
        if skill_md.is_symlink() or not skill_md.is_file():
            raise M3KCodexCellError(f"staged skill has no safe SKILL.md: {skill_id}")
        text = skill_md.read_text(encoding="utf-8", errors="strict")
        name = _frontmatter_scalar(text, "name", skill_id)
        description = _frontmatter_scalar(text, "description", name)
        skills.append(
            SkillArtifact(
                id=skill_id,
                name=name,
                description=description,
                trigger=description,
                steps=[SkillStep(id="skill-body", description=description)],
                status=LifecycleStatus.ACTIVE,
            )
        )
    return skills


def derive_provisioning(cell_root: Path, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the exact harness variant to deterministic full-library retrieval."""

    staged = contract["staged_artifacts"]
    order = staged["presentation_order"]
    if not isinstance(order, list) or len(order) != 209:
        raise M3KCodexCellError("M3-K provisioning requires the full ordered 209-skill library")
    variant_payload = _load_json(cell_root / "harness-variant.json", label="harness variant")
    variant = _variant(variant_payload)
    try:
        runtime = build_runtime_from_variant(variant)
    except (KeyError, TypeError, ValueError) as exc:
        raise M3KCodexCellError(f"cannot reconstruct harness variant: {exc}") from exc
    task_text = (cell_root / "task-visible" / "task.md").read_text(encoding="utf-8")
    oracle_ids = _load_json(
        cell_root / "attestation.template.json", label="attestation template"
    )["oracle_skill_ids"]
    task = TaskSpec(
        id=contract["trajectory"]["task_id"],
        instruction=_task_body(task_text),
        verifier=VerifierSpec(name=contract["trajectory"]["verifier_id"], kind="command"),
        oracle_skill_ids=list(oracle_ids),
    )
    event = HarnessEvent(
        hook=Hook.BEFORE_PROVISION,
        task=task,
        skills=_skill_artifacts(cell_root, order),
        metadata={"exposure_budget": variant.policy.get("exposure_budget", DEFAULT_EXPOSURE_BUDGET)},
    )
    event = runtime.emit(event)
    budget = event.metadata.get("exposure_budget", DEFAULT_EXPOSURE_BUDGET)
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= DEFAULT_EXPOSURE_BUDGET:
        raise M3KCodexCellError("harness exposure budget must be from 1 through 10")
    provisioned = LexicalProvisioner(exposure_budget=budget).provision(
        task.instruction, event.skills
    )
    selection_event = HarnessEvent(
        hook=Hook.BEFORE_SELECT,
        task=task,
        skills=event.skills,
        provisioned_skills=provisioned,
        metadata=dict(event.metadata),
    )
    selection_event = runtime.emit(selection_event)
    provisioned_ids = [item.id for item in selection_event.provisioned_skills]
    if len(provisioned_ids) != len(set(provisioned_ids)):
        raise M3KCodexCellError("harness provisioned duplicate skill IDs")
    if any(item not in order for item in provisioned_ids):
        raise M3KCodexCellError("harness provisioned a skill outside the frozen library")
    return {
        "schema_version": 1,
        "variant_id": variant.id,
        "variant_sha256": content_sha256(variant_payload),
        "task_id": task.id,
        "task_instruction_sha256": contract["trajectory"]["task_instruction_sha256"],
        "library_size": len(order),
        "library_order_sha256": contract["trajectory"]["library_order_sha256"],
        "requested_exposure_budget": variant.policy.get(
            "exposure_budget", DEFAULT_EXPOSURE_BUDGET
        ),
        "effective_exposure_budget": budget,
        "provisioned_skill_ids": provisioned_ids,
        "oracle_skill_ids": list(oracle_ids),
        "processor_audit": event.audit_events + selection_event.audit_events,
        "notes": event.notes + selection_event.notes,
        "boundary": {
            "full_library_is_candidate_pool": True,
            "provisioned_ids_are_model_visible": True,
            "provisioned_ids_are_not_invocation_evidence": True,
            "skill_associated_mcp_exec_is_invocation_evidence": True,
            "provider_native_skill_invocation_claimed": False,
        },
    }


def build_codex_command(
    *,
    codex_executable: Path,
    server_path: Path,
    raw_root: Path,
    container_id: str,
    container_workdir: str,
    allowed_skill_ids_file: Path,
    model: str,
    effort: str,
    timeout_sec: int,
) -> list[str]:
    """Build a feature-suppressed, per-run one-MCP Codex invocation."""

    audit_path = raw_root / "mcp-audit.jsonl"
    server_args = [
        str(server_path),
        "--container",
        container_id,
        "--workdir",
        container_workdir,
        "--timeout-sec",
        str(timeout_sec),
        "--audit-log",
        str(audit_path),
        "--allowed-skill-ids-file",
        str(allowed_skill_ids_file),
    ]
    launch = codex_mcp_stdio_launch(
        codex_executable=codex_executable,
        server_argv=server_args,
    )
    command = [str(codex_executable)]
    for feature in NATIVE_TOOL_FEATURES_TO_DISABLE:
        command.extend(("--disable", feature))
    command.extend(
        (
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "--model",
            model,
            "-c",
            f"model_reasoning_effort={_toml_string(effort)}",
            "-c",
            "developer_instructions=\"Use only the configured MCP exec tool. "
            "Never use or claim a host-native tool. Set skill_id only when a command "
            "reads or applies that provisioned skill; omit it for no-skill work.\"",
            "-c",
            f"mcp_servers.{MCP_SERVER_KEY}.command={_toml_string(launch['command'])}",
            "-c",
            f"mcp_servers.{MCP_SERVER_KEY}.args={json.dumps(launch['args'])}",
            "-c",
            f"mcp_servers.{MCP_SERVER_KEY}.enabled=true",
            "-c",
            f"mcp_servers.{MCP_SERVER_KEY}.required=true",
            "--cd",
            str(raw_root / "empty-workspace"),
            "--output-last-message",
            str(raw_root / "last-message.txt"),
            "-",
        )
    )
    return command


def _run(
    argv: Sequence[str],
    *,
    timeout_sec: float,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            input=input_text,
            text=True,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise M3KCodexCellError(f"command timed out after {timeout_sec:g}s") from exc


def _require_success(report: subprocess.CompletedProcess[str], *, label: str) -> None:
    if report.returncode != 0:
        raise M3KCodexCellError(f"{label} failed with exit {report.returncode}")


def _item_type_counts(raw_jsonl: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line_number, line in enumerate(raw_jsonl.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise M3KCodexCellError(f"Codex JSONL line {line_number} is malformed") from exc
        if not isinstance(event, dict):
            raise M3KCodexCellError("Codex JSONL event must be an object")
        if event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise M3KCodexCellError("Codex item event has no typed item")
        item_type = item["type"]
        counts[item_type] = counts.get(item_type, 0) + 1
    return dict(sorted(counts.items()))


def _token_cost(raw_jsonl: str) -> float:
    totals: list[float] = []
    for line in raw_jsonl.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        total = usage.get("total_tokens")
        if isinstance(total, (int, float)) and not isinstance(total, bool) and total >= 0:
            totals.append(float(total))
            continue
        parts = [usage.get(key) for key in ("input_tokens", "output_tokens")]
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 for value in parts):
            totals.append(float(sum(parts)))
    return max(totals, default=0.0)


def _audit_skill_ids(path: Path, allowed: Sequence[str]) -> tuple[list[str], int]:
    allowed_set = set(allowed)
    invoked: list[str] = []
    call_count = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise M3KCodexCellError(f"MCP audit line {line_number} is malformed") from exc
        if not isinstance(event, dict):
            raise M3KCodexCellError("MCP audit event must be an object")
        if event.get("method") != "tools/call" or event.get("tool_name") != "exec":
            continue
        call_count += 1
        skill_id = event.get("skill_id")
        if skill_id is None:
            continue
        if skill_id not in allowed_set:
            raise M3KCodexCellError("MCP audit contains an unprovisioned skill invocation")
        if skill_id not in invoked:
            invoked.append(skill_id)
    return invoked, call_count


def _safe_cell(root: Path, pointer: Any) -> Path:
    if not isinstance(pointer, str) or not pointer:
        raise M3KCodexCellError("operator cell pointer is malformed")
    pure = PurePosixPath(pointer)
    if pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
        raise M3KCodexCellError("operator cell pointer escapes the bundle")
    candidate = root.joinpath(*pure.parts)
    if candidate.is_symlink():
        raise M3KCodexCellError("operator cell pointer is a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise M3KCodexCellError("operator cell pointer escapes or is missing") from exc
    return resolved


def resolve_m3k_operator_cell(
    *,
    bound_manifest_path: Path,
    library_scale_manifest_path: Path,
    pilot_manifest_path: Path,
    evidence_root: Path,
    operator_bundle: Path | None = None,
    ordinal: int | None = None,
    materialized_cell_root: Path | None = None,
    batch_plan_path: Path | None = None,
    progress_path: Path | None = None,
    pilot_report_path: Path | None = None,
    first_cell_report_path: Path | None = None,
) -> dict[str, Any]:
    """Resolve exactly one pilot or next-pending expansion cell without running it."""

    pilot_mode = operator_bundle is not None
    expansion_mode = materialized_cell_root is not None
    if pilot_mode == expansion_mode:
        raise M3KCodexCellError(
            "choose exactly one operator source: pilot bundle or materialized expansion cell"
        )
    if pilot_mode:
        if ordinal not in range(1, 7):
            raise M3KCodexCellError("pilot ordinal must be from 1 through 6")
        if any(value is not None for value in (batch_plan_path, progress_path, pilot_report_path)):
            raise M3KCodexCellError("pilot mode must not mix post-pilot controls")
        if ordinal == 1 and first_cell_report_path is not None:
            raise M3KCodexCellError("pilot ordinal 1 must run before a first-cell report exists")
        if ordinal > 1 and first_cell_report_path is None:
            raise M3KCodexCellError(
                "pilot ordinals 2 through 6 require a validated ordinal-1 report"
            )
        root = operator_bundle.expanduser().resolve(strict=True)
        manifest = validate_m3k_pilot_operator_bundle(
            bundle_root=root,
            bound_manifest_path=bound_manifest_path,
            pilot_manifest_path=pilot_manifest_path,
            library_scale_manifest_path=library_scale_manifest_path,
        )
        entry = manifest["cells"][ordinal - 1]
        cell_root = _safe_cell(root, entry["cell_pointer"])
        contract = validate_materialized_m3k_cell(
            cell_root, expected_contract_sha256=entry["execution_contract_sha256"]
        )
        first_cell_gate: dict[str, Any] | None = None
        if ordinal > 1:
            try:
                first_cell_report = validate_m3k_first_cell_report(
                    bound_manifest_path=bound_manifest_path,
                    pilot_manifest_path=pilot_manifest_path,
                    evidence_root=evidence_root,
                    report_path=first_cell_report_path,
                )
            except M3KFirstCellEvidenceError as exc:
                raise M3KCodexCellError(str(exc)) from exc
            first_cell_gate = {
                "report_sha256": first_cell_report["report_sha256"],
                "report_file_sha256": sha256_file(first_cell_report_path),
                "ordinal_1_trajectory_id": first_cell_report["first_cell"][
                    "trajectory_id"
                ],
                "ordinals_2_through_6_allowed": True,
            }
        operator_source = {
            "scope": "six_cell_pilot",
            "operator_bundle_sha256": manifest["operator_bundle_sha256"],
            "pilot_manifest_sha256": manifest["source"]["pilot_manifest_sha256"],
            "execution_contract_sha256": contract["execution_contract_sha256"],
            "first_cell_gate": first_cell_gate,
        }
        execution_scope = "six_cell_pilot"
    else:
        if ordinal is not None:
            raise M3KCodexCellError("post-pilot ordinal is derived and must be omitted")
        if first_cell_report_path is not None:
            raise M3KCodexCellError("post-pilot mode uses the complete pilot report only")
        if any(
            value is None
            for value in (batch_plan_path, progress_path, pilot_report_path)
        ):
            raise M3KCodexCellError(
                "post-pilot mode requires batch plan, progress snapshot, and pilot report"
            )
        try:
            progress = validate_m3k_full87_progress(
                progress_path=progress_path,
                plan_path=batch_plan_path,
                bound_manifest_path=bound_manifest_path,
                library_scale_manifest_path=library_scale_manifest_path,
                pilot_manifest_path=pilot_manifest_path,
                pilot_report_path=pilot_report_path,
                evidence_root=evidence_root,
            )
        except M3KFull87ProgressError as exc:
            raise M3KCodexCellError(str(exc)) from exc
        next_pending = progress.get("next_pending")
        if not isinstance(next_pending, dict):
            raise M3KCodexCellError("post-pilot schedule has no pending trajectory")
        expanded = materialized_cell_root.expanduser()
        if expanded.is_symlink():
            raise M3KCodexCellError("materialized expansion cell must not be a symlink")
        try:
            cell_root = expanded.resolve(strict=True)
        except OSError as exc:
            raise M3KCodexCellError("materialized expansion cell is missing") from exc
        if not cell_root.is_dir():
            raise M3KCodexCellError("materialized expansion cell must be a directory")
        contract = validate_materialized_m3k_cell(cell_root)
        trajectory_id = contract.get("trajectory", {}).get("trajectory_id")
        if trajectory_id != next_pending.get("trajectory_id"):
            raise M3KCodexCellError(
                "materialized expansion cell is not the next pending trajectory"
            )
        ordinal = next_pending.get("ordinal")
        if not isinstance(ordinal, int) or not 1 <= ordinal <= 522:
            raise M3KCodexCellError("derived post-pilot ordinal is invalid")
        operator_source = {
            "scope": "post_pilot_full87",
            "batch_id": progress["batch_id"],
            "batch_plan_sha256": progress["source"]["plan_sha256"],
            "progress_snapshot_sha256": progress["snapshot_sha256"],
            "progress_snapshot_file_sha256": sha256_file(progress_path),
            "next_work_key": next_pending["work_key"],
            "execution_contract_sha256": contract["execution_contract_sha256"],
        }
        execution_scope = "post_pilot_full87"
    return {
        "execution_scope": execution_scope,
        "ordinal": ordinal,
        "cell_root": cell_root,
        "contract": contract,
        "operator_source": operator_source,
    }


def _docker_image_name(contract: Mapping[str, Any]) -> str:
    task = re.sub(r"[^a-z0-9_.-]+", "-", contract["trajectory"]["task_id"].lower())
    environment = contract["staged_artifacts"].get("task_environment")
    if not isinstance(environment, dict) or not isinstance(environment.get("records_sha256"), str):
        raise M3KCodexCellError("task environment tree hash is unavailable")
    return f"theking-m3k-{task}:{environment['records_sha256'][:12]}"


def _container_workdir(image_inspect: Any) -> str:
    if not isinstance(image_inspect, list) or len(image_inspect) != 1:
        raise M3KCodexCellError("Docker image inspect must contain exactly one row")
    config = image_inspect[0].get("Config")
    value = config.get("WorkingDir") if isinstance(config, dict) else None
    if not isinstance(value, str) or not value.strip():
        return "/root"
    if not value.startswith("/") or "\x00" in value or "\n" in value:
        raise M3KCodexCellError("Docker image workdir is unsafe")
    return value.rstrip("/") or "/"


def _prompt(task_body: str, provisioned_ids: Sequence[str]) -> str:
    skills = "\n".join(f"- {skill_id}: /merlin/skills/{skill_id}/SKILL.md" for skill_id in provisioned_ids)
    if not skills:
        skills = "- none; solve with general reasoning and omit skill_id on every exec call"
    return (
        "Solve the benchmark task inside the fixed container. Use only the MCP exec tool. "
        "The task instruction is also mounted read-only at /merlin/task.md. Inspect files "
        "and create the requested outputs in the container working directory. Do not look "
        "for a verifier or oracle; neither is available during this turn.\n\n"
        f"Provisioned skills (and only these) are:\n{skills}\n\n"
        "When a command reads or applies one listed skill, pass its exact skill_id. Otherwise "
        "omit skill_id. Finish only after checking the requested output exists.\n\n"
        f"Task:\n{task_body}\n"
    )


def run_m3k_codex_mcp_cell(
    *,
    bound_manifest_path: Path,
    executor_capability_path: Path,
    admission_start_audit_path: Path,
    source_snapshot_manifest_path: Path,
    pilot_manifest_path: Path,
    library_scale_manifest_path: Path,
    codex_executable: Path,
    server_path: Path,
    raw_root: Path,
    evidence_root: Path,
    operator_bundle: Path | None = None,
    ordinal: int | None = None,
    materialized_cell_root: Path | None = None,
    batch_plan_path: Path | None = None,
    progress_path: Path | None = None,
    pilot_report_path: Path | None = None,
    first_cell_report_path: Path | None = None,
    model: str = "gpt-5.6-terra",
    effort: str = "high",
    model_timeout_sec: int = 900,
    verifier_timeout_sec: int = 900,
) -> dict[str, Any]:
    """Run and seal one operator cell; Docker/model calls are not retryable here."""

    if not MODEL_RE.fullmatch(model) or effort not in ALLOWED_EFFORTS:
        raise M3KCodexCellError("requested model or effort is unsupported")
    if not 1 <= model_timeout_sec <= 3600 or not 1 <= verifier_timeout_sec <= 3600:
        raise M3KCodexCellError("model/verifier timeout must be from 1 through 3600 seconds")
    resolved_operator = resolve_m3k_operator_cell(
        bound_manifest_path=bound_manifest_path,
        pilot_manifest_path=pilot_manifest_path,
        library_scale_manifest_path=library_scale_manifest_path,
        evidence_root=evidence_root,
        operator_bundle=operator_bundle,
        ordinal=ordinal,
        materialized_cell_root=materialized_cell_root,
        batch_plan_path=batch_plan_path,
        progress_path=progress_path,
        pilot_report_path=pilot_report_path,
        first_cell_report_path=first_cell_report_path,
    )
    ordinal = resolved_operator["ordinal"]
    cell_root = resolved_operator["cell_root"]
    contract = resolved_operator["contract"]
    operator_source = resolved_operator["operator_source"]
    execution_scope = resolved_operator["execution_scope"]
    bound, capability_bytes, capability_summary = validate_executor_binding(
        bound_manifest_path=bound_manifest_path,
        executor_capability_path=executor_capability_path,
        model=model,
        effort=effort,
    )
    expected_start_sha256 = os.environ.get(
        "MERLIN_DESKTOP_ADMISSION_START_SHA256", ""
    )
    expected_command_sha256 = os.environ.get(
        "MERLIN_DESKTOP_ADMITTED_COMMAND_SHA256", ""
    )
    announced_start_path = os.environ.get("MERLIN_DESKTOP_ADMISSION_START", "")
    if not announced_start_path or Path(announced_start_path).resolve(strict=False) != (
        admission_start_audit_path.expanduser().resolve(strict=False)
    ):
        raise M3KCodexCellError("runner is not inside the announced DESKTOP admission lease")
    admission_bytes, snapshot_bytes, admission_summary = validate_admission_binding(
        admission_start_audit_path=admission_start_audit_path,
        source_snapshot_manifest_path=source_snapshot_manifest_path,
        expected_start_sha256=expected_start_sha256,
        expected_command_sha256=expected_command_sha256,
    )
    validate_materialized_corpus_binding(
        contract=contract, admission_summary=admission_summary
    )
    raw = raw_root.expanduser().resolve(strict=False)
    if raw.exists() or raw.is_symlink():
        raise M3KCodexCellError("raw root must be new-only")
    raw.mkdir(parents=True)
    (raw / "empty-workspace").mkdir()
    _write_new(
        raw / "executor-capability.json",
        capability_bytes,
    )
    _write_new(raw / "desktop-admission-start.json", admission_bytes)
    _write_new(raw / "source-snapshot-manifest.json", snapshot_bytes)
    codex = codex_executable.expanduser().resolve(strict=True)
    server = server_path.expanduser().resolve(strict=True)
    suppression = probe_codex_feature_suppression(codex)
    if suppression.get("all_requested_features_disabled") is not True:
        raise M3KCodexCellError("native tool feature suppression contract failed")
    _write_new(raw / "feature-suppression.json", _json_bytes(suppression))

    provisioning = derive_provisioning(cell_root, contract)
    provisioned_ids = provisioning["provisioned_skill_ids"]
    allowed_path = raw / "allowed-skill-ids.json"
    _write_new(allowed_path, _json_bytes(provisioned_ids))
    _write_new(raw / "provisioning.json", _json_bytes(provisioning))
    image = _docker_image_name(contract)
    container = f"theking-m3k-{ordinal}-{uuid.uuid4().hex[:12]}"
    environment = cell_root / "task-visible" / "environment"
    if environment.is_symlink() or not (environment / "Dockerfile").is_file():
        raise M3KCodexCellError("task environment has no safe Dockerfile")

    build = _run(
        ["docker", "build", "--label", "theking.m3k=true", "-t", image, str(environment)],
        timeout_sec=1800,
    )
    _write_new(raw / "docker-build.stdout.txt", build.stdout.encode("utf-8"))
    _write_new(raw / "docker-build.stderr.txt", build.stderr.encode("utf-8"))
    _require_success(build, label="Docker image build")
    image_report = _run(["docker", "image", "inspect", image], timeout_sec=60)
    _require_success(image_report, label="Docker image inspect")
    image_bytes = image_report.stdout.encode("utf-8")
    _write_new(raw / "image-inspect.json", image_bytes)
    try:
        image_inspect = json.loads(image_report.stdout)
    except json.JSONDecodeError as exc:
        raise M3KCodexCellError("Docker image inspect is malformed") from exc
    workdir = _container_workdir(image_inspect)
    image_id = image_inspect[0].get("Id")
    if not isinstance(image_id, str) or not image_id:
        raise M3KCodexCellError("Docker image inspect has no image ID")

    run_argv = [
        "docker",
        "run",
        "-d",
        "--name",
        container,
        "--network",
        "none",
        "--cpus",
        "1",
        "--memory",
        "4g",
        "--pids-limit",
        "512",
        "--label",
        f"theking.m3k.trajectory_sha256={_sha256_bytes(contract['trajectory']['trajectory_id'].encode())}",
        "--mount",
        f"type=bind,src={cell_root / 'task-visible' / 'task.md'},dst=/merlin/task.md,readonly",
    ]
    for skill_id in provisioned_ids:
        run_argv.extend(
            (
                "--mount",
                f"type=bind,src={cell_root / 'skills' / skill_id},dst=/merlin/skills/{skill_id},readonly",
            )
        )
    run_argv.extend((image, "sleep", "infinity"))
    started = _run(run_argv, timeout_sec=120)
    _require_success(started, label="Docker container start")
    try:
        container_report = _run(["docker", "inspect", container], timeout_sec=60)
        _require_success(container_report, label="Docker container inspect")
        container_bytes = container_report.stdout.encode("utf-8")
        _write_new(raw / "container-inspect.json", container_bytes)
        try:
            container_inspect = json.loads(container_report.stdout)
        except json.JSONDecodeError as exc:
            raise M3KCodexCellError("Docker container inspect is malformed") from exc
        if not isinstance(container_inspect, list) or len(container_inspect) != 1:
            raise M3KCodexCellError("Docker container inspect must contain one row")
        container_id = container_inspect[0].get("Id")
        state = container_inspect[0].get("State")
        if not isinstance(container_id, str) or not container_id:
            raise M3KCodexCellError("Docker container inspect has no container ID")
        if not isinstance(state, dict) or state.get("Running") is not True:
            raise M3KCodexCellError("Docker container is not running")

        command = build_codex_command(
            codex_executable=codex,
            server_path=server,
            raw_root=raw,
            container_id=container_id,
            container_workdir=workdir,
            allowed_skill_ids_file=allowed_path,
            model=model,
            effort=effort,
            timeout_sec=model_timeout_sec,
        )
        run_config = {
            "schema_version": 1,
            "trajectory_id": contract["trajectory"]["trajectory_id"],
            "execution_contract_sha256": contract["execution_contract_sha256"],
            "operator_source": operator_source,
            "requested_model": model,
            "requested_effort": effort,
            "requested_model_contract": requested_model_contract(bound),
            "executor_capability_file_sha256": sha256_file(
                raw / "executor-capability.json"
            ),
            "executor_capability_safe_summary": capability_summary,
            "desktop_admission": admission_summary,
            "container_id": container_id,
            "image_id": image_id,
            "container_workdir": workdir,
            "network_mode": "none",
            "provisioning_sha256": sha256_file(raw / "provisioning.json"),
            "allowed_skill_ids_sha256": sha256_file(allowed_path),
            "feature_suppression_sha256": suppression["features_list_sha256"],
            "codex_command_contract_sha256": _sha256_bytes(
                json.dumps(command[:-1], ensure_ascii=False, separators=(",", ":")).encode()
            ),
        }
        _write_new(raw / "run-config.json", _json_bytes(run_config))
        task_text = (cell_root / "task-visible" / "task.md").read_text(encoding="utf-8")
        model_report = _run(
            command,
            timeout_sec=model_timeout_sec + 60,
            cwd=raw / "empty-workspace",
            input_text=_prompt(_task_body(task_text), provisioned_ids),
        )
        raw_jsonl = model_report.stdout or ""
        if len(raw_jsonl.encode("utf-8")) > MAX_RAW_BYTES:
            raise M3KCodexCellError("Codex JSONL exceeded the raw evidence limit")
        _write_new(raw / "codex.jsonl", raw_jsonl.encode("utf-8"))
        _write_new(raw / "codex.stderr.txt", (model_report.stderr or "").encode("utf-8"))
        _require_success(model_report, label="Codex model execution")
        try:
            codex_summary = parse_codex_exec_jsonl(raw_jsonl)
        except CodexCliAdapterError as exc:
            raise M3KCodexCellError(str(exc)) from exc
        if (
            codex_summary.reported_model_ids
            and model not in codex_summary.reported_model_ids
        ):
            raise M3KCodexCellError(
                "provider-reported model identity differs from the bound request"
            )
        item_counts = _item_type_counts(raw_jsonl)
        forbidden = sorted(set(item_counts) & FORBIDDEN_NATIVE_ITEM_TYPES)
        if forbidden:
            raise M3KCodexCellError("Codex emitted a forbidden host-native tool item")
        mcp_audit = raw / "mcp-audit.jsonl"
        if not mcp_audit.is_file():
            raise M3KCodexCellError("MCP protocol audit is missing")
        audit_summary = summarize_recorded_audit(mcp_audit)
        invoked_ids, exec_count = _audit_skill_ids(mcp_audit, provisioned_ids)
        if (
            audit_summary.get("initialize_observed") is not True
            or audit_summary.get("tools_list_observed") is not True
            or exec_count < 1
            or audit_summary.get("exec_tool_call_count") != exec_count
        ):
            raise M3KCodexCellError("Codex MCP execution audit is incomplete")

        mkdir = _run(
            ["docker", "exec", container_id, "mkdir", "-p", "/verifier", "/logs/verifier"],
            timeout_sec=30,
        )
        _require_success(mkdir, label="hidden verifier destination setup")
        copied = _run(
            ["docker", "cp", f"{cell_root / 'verifier-hidden'}/.", f"{container_id}:/verifier"],
            timeout_sec=120,
        )
        _require_success(copied, label="hidden verifier copy")
        verifier = _run(
            [
                "docker",
                "exec",
                "-w",
                workdir,
                container_id,
                "timeout",
                "--signal=TERM",
                "--kill-after=2s",
                f"{verifier_timeout_sec}s",
                "bash",
                "-lc",
                "/verifier/test.sh",
            ],
            timeout_sec=verifier_timeout_sec + 15,
        )
        _write_new(raw / "verifier.stdout.txt", verifier.stdout.encode("utf-8"))
        _write_new(raw / "verifier.stderr.txt", verifier.stderr.encode("utf-8"))
        reward_report = _run(
            ["docker", "exec", container_id, "cat", "/logs/verifier/reward.txt"],
            timeout_sec=30,
        )
        _require_success(reward_report, label="verifier reward read")
        try:
            reward = float(reward_report.stdout.strip())
        except ValueError as exc:
            raise M3KCodexCellError("verifier reward is not numeric") from exc
        if not 0.0 <= reward <= 1.0:
            raise M3KCodexCellError("verifier reward must be in [0,1]")
        verifier_result = {
            "schema_version": 1,
            "exit_code": verifier.returncode,
            "reward": reward,
            "passed": reward >= 1.0,
            "stdout_sha256": sha256_file(raw / "verifier.stdout.txt"),
            "stderr_sha256": sha256_file(raw / "verifier.stderr.txt"),
            "hidden_verifier_tree_sha256": contract["staged_artifacts"]["hidden_verifier"][
                "records_sha256"
            ],
        }
        _write_new(raw / "verifier-result.json", _json_bytes(verifier_result))
        execution_event = {
            "schema_version": 1,
            "trajectory_id": contract["trajectory"]["trajectory_id"],
            "raw_artifact_hashes": {
                name: sha256_file(raw / name)
                for name in (
                    "allowed-skill-ids.json",
                    "codex.jsonl",
                    "codex.stderr.txt",
                    "mcp-audit.jsonl",
                    "executor-capability.json",
                    "feature-suppression.json",
                    "desktop-admission-start.json",
                    "source-snapshot-manifest.json",
                    "container-inspect.json",
                    "image-inspect.json",
                    "run-config.json",
                    "provisioning.json",
                    "docker-build.stdout.txt",
                    "docker-build.stderr.txt",
                    "verifier-result.json",
                    "verifier.stdout.txt",
                    "verifier.stderr.txt",
                )
            },
            "mcp_exec_call_count": exec_count,
            "invoked_skill_ids": invoked_ids,
            "provider_reported_model_ids": list(codex_summary.reported_model_ids),
            "forbidden_native_item_types": forbidden,
        }
        _write_new(raw / "execution-event.json", _json_bytes(execution_event))

        attestation = _load_json(
            cell_root / "attestation.template.json", label="attestation template"
        )
        attestation.update(
            {
                "actual_invocation_evidence_complete": True,
                "invoked_skill_ids": invoked_ids,
                "verifier_passed": bool(verifier_result["passed"]),
                "verifier_score": reward,
                "cost": _token_cost(raw_jsonl),
            }
        )
        _write_new(raw / "attestation.json", _json_bytes(attestation))
        runtime_audit = _load_json(
            cell_root / "runtime-audit.template.json", label="runtime audit template"
        )
        runtime_audit.update(
            {
                "raw_provider_trace_sha256": sha256_file(raw / "codex.jsonl"),
                "schema_version": 2,
                "tool_feature_suppression_enforced": True,
                "feature_suppression_sha256": sha256_file(
                    raw / "feature-suppression.json"
                ),
                "strict_config_enforced": True,
                "user_config_suppressed": True,
                "rules_suppressed": True,
                "per_run_mcp_isolation": True,
                "host_native_tool_event_observed": False,
                "exec_tool_call_observed": True,
                "inspected_container_id": container_id,
                "inspected_container_sha256": sha256_file(raw / "container-inspect.json"),
                "inspected_image_id": image_id,
                "inspected_image_sha256": sha256_file(raw / "image-inspect.json"),
                "run_config_sha256": sha256_file(raw / "run-config.json"),
                "audit_event_sha256": sha256_file(raw / "execution-event.json"),
            }
        )
        _write_new(raw / "runtime-audit.json", _json_bytes(runtime_audit))
        record = record_m3k_external_trajectory(
            bound_manifest_path=bound_manifest_path,
            attestation_path=raw / "attestation.json",
            raw_provider_trace_path=raw / "codex.jsonl",
            runtime_audit_path=raw / "runtime-audit.json",
            execution_event_path=raw / "execution-event.json",
            raw_artifact_root=raw,
            evidence_root=evidence_root,
        )
        safe = {
            "schema_version": 1,
            "status": "recorded",
            "trajectory_id": record["trajectory_id"],
            "ordinal": ordinal,
            "execution_scope": execution_scope,
            "variant_role": record["variant_role"],
            "task_id": record["task_id"],
            "verifier_passed": record["verifier_passed"],
            "verifier_score": record["verifier_score"],
            "actual_invocation_evidence_complete": record[
                "actual_invocation_evidence_complete"
            ],
            "invoked_skill_ids": record["invoked_skill_ids"],
            "provider_reported_model_ids": list(codex_summary.reported_model_ids),
            "model_evidence_level": (
                "provider_reported"
                if codex_summary.reported_model_ids
                else "requested_cli_contract_only"
            ),
            "raw_provider_trace_sha256": record["raw_provider_trace_sha256"],
            "runtime_audit_sha256": record["runtime_audit_sha256"],
            "execution_pack_sha256": record["execution_pack_sha256"],
            "execution_event_sha256": sha256_file(raw / "execution-event.json"),
            "desktop_admission_start_sha256": sha256_file(
                raw / "desktop-admission-start.json"
            ),
            "source_snapshot_manifest_sha256": sha256_file(
                raw / "source-snapshot-manifest.json"
            ),
            "claim_boundary": {
                "this_is_one_live_model_trajectory": True,
                "this_is_six_cell_completion": False,
                "this_is_full87_completion": False,
                "candidate_promotion_claimed": False,
                "provider_native_skill_invocation_claimed": False,
                "skill_invocation_scope": "provisioned-skill-associated fixed-container MCP exec",
            },
        }
        safe["result_sha256"] = content_sha256(safe)
        _write_new(raw / "safe-result.json", _json_bytes(safe))
        return safe
    finally:
        try:
            removed = _run(["docker", "rm", "-f", container], timeout_sec=120)
        except M3KCodexCellError as cleanup_error:
            # Cleanup diagnostics must not mask the original execution failure.
            cleanup_bytes = (str(cleanup_error) + "\n").encode("utf-8")
            if not (raw / "container-cleanup.stderr.txt").exists():
                _write_new(raw / "container-cleanup.stderr.txt", cleanup_bytes)
        else:
            if removed.returncode != 0:
                # Preserve the execution failure rather than falsely claiming cleanup.
                _write_new(
                    raw / "container-cleanup.stderr.txt",
                    removed.stderr.encode("utf-8"),
                )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--operator-bundle", type=Path)
    source.add_argument("--materialized-cell", type=Path)
    parser.add_argument("--bound-manifest", type=Path, required=True)
    parser.add_argument("--executor-capability", type=Path, required=True)
    parser.add_argument("--admission-start-audit", type=Path, required=True)
    parser.add_argument("--source-snapshot-manifest", type=Path, required=True)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--pilot-report", type=Path)
    parser.add_argument("--first-cell-report", type=Path)
    parser.add_argument("--batch-plan", type=Path)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--library-scale-manifest", type=Path, default=DEFAULT_LIBRARY_SCALE)
    parser.add_argument("--ordinal", type=int)
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--server", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--effort", default="high", choices=sorted(ALLOWED_EFFORTS))
    parser.add_argument("--model-timeout-sec", type=int, default=900)
    parser.add_argument("--verifier-timeout-sec", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        report = run_m3k_codex_mcp_cell(
            operator_bundle=args.operator_bundle,
            bound_manifest_path=args.bound_manifest,
            executor_capability_path=args.executor_capability,
            admission_start_audit_path=args.admission_start_audit,
            source_snapshot_manifest_path=args.source_snapshot_manifest,
            pilot_manifest_path=args.pilot_manifest,
            pilot_report_path=args.pilot_report,
            first_cell_report_path=args.first_cell_report,
            batch_plan_path=args.batch_plan,
            progress_path=args.progress,
            library_scale_manifest_path=args.library_scale_manifest,
            ordinal=args.ordinal,
            materialized_cell_root=args.materialized_cell,
            codex_executable=detect_codex_executable(args.codex_executable),
            server_path=args.server,
            raw_root=args.raw_root,
            evidence_root=args.evidence_root,
            model=args.model,
            effort=args.effort,
            model_timeout_sec=args.model_timeout_sec,
            verifier_timeout_sec=args.verifier_timeout_sec,
        )
    except (M3KCodexCellError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print("Merlin M3-K Codex MCP cell")
    print(f"status={report['status']}")
    print(f"execution_scope={report['execution_scope']}")
    print(f"ordinal={report['ordinal']}")
    print(f"trajectory_id={report['trajectory_id']}")
    print(f"verifier_passed={str(report['verifier_passed']).lower()}")
    print(f"invoked_skill_ids={','.join(report['invoked_skill_ids']) or '(none)'}")
    print("six_cell_completion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
