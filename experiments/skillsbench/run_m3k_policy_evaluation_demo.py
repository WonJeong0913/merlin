"""Run a real deterministic M3-K harness variant evaluation and export evidence.

The six-task fixture uses the actual HarnessRuntime, processor reconstruction,
RecipeSkillExecutor, task workspaces, and deterministic verifiers.  It is a
model-free implementation proof, not a GPT-5.6 or full-87 result.  The same
report also binds the canonical 87-task/3-repeat M3-K schedule as ``not_run``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from experiments.skillsbench.harness_policy_evaluation import (
    M3KCell,
    M3KEvaluationContract,
    M3KSplit,
    M3KTaskContract,
    M3KTrajectoryResult,
    M3KVariantLineage,
    VariantRole,
    build_cells,
    build_full87_m3k_contract,
    run_m3k_policy_evaluation,
)
from src.merlin_harness.harness import (
    DoNotUseConstraintProcessor,
    HarnessEvolutionProposal,
    HarnessRuntime,
    Hook,
    ShadowingMonitorProcessor,
    SkillStateProcessor,
    build_runtime_from_variant,
    snapshot_harness_variant,
)
from src.merlin_harness.management import content_sha256
from src.merlin_harness.models import (
    LifecycleStatus,
    SkillArtifact,
    SkillStep,
    TaskSpec,
    VerifierSpec,
)
from src.merlin_harness.runner import run_seeded_condition


REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _task(task_id: str, *, split: M3KSplit) -> TaskSpec:
    if split is M3KSplit.REGRESSION:
        return TaskSpec(
            id=task_id,
            instruction="Return the stable regression token.",
            verifier=VerifierSpec(
                name=f"exact-{task_id}", kind="exact_match", expected="STABLE"
            ),
            oracle_skill_ids=["stable"],
        )
    return TaskSpec(
        id=task_id,
        instruction="Count nonempty lines from input text and return OK.",
        verifier=VerifierSpec(
            name=f"exact-{task_id}", kind="exact_match", expected="OK"
        ),
        oracle_skill_ids=["z-oracle"],
    )


def controlled_fixture() -> tuple[
    tuple[TaskSpec, ...],
    tuple[SkillArtifact, ...],
    M3KEvaluationContract,
    object,
    HarnessEvolutionProposal,
]:
    task_splits = (
        ("held-in-a", M3KSplit.HELD_IN),
        ("held-in-b", M3KSplit.HELD_IN),
        ("held-out-a", M3KSplit.HELD_OUT),
        ("held-out-b", M3KSplit.HELD_OUT),
        ("regression-a", M3KSplit.REGRESSION),
        ("regression-b", M3KSplit.REGRESSION),
    )
    tasks = tuple(_task(task_id, split=split) for task_id, split in task_splits)
    held_ids = [task.id for task in tasks if task.id.startswith(("held-in", "held-out"))]
    regression_ids = [task.id for task in tasks if task.id.startswith("regression")]
    distractor = SkillArtifact(
        id="a-distractor",
        name="Count Nonempty Lines",
        description="Count nonempty lines from input text and return result.",
        trigger="Count nonempty lines from input text.",
        do_not_use_when=["Count all lines including blanks."],
        steps=[SkillStep("answer", "Return a line-count answer.")],
        status=LifecycleStatus.ACTIVE,
        metadata={"solves": {task_id: {"answer": "WRONG"} for task_id in held_ids}},
    )
    oracle = SkillArtifact(
        id="z-oracle",
        name="Count Nonempty Lines",
        description="Count nonempty lines from input text and return result.",
        trigger="Count nonempty lines from input text.",
        steps=[SkillStep("answer", "Return the verified nonempty-line answer.")],
        status=LifecycleStatus.ACTIVE,
        metadata={"solves": {task_id: {"answer": "OK"} for task_id in held_ids}},
    )
    stable = SkillArtifact(
        id="stable",
        name="Stable Regression Token",
        description="Return the stable regression token.",
        trigger="Return stable regression token.",
        steps=[SkillStep("answer", "Return the stable token.")],
        status=LifecycleStatus.ACTIVE,
        metadata={
            "solves": {task_id: {"answer": "STABLE"} for task_id in regression_ids}
        },
    )
    skills = (distractor, oracle, stable)

    split_payload = {
        split.value: [task_id for task_id, item_split in task_splits if item_split is split]
        for split in M3KSplit
    }
    task_contract_payload = [
        {
            "task_id": task.id,
            "instruction_sha256": _sha_text(task.instruction),
            "verifier": asdict(task.verifier),
        }
        for task in tasks
    ]
    contract = M3KEvaluationContract(
        experiment_id="controlled-m3k-policy-evaluation-v1",
        split_manifest_sha256=content_sha256(split_payload),
        task_contract_source_sha256=content_sha256(task_contract_payload),
        tasks=tuple(
            M3KTaskContract(
                task_id=task.id,
                split=split,
                verifier_id=task.verifier.name,
                task_instruction_sha256=_sha_text(task.instruction),
            )
            for task, (_, split) in zip(tasks, task_splits, strict=True)
        ),
        repeats=2,
        base_agent_id="merlin-deterministic-agent",
        base_agent_version="1",
        backend="recipe-skill-executor",
        model_id="no-model-controlled-fixture",
        effort="none",
        tools=("recipe-skill-runtime", "deterministic-verifier"),
        budget_id="controlled-m3k-budget-v1",
    )

    parent_runtime = HarnessRuntime(
        [
            SkillStateProcessor(),
            DoNotUseConstraintProcessor(min_token_overlap=0.9),
            ShadowingMonitorProcessor(),
        ]
    )
    candidate_runtime = HarnessRuntime(
        [
            SkillStateProcessor(),
            DoNotUseConstraintProcessor(min_token_overlap=0.5),
            ShadowingMonitorProcessor(),
        ]
    )
    parent = snapshot_harness_variant(
        parent_runtime,
        variant_id="controlled-h0",
        summary="strict negative-constraint overlap threshold",
        metadata={"fixture": True},
    )
    candidate = snapshot_harness_variant(
        candidate_runtime,
        variant_id="controlled-h1",
        parent_id=parent.id,
        summary="bounded lower negative-constraint overlap threshold",
        metadata={"fixture": True},
    )
    proposal = HarnessEvolutionProposal(
        id="controlled-m3k-proposal-v1",
        parent_variant_id=parent.id,
        candidate=candidate,
        rationale=(
            "Repeated wrong-route traces show a declared negative constraint is "
            "too strict to block the incomplete distractor."
        ),
        changed_hooks=[Hook.BEFORE_SELECT.value],
        evidence_trace_ids=["controlled-route-risk-1", "controlled-route-risk-2"],
    )
    return tasks, skills, contract, parent, proposal


class ControlledHarnessVariantExecutor:
    def __init__(
        self,
        *,
        output: Path,
        tasks: tuple[TaskSpec, ...],
        skills: tuple[SkillArtifact, ...],
    ) -> None:
        self.output = output
        self.tasks = {task.id: task for task in tasks}
        self.skills = list(skills)

    def run(self, variant, cells: tuple[M3KCell, ...], lineage: M3KVariantLineage):
        rows: list[M3KTrajectoryResult] = []
        for cell in cells:
            runtime = build_runtime_from_variant(variant)
            safe_cell_id = cell.cell_id.replace(":", "__")
            cell_root = self.output / safe_cell_id
            records = run_seeded_condition(
                tasks=[self.tasks[cell.task_id]],
                skills=self.skills,
                workspaces_root=cell_root / "workspace",
                traces_root=cell_root / "traces",
                condition=f"m3k-{lineage.variant_role.value}-{safe_cell_id}",
                exposure_budget=3,
                harness=runtime,
            )
            if len(records) != 1:
                raise RuntimeError(f"controlled executor expected one trace for {cell.cell_id}")
            record = records[0]
            trace_files = list((cell_root / "traces").glob("*.json"))
            if len(trace_files) != 1:
                raise RuntimeError(f"controlled executor expected one stored trace for {cell.cell_id}")
            raw_bytes = trace_files[0].read_bytes()
            invoked = tuple(
                dict.fromkeys(
                    event["skill"]
                    for event in record.events
                    if event.get("type") in {"TOOL", "WRITE"}
                    and isinstance(event.get("skill"), str)
                )
            )
            if record.invocation is None or not record.validation:
                raise RuntimeError(f"controlled executor produced incomplete trace for {cell.cell_id}")
            rows.append(
                M3KTrajectoryResult(
                    cell_id=cell.cell_id,
                    task_id=cell.task_id,
                    split=cell.split,
                    trial_index=cell.trial_index,
                    verifier_id=cell.verifier_id,
                    task_instruction_sha256=cell.task_instruction_sha256,
                    variant_role=lineage.variant_role,
                    variant_id=lineage.variant_id,
                    variant_sha256=lineage.variant_sha256,
                    evaluation_contract_sha256=lineage.evaluation_contract_sha256,
                    trace_id=record.id,
                    raw_trace_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                    verifier_passed=bool(record.validation[0].passed),
                    verifier_score=float(record.validation[0].score or 0.0),
                    cost=record.invocation.cost,
                    actual_invocation_evidence_complete=True,
                    invoked_skill_ids=invoked,
                    oracle_skill_ids=tuple(record.invocation.oracle_skill_ids),
                )
            )
        return tuple(rows)


class ControlledExecutorFactory:
    def __init__(self, output: Path, tasks, skills) -> None:
        self.output = output
        self.tasks = tasks
        self.skills = skills

    def __call__(self, role: VariantRole):
        return ControlledHarnessVariantExecutor(
            output=self.output / role.value,
            tasks=self.tasks,
            skills=self.skills,
        )


def _full87_readiness() -> dict[str, object]:
    contract = build_full87_m3k_contract(
        split_manifest=REPO_ROOT / "experiments/skillsbench/split-manifest.json",
        library_scale_manifest=REPO_ROOT
        / "experiments/skillsbench/library-scale-manifest.json",
        experiment_id="m3k-full87-contract-v1",
        base_agent_id="merlin-agent",
        base_agent_version="1",
        backend="strict-executor-required",
        model_id="gpt-5.6-terra",
        effort="high",
        tools=("fixed-container-exec",),
        budget_id="m3k-full87-budget-v1",
        repeats=3,
    )
    cells = build_cells(contract)
    counts = {
        split.value: sum(task.split is split for task in contract.tasks)
        for split in M3KSplit
    }
    return {
        "execution_status": "not_run",
        "claim": "schedule/readiness only; no model or trajectory result",
        "contract_sha256": contract.contract_sha256,
        "task_count": len(contract.tasks),
        "split_task_counts": counts,
        "repeats": contract.repeats,
        "cells_per_variant": len(cells),
        "paired_variant_cells": len(cells) * 2,
        "required_executor": "strict tool-controlled container executor",
    }


def run_demo(output: Path) -> dict[str, object]:
    output = output.expanduser().resolve(strict=False)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite M3-K evidence root: {output}")
    output.mkdir(parents=True)
    tasks, skills, contract, parent, proposal = controlled_fixture()
    result = run_m3k_policy_evaluation(
        contract=contract,
        parent=parent,
        proposal=proposal,
        executor_factory=ControlledExecutorFactory(output / "runs", tasks, skills),
    )
    report = result.to_dict()
    report["experiment"] = {
        "name": "controlled-m3k-internal-policy-evaluation-v1",
        "runtime": "HarnessRuntime + RecipeSkillExecutor + deterministic verifier",
        "scope": "model-free bounded implementation proof",
        "claims_not_made": [
            "GPT-5.6 execution",
            "provider-native invocation",
            "full-87 result",
            "production performance",
        ],
    }
    report["full87_contract_readiness"] = _full87_readiness()
    evidence_path = output / "m3k_policy_evaluation.json"
    evidence_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_demo(args.output)
    deltas = {item["split"]: item for item in report["deltas"]}
    readiness = report["full87_contract_readiness"]
    print("Merlin controlled M3-K internal policy evaluation")
    print(f"accepted={str(report['accepted']).lower()}")
    print(f"checks={sum(item['passed'] for item in report['checks'])}/{len(report['checks'])}")
    print(
        "pass_deltas="
        f"held_in:{deltas['held_in']['pass_rate_delta']:+.3f},"
        f"held_out:{deltas['held_out']['pass_rate_delta']:+.3f},"
        f"regression:{deltas['regression']['pass_rate_delta']:+.3f}"
    )
    print(
        f"full87_schedule={readiness['task_count']} tasks × {readiness['repeats']} repeats "
        f"× 2 variants = {readiness['paired_variant_cells']} cells ({readiness['execution_status']})"
    )
    print(f"saved -> {(args.output.resolve() / 'm3k_policy_evaluation.json')}")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
