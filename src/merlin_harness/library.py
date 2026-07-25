"""File-backed skill library for the MVP."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .models import LifecycleStatus, SkillArtifact, SkillEdge, SkillStep


def _json_default(value: Any) -> Any:
    if isinstance(value, LifecycleStatus):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class FileSkillLibrary:
    """Tiny JSON store used before a database is justified."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def skill_path(self, skill_id: str) -> Path:
        return self.root / f"{skill_id}.json"

    def save(self, skill: SkillArtifact) -> Path:
        path = self.skill_path(skill.id)
        path.write_text(
            json.dumps(skill.to_dict(), indent=2, sort_keys=True, default=_json_default),
            encoding="utf-8",
        )
        return path

    def load(self, skill_id: str) -> SkillArtifact:
        data = json.loads(self.skill_path(skill_id).read_text(encoding="utf-8"))
        return self._from_dict(data)

    def list(self, status: LifecycleStatus | None = None) -> list[SkillArtifact]:
        skills = [self._from_dict(json.loads(path.read_text(encoding="utf-8"))) for path in sorted(self.root.glob("*.json"))]
        if status is None:
            return skills
        return [skill for skill in skills if skill.status == status]

    @staticmethod
    def _from_dict(data: dict[str, Any]) -> SkillArtifact:
        return SkillArtifact(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            trigger=data["trigger"],
            do_not_use_when=list(data.get("do_not_use_when", [])),
            steps=[SkillStep(**step) for step in data.get("steps", [])],
            edges=[SkillEdge(**edge) for edge in data.get("edges", [])],
            validators=list(data.get("validators", [])),
            expected_artifacts=list(data.get("expected_artifacts", [])),
            failure_modes=list(data.get("failure_modes", [])),
            provenance_trace_ids=list(data.get("provenance_trace_ids", [])),
            status=LifecycleStatus(data.get("status", LifecycleStatus.CANDIDATE.value)),
            version=int(data.get("version", 1)),
            metadata=dict(data.get("metadata", {})),
        )

