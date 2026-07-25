"""Run a deterministic skill-local repair through the real task verifier path."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from src.merlin_harness.executors import ExecutionRequest, RecipeSkillExecutor
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.models import SkillArtifact, TaskSpec
from src.merlin_harness.skill_repair import (
    RepairCase,
    RepairCaseResult,
    RepairDiagnosis,
    run_skill_repair,
    skill_library_snapshot_sha256,
)
from src.merlin_harness.task_io import load_tasks
from src.merlin_harness.tasks import materialize_task_workspace, run_verifier
from src.merlin_harness.verifier_trust import VerifierTrustLevel, VerifierTrustProfile


REPO_ROOT = Path(__file__).resolve().parents[2]
MVP_ROOT = REPO_ROOT / "experiments" / "mvp"


class TaskSuiteRepairEvaluator:
    """Adapter from repair cases to the existing deterministic task runtime."""

    def __init__(self, tasks: tuple[TaskSpec, ...]) -> None:
        self.tasks = {task.id: task for task in tasks}

    def _run(self, skill: SkillArtifact, case: RepairCase) -> RepairCaseResult:
        task = self.tasks[case.case_id]
        if task.verifier.name != case.verifier_id:
            raise ValueError(f"frozen verifier mismatch for {case.case_id}")
        with tempfile.TemporaryDirectory(prefix="merlin-repair-") as temporary:
            workspace = materialize_task_workspace(task, temporary)
            execution = RecipeSkillExecutor().execute(
                ExecutionRequest(
                    task=task,
                    workspace=workspace,
                    condition=f"repair-v{skill.version}",
                    provisioned_skills=[skill],
                    selected_skill=skill,
                )
            )
            validation = run_verifier(task, workspace, answer=execution.answer)
        return RepairCaseResult(
            case_id=case.case_id,
            verifier_id=case.verifier_id,
            passed=validation.passed,
            score=validation.score,
            evidence=validation.evidence,
        )

    def evaluate_skill(self, skill, cases):
        return tuple(self._run(skill, case) for case in cases)

    def evaluate_library(self, skills, cases):
        by_id = {skill.id: skill for skill in skills}
        results = []
        for case in cases:
            task = self.tasks[case.case_id]
            oracle_ids = [skill_id for skill_id in task.oracle_skill_ids if skill_id in by_id]
            if len(oracle_ids) != 1:
                raise ValueError(f"library regression case {case.case_id} needs one oracle skill")
            results.append(self._run(by_id[oracle_ids[0]], case))
        return tuple(results)


class NonEmptyLineRepairV1:
    """Bounded v1 repair operator for one reproduced recipe defect.

    It receives target feedback only, preserves the routing and verifier
    contracts, and changes the failing recipe in a new version.
    """

    def propose(self, original, diagnosis, target_feedback, max_candidates):
        if max_candidates < 1:
            return ()
        if {item.case_id for item in target_feedback} != {"summarize-lines"}:
            return ()
        repaired = copy.deepcopy(original)
        repaired.version = original.version + 1
        repaired.metadata["solves"]["summarize-lines"] = {
            "count_nonempty_lines": {"input": "input.txt", "output": "summary.txt"}
        }
        repaired.metadata["repair_operator"] = "nonempty-line-recipe-v1"
        repaired.metadata["repair_diagnosis_trace_ids"] = list(diagnosis.trace_ids)
        return (repaired,)


def repair_demo_contract() -> tuple[
    RepairDiagnosis,
    tuple[SkillArtifact, ...],
    tuple[RepairCase, ...],
    tuple[RepairCase, ...],
    tuple[RepairCase, ...],
    TaskSuiteRepairEvaluator,
]:
    tasks = tuple(load_tasks(MVP_ROOT / "tasks"))
    task_by_id = {task.id: task for task in tasks}
    library = tuple(FileSkillLibrary(MVP_ROOT / "skills").list())
    broken_library = copy.deepcopy(library)
    line_summary = next(skill for skill in broken_library if skill.id == "line-summary")
    line_summary.metadata["solves"]["summarize-lines"] = {
        "write_file": {"path": "summary.txt", "content": "5\n"}
    }
    line_summary.metadata["injected_defect"] = "counts blank lines"
    target = (
        RepairCase(
            "summarize-lines",
            "target",
            task_by_id["summarize-lines"].verifier.name,
        ),
    )
    held_out = (
        RepairCase(
            "count-errors",
            "held_out",
            task_by_id["count-errors"].verifier.name,
        ),
    )
    regression = (
        RepairCase(
            "count-records",
            "library_regression",
            task_by_id["count-records"].verifier.name,
        ),
    )
    diagnosis = RepairDiagnosis(
        skill_id="line-summary",
        failure_kind="skill_local",
        trace_ids=("repair-demo-summarize-lines-v1",),
        failed_target_case_ids=("summarize-lines",),
        verifier_feedback=("expected summary.txt to contain 3; observed 5",),
        library_snapshot_sha256=skill_library_snapshot_sha256(broken_library),
    )
    return (
        diagnosis,
        broken_library,
        target,
        held_out,
        regression,
        TaskSuiteRepairEvaluator(tasks),
    )


def run_skill_repair_demo(output: Path) -> dict:
    diagnosis, library, target, held_out, regression, evaluator = repair_demo_contract()
    task_by_id = evaluator.tasks
    verifier_profiles = {
        case.verifier_id: VerifierTrustProfile(
            verifier_id=case.verifier_id,
            level=VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL,
            deterministic=True,
            requirement_ids=(f"correct-output:{case.case_id}",),
            covered_requirement_ids=(f"correct-output:{case.case_id}",),
            behavioral_assertion_count=1,
            author_independent_from_candidate=True,
            hidden_from_reviser=case.split != "target",
            provenance_sha256=hashlib.sha256(
                json.dumps(
                    asdict(task_by_id[case.case_id].verifier),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )
        for case in target + held_out + regression
    }
    result = run_skill_repair(
        diagnosis=diagnosis,
        library=library,
        target_cases=target,
        held_out_cases=held_out,
        regression_cases=regression,
        evaluator=evaluator,
        reviser=NonEmptyLineRepairV1(),
        verifier_profiles=verifier_profiles,
        max_candidates=2,
    )
    report = result.to_dict()
    report["experiment"] = {
        "name": "skill-repair-v1",
        "failure_scope": "skill_local",
        "runtime": "RecipeSkillExecutor + task command/file verifiers",
        "claim": "bounded repair lifecycle closure, not open-ended model repair",
    }
    output = output.expanduser().resolve(strict=False)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite repair demo output: {output}")
    output.mkdir(parents=True)
    (output / "skill_repair.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_skill_repair_demo(args.output)
    print(json.dumps({
        "adopted": report["adopted"],
        "selected_candidate_key": report["selected_candidate_key"],
        "gates_passed": sum(item["passed"] for item in report["gates"]),
        "gates_total": len(report["gates"]),
        "output": str(args.output.resolve()),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report["adopted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
