"""Merge targeted oracle-readiness reruns into a preserved base summary."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    observed_rewards = [float(record["reward"]) for record in records if record.get("reward") is not None]
    return {
        "task_count": len(records),
        "passed": sum(bool(record.get("passed")) for record in records),
        "reward_observed": len(observed_rewards),
        "mean_observed_reward": sum(observed_rewards) / len(observed_rewards) if observed_rewards else None,
        "status_counts": dict(sorted(counts.items())),
    }


def merge_readiness_summaries(base_path: Path, overlay_paths: list[Path]) -> dict[str, Any]:
    base = json.loads(base_path.read_text(encoding="utf-8"))
    records_by_id = {
        record["task_id"]: deepcopy(record)
        for record in base.get("records", [])
    }
    original_ids = set(records_by_id)
    replacements: list[dict[str, Any]] = []

    for overlay_path in overlay_paths:
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        seen_in_overlay: set[str] = set()
        for replacement in overlay.get("records", []):
            task_id = replacement["task_id"]
            if task_id in seen_in_overlay:
                raise ValueError(f"duplicate task in overlay {overlay_path}: {task_id}")
            seen_in_overlay.add(task_id)
            if task_id not in original_ids:
                raise ValueError(f"overlay task not present in base summary: {task_id}")
            previous = records_by_id[task_id]
            replacement_copy = deepcopy(replacement)
            replacement_copy["reconciliation"] = {
                "base_status": previous.get("status"),
                "base_reward": previous.get("reward"),
                "overlay_run_id": overlay.get("run_id", overlay_path.parent.name),
                "overlay_summary": str(overlay_path),
                "overlay_summary_sha256": _sha256(overlay_path),
            }
            records_by_id[task_id] = replacement_copy
            replacements.append(
                {
                    "task_id": task_id,
                    **replacement_copy["reconciliation"],
                    "new_status": replacement_copy.get("status"),
                    "new_reward": replacement_copy.get("reward"),
                }
            )

    records = [records_by_id[task_id] for task_id in sorted(records_by_id)]
    return {
        "run_id": "strict-readiness-reconciled-20260710",
        "derived_artifact": True,
        "policy": base.get("policy", "strict"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_summary": str(base_path),
        "base_summary_sha256": _sha256(base_path),
        "overlay_summaries": [
            {"path": str(path), "sha256": _sha256(path)}
            for path in overlay_paths
        ],
        "summary": _summary(records),
        "records": records,
        "replacements": replacements,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    merged = merge_readiness_summaries(args.base, args.overlay)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(merged["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
