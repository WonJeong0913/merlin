"""Adapter from vendored SkillsBench curated skills to AIP-lite skill artifacts.

Vendored layout (see experiments/skillsbench/README.md):

    experiments/skillsbench/
      skills/<variant>/SKILL.md [+ extra files]
      skills-index.json

The adapter is read-only over the vendored tree. It does not copy skill
bodies into the artifact; steps reference the markdown sections and the
metadata records provenance (source repo, commit, content hash).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import LifecycleStatus, SkillArtifact, SkillStep

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_H2_RE = re.compile(r"^##\s+(.+)$", re.M)


def parse_skill_md(text: str) -> dict:
    """Extract name, description, and section titles from a SKILL.md."""

    name = ""
    description = ""
    match = _FRONTMATTER_RE.match(text)
    body = text
    if match:
        frontmatter = match.group(1)
        body = text[match.end():]
        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.M)
        if name_match:
            name = name_match.group(1).strip().strip("\"'")
        desc_match = re.search(r"^description:\s*(.+)$", frontmatter, re.M | re.S)
        if desc_match:
            raw = desc_match.group(1)
            next_key = re.search(r"\n\w[\w-]*:", raw)
            if next_key:
                raw = raw[: next_key.start()]
            description = " ".join(raw.split()).strip().strip("\"'")
    sections = _H2_RE.findall(body)
    return {"name": name, "description": description, "sections": sections}


def skill_artifact_from_variant(
    variant_dir: Path,
    *,
    index_entry: dict,
    status: LifecycleStatus = LifecycleStatus.ACTIVE,
) -> SkillArtifact:
    """Build an AIP-lite artifact for one vendored skill variant."""

    skill_md = variant_dir / "SKILL.md"
    parsed = {"name": "", "description": "", "sections": []}
    if skill_md.exists():
        parsed = parse_skill_md(skill_md.read_text(encoding="utf-8", errors="replace"))

    name = parsed["name"] or index_entry["name"]
    description = parsed["description"] or f"SkillsBench curated skill {name}"
    steps = [
        SkillStep(id=f"section-{i + 1}", description=title)
        for i, title in enumerate(parsed["sections"])
    ] or [SkillStep(id="section-1", description="Follow SKILL.md")]

    return SkillArtifact(
        id=f"sb/{index_entry['variant']}",
        name=name,
        description=description,
        trigger=description,
        steps=steps,
        expected_artifacts=[],
        status=status,
        metadata={
            "source": "skillsbench",
            "variant": index_entry["variant"],
            "content_hash": index_entry["content_hash"],
            "size_bytes": index_entry["size_bytes"],
            "n_files": index_entry["n_files"],
            "used_by_tasks": index_entry["used_by_tasks"],
            "skill_md_path": str(skill_md),
        },
    )


def load_skillsbench_artifacts(
    vendored_root: str | Path,
    *,
    limit: int | None = None,
    status: LifecycleStatus = LifecycleStatus.ACTIVE,
) -> list[SkillArtifact]:
    """Load vendored SkillsBench skills as skill artifacts, index order."""

    root = Path(vendored_root)
    index = json.loads((root / "skills-index.json").read_text(encoding="utf-8"))
    artifacts: list[SkillArtifact] = []
    for entry in index["skills"][: limit if limit is not None else len(index["skills"])]:
        variant_dir = root / "skills" / entry["variant"]
        if not variant_dir.is_dir():
            continue
        artifacts.append(
            skill_artifact_from_variant(variant_dir, index_entry=entry, status=status)
        )
    return artifacts
