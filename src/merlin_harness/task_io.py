"""JSON loading for deterministic task specs."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import TaskSpec, VerifierSpec


def task_from_dict(data: dict[str, Any]) -> TaskSpec:
    verifier = VerifierSpec(**data["verifier"])
    return TaskSpec(
        id=data["id"],
        instruction=data["instruction"],
        verifier=verifier,
        setup_files=dict(data.get("setup_files", {})),
        oracle_skill_ids=list(data.get("oracle_skill_ids", [])),
        regression_group=data.get("regression_group"),
        metadata=dict(data.get("metadata", {})),
    )


def task_to_dict(task: TaskSpec) -> dict[str, Any]:
    return asdict(task)


def load_task(path: str | Path) -> TaskSpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return task_from_dict(data)


def save_task(task: TaskSpec, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(task_to_dict(task), indent=2, sort_keys=True), encoding="utf-8")
    return target


def load_tasks(root: str | Path) -> list[TaskSpec]:
    path = Path(root)
    if path.is_file():
        return [load_task(path)]
    return [load_task(item) for item in sorted(path.glob("*.json"))]

