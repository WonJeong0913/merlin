"""Create a deterministic 87-task SkillsBench split manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_TARGETS = {"adaptation": 35, "held_out": 30, "regression": 22}
DEFAULT_SEED = 20260708


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_task_key(task_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).hexdigest()


def _task_id(task: dict[str, Any]) -> str:
    return task.get("id") or task["task_id"]


def _readiness_by_id(readiness: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if readiness is None:
        return {}
    return {task["task_id"]: task for task in readiness.get("tasks", [])}


def _assign_split(
    tasks: list[dict[str, Any]],
    *,
    seed: int,
    targets: dict[str, int],
) -> dict[str, list[dict[str, Any]]]:
    total_target = sum(targets.values())
    if total_target != len(tasks):
        raise ValueError(f"split targets sum to {total_target}, but task count is {len(tasks)}")

    split_order = list(targets)
    assigned: dict[str, list[dict[str, Any]]] = {name: [] for name in split_order}
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        strata[(task.get("category", "unknown"), task.get("difficulty", "unknown"))].append(task)

    for _, group in sorted(strata.items(), key=lambda item: item[0]):
        ordered = sorted(group, key=lambda task: stable_task_key(_task_id(task), seed))
        for task in ordered:
            candidates = [name for name in split_order if len(assigned[name]) < targets[name]]
            if not candidates:
                raise ValueError("no split capacity left")
            split = min(
                candidates,
                key=lambda name: (
                    len(assigned[name]) / targets[name],
                    len(assigned[name]),
                    split_order.index(name),
                ),
            )
            assigned[split].append(task)

    for name, expected in targets.items():
        actual = len(assigned[name])
        if actual != expected:
            raise ValueError(f"split {name} has {actual} tasks, expected {expected}")
    return assigned


def _task_entry(task: dict[str, Any], readiness_entry: dict[str, Any] | None) -> dict[str, Any]:
    task_id = _task_id(task)
    entry = {
        "task_id": task_id,
        "category": task.get("category"),
        "difficulty": task.get("difficulty"),
        "required_skills": task.get("required_skills", []),
        "curated_skill_variants": task.get("curated_skill_variants", []),
    }
    if readiness_entry:
        entry.update(
            {
                "static_status": readiness_entry.get("static_status"),
                "infrastructure_flags": readiness_entry.get("infrastructure_flags", []),
                "has_oracle": readiness_entry.get("has_oracle"),
                "has_verifier": readiness_entry.get("has_verifier"),
            }
        )
    return entry


def _counter_for(entries: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(entry.get(field, "unknown")) for entry in entries).items()))


def build_split_manifest(
    *,
    index: dict[str, Any],
    readiness: dict[str, Any] | None = None,
    seed: int = DEFAULT_SEED,
    targets: dict[str, int] | None = None,
) -> dict[str, Any]:
    split_targets = targets or dict(DEFAULT_TARGETS)
    tasks = list(index.get("tasks", []))
    if not tasks:
        raise ValueError("skills index has no tasks")

    task_ids = [_task_id(task) for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("skills index contains duplicate task ids")

    readiness_map = _readiness_by_id(readiness)
    assigned = _assign_split(tasks, seed=seed, targets=split_targets)
    splits = {
        name: [_task_entry(task, readiness_map.get(_task_id(task))) for task in split_tasks]
        for name, split_tasks in assigned.items()
    }

    all_assigned = [entry["task_id"] for entries in splits.values() for entry in entries]
    if sorted(all_assigned) != sorted(task_ids):
        raise ValueError("split manifest does not cover exactly the index task ids")

    summary = {
        "counts": {name: len(entries) for name, entries in splits.items()},
        "category_counts": {name: _counter_for(entries, "category") for name, entries in splits.items()},
        "difficulty_counts": {name: _counter_for(entries, "difficulty") for name, entries in splits.items()},
        "held_out_min_seeds_for_100_trials": math.ceil(100 / len(splits["held_out"])),
    }

    return {
        "created": date.today().isoformat(),
        "source": index.get("source"),
        "commit": index.get("commit"),
        "license": index.get("license"),
        "seed": seed,
        "task_count": len(tasks),
        "split_policy": {
            "targets": split_targets,
            "strata": ["category", "difficulty"],
            "assignment": "stable hash inside each stratum, then lowest fill-ratio split assignment",
            "regression_definition": (
                "This is the pre-registered regression-candidate split. "
                "The final regression set is the subset passed by the t0 harness."
            ),
            "held_out_power_rule": "Use n_heldout_tasks * n_seeds >= 100 for headline claims.",
        },
        "summary": summary,
        "splits": splits,
    }


def parse_targets(args: argparse.Namespace) -> dict[str, int]:
    return {"adaptation": args.adaptation, "held_out": args.held_out, "regression": args.regression}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create deterministic SkillsBench train/eval split manifest.")
    parser.add_argument("--index", type=Path, default=Path("experiments/skillsbench/skills-index.json"))
    parser.add_argument("--readiness", type=Path, default=Path("experiments/skillsbench/readiness-87.json"))
    parser.add_argument("--output", type=Path, default=Path("experiments/skillsbench/split-manifest.json"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--adaptation", type=int, default=DEFAULT_TARGETS["adaptation"])
    parser.add_argument("--held-out", type=int, default=DEFAULT_TARGETS["held_out"])
    parser.add_argument("--regression", type=int, default=DEFAULT_TARGETS["regression"])
    args = parser.parse_args(argv)

    readiness = load_json(args.readiness) if args.readiness.exists() else None
    manifest = build_split_manifest(
        index=load_json(args.index),
        readiness=readiness,
        seed=args.seed,
        targets=parse_targets(args),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    counts = manifest["summary"]["counts"]
    min_seeds = manifest["summary"]["held_out_min_seeds_for_100_trials"]
    print(
        "wrote="
        f"{args.output} task_count={manifest['task_count']} "
        f"adaptation={counts['adaptation']} held_out={counts['held_out']} "
        f"regression={counts['regression']} held_out_min_seeds={min_seeds}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
