"""Execute and seal one frozen 435-plan cell through Codex and fixed Docker MCP.

The candidate library is byte-bound by the materialized cell.  Only the
bounded metadata-first provisioning result is mounted and named in the model
prompt.  Skill-associated MCP calls become invocation events; retrieval and
prompt exposure remain separate evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_CORPUS_PROVENANCE,
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    DEFAULT_TASKS_ROOT,
    sha256_file,
    sha256_json,
)
from experiments.skillsbench.create_library_scale_trial1_runtime_contract import (
    LibraryScaleRuntimeContractError,
    validate_library_scale_trial1_runtime_contract,
)
from experiments.skillsbench.derive_library_scale_trial1_plan import (
    LibraryScaleTrial1PlanError,
    validate_library_scale_trial1_plan,
)
from experiments.skillsbench.materialize_library_scale_cell import (
    LibraryScaleMaterializationError,
    materialize_library_scale_cell,
    validate_materialized_library_scale_cell,
)
from experiments.skillsbench.library_scale_progress import (
    LibraryScaleProgressError,
    build_library_scale_progress,
)
from experiments.skillsbench.probe_codex_mcp_capability import (
    DEFAULT_SERVER,
    detect_codex_executable,
    probe_codex_feature_suppression,
    summarize_recorded_audit,
)
from experiments.skillsbench.run_m3k_codex_mcp_cell import (
    FORBIDDEN_NATIVE_ITEM_TYPES,
    MAX_RAW_BYTES,
    M3KCodexCellError,
    _audit_skill_ids,
    _container_workdir,
    _item_type_counts,
    _json_bytes,
    _prompt,
    _require_success,
    _run,
    _skill_artifacts,
    _task_body,
    _token_cost,
    _write_new,
    build_codex_command,
    validate_admission_binding,
    validate_materialized_corpus_binding,
)
from experiments.skillsbench.run_library_scale_trial1_supervisor import (
    LibraryScaleSupervisorError,
    validate_trial1_first_cell_admission,
)
from src.merlin_harness.codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl
from src.merlin_harness.governed_provisioning import GovernedProvisioner
from src.merlin_harness.management import content_sha256
from src.merlin_harness.models import (
    AgentRunContract,
    AgentRunResult,
    InvocationRecord,
    RawTraceReference,
    SkillInvocationEvent,
    TraceRecord,
    ValidationResult,
)
from src.merlin_harness.traces import (
    AGENT_TRACE_EVIDENCE_KEY,
    FileTraceStore,
    serialize_agent_run_evidence,
)


SAFE_CELL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


class LibraryScaleCodexCellError(ValueError):
    """Raised when a live library-scale cell cannot be sealed safely."""


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LibraryScaleCodexCellError(f"{label} must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryScaleCodexCellError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise LibraryScaleCodexCellError(f"{label} must be a JSON object")
    return value


def _plan_cell(plan: Mapping[str, Any], cell_id: str) -> dict[str, Any]:
    matches = [
        cell
        for cell in plan.get("cells", [])
        if isinstance(cell, dict) and cell.get("cell_id") == cell_id
    ]
    if len(matches) != 1:
        raise LibraryScaleCodexCellError("cell id must resolve exactly once in the 435 plan")
    return matches[0]


def validate_first_cell_expansion_gate(
    *,
    planned: Mapping[str, Any],
    first_cell_admission_path: Path,
    plan: Mapping[str, Any],
    plan_path: Path,
    source_plan_path: Path,
    manifest_path: Path,
    cell_root: Path,
    trace_root: Path,
    index_path: Path,
    skills_root: Path,
) -> None:
    """Open ordinal 1 only, then require its hash-bound admission report."""

    ordinal = planned.get("ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise LibraryScaleCodexCellError("planned cell ordinal is invalid")
    admission = first_cell_admission_path.expanduser()
    if ordinal == 1:
        if admission.exists() or admission.is_symlink():
            raise LibraryScaleCodexCellError(
                "ordinal 1 requires a new first-cell admission path"
            )
        return
    try:
        progress = build_library_scale_progress(
            plan_path=plan_path,
            source_plan_path=source_plan_path,
            manifest_path=manifest_path,
            cell_root=cell_root,
            trace_root=trace_root,
            index_path=index_path,
            skills_root=skills_root,
        )
        counts = progress.get("counts")
        next_pending = progress.get("next_pending")
        if (
            not isinstance(counts, dict)
            or counts.get("sealed_validated_cells") != ordinal - 1
            or not isinstance(next_pending, dict)
            or next_pending.get("cell_id") != planned.get("cell_id")
            or next_pending.get("ordinal") != ordinal
        ):
            raise LibraryScaleCodexCellError(
                "cell is not the exact next ordinal after sealed evidence"
            )
        validate_trial1_first_cell_admission(
            path=admission,
            plan=plan,
            progress=progress,
        )
    except (LibraryScaleProgressError, LibraryScaleSupervisorError) as exc:
        raise LibraryScaleCodexCellError(
            f"first-cell expansion gate failed: {exc}"
        ) from exc


def derive_metadata_first_provisioning(
    *, cell_root: Path, contract: Mapping[str, Any], exposure_budget: int
) -> dict[str, Any]:
    order = contract.get("presentation_order")
    if not isinstance(order, list) or len(order) != contract.get("library_size"):
        raise LibraryScaleCodexCellError("candidate library presentation order is invalid")
    task_text = (cell_root / "task-visible" / "task.md").read_text(encoding="utf-8")
    try:
        decision = GovernedProvisioner(exposure_budget=exposure_budget).decide(
            _task_body(task_text), _skill_artifacts(cell_root, order)
        )
    except (M3KCodexCellError, OSError, UnicodeError, ValueError) as exc:
        raise LibraryScaleCodexCellError(f"metadata-first provisioning failed: {exc}") from exc
    safe = decision.to_safe_dict()
    provisioned = list(decision.provisioned_ids)
    if len(provisioned) > exposure_budget or set(provisioned) - set(order):
        raise LibraryScaleCodexCellError("provisioning escaped its frozen candidate library")
    return {
        "schema_version": 1,
        "policy_version": decision.policy_version,
        "task_id": contract["task_id"],
        "task_instruction_sha256": contract["task_instruction_sha256"],
        "candidate_library_size": len(order),
        "candidate_library_order_sha256": sha256_json(order),
        "candidate_library_materialized_sha256": contract[
            "materialized_byte_snapshot_sha256"
        ],
        "exposure_budget": exposure_budget,
        "provisioned_skill_ids": provisioned,
        "decision": safe,
        "boundary": {
            "candidate_library_is_not_prompt_body_exposure": True,
            "provisioned_ids_are_prompt_exposure": True,
            "provisioned_ids_are_not_invocation_evidence": True,
            "skill_associated_mcp_exec_is_invocation_evidence": True,
            "provider_native_skill_invocation_claimed": False,
        },
    }


def build_library_scale_trace(
    *,
    cell: Mapping[str, Any],
    materialized: Mapping[str, Any],
    runtime_contract: Mapping[str, Any],
    raw_root: Path,
    raw_trace_path: Path,
    provisioning: Mapping[str, Any],
    invoked_skill_ids: Sequence[str],
    verifier_passed: bool,
    reward: float,
    staged_verifier_tree_sha256: str,
    wall_time_sec: float,
    provider_reported_model_ids: Sequence[str],
    outcome_status: str = "scored_verifier",
) -> TraceRecord:
    model_contract = runtime_contract["requested_model_contract"]
    harness = runtime_contract["harness_contract"]
    provisioned = list(provisioning["provisioned_skill_ids"])
    invoked = list(dict.fromkeys(invoked_skill_ids))
    if set(invoked) - set(provisioned):
        raise LibraryScaleCodexCellError("invocation evidence escaped provisioned skills")
    raw_reference = RawTraceReference(
        pointer=raw_trace_path.relative_to(raw_root).as_posix(),
        sha256=sha256_file(raw_trace_path),
    )
    contract = AgentRunContract(
        run_id=f"trial1-{cell['cell_id']}",
        task_id=cell["task_id"],
        condition=cell["cell_id"],
        workspace_root=str((raw_root / "empty-workspace").resolve()),
        raw_trace_root=str(raw_root.resolve()),
        agent_id="merlin",
        agent_version="trial1-live-v1",
        backend=model_contract["backend"],
        model_id=model_contract["model_id"],
        effort=model_contract["effort"],
        budget_id=runtime_contract["contract_sha256"],
        library_snapshot_id=cell["cell_id"],
        library_snapshot_sha256=materialized["materialized_byte_snapshot_sha256"],
        verifier_id=cell["verifier_contract_sha256"],
    )
    events = [
        SkillInvocationEvent(
            skill_id=skill_id,
            event_kind="skill_body_loaded",
            source="merlin-fixed-container-mcp",
            event_id=f"mcp-skill-{index:04d}-{skill_id}",
            sequence=index,
        )
        for index, skill_id in enumerate(invoked)
    ]
    result = AgentRunResult(
        contract=contract,
        workspace_root=contract.workspace_root,
        raw_trace=raw_reference,
        actual_invocation_evidence_complete=True,
        selected_skill_ids=provisioned,
        invocation_events=events,
    )
    return TraceRecord(
        id=cell["cell_id"],
        task_id=cell["task_id"],
        condition=cell["cell_id"],
        invocation=InvocationRecord(
            task_id=cell["task_id"],
            provisioned_skill_ids=provisioned,
            selected_skill_ids=provisioned,
            oracle_skill_ids=[],
            success=verifier_passed,
            score=reward,
            cost=None,
            latency_s=wall_time_sec,
        ),
        validation=[
            ValidationResult(
                name=cell["verifier_contract_sha256"],
                passed=verifier_passed,
                score=reward,
            )
        ],
        metadata={
            AGENT_TRACE_EVIDENCE_KEY: serialize_agent_run_evidence(result),
            "workspace": contract.workspace_root,
            "staged_verifier_tree_sha256": staged_verifier_tree_sha256,
            "verifier_contract_sha256": cell["verifier_contract_sha256"],
            "outcome_status": outcome_status,
            "harness_mode": harness["mode"],
            "candidate_library_order_sha256": provisioning[
                "candidate_library_order_sha256"
            ],
            "exposure_budget": provisioning["exposure_budget"],
            "provisioning_decision_sha256": content_sha256(provisioning),
            "runtime_contract_sha256": runtime_contract["contract_sha256"],
            "provider_reported_model_ids": list(provider_reported_model_ids),
            "provider_resolved_model_identity_claimed": bool(
                provider_reported_model_ids
            ),
            "provider_native_skill_invocation_claimed": False,
        },
    )


def _docker_image_name(contract: Mapping[str, Any]) -> str:
    task = re.sub(r"[^a-z0-9_.-]+", "-", str(contract["task_id"]).lower())
    environment = contract.get("task_environment")
    if not isinstance(environment, dict) or not isinstance(
        environment.get("records_sha256"), str
    ):
        raise LibraryScaleCodexCellError("task environment hash is unavailable")
    return f"theking-trial1-{task}:{environment['records_sha256'][:12]}"


def _validate_runtime_and_plan(
    *,
    runtime_contract_path: Path,
    plan_path: Path,
    source_plan_path: Path,
    manifest_path: Path,
    executor_capability_path: Path,
    source_snapshot_manifest_path: Path,
    corpus_provenance_path: Path,
    index_path: Path,
    skills_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    stored = _load_json(runtime_contract_path, label="runtime contract")
    model = stored.get("requested_model_contract", {})
    harness = stored.get("harness_contract", {})
    timeouts = stored.get("timeouts", {})
    try:
        runtime = validate_library_scale_trial1_runtime_contract(
            contract_path=runtime_contract_path,
            plan_path=plan_path,
            source_plan_path=source_plan_path,
            manifest_path=manifest_path,
            executor_capability_path=executor_capability_path,
            source_snapshot_manifest_path=source_snapshot_manifest_path,
            corpus_provenance_path=corpus_provenance_path,
            model=model.get("model_id"),
            effort=model.get("effort"),
            exposure_budget=harness.get("exposure_budget"),
            model_timeout_sec=timeouts.get("model_sec"),
            verifier_timeout_sec=timeouts.get("verifier_sec"),
            index_path=index_path,
            skills_root=skills_root,
        )
        plan = validate_library_scale_trial1_plan(
            plan_path=plan_path,
            source_plan_path=source_plan_path,
            manifest_path=manifest_path,
            index_path=index_path,
            skills_root=skills_root,
        )
    except (LibraryScaleRuntimeContractError, LibraryScaleTrial1PlanError) as exc:
        raise LibraryScaleCodexCellError(str(exc)) from exc
    return runtime, plan


def run_library_scale_codex_mcp_cell(
    *,
    runtime_contract_path: Path,
    executor_capability_path: Path,
    admission_start_audit_path: Path,
    source_snapshot_manifest_path: Path,
    plan_path: Path,
    source_plan_path: Path,
    manifest_path: Path,
    first_cell_admission_path: Path,
    cell_id: str,
    cell_root: Path,
    trace_root: Path,
    raw_root: Path,
    codex_executable: Path,
    server_path: Path = DEFAULT_SERVER,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
) -> dict[str, Any]:
    """Materialize, execute, verify, normalize, and immutably save one cell."""

    if not SAFE_CELL_RE.fullmatch(cell_id):
        raise LibraryScaleCodexCellError("cell id is unsafe")
    runtime, plan = _validate_runtime_and_plan(
        runtime_contract_path=runtime_contract_path,
        plan_path=plan_path,
        source_plan_path=source_plan_path,
        manifest_path=manifest_path,
        executor_capability_path=executor_capability_path,
        source_snapshot_manifest_path=source_snapshot_manifest_path,
        corpus_provenance_path=corpus_provenance_path,
        index_path=index_path,
        skills_root=skills_root,
    )
    planned = _plan_cell(plan, cell_id)
    validate_first_cell_expansion_gate(
        planned=planned,
        first_cell_admission_path=first_cell_admission_path,
        plan=plan,
        plan_path=plan_path,
        source_plan_path=source_plan_path,
        manifest_path=manifest_path,
        cell_root=cell_root,
        trace_root=trace_root,
        index_path=index_path,
        skills_root=skills_root,
    )
    expected_start_sha256 = os.environ.get(
        "MERLIN_DESKTOP_ADMISSION_START_SHA256", ""
    )
    expected_command_sha256 = os.environ.get(
        "MERLIN_DESKTOP_ADMITTED_COMMAND_SHA256", ""
    )
    announced = os.environ.get("MERLIN_DESKTOP_ADMISSION_START", "")
    if not announced or Path(announced).resolve(strict=False) != (
        admission_start_audit_path.expanduser().resolve(strict=False)
    ):
        raise LibraryScaleCodexCellError(
            "runner is not inside the announced DESKTOP admission lease"
        )
    try:
        admission_bytes, snapshot_bytes, admission = validate_admission_binding(
            admission_start_audit_path=admission_start_audit_path,
            source_snapshot_manifest_path=source_snapshot_manifest_path,
            expected_start_sha256=expected_start_sha256,
            expected_command_sha256=expected_command_sha256,
        )
    except (M3KCodexCellError, OSError) as exc:
        raise LibraryScaleCodexCellError(str(exc)) from exc

    cells = cell_root.expanduser().resolve(strict=True)
    traces = trace_root.expanduser().resolve(strict=True)
    if cells.is_symlink() or traces.is_symlink() or not cells.is_dir() or not traces.is_dir():
        raise LibraryScaleCodexCellError("cell and trace roots must be safe directories")
    materialized_root = cells / cell_id
    try:
        materialized = materialize_library_scale_cell(
            manifest_path=manifest_path,
            cell_id=cell_id,
            output_root=materialized_root,
            tasks_root=tasks_root,
            skills_root=skills_root,
            index_path=index_path,
            corpus_provenance_path=corpus_provenance_path,
        )
        validate_materialized_library_scale_cell(
            materialized_root, expected_cell_id=cell_id
        )
        validate_materialized_corpus_binding(
            contract=materialized, admission_summary=admission
        )
    except (LibraryScaleMaterializationError, M3KCodexCellError) as exc:
        raise LibraryScaleCodexCellError(str(exc)) from exc
    if any(
        observed != expected
        for observed, expected in (
            (materialized.get("cell_id"), planned.get("cell_id")),
            (materialized.get("task_id"), planned.get("task_id")),
            (materialized.get("trial_index"), planned.get("trial_index")),
            (materialized.get("arm_id"), planned.get("arm_id")),
            (materialized.get("library_size"), planned.get("library_size")),
            (
                materialized.get("manifest_library_snapshot_sha256"),
                planned.get("library_snapshot_sha256"),
            ),
            (
                materialized.get("verifier_contract_sha256"),
                planned.get("verifier_contract_sha256"),
            ),
        )
    ):
        raise LibraryScaleCodexCellError("materialized cell differs from the frozen plan")

    raw = raw_root.expanduser().resolve(strict=True) / cell_id
    if raw.exists() or raw.is_symlink():
        raise LibraryScaleCodexCellError("cell raw root must be new-only")
    raw.mkdir(parents=True)
    (raw / "empty-workspace").mkdir()
    _write_new(raw / "runtime-contract.json", runtime_contract_path.read_bytes())
    _write_new(raw / "executor-capability.json", executor_capability_path.read_bytes())
    _write_new(raw / "desktop-admission-start.json", admission_bytes)
    _write_new(raw / "source-snapshot-manifest.json", snapshot_bytes)
    codex = codex_executable.expanduser().resolve(strict=True)
    server = server_path.expanduser().resolve(strict=True)
    try:
        suppression = probe_codex_feature_suppression(codex)
    except (OSError, ValueError) as exc:
        raise LibraryScaleCodexCellError(f"feature suppression probe failed: {exc}") from exc
    if suppression.get("all_requested_features_disabled") is not True:
        raise LibraryScaleCodexCellError("native tool feature suppression failed")
    _write_new(raw / "feature-suppression.json", _json_bytes(suppression))

    provisioning = derive_metadata_first_provisioning(
        cell_root=materialized_root,
        contract=materialized,
        exposure_budget=runtime["harness_contract"]["exposure_budget"],
    )
    provisioned = provisioning["provisioned_skill_ids"]
    allowed_path = raw / "allowed-skill-ids.json"
    _write_new(allowed_path, _json_bytes(provisioned))
    _write_new(raw / "provisioning.json", _json_bytes(provisioning))

    environment = materialized_root / "task-visible" / "environment"
    if environment.is_symlink() or not (environment / "Dockerfile").is_file():
        raise LibraryScaleCodexCellError("task environment has no safe Dockerfile")
    image = _docker_image_name(materialized)
    container = f"theking-trial1-{planned['ordinal']}-{uuid.uuid4().hex[:12]}"
    build = _run(
        [
            "docker",
            "build",
            "--label",
            "theking.library_scale_trial1=true",
            "-t",
            image,
            str(environment),
        ],
        timeout_sec=1800,
    )
    _write_new(raw / "docker-build.stdout.txt", build.stdout.encode("utf-8"))
    _write_new(raw / "docker-build.stderr.txt", build.stderr.encode("utf-8"))
    try:
        _require_success(build, label="Docker image build")
        image_report = _run(["docker", "image", "inspect", image], timeout_sec=60)
        _require_success(image_report, label="Docker image inspect")
        _write_new(raw / "image-inspect.json", image_report.stdout.encode("utf-8"))
        image_inspect = json.loads(image_report.stdout)
        workdir = _container_workdir(image_inspect)
        image_id = image_inspect[0].get("Id")
        if not isinstance(image_id, str) or not image_id:
            raise LibraryScaleCodexCellError("Docker image has no ID")
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
            f"theking.library_scale.cell={cell_id}",
            "--mount",
            f"type=bind,src={materialized_root / 'task-visible' / 'task.md'},dst=/merlin/task.md,readonly",
        ]
        for skill_id in provisioned:
            run_argv.extend(
                (
                    "--mount",
                    f"type=bind,src={materialized_root / 'skills' / skill_id},dst=/merlin/skills/{skill_id},readonly",
                )
            )
        run_argv.extend((image, "sleep", "infinity"))
        started = _run(run_argv, timeout_sec=120)
        _require_success(started, label="Docker container start")
        try:
            inspect = _run(["docker", "inspect", container], timeout_sec=60)
            _require_success(inspect, label="Docker container inspect")
            _write_new(raw / "container-inspect.json", inspect.stdout.encode("utf-8"))
            inspected = json.loads(inspect.stdout)
            if not isinstance(inspected, list) or len(inspected) != 1:
                raise LibraryScaleCodexCellError("container inspect is malformed")
            container_id = inspected[0].get("Id")
            if not isinstance(container_id, str) or not container_id:
                raise LibraryScaleCodexCellError("container inspect has no ID")
            model = runtime["requested_model_contract"]
            timeouts = runtime["timeouts"]
            command = build_codex_command(
                codex_executable=codex,
                server_path=server,
                raw_root=raw,
                container_id=container_id,
                container_workdir=workdir,
                allowed_skill_ids_file=allowed_path,
                model=model["model_id"],
                effort=model["effort"],
                timeout_sec=timeouts["model_sec"],
            )
            config = {
                "schema_version": 1,
                "cell_id": cell_id,
                "ordinal": planned["ordinal"],
                "runtime_contract_sha256": runtime["contract_sha256"],
                "cell_contract_sha256": materialized["cell_contract_sha256"],
                "requested_model_contract": model,
                "desktop_admission": admission,
                "container_id": container_id,
                "image_id": image_id,
                "network_mode": "none",
                "provisioning_sha256": sha256_file(raw / "provisioning.json"),
                "codex_command_contract_sha256": content_sha256(command[:-1]),
            }
            _write_new(raw / "run-config.json", _json_bytes(config))
            task_text = (materialized_root / "task-visible" / "task.md").read_text(
                encoding="utf-8"
            )
            run_started = time.monotonic()
            model_report = _run(
                command,
                timeout_sec=timeouts["model_sec"] + 60,
                cwd=raw / "empty-workspace",
                input_text=_prompt(_task_body(task_text), provisioned),
            )
            raw_jsonl = model_report.stdout or ""
            if len(raw_jsonl.encode("utf-8")) > MAX_RAW_BYTES:
                raise LibraryScaleCodexCellError("Codex JSONL exceeded the raw evidence limit")
            _write_new(raw / "codex.jsonl", raw_jsonl.encode("utf-8"))
            _write_new(
                raw / "codex.stderr.txt", (model_report.stderr or "").encode("utf-8")
            )
            _require_success(model_report, label="Codex model execution")
            try:
                codex_summary = parse_codex_exec_jsonl(raw_jsonl)
            except CodexCliAdapterError as exc:
                raise LibraryScaleCodexCellError(str(exc)) from exc
            if (
                codex_summary.reported_model_ids
                and model["model_id"] not in codex_summary.reported_model_ids
            ):
                raise LibraryScaleCodexCellError("provider-reported model differs")
            item_counts = _item_type_counts(raw_jsonl)
            forbidden = sorted(set(item_counts) & FORBIDDEN_NATIVE_ITEM_TYPES)
            if forbidden:
                raise LibraryScaleCodexCellError("Codex emitted a forbidden host tool item")
            mcp_audit = raw / "mcp-audit.jsonl"
            if not mcp_audit.is_file():
                raise LibraryScaleCodexCellError("MCP audit is missing")
            audit = summarize_recorded_audit(mcp_audit)
            invoked, exec_count = _audit_skill_ids(mcp_audit, provisioned)
            if (
                audit.get("initialize_observed") is not True
                or audit.get("tools_list_observed") is not True
                or exec_count < 1
            ):
                raise LibraryScaleCodexCellError("MCP execution audit is incomplete")
            mkdir = _run(
                ["docker", "exec", container_id, "mkdir", "-p", "/verifier", "/logs/verifier"],
                timeout_sec=30,
            )
            _require_success(mkdir, label="verifier destination setup")
            copied = _run(
                [
                    "docker",
                    "cp",
                    f"{materialized_root / 'verifier-hidden'}/.",
                    f"{container_id}:/verifier",
                ],
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
                    f"{timeouts['verifier_sec']}s",
                    "bash",
                    "-lc",
                    "/verifier/test.sh",
                ],
                timeout_sec=timeouts["verifier_sec"] + 15,
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
                raise LibraryScaleCodexCellError("verifier reward is not numeric") from exc
            if not 0.0 <= reward <= 1.0:
                raise LibraryScaleCodexCellError("verifier reward must be in [0,1]")
            verifier_passed = reward >= 1.0
            verifier_result = {
                "schema_version": 1,
                "exit_code": verifier.returncode,
                "reward": reward,
                "passed": verifier_passed,
                "hidden_verifier_tree_sha256": materialized["hidden_verifier"][
                    "records_sha256"
                ],
            }
            _write_new(raw / "verifier-result.json", _json_bytes(verifier_result))
            trace = build_library_scale_trace(
                cell=planned,
                materialized=materialized,
                runtime_contract=runtime,
                raw_root=raw,
                raw_trace_path=raw / "codex.jsonl",
                provisioning=provisioning,
                invoked_skill_ids=invoked,
                verifier_passed=verifier_passed,
                reward=reward,
                staged_verifier_tree_sha256=materialized["hidden_verifier"][
                    "records_sha256"
                ],
                wall_time_sec=round(time.monotonic() - run_started, 3),
                provider_reported_model_ids=codex_summary.reported_model_ids,
            )
            trace_path = FileTraceStore(traces).save_immutable(trace)
            safe: dict[str, Any] = {
                "schema_version": 1,
                "status": "sealed_validated_trace",
                "cell_id": cell_id,
                "ordinal": planned["ordinal"],
                "task_id": planned["task_id"],
                "arm_id": planned["arm_id"],
                "library_size": planned["library_size"],
                "provisioned_skill_ids": provisioned,
                "invoked_skill_ids": invoked,
                "verifier_passed": verifier_passed,
                "reward": reward,
                "trace_file_sha256": sha256_file(trace_path),
                "raw_provider_trace_sha256": sha256_file(raw / "codex.jsonl"),
                "provider_reported_model_ids": list(codex_summary.reported_model_ids),
                "model_evidence_level": (
                    "provider_reported"
                    if codex_summary.reported_model_ids
                    else "requested_cli_contract_only"
                ),
                "token_proxy": _token_cost(raw_jsonl),
                "claim_boundary": {
                    "this_is_one_live_model_cell": True,
                    "this_is_five_cell_canary_completion": False,
                    "this_is_435_completion": False,
                    "this_is_1305_completion": False,
                    "provider_native_skill_invocation_claimed": False,
                },
            }
            safe["result_sha256"] = content_sha256(safe)
            _write_new(raw / "safe-result.json", _json_bytes(safe))
            return safe
        finally:
            removed = _run(["docker", "rm", "-f", container], timeout_sec=120)
            if removed.returncode != 0 and not (raw / "container-cleanup.stderr.txt").exists():
                _write_new(
                    raw / "container-cleanup.stderr.txt", removed.stderr.encode("utf-8")
                )
    except (M3KCodexCellError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise LibraryScaleCodexCellError(str(exc)) from exc
    finally:
        if planned.get("arm_id") == "full-209":
            try:
                _run(["docker", "image", "rm", image], timeout_sec=120)
            except Exception:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-contract", type=Path, required=True)
    parser.add_argument("--executor-capability", type=Path, required=True)
    parser.add_argument("--admission-start-audit", type=Path, required=True)
    parser.add_argument("--source-snapshot-manifest", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--server", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    # Appended by run_library_scale_trial1_supervisor.
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--first-cell-admission", type=Path, required=True)
    parser.add_argument("--cell-id", required=True)
    parser.add_argument("--cell-root", type=Path, required=True)
    parser.add_argument("--trace-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_library_scale_codex_mcp_cell(
            runtime_contract_path=args.runtime_contract,
            executor_capability_path=args.executor_capability,
            admission_start_audit_path=args.admission_start_audit,
            source_snapshot_manifest_path=args.source_snapshot_manifest,
            plan_path=args.plan,
            source_plan_path=args.source_plan,
            manifest_path=args.manifest,
            first_cell_admission_path=args.first_cell_admission,
            cell_id=args.cell_id,
            cell_root=args.cell_root,
            trace_root=args.trace_root,
            raw_root=args.raw_root,
            codex_executable=detect_codex_executable(args.codex_executable),
            server_path=args.server,
            tasks_root=args.tasks_root,
            skills_root=args.skills_root,
            index_path=args.index,
            corpus_provenance_path=args.corpus_provenance,
        )
    except LibraryScaleCodexCellError as exc:
        parser.error(str(exc))
    print("Merlin 435-plan Codex MCP cell")
    print(f"status={result['status']}")
    print(f"ordinal={result['ordinal']}")
    print(f"cell_id={result['cell_id']}")
    print(f"verifier_passed={str(result['verifier_passed']).lower()}")
    print("canary_completion=false")
    print("full_435_completion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
