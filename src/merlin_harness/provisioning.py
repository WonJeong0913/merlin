"""Task-conditioned skill provisioning for the first MVP."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .models import LifecycleStatus, SkillArtifact, SkillStep


_TOKEN_RE = re.compile(r"[A-Za-z0-9_가-힣]+")

_STOPWORDS = frozenset(
    "a an the and or of in on to for with is are be been when this that it as by at "
    "from into your you we i not no do does did if then than so such any all use "
    "using used uses task tasks".split()
)


def tokenize(text: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in _STOPWORDS
    ]


def skill_text(skill: SkillArtifact) -> str:
    step_text = " ".join(step.description for step in skill.steps)
    blocked = " ".join(skill.do_not_use_when)
    failures = " ".join(skill.failure_modes)
    return " ".join([skill.name, skill.description, skill.trigger, step_text, blocked, failures])


def lexical_score(task_text: str, skill: SkillArtifact) -> float:
    task_counts = Counter(tokenize(task_text))
    skill_counts = Counter(tokenize(skill_text(skill)))
    if not task_counts or not skill_counts:
        return 0.0
    overlap = sum(min(task_counts[token], skill_counts[token]) for token in task_counts.keys() & skill_counts.keys())
    return overlap / max(1, sum(task_counts.values()))


class LexicalProvisioner:
    """Simple top-k provisioner.

    This is deliberately boring. The point of the first implementation is to
    make exposure measurable before optimizing retrieval.
    """

    def __init__(self, exposure_budget: int = 3) -> None:
        if exposure_budget < 1:
            raise ValueError("exposure_budget must be >= 1")
        self.exposure_budget = exposure_budget

    def provision(self, task_text: str, skills: Iterable[SkillArtifact]) -> list[SkillArtifact]:
        candidates = [skill for skill in skills if skill.status == LifecycleStatus.ACTIVE]
        scored = [(lexical_score(task_text, skill), skill) for skill in candidates]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [skill for _score, skill in scored[: self.exposure_budget]]


def select_best_skill(
    task_text: str,
    provisioned: Iterable[SkillArtifact],
    *,
    min_score: float = 0.1,
) -> SkillArtifact | None:
    """Deterministic greedy selector over provisioned skills.

    Returns the highest lexical-scoring skill at or above `min_score`,
    or None (no-skill fallback). Ties break on skill id for determinism.
    """

    scored = [(lexical_score(task_text, skill), skill) for skill in provisioned]
    scored = [item for item in scored if item[0] >= min_score]
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return scored[0][1]


def make_single_step_skill(
    *,
    skill_id: str,
    name: str,
    description: str,
    trigger: str,
    step_description: str,
    status: LifecycleStatus = LifecycleStatus.CANDIDATE,
) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name=name,
        description=description,
        trigger=trigger,
        steps=[SkillStep(id="step-1", description=step_description)],
        status=status,
    )

