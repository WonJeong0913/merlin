"""Model-free evaluator for Merlin's governed provisioning policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.merlin_harness.governed_provisioning import GovernedProvisioner, active_library_snapshot
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.models import SkillArtifact, TaskSpec
from src.merlin_harness.provisioning import LexicalProvisioner
from src.merlin_harness.task_io import load_tasks


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS = REPO_ROOT / "experiments" / "mvp" / "tasks"
DEFAULT_SKILLS = REPO_ROOT / "experiments" / "mvp" / "skills"
DEFAULT_DISTRACTORS = REPO_ROOT / "experiments" / "mvp" / "distractors"


def _load_library(skills_root: Path, distractors_root: Path) -> list[SkillArtifact]:
    skills = FileSkillLibrary(skills_root).list() + FileSkillLibrary(distractors_root).list()
    ids = [skill.id for skill in skills]
    if len(ids) != len(set(ids)):
        raise ValueError("provisioning evaluation library contains duplicate skill IDs")
    return sorted(skills, key=lambda skill: skill.id)


def _policy_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    oracle_rows = [row for row in rows if row["oracle_skill_ids"]]
    control_rows = [row for row in rows if not row["oracle_skill_ids"]]
    clean = 0
    oracle_only = 0
    mixed = 0
    distractor = 0
    for row in oracle_rows:
        exposed = set(row[field])
        oracle = set(row["oracle_skill_ids"])
        if exposed and exposed <= oracle:
            oracle_only += 1
        if exposed == oracle:
            clean += 1
        if exposed & oracle and exposed - oracle:
            mixed += 1
        if exposed - oracle:
            distractor += 1
    control_abstain = sum(not row[field] for row in control_rows)
    return {
        "task_count": len(rows),
        "oracle_bearing_task_count": len(oracle_rows),
        "oracle_only_exposure_count": oracle_only,
        "clean_oracle_only_count": clean,
        "clean_oracle_only_rate": clean / len(oracle_rows) if oracle_rows else None,
        "control_task_count": len(control_rows),
        "control_abstain_count": control_abstain,
        "mixed_exposure_count": mixed,
        "distractor_exposure_count": distractor,
    }


def evaluate_fixed_sample(
    *,
    tasks: list[TaskSpec],
    skills: list[SkillArtifact],
    exposure_budget: int = 3,
) -> dict[str, Any]:
    snapshot_id, snapshot_sha256, active_ids = active_library_snapshot(skills)
    naive = LexicalProvisioner(exposure_budget=exposure_budget)
    governed = GovernedProvisioner(exposure_budget=exposure_budget)
    rows: list[dict[str, Any]] = []
    for task in sorted(tasks, key=lambda item: item.id):
        naive_ids = tuple(skill.id for skill in naive.provision(task.instruction, skills))
        decision = governed.decide(task.instruction, skills)
        if decision.active_library_snapshot_sha256 != snapshot_sha256:
            raise RuntimeError("governed policy evaluated a different library snapshot")
        rows.append(
            {
                "task_id": task.id,
                "oracle_skill_ids": sorted(task.oracle_skill_ids),
                "naive_provisioned_ids": list(naive_ids),
                "governed_provisioned_ids": list(decision.provisioned_ids),
                "governed_primary_id": decision.primary_id,
                "governed_abstain_reason": decision.abstain_reason,
                "governed_explicit_artifact_anchors": list(decision.explicit_artifact_anchors),
                "governed_explicit_input_anchors": list(decision.explicit_input_anchors),
            }
        )
    naive_metrics = _policy_metrics(rows, "naive_provisioned_ids")
    governed_metrics = _policy_metrics(rows, "governed_provisioned_ids")
    acceptance = {
        "nine_oracle_tasks_clean_oracle_only": governed_metrics["clean_oracle_only_count"] == 9,
        "answer_yes_abstains": any(
            row["task_id"] == "answer-yes" and not row["governed_provisioned_ids"]
            for row in rows
        ),
        "mixed_exposure_zero": governed_metrics["mixed_exposure_count"] == 0,
        "distractor_exposure_zero": governed_metrics["distractor_exposure_count"] == 0,
    }
    return {
        "schema_version": 1,
        "evaluation_id": "controlled-10-task-provisioning-v1",
        "scope": "model-free controlled fixed sample only",
        "headline_claim_allowed": False,
        "policy_version": governed.policy_version,
        "exposure_budget": exposure_budget,
        "active_library_size": len(active_ids),
        "active_library_snapshot_id": snapshot_id,
        "active_library_snapshot_sha256": snapshot_sha256,
        "active_skill_ids": list(active_ids),
        "same_library_snapshot_for_both_policies": True,
        "research_contract": {
            "skillsbench": {
                "matched_no_skill_outcome_available": False,
                "normalized_gain": None,
                "reason": "provisioning exposure has no matched task-success outcome",
            },
            "skillops": {
                "candidate_contract": "P/O/A/V/F presence",
                "health_action_scope": "read-only provisioning eligibility/exclusion",
            },
            "more_skills": {
                "loaded_evidence_available": False,
                "invoked_evidence_available": False,
                "shadowing_proxy_scope": "mixed/distractor prompt exposure only",
            },
            "aip": {
                "anchor_scope": "declared graph step inputs/outputs and expected artifacts",
            },
            "deferred_not_measured": [
                "SkillRevise revision quality",
                "Counterfactual Trace Auditing outcome bundles",
                "Self-Harness held-out promotion",
                "SkillOS learned curation",
            ],
        },
        "naive_lexical": naive_metrics,
        "governed": governed_metrics,
        "acceptance": acceptance,
        "acceptance_passed": all(acceptance.values()),
        "rows": rows,
        "limitations": [
            "The ten tasks and two distractors are controlled fixtures.",
            "This measures provisioning exposure, not provider-native skill loading or invocation.",
            "No model, task execution, or verifier performance claim is made by this evaluator.",
            "The result must not be generalized to full SkillsBench or production libraries.",
        ],
    }


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"refusing to overwrite evaluator output: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate governed provisioning without a model.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS)
    parser.add_argument("--distractors-root", type=Path, default=DEFAULT_DISTRACTORS)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args(argv)

    output = args.output.expanduser().resolve(strict=False)
    if output.exists():
        parser.error("--output must not already exist")
    try:
        output.mkdir(parents=True, exist_ok=False)
        report = evaluate_fixed_sample(
            tasks=load_tasks(args.tasks_root),
            skills=_load_library(args.skills_root, args.distractors_root),
            exposure_budget=args.top_k,
        )
        _write_new_json(output / "provisioning_evaluation.json", report)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print("Merlin governed provisioning evaluation")
    print(f"acceptance_passed={str(report['acceptance_passed']).lower()}")
    print(
        "governed_clean_oracle_only="
        f"{report['governed']['clean_oracle_only_count']}/"
        f"{report['governed']['oracle_bearing_task_count']}"
    )
    print(f"governed_control_abstain={report['governed']['control_abstain_count']}")
    print(f"saved -> {output / 'provisioning_evaluation.json'}")
    return 0 if report["acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
