"""Normalize the SkillsBench skill index to paper-safe curated terminology.

Only directories containing ``SKILL.md`` are skills. Task-local curated
bundles are not empirical oracle sets; oracle membership must be estimated by
isolated uplift for each model/backend/harness/threshold configuration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def normalize_index(index: dict[str, Any], *, skills_root: Path) -> dict[str, Any]:
    valid_variants = {
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    normalized = dict(index)
    normalized["skills"] = [
        skill
        for skill in index.get("skills", [])
        if skill.get("variant") in valid_variants
    ]
    normalized_tasks: list[dict[str, Any]] = []
    for source_task in index.get("tasks", []):
        task = dict(source_task)
        curated = task.pop(
            "oracle_skill_variants",
            task.get("curated_skill_variants", []),
        )
        task["curated_skill_variants"] = [
            variant for variant in curated if variant in valid_variants
        ]
        normalized_tasks.append(task)
    normalized["tasks"] = normalized_tasks
    normalized["skill_semantics"] = {
        "curated_skill_variants": "task-local upstream authored bundle; not an empirical oracle set",
        "empirical_oracle_required_fields": [
            "task_id",
            "model_id",
            "backend",
            "harness_mode",
            "tau",
            "candidate_pool_hash",
            "repeats",
            "skill_ids",
        ],
        "skill_directory_rule": "directory must contain SKILL.md",
    }
    return normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "skills-index.json")
    parser.add_argument("--skills-root", type=Path, default=ROOT / "skills")
    parser.add_argument("--output", type=Path, default=ROOT / "skills-index.json")
    args = parser.parse_args(argv)

    source = json.loads(args.index.read_text(encoding="utf-8"))
    normalized = normalize_index(source, skills_root=args.skills_root)
    args.output.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"tasks={len(normalized['tasks'])} skills={len(normalized['skills'])} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
