"""Run the frozen 435-cell trial-1 plan through an evidence-gated child executor.

The supervisor is deliberately not a model executor.  It advances exactly one
cell at a time, reconstructs progress from immutable cell/trace evidence after
every child exit, and refuses to continue if a materialization exists without
a validated trace.  A five-cell canary (one task across all five arms) must be
sealed before the full phase can open.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    sha256_file,
)
from experiments.skillsbench.derive_library_scale_trial1_plan import (
    ARM_ORDER,
    EXPECTED_CELLS,
    LibraryScaleTrial1PlanError,
    validate_library_scale_trial1_plan,
)
from experiments.skillsbench.library_scale_progress import (
    LibraryScaleProgressError,
    build_library_scale_progress,
    validate_library_scale_progress,
    write_library_scale_progress,
)
from src.merlin_harness.management import content_sha256


CANARY_CELLS = len(ARM_ORDER)
MAX_CAPTURE_BYTES = 1_000_000
PHASES = frozenset({"canary", "full"})


class LibraryScaleSupervisorError(ValueError):
    """Raised when the 435-cell execution frontier is unsafe to advance."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _safe_directory(path: Path, *, label: str, create: bool) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise LibraryScaleSupervisorError(f"{label} must not be a symlink")
    if create and not expanded.exists():
        expanded.mkdir(parents=True, exist_ok=False)
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise LibraryScaleSupervisorError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise LibraryScaleSupervisorError(f"{label} must be a directory")
    return resolved


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise LibraryScaleSupervisorError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryScaleSupervisorError(f"{label} is missing or invalid") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise LibraryScaleSupervisorError(f"{label} must be a regular JSON object")
    return value


def _write_new_json(path: Path, payload: Mapping[str, Any], *, label: str) -> None:
    destination = path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise LibraryScaleSupervisorError(f"{label} must be new-only")
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(raw)
    except FileExistsError as exc:
        raise LibraryScaleSupervisorError(f"{label} must be new-only") from exc


def _sealed_prefix(snapshot: Mapping[str, Any]) -> int:
    states = snapshot.get("cells")
    if not isinstance(states, list) or len(states) != EXPECTED_CELLS:
        raise LibraryScaleSupervisorError("progress does not cover the exact 435-cell plan")
    prefix = 0
    unfinished_seen = False
    for expected_ordinal, state in enumerate(states, start=1):
        if not isinstance(state, dict) or state.get("ordinal") != expected_ordinal:
            raise LibraryScaleSupervisorError("progress cell order drifted")
        status = state.get("status")
        if status == "materialized_without_validated_trace":
            raise LibraryScaleSupervisorError(
                "operator audit is required for an unsealed materialization"
            )
        if status == "sealed_validated_trace":
            if unfinished_seen:
                raise LibraryScaleSupervisorError(
                    "sealed evidence is not an exact execution-order prefix"
                )
            prefix += 1
        elif status == "pending":
            unfinished_seen = True
        else:
            raise LibraryScaleSupervisorError("progress contains an unsupported cell state")
    counts = snapshot.get("counts")
    if not isinstance(counts, dict) or counts.get("sealed_validated_cells") != prefix:
        raise LibraryScaleSupervisorError("progress sealed count does not match its cell states")
    return prefix


def _progress_kwargs(
    *,
    plan_path: Path,
    source_plan_path: Path,
    manifest_path: Path,
    cell_root: Path,
    trace_root: Path,
    index_path: Path,
    skills_root: Path,
) -> dict[str, Any]:
    return {
        "plan_path": plan_path,
        "source_plan_path": source_plan_path,
        "manifest_path": manifest_path,
        "cell_root": cell_root,
        "trace_root": trace_root,
        "index_path": index_path,
        "skills_root": skills_root,
    }


