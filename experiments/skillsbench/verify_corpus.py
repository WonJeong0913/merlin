"""Verify the vendored SkillsBench corpus mirror.

Run from the repository root:

    python3 experiments/skillsbench/verify_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_COMMIT = "5433cf15c343f0da5fb942b80dc7dcb7c76506df"
EXPECTED_TASKS = 87


def main() -> int:
    index = json.loads((ROOT / "skills-index.json").read_text(encoding="utf-8"))
    task_dirs = sorted(path for path in (ROOT / "tasks").iterdir() if path.is_dir())
    task_md = sorted((ROOT / "tasks").glob("*/task.md"))
    per_task_skill_dirs = sorted(
        path
        for path in (ROOT / "tasks").glob("*/environment/skills/*")
        if path.is_dir()
    )
    per_task_skill_md = sorted((ROOT / "tasks").glob("*/environment/skills/*/SKILL.md"))
    dedup_skill_dirs = sorted(path for path in (ROOT / "skills").iterdir() if path.is_dir())
    dedup_skill_packages = [path for path in dedup_skill_dirs if (path / "SKILL.md").is_file()]

    checks = {
        "source_commit": index.get("commit") == EXPECTED_COMMIT,
        "index_tasks": len(index.get("tasks", [])) == EXPECTED_TASKS,
        "task_dirs": len(task_dirs) == EXPECTED_TASKS,
        "task_md": len(task_md) == EXPECTED_TASKS,
        "dedup_skill_packages": len(dedup_skill_packages) == len(index.get("skills", [])),
        "indexed_skill_packages_have_skill_md": all(
            (ROOT / "skills" / skill["variant"] / "SKILL.md").is_file()
            for skill in index.get("skills", [])
        ),
        "curated_mapping_uses_valid_packages": all(
            set(task.get("curated_skill_variants", []))
            <= {path.name for path in dedup_skill_packages}
            for task in index.get("tasks", [])
        ),
        "task_names_match_index": {path.name for path in task_dirs}
        == {task["id"] for task in index.get("tasks", [])},
    }

    print(f"source={index.get('source')}")
    print(f"commit={index.get('commit')}")
    print(f"tasks_index={len(index.get('tasks', []))}")
    print(f"tasks_dirs={len(task_dirs)}")
    print(f"task_md={len(task_md)}")
    print(f"per_task_skill_dirs={len(per_task_skill_dirs)}")
    print(f"per_task_skill_md={len(per_task_skill_md)}")
    print(f"dedup_directories_including_helpers={len(dedup_skill_dirs)}")
    print(f"dedup_skill_packages={len(dedup_skill_packages)}")
    print(f"dedup_skill_index_entries={len(index.get('skills', []))}")
    for name, passed in checks.items():
        print(f"{name}={'ok' if passed else 'FAIL'}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
