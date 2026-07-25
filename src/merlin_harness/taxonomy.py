"""Task taxonomy borrowed from SkillsBench and extended for Merlin."""

from __future__ import annotations

from .models import TaskSpec, ValidationResult


SKILLSBENCH_DOMAINS = {
    "Software Engineering",
    "Industrial & Physical Systems",
    "Natural Science",
    "Office & White Collar",
    "Finance & Economics",
    "Mathematics & OR",
    "Cybersecurity",
    "Media & Content Production",
}

SKILLSBENCH_CAPABILITIES = {
    "Reasoning",
    "Agentic Coding",
    "Multimodal",
    "Tool Use",
    "Search & Research",
}

SKILLSBENCH_DIFFICULTIES = {
    "C": "Core, under 60 minutes for a domain specialist",
    "X": "Extended, 1-4 hours for a domain specialist",
    "E": "Extreme, over 4 hours for a domain specialist",
}

MERLIN_SKILL_DEPENDENCY = {"none", "low", "medium", "high"}

MERLIN_SHADOWING_ROLES = {
    "control",
    "oracle_target",
    "distractor_candidate",
    "regression_probe",
}

MERLIN_MVP_TIERS = {"smoke", "mvp", "extended"}


def validate_task_taxonomy(task: TaskSpec) -> list[ValidationResult]:
    """Validate task classification metadata.

    SkillsBench supplies the first three axes. Merlin adds three extra axes
    so the same task set can later test provisioning and shadowing behavior.
    """

    metadata = task.metadata
    checks = [
        ValidationResult("taxonomy:benchmark_family", metadata.get("benchmark_family") == "SkillsBench-style"),
        ValidationResult("taxonomy:domain", metadata.get("domain") in SKILLSBENCH_DOMAINS),
        ValidationResult("taxonomy:capability", metadata.get("capability") in SKILLSBENCH_CAPABILITIES),
        ValidationResult("taxonomy:difficulty", metadata.get("difficulty") in SKILLSBENCH_DIFFICULTIES),
        ValidationResult("taxonomy:skill_dependency", metadata.get("skill_dependency") in MERLIN_SKILL_DEPENDENCY),
        ValidationResult("taxonomy:shadowing_role", metadata.get("shadowing_role") in MERLIN_SHADOWING_ROLES),
        ValidationResult("taxonomy:mvp_tier", metadata.get("mvp_tier") in MERLIN_MVP_TIERS),
    ]
    return checks