def build_trial1_canary_admission(
    *,
    plan: Mapping[str, Any],
    progress: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind admission to the exact first task's five sealed arm traces."""

    if _sealed_prefix(progress) < CANARY_CELLS:
        raise LibraryScaleSupervisorError("all five canary cells are not sealed")
    plan_cells = plan.get("cells")
    progress_cells = progress.get("cells")
    if not isinstance(plan_cells, list) or not isinstance(progress_cells, list):
        raise LibraryScaleSupervisorError("canary plan/progress cells are missing")
    selected_plan = plan_cells[:CANARY_CELLS]
    selected_progress = progress_cells[:CANARY_CELLS]
    if (
        [cell.get("ordinal") for cell in selected_plan] != list(range(1, CANARY_CELLS + 1))
        or [cell.get("arm_id") for cell in selected_plan] != list(ARM_ORDER)
        or len({cell.get("task_id") for cell in selected_plan}) != 1
        or len({cell.get("trial_index") for cell in selected_plan}) != 1
        or selected_plan[0].get("trial_index") != 1
    ):
        raise LibraryScaleSupervisorError("the frozen five-cell canary grouping drifted")
    records: list[dict[str, Any]] = []
    for planned, observed in zip(selected_plan, selected_progress, strict=True):
        if (
            observed.get("cell_id") != planned.get("cell_id")
            or observed.get("status") != "sealed_validated_trace"
            or not isinstance(observed.get("trace_file_sha256"), str)
        ):
            raise LibraryScaleSupervisorError("canary trace evidence is incomplete")
        records.append(
            {
                "ordinal": planned["ordinal"],
                "cell_id": planned["cell_id"],
                "task_id": planned["task_id"],
                "arm_id": planned["arm_id"],
                "trace_file_sha256": observed["trace_file_sha256"],
            }
        )
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_kind": "library-scale-trial1-five-arm-canary-admission-v1",
        "batch_id": plan.get("batch_id"),
        "plan_sha256": plan.get("plan_sha256"),
        "canary_policy": {
            "task_selection": "first_task_in_frozen_source_order",
            "trial_index": 1,
            "arm_order": list(ARM_ORDER),
            "cell_count": CANARY_CELLS,
        },
        "records": records,
        "canary_passed": True,
        "claim_boundary": {
            "canary_is_435_completion": False,
            "canary_is_1305_completion": False,
            "canary_is_generalization_result": False,
            "provider_resolved_model_identity_claimed": False,
        },
    }
    report["report_sha256"] = content_sha256(report)
    return report


