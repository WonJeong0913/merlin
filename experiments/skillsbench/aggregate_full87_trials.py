"""Produce a coverage-preserving diagnostic aggregate for the frozen full87 batch."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.skillsbench.run_full87_c0_c1_batch import (
    expected_run_id,
    read_pair_summary,
)


def numeric_reward(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def aggregate_full87(manifest: dict[str, Any], runs_root: Path) -> dict[str, Any]:
    condition_id = manifest["condition_id"]
    cell_records: dict[tuple[str, int, str], dict[str, Any]] = {}
    source_summaries: list[str] = []
    missing_pairs: list[dict[str, Any]] = []
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
                missing_pairs.append({"task_id": task_id, "trial_index": trial_index})
                continue
            source_summaries.append(str(summary_path))
            for record in summary["records"]:
                cell_records[(task_id, trial_index, record["arm"])] = record

    by_task: list[dict[str, Any]] = []
    complete_task_c0: list[float] = []
    complete_task_c1: list[float] = []
    complete_task_deltas: list[float] = []
    observed_pair_deltas: list[float] = []
    for task_id in manifest["task_ids"]:
        arm_rewards: dict[str, list[float | None]] = {"C0": [], "C1": []}
        paired_deltas: list[float | None] = []
        for trial_index in manifest["trial_indices"]:
            c0 = numeric_reward(
                cell_records.get((task_id, trial_index, "C0"), {}).get("reward")
            )
            c1 = numeric_reward(
                cell_records.get((task_id, trial_index, "C1"), {}).get("reward")
            )
            arm_rewards["C0"].append(c0)
            arm_rewards["C1"].append(c1)
            delta = c1 - c0 if c0 is not None and c1 is not None else None
            paired_deltas.append(delta)
            if delta is not None:
                observed_pair_deltas.append(delta)
        c0_observed = [value for value in arm_rewards["C0"] if value is not None]
        c1_observed = [value for value in arm_rewards["C1"] if value is not None]
        delta_observed = [value for value in paired_deltas if value is not None]
        fully_scored = (
            len(c0_observed) == len(manifest["trial_indices"])
            and len(c1_observed) == len(manifest["trial_indices"])
        )
        mean_c0 = sum(c0_observed) / len(c0_observed) if c0_observed else None
        mean_c1 = sum(c1_observed) / len(c1_observed) if c1_observed else None
        mean_delta = (
            sum(delta_observed) / len(delta_observed) if delta_observed else None
        )
        if fully_scored and mean_c0 is not None and mean_c1 is not None and mean_delta is not None:
            complete_task_c0.append(mean_c0)
            complete_task_c1.append(mean_c1)
            complete_task_deltas.append(mean_delta)
        by_task.append(
            {
                "task_id": task_id,
                "c0_rewards": arm_rewards["C0"],
                "c1_rewards": arm_rewards["C1"],
                "paired_deltas_c1_minus_c0": paired_deltas,
                "fully_scored": fully_scored,
                "mean_observed_c0_reward": mean_c0,
                "mean_observed_c1_reward": mean_c1,
                "mean_observed_paired_delta": mean_delta,
            }
        )

    records = list(cell_records.values())
    status_counts = Counter(str(record.get("status")) for record in records)
    c0_records = [record for record in records if record.get("arm") == "C0"]
    c1_records = [record for record in records if record.get("arm") == "C1"]
    c1_skill_calls = [
        int(record.get("tool_trace", {}).get("skill_call_count", 0) or 0)
        for record in c1_records
    ]
    skill_names = Counter(
        call.get("skill")
        for record in c1_records
        for call in record.get("tool_trace", {}).get("skill_calls", [])
        if isinstance(call, dict) and isinstance(call.get("skill"), str)
    )
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "diagnostic_only": True,
        "strict_aggregate_required_for_claim": True,
        "coverage": {
            "expected_tasks": len(manifest["task_ids"]),
            "fully_scored_tasks": len(complete_task_deltas),
            "expected_pairs": len(manifest["task_ids"]) * len(manifest["trial_indices"]),
            "observed_pairs": len(source_summaries),
            "valid_reward_pairs": len(observed_pair_deltas),
            "expected_cells": manifest["expected_cells"],
            "observed_cells": len(records),
            "reward_observed_cells": sum(
                numeric_reward(record.get("reward")) is not None for record in records
            ),
            "missing_pairs": missing_pairs,
            "status_counts": dict(sorted(status_counts.items())),
        },
        "macro_over_fully_scored_tasks": {
            "task_count": len(complete_task_deltas),
            "mean_c0_reward": (
                sum(complete_task_c0) / len(complete_task_c0)
                if complete_task_c0
                else None
            ),
            "mean_c1_reward": (
                sum(complete_task_c1) / len(complete_task_c1)
                if complete_task_c1
                else None
            ),
            "mean_paired_delta_c1_minus_c0": (
                sum(complete_task_deltas) / len(complete_task_deltas)
                if complete_task_deltas
                else None
            ),
        },
        "invocation": {
            "c0_cells": len(c0_records),
            "c0_task_skill_calls": sum(
                int(record.get("tool_trace", {}).get("skill_call_count", 0) or 0)
                for record in c0_records
            ),
            "c1_cells": len(c1_records),
            "c1_cells_with_skill_call": sum(count > 0 for count in c1_skill_calls),
            "c1_task_skill_calls": sum(c1_skill_calls),
            "c1_invoked_skill_names": dict(sorted(skill_names.items())),
        },
        "by_task": by_task,
        "source_summaries": source_summaries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = aggregate_full87(manifest, args.runs_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result["coverage"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
