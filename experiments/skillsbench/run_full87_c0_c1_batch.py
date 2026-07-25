"""Run the frozen 87-task C0/C1 x 3 account-auth batch with durable resume state."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
RUNNER = ROOT / "run_model_c0_c1_pilot.py"
DEFAULT_RUNS_ROOT = ROOT / "runs" / "model-c0-c1-full87"
INFRASTRUCTURE_STATUSES = {
    "account_isolation_preflight_failed",
    "agent_failed",
    "build_failed",
    "configuration_contaminated",
    "container_exposure_preflight_failed",
    "container_not_quiescent",
    "container_start_failed",
    "mcp_control_barrier_failed",
    "reward_missing",
    "verifier_command_failed",
    "verifier_contract_inconsistent",
    "verifier_staging_failed",
    "verifier_timeout",
    "workspace_materialization_failed",
}
MODEL_NONCOMPLETION_STATUSES = {"agent_timeout"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def expected_run_id(manifest: dict[str, Any], task_id: str, trial_index: int) -> str:
    return f"{manifest['run_prefix']}-{task_id}-t{trial_index}"


def read_pair_summary(
    summary_path: Path,
    *,
    task_id: str,
    trial_index: int,
    condition_id: str,
) -> dict[str, Any] | None:
    if not summary_path.exists():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    records = summary.get("records")
    if not isinstance(records, list) or len(records) != 2:
        return None
    if {record.get("arm") for record in records} != {"C0", "C1"}:
        return None
    if any(
        record.get("task_id") != task_id
        or record.get("condition_id") != condition_id
        or record.get("trial_index") != trial_index
        for record in records
    ):
        return None
    return summary


def pair_is_scored(summary: dict[str, Any]) -> bool:
    records = summary.get("records")
    return bool(
        isinstance(records, list)
        and len(records) == 2
        and all(
            isinstance(record.get("reward"), (int, float))
            and not isinstance(record.get("reward"), bool)
            for record in records
        )
    )


def infrastructure_guardrail_reached(
    progress: dict[str, Any], max_consecutive_pairs: int
) -> bool:
    return int(progress.get("trailing_infrastructure_pairs", 0)) >= int(
        max_consecutive_pairs
    )


def failed_pair_is_infrastructure(
    runner_returncode: int, summary: dict[str, Any] | None
) -> bool:
    if runner_returncode != 0:
        return True
    if summary is None:
        return True
    records = summary.get("records")
    return bool(
        isinstance(records, list)
        and any(record.get("status") in INFRASTRUCTURE_STATUSES for record in records)
    )


def summarize_progress(manifest: dict[str, Any], runs_root: Path) -> dict[str, Any]:
    condition_id = manifest["condition_id"]
    summaries: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    infrastructure_pair_flags: list[bool] = []
    invalid_pair_runs: list[dict[str, Any]] = []
    for task_id in manifest["task_ids"]:
        for trial_index in manifest["trial_indices"]:
            run_id = expected_run_id(manifest, task_id, trial_index)
            summary_path = runs_root / run_id / "summary.json"
            summary = read_pair_summary(
                summary_path,
                task_id=task_id,
                trial_index=trial_index,
                condition_id=condition_id,
            )
            if summary is None:
                continue
            if not pair_is_scored(summary):
                invalid_pair_runs.append(
                    {
                        "task_id": task_id,
                        "trial_index": trial_index,
                        "run_id": run_id,
                        "statuses": [
                            record.get("status") for record in summary.get("records", [])
                        ],
                    }
                )
                continue
            summaries.append(summary)
            infrastructure_pair_flags.append(
                any(
                    record.get("status") in INFRASTRUCTURE_STATUSES
                    for record in summary["records"]
                )
            )
            completed.append(
                {
                    "task_id": task_id,
                    "trial_index": trial_index,
                    "run_id": run_id,
                    "summary_sha256": sha256_file(summary_path),
                }
            )

    records = [record for summary in summaries for record in summary["records"]]
    observed_rewards = [
        float(record["reward"])
        for record in records
        if isinstance(record.get("reward"), (int, float))
        and not isinstance(record.get("reward"), bool)
    ]
    provider_cost = sum(
        float(record.get("account_usage", {}).get("total_cost_usd", 0.0) or 0.0)
        for record in records
    )
    trailing_infrastructure_pairs = 0
    for is_infrastructure in reversed(infrastructure_pair_flags):
        if not is_infrastructure:
            break
        trailing_infrastructure_pairs += 1
    return {
        "completed_pairs": len(summaries),
        "expected_pairs": len(manifest["task_ids"]) * len(manifest["trial_indices"]),
        "completed_cells": len(records),
        "expected_cells": manifest["expected_cells"],
        "reward_observed_cells": len(observed_rewards),
        "passed_cells": sum(record.get("passed") is True for record in records),
        "mean_observed_reward": (
            sum(observed_rewards) / len(observed_rewards) if observed_rewards else None
        ),
        "provider_reported_usage_estimate_usd": provider_cost,
        "trailing_infrastructure_pairs": trailing_infrastructure_pairs,
        "infrastructure_status_counts": {
            status: sum(record.get("status") == status for record in records)
            for status in sorted(INFRASTRUCTURE_STATUSES)
            if any(record.get("status") == status for record in records)
        },
        "model_noncompletion_status_counts": {
            status: sum(record.get("status") == status for record in records)
            for status in sorted(MODEL_NONCOMPLETION_STATUSES)
            if any(record.get("status") == status for record in records)
        },
        "invalid_pair_runs": invalid_pair_runs,
        "completed": completed,
    }


def validate_manifest(manifest_path: Path, manifest: dict[str, Any]) -> None:
    task_ids = manifest.get("task_ids")
    if not isinstance(task_ids, list) or len(task_ids) != 87 or len(set(task_ids)) != 87:
        raise ValueError("full87 manifest must contain 87 unique task ids")
    if manifest.get("trial_indices") != [1, 2, 3]:
        raise ValueError("full87 manifest must freeze trial indices [1, 2, 3]")
    if manifest.get("arms") != ["C0", "C1"] or manifest.get("expected_cells") != 522:
        raise ValueError("full87 manifest must freeze C0/C1 and 522 cells")
    for relative, expected_hash in manifest.get("frozen_inputs", {}).items():
        path = REPO_ROOT / relative
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ValueError(f"frozen input hash mismatch: {relative}")
    if manifest.get("manifest_sha256") not in {None, "self"}:
        raise ValueError("manifest_sha256 must be omitted or use the literal self marker")
    if not manifest_path.is_file():
        raise ValueError("manifest path does not exist")


def archive_incomplete_run(run_root: Path) -> None:
    if not run_root.exists():
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = run_root.with_name(f"{run_root.name}-incomplete-{timestamp}")
    suffix = 1
    while destination.exists():
        destination = run_root.with_name(
            f"{run_root.name}-incomplete-{timestamp}-{suffix}"
        )
        suffix += 1
    run_root.rename(destination)


def cleanup_task_image(task_id: str) -> None:
    from experiments.skillsbench.run_oracle_readiness import run_command, safe_name

    image = f"theking-skillsbench-model-{safe_name(task_id)}:latest"
    run_command(["docker", "rmi", "-f", image], timeout_sec=180)
    run_command(["docker", "image", "prune", "-f"], timeout_sec=300)


def run_batch(
    manifest_path: Path,
    *,
    runs_root: Path,
    state_path: Path,
    max_pairs: int | None,
    dry_run: bool,
) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest_path, manifest)
    runs_root.mkdir(parents=True, exist_ok=True)
    readiness_path = REPO_ROOT / manifest["readiness_summary"]
    condition_id = manifest["condition_id"]
    min_free_bytes = int(float(manifest["guardrails"]["min_free_disk_gb"]) * 1024**3)
    max_cost = float(manifest["guardrails"]["provider_equivalent_usage_usd"])
    max_consecutive_infra = int(manifest["guardrails"]["max_consecutive_infrastructure_pairs"])

    from experiments.skillsbench.run_oracle_readiness import ensure_docker, stop_process

    dockerd_proc = None
    executed_pairs = 0
    initial_progress = summarize_progress(manifest, runs_root)
    previous_state = read_json_object(state_path)
    previous_progress = previous_state.get("progress", {})
    previous_completed_pairs = (
        int(previous_progress.get("completed_pairs", 0))
        if isinstance(previous_progress, dict)
        else 0
    )
    if initial_progress["completed_pairs"] > previous_completed_pairs:
        consecutive_infra = int(initial_progress["trailing_infrastructure_pairs"])
    else:
        consecutive_infra = max(
            int(initial_progress["trailing_infrastructure_pairs"]),
            int(previous_state.get("consecutive_infrastructure_failures", 0)),
        )
    state: dict[str, Any] = {}
    try:
        if not dry_run:
            dockerd_proc = ensure_docker(runs_root / "batch-runtime")
        for task_id in manifest["task_ids"]:
            for trial_index in manifest["trial_indices"]:
                progress = summarize_progress(manifest, runs_root)
                state = {
                    "schema_version": 1,
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "running",
                    "manifest": str(manifest_path),
                    "manifest_sha256": sha256_file(manifest_path),
                    "progress": progress,
                    "consecutive_infrastructure_failures": consecutive_infra,
                }
                write_json_atomic(state_path, state)
                if (
                    not dry_run
                    and infrastructure_guardrail_reached(
                        {
                            "trailing_infrastructure_pairs": consecutive_infra
                        },
                        max_consecutive_infra,
                    )
                ):
                    state["status"] = "guardrail_stop"
                    state["reason"] = (
                        "consecutive_infrastructure_pair_guardrail_reached"
                    )
                    write_json_atomic(state_path, state)
                    return 75
                if (
                    not dry_run
                    and progress["provider_reported_usage_estimate_usd"] >= max_cost
                ):
                    state["status"] = "guardrail_stop"
                    state["reason"] = "provider_equivalent_usage_guardrail_reached"
                    write_json_atomic(state_path, state)
                    return 75
                free_bytes = shutil.disk_usage(REPO_ROOT).free
                if not dry_run and free_bytes < min_free_bytes:
                    state["status"] = "guardrail_stop"
                    state["reason"] = "free_disk_guardrail_reached"
                    state["free_disk_bytes"] = free_bytes
                    write_json_atomic(state_path, state)
                    return 75

                run_id = expected_run_id(manifest, task_id, trial_index)
                run_root = runs_root / run_id
                summary = read_pair_summary(
                    run_root / "summary.json",
                    task_id=task_id,
                    trial_index=trial_index,
                    condition_id=condition_id,
                )
                if summary is not None and pair_is_scored(summary):
                    print(f"SKIP {task_id} trial={trial_index}", flush=True)
                    if (
                        not dry_run
                        and trial_index == manifest["trial_indices"][-1]
                    ):
                        cleanup_task_image(task_id)
                    continue
                if dry_run:
                    print(f"DRY-RUN {task_id} trial={trial_index} run_id={run_id}", flush=True)
                    executed_pairs += 1
                    if max_pairs is not None and executed_pairs >= max_pairs:
                        state["status"] = "bounded_stop"
                        state["reason"] = "dry_run_max_pairs_reached"
                        state["dry_run_planned_pairs"] = executed_pairs
                        write_json_atomic(state_path, state)
                        return 0
                    continue

                archive_incomplete_run(run_root)
                command = [
                    sys.executable,
                    str(RUNNER),
                    "--task",
                    task_id,
                    "--condition",
                    condition_id,
                    "--oracle-summary",
                    str(readiness_path),
                    "--readiness-policy",
                    "all",
                    "--run-id",
                    run_id,
                    "--runs-root",
                    str(runs_root),
                    "--trial-index",
                    str(trial_index),
                    "--harness-mode",
                    manifest["harness_mode"],
                    "--keep-image",
                    "--discard-workspace",
                ]
                print(f"RUN {task_id} trial={trial_index} run_id={run_id}", flush=True)
                report = subprocess.run(command, cwd=REPO_ROOT, check=False)
                summary = read_pair_summary(
                    run_root / "summary.json",
                    task_id=task_id,
                    trial_index=trial_index,
                    condition_id=condition_id,
                )
                if report.returncode != 0 or summary is None or not pair_is_scored(summary):
                    is_infrastructure = failed_pair_is_infrastructure(
                        report.returncode, summary
                    )
                    if is_infrastructure:
                        consecutive_infra += 1
                    state["consecutive_infrastructure_failures"] = consecutive_infra
                    state["status"] = (
                        "guardrail_stop"
                        if is_infrastructure
                        and consecutive_infra >= max_consecutive_infra
                        else "runner_failed"
                    )
                    if state["status"] == "guardrail_stop":
                        state["reason"] = (
                            "consecutive_infrastructure_pair_guardrail_reached"
                        )
                    elif report.returncode != 0:
                        state["reason"] = f"runner_exit_{report.returncode}"
                    elif is_infrastructure:
                        state["reason"] = "pair_unscored_infrastructure"
                    else:
                        state["reason"] = "pair_unscored_noninfrastructure"
                    state["task_id"] = task_id
                    state["trial_index"] = trial_index
                    write_json_atomic(state_path, state)
                    return 75 if state["status"] == "guardrail_stop" else (report.returncode or 1)

                statuses = {record.get("status") for record in summary["records"]}
                if statuses.intersection(INFRASTRUCTURE_STATUSES):
                    consecutive_infra += 1
                else:
                    consecutive_infra = 0
                if consecutive_infra >= max_consecutive_infra:
                    state["status"] = "guardrail_stop"
                    state["reason"] = "consecutive_infrastructure_pair_guardrail_reached"
                    state["task_id"] = task_id
                    state["trial_index"] = trial_index
                    state["consecutive_infrastructure_failures"] = consecutive_infra
                    write_json_atomic(state_path, state)
                    return 75

                executed_pairs += 1
                if trial_index == manifest["trial_indices"][-1]:
                    cleanup_task_image(task_id)
                if max_pairs is not None and executed_pairs >= max_pairs:
                    break
            if max_pairs is not None and executed_pairs >= max_pairs:
                break

        progress = summarize_progress(manifest, runs_root)
        state = {
            "schema_version": 1,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": (
                "complete"
                if progress["completed_pairs"] == progress["expected_pairs"]
                else "bounded_stop"
            ),
            "manifest": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "progress": progress,
            "consecutive_infrastructure_failures": consecutive_infra,
        }
        write_json_atomic(state_path, state)
        return 0
    finally:
        stop_process(dockerd_proc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--max-pairs", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return run_batch(
        args.manifest,
        runs_root=args.runs_root,
        state_path=args.state,
        max_pairs=args.max_pairs,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
