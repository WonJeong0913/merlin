"""Create a static readiness manifest for the 87-task SkillsBench mirror.

This is E1's first pass. It does not execute tasks or verifiers. It records
whether each task has the expected files and which infrastructure hints must be
handled before paper-level 87-task runs.

Run from the repository root:

    python3 experiments/skillsbench/audit_readiness.py
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "readiness-87.json"

DEPENDENCY_FILE_NAMES = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Makefile",
}


@dataclass(slots=True)
class TaskReadiness:
    task_id: str
    title: str | None
    category: str | None
    difficulty: str | None
    required_skills: list[str]
    curated_skill_variants: list[str]
    has_task_md: bool
    has_environment: bool
    has_oracle: bool
    has_verifier: bool
    verifier_files: list[str] = field(default_factory=list)
    per_task_skill_dirs: list[str] = field(default_factory=list)
    per_task_skill_md: list[str] = field(default_factory=list)
    missing_skill_md_dirs: list[str] = field(default_factory=list)
    dependency_files: list[str] = field(default_factory=list)
    infrastructure_flags: list[str] = field(default_factory=list)
    static_status: str = "unknown"
    repair_notes: list[str] = field(default_factory=list)


def relative_paths(paths: list[Path], root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in sorted(paths)]


def task_title(task_md: Path) -> str | None:
    if not task_md.exists():
        return None
    for line in task_md.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def dependency_files(task_dir: Path) -> list[Path]:
    return sorted(path for path in task_dir.rglob("*") if path.is_file() and path.name in DEPENDENCY_FILE_NAMES)


def infrastructure_flags(deps: list[Path], task_dir: Path) -> list[str]:
    flags: set[str] = set()
    names = {path.name for path in deps}
    if "Dockerfile" in names or "docker-compose.yml" in names or "docker-compose.yaml" in names:
        flags.add("docker")
    if "package.json" in names:
        flags.add("node")
    if "requirements.txt" in names or "pyproject.toml" in names:
        flags.add("python")
    if any(path.name in {"Makefile"} for path in deps):
        flags.add("make")
    if (task_dir / "environment" / "workspace").exists():
        flags.add("workspace_seed")
    return sorted(flags)


def compute_status(record: TaskReadiness) -> None:
    critical_missing: list[str] = []
    if not record.has_task_md:
        critical_missing.append("task_md")
    if not record.has_environment:
        critical_missing.append("environment")
    if not record.has_verifier:
        critical_missing.append("verifier")
    if not record.has_oracle:
        critical_missing.append("oracle")
    if not record.curated_skill_variants:
        critical_missing.append("curated_skill_variants")
    if not record.per_task_skill_md:
        critical_missing.append("per_task_skill_md")

    if critical_missing:
        record.static_status = "needs_repair"
        record.repair_notes.extend(f"missing:{item}" for item in critical_missing)
    elif record.infrastructure_flags:
        record.static_status = "needs_infrastructure_review"
        record.repair_notes.append("static files present; dependency/runtime review required")
    else:
        record.static_status = "static_ready"

    if record.missing_skill_md_dirs:
        record.repair_notes.append("some environment/skills subdirectories do not contain SKILL.md")


def build_manifest(root: Path = ROOT) -> dict[str, Any]:
    index = json.loads((root / "skills-index.json").read_text(encoding="utf-8"))
    tasks_dir = root / "tasks"
    index_by_id = {task["id"]: task for task in index.get("tasks", [])}
    task_ids = sorted(index_by_id)

    records: list[TaskReadiness] = []
    for task_id in task_ids:
        item = index_by_id[task_id]
        task_dir = tasks_dir / task_id
        task_md = task_dir / "task.md"
        environment_dir = task_dir / "environment"
        oracle_dir = task_dir / "oracle"
        verifier_dir = task_dir / "verifier"
        skill_dirs = [path for path in (environment_dir / "skills").glob("*") if path.is_dir()]
        skill_md = [path / "SKILL.md" for path in skill_dirs if (path / "SKILL.md").exists()]
        missing_skill_md = [path for path in skill_dirs if not (path / "SKILL.md").exists()]
        deps = dependency_files(task_dir)

        record = TaskReadiness(
            task_id=task_id,
            title=task_title(task_md),
            category=item.get("category"),
            difficulty=item.get("difficulty"),
            required_skills=list(item.get("required_skills", [])),
            curated_skill_variants=list(item.get("curated_skill_variants", [])),
            has_task_md=task_md.exists(),
            has_environment=environment_dir.exists(),
            has_oracle=oracle_dir.exists(),
            has_verifier=verifier_dir.exists(),
            verifier_files=relative_paths([path for path in verifier_dir.rglob("*") if path.is_file()], task_dir)
            if verifier_dir.exists()
            else [],
            per_task_skill_dirs=relative_paths(skill_dirs, task_dir),
            per_task_skill_md=relative_paths(skill_md, task_dir),
            missing_skill_md_dirs=relative_paths(missing_skill_md, task_dir),
            dependency_files=relative_paths(deps, task_dir),
            infrastructure_flags=infrastructure_flags(deps, task_dir),
        )
        compute_status(record)
        records.append(record)

    status_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    for record in records:
        status_counts[record.static_status] = status_counts.get(record.static_status, 0) + 1
        for flag in record.infrastructure_flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    return {
        "source": index.get("source"),
        "commit": index.get("commit"),
        "license": index.get("license"),
        "task_count": len(records),
        "target_task_count": 87,
        "status_counts": dict(sorted(status_counts.items())),
        "infrastructure_flag_counts": dict(sorted(flag_counts.items())),
        "notes": [
            "Static readiness only; this manifest does not execute verifiers.",
            "Paper-level SkillsBench claims should target all 87 tasks.",
            "Tasks with infrastructure issues require repair or pre-registered exceptions, not silent removal.",
        ],
        "tasks": [asdict(record) for record in records],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SkillsBench 87-task static readiness manifest.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Manifest output path.")
    args = parser.parse_args(argv)

    manifest = build_manifest(ROOT)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"saved -> {output}")
    print(f"task_count={manifest['task_count']}")
    print(f"status_counts={manifest['status_counts']}")
    print(f"infrastructure_flag_counts={manifest['infrastructure_flag_counts']}")
    return 0 if manifest["task_count"] == manifest["target_task_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