def build_trial1_first_cell_admission(
    *, plan: Mapping[str, Any], progress: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind the executor-capability expansion gate to ordinal 1 evidence."""

    if _sealed_prefix(progress) < 1:
        raise LibraryScaleSupervisorError("the first cell is not sealed")
    planned = plan.get("cells", [None])[0]
    observed = progress.get("cells", [None])[0]
    if (
        not isinstance(planned, dict)
        or not isinstance(observed, dict)
        or planned.get("ordinal") != 1
        or observed.get("cell_id") != planned.get("cell_id")
        or observed.get("status") != "sealed_validated_trace"
        or not isinstance(observed.get("trace_file_sha256"), str)
    ):
        raise LibraryScaleSupervisorError("first-cell evidence is incomplete")
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_kind": "library-scale-trial1-first-cell-admission-v1",
        "batch_id": plan.get("batch_id"),
        "plan_sha256": plan.get("plan_sha256"),
        "first_cell": {
            "ordinal": 1,
            "cell_id": planned["cell_id"],
            "task_id": planned["task_id"],
            "arm_id": planned["arm_id"],
            "trace_file_sha256": observed["trace_file_sha256"],
        },
        "additional_canary_cells_allowed": True,
        "claim_boundary": {
            "first_cell_is_five_cell_canary": False,
            "first_cell_is_435_completion": False,
            "first_cell_is_1305_completion": False,
            "provider_resolved_model_identity_claimed": False,
        },
    }
    report["report_sha256"] = content_sha256(report)
    return report


def write_trial1_first_cell_admission(
    *, path: Path, plan: Mapping[str, Any], progress: Mapping[str, Any]
) -> dict[str, Any]:
    report = build_trial1_first_cell_admission(plan=plan, progress=progress)
    _write_new_json(path, report, label="first-cell admission report")
    return report


def validate_trial1_first_cell_admission(
    *, path: Path, plan: Mapping[str, Any], progress: Mapping[str, Any]
) -> dict[str, Any]:
    stored = _load_json(path, label="first-cell admission report")
    stored_hash = stored.get("report_sha256")
    unhashed = dict(stored)
    unhashed.pop("report_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise LibraryScaleSupervisorError("first-cell admission hash mismatch")
    expected = build_trial1_first_cell_admission(plan=plan, progress=progress)
    if stored != expected:
        raise LibraryScaleSupervisorError("first-cell admission drifted from current evidence")
    return stored


def write_trial1_canary_admission(
    *, path: Path, plan: Mapping[str, Any], progress: Mapping[str, Any]
) -> dict[str, Any]:
    report = build_trial1_canary_admission(plan=plan, progress=progress)
    _write_new_json(path, report, label="canary admission report")
    return report


def validate_trial1_canary_admission(
    *, path: Path, plan: Mapping[str, Any], progress: Mapping[str, Any]
) -> dict[str, Any]:
    stored = _load_json(path, label="canary admission report")
    stored_hash = stored.get("report_sha256")
    unhashed = dict(stored)
    unhashed.pop("report_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise LibraryScaleSupervisorError("canary admission hash mismatch")
    expected = build_trial1_canary_admission(plan=plan, progress=progress)
    if stored != expected:
        raise LibraryScaleSupervisorError("canary admission drifted from current evidence")
    return stored


def _save_progress_snapshot(
    *,
    progress_root: Path,
    sealed_count: int,
    progress_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    path = progress_root / f"progress-{sealed_count:04d}.json"
    if path.exists():
        return validate_library_scale_progress(progress_path=path, **progress_kwargs)
    return write_library_scale_progress(output_path=path, **progress_kwargs)


def _command_hash(argv: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(list(argv), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _bounded_size(value: str | None) -> tuple[int, str]:
    raw = (value or "").encode("utf-8", errors="replace")
    return len(raw), hashlib.sha256(raw).hexdigest()


def supervise_library_scale_trial1(
    *,
    phase: str,
    plan_path: Path,
    source_plan_path: Path,
    manifest_path: Path,
    cell_root: Path,
    trace_root: Path,
    progress_root: Path,
    first_cell_admission_path: Path,
    canary_admission_path: Path,
    executor_argv: Sequence[str],
    max_cells: int | None = None,
    child_timeout_sec: int = 1800,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Advance the immutable frontier and return a safe orchestration report."""

    if phase not in PHASES:
        raise LibraryScaleSupervisorError("phase must be canary or full")
    if not executor_argv or any(not isinstance(item, str) or not item for item in executor_argv):
        raise LibraryScaleSupervisorError("executor argv must be a non-empty string sequence")
    if isinstance(child_timeout_sec, bool) or child_timeout_sec < 1:
        raise LibraryScaleSupervisorError("child timeout must be a positive integer")
    if max_cells is not None and (
        isinstance(max_cells, bool) or not isinstance(max_cells, int) or max_cells < 1
    ):
        raise LibraryScaleSupervisorError("max_cells must be a positive integer")
    try:
        plan = validate_library_scale_trial1_plan(
            plan_path=plan_path,
            source_plan_path=source_plan_path,
            manifest_path=manifest_path,
            index_path=index_path,
            skills_root=skills_root,
        )
    except LibraryScaleTrial1PlanError as exc:
        raise LibraryScaleSupervisorError(str(exc)) from exc
    cell_directory = _safe_directory(cell_root, label="cell root", create=True)
    trace_directory = _safe_directory(trace_root, label="trace root", create=True)
    progress_directory = _safe_directory(
        progress_root, label="progress root", create=True
    )
    kwargs = _progress_kwargs(
        plan_path=plan_path,
        source_plan_path=source_plan_path,
        manifest_path=manifest_path,
        cell_root=cell_directory,
        trace_root=trace_directory,
        index_path=index_path,
        skills_root=skills_root,
    )
    try:
        progress = build_library_scale_progress(**kwargs)
    except LibraryScaleProgressError as exc:
        raise LibraryScaleSupervisorError(str(exc)) from exc
    initial_sealed = _sealed_prefix(progress)
    _save_progress_snapshot(
        progress_root=progress_directory,
        sealed_count=initial_sealed,
        progress_kwargs=kwargs,
    )
    if initial_sealed >= 1:
        validate_trial1_first_cell_admission(
            path=first_cell_admission_path,
            plan=plan,
            progress=progress,
        )

    if phase == "canary":
        if initial_sealed > CANARY_CELLS:
            raise LibraryScaleSupervisorError(
                "canary phase cannot operate after the full frontier has opened"
            )
        target = CANARY_CELLS
    else:
        validate_trial1_first_cell_admission(
            path=first_cell_admission_path,
            plan=plan,
            progress=progress,
        )
        validate_trial1_canary_admission(
            path=canary_admission_path,
            plan=plan,
            progress=progress,
        )
        target = EXPECTED_CELLS
    remaining = target - initial_sealed
    budget = remaining if max_cells is None else min(remaining, max_cells)
    executed = 0
    for _ in range(budget):
        current_sealed = _sealed_prefix(progress)
        if current_sealed >= 1:
            validate_trial1_first_cell_admission(
                path=first_cell_admission_path,
                plan=plan,
                progress=progress,
            )
        next_pending = progress.get("next_pending")
        if not isinstance(next_pending, dict):
            raise LibraryScaleSupervisorError("progress has no next pending cell")
        if (
            next_pending.get("operator_audit_required_before_retry") is not False
            or next_pending.get("materialization_exists") is not False
        ):
            raise LibraryScaleSupervisorError("next cell requires operator audit")
        cell_id = next_pending.get("cell_id")
        ordinal = next_pending.get("ordinal")
        if not isinstance(cell_id, str) or ordinal != initial_sealed + executed + 1:
            raise LibraryScaleSupervisorError("next pending identity drifted")
        command = [
            *executor_argv,
            "--plan",
            str(plan_path.resolve()),
            "--source-plan",
            str(source_plan_path.resolve()),
            "--manifest",
            str(manifest_path.resolve()),
            "--cell-id",
            cell_id,
            "--cell-root",
            str(cell_directory),
            "--trace-root",
            str(trace_directory),
            "--first-cell-admission",
            str(first_cell_admission_path.resolve(strict=False)),
        ]
        started = time.monotonic()
        try:
            completed = runner(
                command,
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=child_timeout_sec,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LibraryScaleSupervisorError(
                f"cell executor failed before evidence validation: {cell_id}: {exc}"
            ) from exc
        stdout_bytes, stdout_sha = _bounded_size(completed.stdout)
        stderr_bytes, stderr_sha = _bounded_size(completed.stderr)
        if stdout_bytes > MAX_CAPTURE_BYTES or stderr_bytes > MAX_CAPTURE_BYTES:
            raise LibraryScaleSupervisorError("cell executor output exceeded the supervisor budget")
        event: dict[str, Any] = {
            "schema_version": 1,
            "event_kind": "library-scale-trial1-supervisor-cell-v1",
            "phase": phase,
            "ordinal": ordinal,
            "cell_id": cell_id,
            "command_sha256": _command_hash(command),
            "exit_code": completed.returncode,
            "wall_time_sec": round(time.monotonic() - started, 3),
            "stdout": {"bytes": stdout_bytes, "sha256": stdout_sha},
            "stderr": {"bytes": stderr_bytes, "sha256": stderr_sha},
            "raw_child_output_stored": False,
        }
        if completed.returncode != 0:
            event["evidence_advanced"] = False
            event["event_sha256"] = content_sha256(event)
            failure = progress_directory / (
                f"failure-{ordinal:04d}-{time.time_ns()}.json"
            )
            _write_new_json(failure, event, label="failure event")
            raise LibraryScaleSupervisorError(
                f"cell executor exited nonzero before sealing evidence: {cell_id}"
            )
        try:
            after = build_library_scale_progress(**kwargs)
        except LibraryScaleProgressError as exc:
            raise LibraryScaleSupervisorError(
                f"cell executor returned zero but evidence is invalid: {cell_id}: {exc}"
            ) from exc
        sealed_after = _sealed_prefix(after)
        if sealed_after != ordinal:
            raise LibraryScaleSupervisorError(
                f"cell executor did not advance exactly one sealed cell: {cell_id}"
            )
        event["evidence_advanced"] = True
        event["sealed_after"] = sealed_after
        event["trace_file_sha256"] = after["cells"][ordinal - 1][
            "trace_file_sha256"
        ]
        event["event_sha256"] = content_sha256(event)
        _write_new_json(
            progress_directory / f"event-{ordinal:04d}.json",
            event,
            label="cell event",
        )
        _save_progress_snapshot(
            progress_root=progress_directory,
            sealed_count=sealed_after,
            progress_kwargs=kwargs,
        )
        progress = after
        executed += 1
        if sealed_after == 1:
            if first_cell_admission_path.exists():
                validate_trial1_first_cell_admission(
                    path=first_cell_admission_path,
                    plan=plan,
                    progress=progress,
                )
            else:
                write_trial1_first_cell_admission(
                    path=first_cell_admission_path,
                    plan=plan,
                    progress=progress,
                )

    sealed = _sealed_prefix(progress)
    canary = None
    if phase == "canary" and sealed == CANARY_CELLS:
        if canary_admission_path.exists():
            canary = validate_trial1_canary_admission(
                path=canary_admission_path, plan=plan, progress=progress
            )
        else:
            canary = write_trial1_canary_admission(
                path=canary_admission_path, plan=plan, progress=progress
            )
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_kind": "library-scale-trial1-supervisor-run-v1",
        "phase": phase,
        "batch_id": plan["batch_id"],
        "plan_sha256": plan["plan_sha256"],
        "initial_sealed": initial_sealed,
        "executed_cells": executed,
        "sealed_after": sealed,
        "target_cells": target,
        "phase_complete": sealed == target,
        "canary_admission_sha256": canary.get("report_sha256") if canary else None,
        "claim_boundary": {
            "supervisor_is_model_executor": False,
            "child_evidence_revalidated": True,
            "full_435_completion_claimed": phase == "full" and sealed == EXPECTED_CELLS,
            "full_1305_completion_claimed": False,
            "provider_resolved_model_identity_claimed": False,
        },
    }
    report["report_sha256"] = content_sha256(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cell-root", type=Path, required=True)
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--progress-root", type=Path, required=True)
    parser.add_argument("--first-cell-admission", type=Path, required=True)
    parser.add_argument("--canary-admission", type=Path, required=True)
    parser.add_argument("--max-cells", type=int)
    parser.add_argument("--child-timeout-sec", type=int, default=1800)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("executor_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.executor_argv and args.executor_argv[0] == "--":
        args.executor_argv = args.executor_argv[1:]
    try:
        report = supervise_library_scale_trial1(
            phase=args.phase,
            plan_path=args.plan,
            source_plan_path=args.source_plan,
            manifest_path=args.manifest,
            cell_root=args.cell_root,
            trace_root=args.trace_root,
            progress_root=args.progress_root,
            first_cell_admission_path=args.first_cell_admission,
            canary_admission_path=args.canary_admission,
            executor_argv=args.executor_argv,
            max_cells=args.max_cells,
            child_timeout_sec=args.child_timeout_sec,
            index_path=args.index,
            skills_root=args.skills_root,
        )
    except LibraryScaleSupervisorError as exc:
        parser.error(str(exc))
    print("Merlin 435-cell trial-1 supervisor")
    print(f"phase={report['phase']}")
    print(f"sealed={report['sealed_after']}/{report['target_cells']}")
    print(f"executed={report['executed_cells']}")
    print(f"phase_complete={str(report['phase_complete']).lower()}")
    print("full_1305_completion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
