"""Minimal deterministic runners for MVP task corpora."""

from __future__ import annotations

import argparse
import uuid
from copy import deepcopy
from pathlib import Path

from .agent_adapter import (
    AgentContractError,
    AgentRunRequest,
    BaseAgentAdapter,
    validate_agent_run_request,
    validate_agent_run_result,
)
from .executors import ExecutionRequest, NoSkillExecutor, RecipeSkillExecutor, TaskExecutor
from .harness import HarnessEvent, HarnessRuntime, Hook, make_default_harness_runtime
from .models import AgentRunContract, InvocationRecord, SkillArtifact, TaskSpec, TraceRecord
from .provisioning import LexicalProvisioner, select_best_skill
from .task_io import load_tasks
from .tasks import materialize_task_workspace, run_verifier
from .traces import FileTraceStore, serialize_agent_run_evidence


def apply_skill_recipe(skill: SkillArtifact, task: TaskSpec, workspace: Path) -> tuple[str | None, list[dict]]:
    """Deterministically execute a seed skill's recipe for a task.

    A skill solves a task only if its metadata carries a recipe for that
    task id. Wrong-skill selections therefore produce no artifacts, which
    is exactly the failure the shadowing experiment needs to observe.
    Returns (answer, events).
    """

    result = RecipeSkillExecutor().execute(
        ExecutionRequest(
            task=task,
            workspace=workspace,
            condition="recipe",
            provisioned_skills=[skill],
            selected_skill=skill,
        )
    )
    return result.answer, result.events


def run_task_once(
    *,
    task: TaskSpec,
    workspace: str | Path,
    condition: str,
    answer: str | None = None,
    provisioned_skill_ids: list[str] | None = None,
    selected_skill_ids: list[str] | None = None,
    trace_id: str | None = None,
    executor: TaskExecutor | None = None,
) -> TraceRecord:
    """Run a deterministic task attempt and return a trace record.

    This does not call an LLM yet. It creates the contract that future executors
    must satisfy: materialize workspace, attempt task, verify, and log.
    """

    workspace_path = materialize_task_workspace(task, workspace)
    execution_events: list[dict] = []
    execution_metadata: dict = {}
    if answer is None and executor is not None:
        execution = executor.execute(
            ExecutionRequest(
                task=task,
                workspace=workspace_path,
                condition=condition,
                metadata={"provisioned_skill_ids": provisioned_skill_ids or [], "selected_skill_ids": selected_skill_ids or []},
            )
        )
        answer = execution.answer
        execution_events = execution.events
        execution_metadata = execution.metadata
    validation = run_verifier(task, workspace_path, answer=answer)
    run_id = trace_id or f"{condition}-{task.id}-{uuid.uuid4().hex[:8]}"

    events = [
        {
            "type": "THINK",
            "message": "deterministic runner attempt",
            "condition": condition,
        },
        *execution_events,
        {
            "type": "VALIDATION",
            "verifier": validation.name,
            "passed": validation.passed,
            "score": validation.score,
            "evidence": validation.evidence,
        },
    ]

    invocation = InvocationRecord(
        task_id=task.id,
        provisioned_skill_ids=provisioned_skill_ids or [],
        selected_skill_ids=selected_skill_ids or [],
        oracle_skill_ids=task.oracle_skill_ids,
        success=validation.passed,
        score=validation.score,
    )

    return TraceRecord(
        id=run_id,
        task_id=task.id,
        condition=condition,
        events=events,
        invocation=invocation,
        validation=[validation],
        failure_label=None if validation.passed else "verifier_failed",
        metadata={"workspace": str(workspace_path), "executor": executor.name if executor else None, "executor_metadata": execution_metadata},
    )


def run_agent_adapter_once(
    *,
    task: TaskSpec,
    workspace: str | Path,
    condition: str,
    contract: AgentRunContract,
    adapter: BaseAgentAdapter,
    provisioned_skills: list[SkillArtifact],
    trace_id: str | None = None,
    trace_store: FileTraceStore | None = None,
) -> TraceRecord:
    """Run one base-agent adapter attempt and attach immutable invocation evidence.

    The adapter is responsible only for task execution and normalized evidence.
    Merlin validates that evidence, then runs the supplied deterministic
    verifier exactly once.  A selected skill is stored for diagnosis but is
    never promoted into the actual-invocation evidence set.
    """

    # Keep the verifier input and the provisioned library snapshot outside the
    # adapter's mutable request object.  A base agent may inspect those objects,
    # but it must not be able to change the task/verifier or the exposed skill
    # set that Merlin later records and scores.
    frozen_task = deepcopy(task)
    frozen_provisioned_skills = deepcopy(provisioned_skills)

    if condition != contract.condition:
        raise AgentContractError(
            f"condition/run contract mismatch: condition={condition!r} contract={contract.condition!r}"
        )
    if frozen_task.verifier.name != contract.verifier_id:
        raise AgentContractError(
            f"verifier/run contract mismatch: task verifier={frozen_task.verifier.name!r} "
            f"contract verifier={contract.verifier_id!r}"
        )

    # Validate the requested root before materializing setup files.  An invalid
    # caller must not be able to cause even task setup writes outside the frozen
    # workspace boundary.
    preflight_request = AgentRunRequest(
        contract=contract,
        task=frozen_task,
        workspace=Path(workspace),
        provisioned_skills=frozen_provisioned_skills,
    )
    validate_agent_run_request(preflight_request)
    workspace_path = materialize_task_workspace(frozen_task, workspace)
    request = AgentRunRequest(
        contract=contract,
        task=deepcopy(frozen_task),
        workspace=workspace_path,
        provisioned_skills=deepcopy(frozen_provisioned_skills),
    )
    validate_agent_run_request(request)
    requested_task_snapshot = deepcopy(request.task)
    requested_skills_snapshot = deepcopy(request.provisioned_skills)
    result = adapter.run(request)
    if request.task != requested_task_snapshot:
        raise AgentContractError("adapter mutated the requested task or verifier contract")
    if request.provisioned_skills != requested_skills_snapshot:
        raise AgentContractError("adapter mutated the requested provisioned skill snapshot")
    validate_agent_run_result(request, result)

    # This is the only verifier call in this adapter-mediated path.  Validation
    # happens after adapter evidence is accepted, so an invalid adapter result
    # cannot obtain a score from a mismatched task, workspace, or raw trace.
    validation = run_verifier(frozen_task, workspace_path, answer=result.answer)
    invocation = InvocationRecord(
        task_id=frozen_task.id,
        provisioned_skill_ids=[skill.id for skill in frozen_provisioned_skills],
        selected_skill_ids=list(result.selected_skill_ids),
        oracle_skill_ids=list(frozen_task.oracle_skill_ids),
        success=validation.passed,
        score=validation.score,
    )
    run_id = trace_id or f"{condition}-{frozen_task.id}-{uuid.uuid4().hex[:8]}"
    evidence = serialize_agent_run_evidence(result)
    adapter_name = getattr(adapter, "name", type(adapter).__name__)
    trace = TraceRecord(
        id=run_id,
        task_id=frozen_task.id,
        condition=condition,
        events=[
            {
                "type": "AGENT_RUN",
                "adapter": adapter_name,
                "agent_id": contract.agent_id,
                "backend": contract.backend,
                "model_id": contract.model_id,
            },
            *result.events,
            {
                "type": "VALIDATION",
                "verifier": validation.name,
                "passed": validation.passed,
                "score": validation.score,
                "evidence": validation.evidence,
            },
        ],
        invocation=invocation,
        validation=[validation],
        failure_label=None if validation.passed else "verifier_failed",
        metadata={
            "workspace": str(workspace_path.resolve()),
            "agent_adapter": adapter_name,
            "agent_adapter_metadata": dict(result.metadata),
            "agent_run_evidence": evidence,
        },
    )
    if trace_store is not None:
        trace_store.save_immutable(trace)
    return trace


def run_no_skill_baseline(
    *,
    tasks: list[TaskSpec],
    workspaces_root: str | Path,
    traces_root: str | Path,
    executor: TaskExecutor | None = None,
) -> list[TraceRecord]:
    store = FileTraceStore(traces_root)
    records: list[TraceRecord] = []
    baseline_executor = executor or NoSkillExecutor()
    for task in tasks:
        workspace = Path(workspaces_root) / task.id
        record = run_task_once(task=task, workspace=workspace, condition="no_skill", executor=baseline_executor)
        store.save(record)
        records.append(record)
    return records


def provision_for_task(task: TaskSpec, skills: list[SkillArtifact], exposure_budget: int = 3) -> list[SkillArtifact]:
    return LexicalProvisioner(exposure_budget=exposure_budget).provision(task.instruction, skills)


def run_seeded_condition(
    *,
    tasks: list[TaskSpec],
    skills: list[SkillArtifact],
    workspaces_root: str | Path,
    traces_root: str | Path,
    condition: str = "seeded",
    exposure_budget: int = 3,
    min_select_score: float = 0.1,
    harness: HarnessRuntime | None = None,
    executor: TaskExecutor | None = None,
) -> list[TraceRecord]:
    """Provision, select, execute recipes, verify, and log one full pass.

    The run moves through HarnessX-lite hooks so processors can clamp exposure,
    block known-bad skill contracts, annotate route risk, and later feed
    lifecycle or policy decisions.
    """

    store = FileTraceStore(traces_root)
    runtime = harness or make_default_harness_runtime()
    task_executor = executor or RecipeSkillExecutor()
    records: list[TraceRecord] = []
    for task in tasks:
        workspace = materialize_task_workspace(task, Path(workspaces_root) / task.id)
        metadata = {"exposure_budget": exposure_budget, "min_select_score": min_select_score}
        processor_events: list[dict] = []

        task_start = runtime.emit(
            HarnessEvent(
                hook=Hook.TASK_START,
                task=task,
                skills=list(skills),
                metadata=metadata,
            )
        )
        processor_events.extend(task_start.audit_events)

        before_provision = runtime.emit(
            HarnessEvent(
                hook=Hook.BEFORE_PROVISION,
                task=task,
                skills=list(task_start.skills),
                metadata=task_start.metadata,
            )
        )
        processor_events.extend(before_provision.audit_events)
        metadata = before_provision.metadata
        current_exposure_budget = int(metadata.get("exposure_budget", exposure_budget))

        provisioner = LexicalProvisioner(exposure_budget=current_exposure_budget)
        provisioned = provisioner.provision(task.instruction, before_provision.skills)

        after_provision = runtime.emit(
            HarnessEvent(
                hook=Hook.AFTER_PROVISION,
                task=task,
                skills=before_provision.skills,
                provisioned_skills=provisioned,
                metadata=metadata,
            )
        )
        processor_events.extend(after_provision.audit_events)
        provisioned = after_provision.provisioned_skills

        before_select = runtime.emit(
            HarnessEvent(
                hook=Hook.BEFORE_SELECT,
                task=task,
                skills=before_provision.skills,
                provisioned_skills=provisioned,
                metadata=after_provision.metadata,
            )
        )
        processor_events.extend(before_select.audit_events)
        metadata = before_select.metadata
        provisioned = before_select.provisioned_skills
        current_min_select_score = float(metadata.get("min_select_score", min_select_score))
        selected = select_best_skill(task.instruction, provisioned, min_score=current_min_select_score)

        after_select = runtime.emit(
            HarnessEvent(
                hook=Hook.AFTER_SELECT,
                task=task,
                skills=before_provision.skills,
                provisioned_skills=provisioned,
                selected_skill=selected,
                metadata=metadata,
            )
        )
        processor_events.extend(after_select.audit_events)
        metadata = after_select.metadata
        selected = after_select.selected_skill

        execution = task_executor.execute(
            ExecutionRequest(
                task=task,
                workspace=workspace,
                condition=condition,
                provisioned_skills=provisioned,
                selected_skill=selected,
                metadata=metadata,
            )
        )
        answer = execution.answer
        skill_events = execution.events

        validation = run_verifier(task, workspace, answer=answer)
        exposure_cost = float(sum(len(skill.description) for skill in provisioned))
        invocation = InvocationRecord(
            task_id=task.id,
            provisioned_skill_ids=[skill.id for skill in provisioned],
            selected_skill_ids=[selected.id] if selected else [],
            oracle_skill_ids=task.oracle_skill_ids,
            success=validation.passed,
            score=validation.score,
            cost=exposure_cost,
        )

        after_verify = runtime.emit(
            HarnessEvent(
                hook=Hook.AFTER_VERIFY,
                task=task,
                skills=before_provision.skills,
                provisioned_skills=provisioned,
                selected_skill=selected,
                invocation=invocation,
                validation=[validation],
                metadata=metadata,
            )
        )
        processor_events.extend(after_verify.audit_events)
        metadata = after_verify.metadata

        events = [
            {"type": "THINK", "message": "seeded runner attempt", "condition": condition},
            {"type": "PROVISION", "skills": [skill.id for skill in provisioned]},
            {"type": "SELECT", "skill": selected.id if selected else None},
            *processor_events,
            *skill_events,
            {
                "type": "VALIDATION",
                "verifier": validation.name,
                "passed": validation.passed,
                "score": validation.score,
                "evidence": validation.evidence,
            },
        ]
        record = TraceRecord(
            id=f"{condition}-{task.id}-{uuid.uuid4().hex[:8]}",
            task_id=task.id,
            condition=condition,
            events=events,
            invocation=invocation,
            validation=[validation],
            failure_label=None if validation.passed else "verifier_failed",
            metadata={
                "workspace": str(workspace),
                "library_size": len(skills),
                "route_event": metadata.get("route_event"),
                "exposure_budget": current_exposure_budget,
                "min_select_score": current_min_select_score,
                "constraint_blocked_skill_ids": list(metadata.get("constraint_blocked_skill_ids", [])),
                "executor": task_executor.name,
                "executor_metadata": execution.metadata,
            },
        )
        trace_closed = runtime.emit(
            HarnessEvent(
                hook=Hook.TRACE_CLOSED,
                task=task,
                skills=before_provision.skills,
                provisioned_skills=provisioned,
                selected_skill=selected,
                invocation=invocation,
                trace=record,
                validation=[validation],
                metadata=metadata,
            )
        )
        record.events.extend(trace_closed.audit_events)
        if trace_closed.trace is not None:
            record = trace_closed.trace
        store.save(record)
        records.append(record)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Merlin deterministic no-skill baseline.")
    parser.add_argument("--tasks", required=True, help="Task JSON file or directory.")
    parser.add_argument("--workspaces", required=True, help="Workspace root for materialized task files.")
    parser.add_argument("--traces", required=True, help="Trace output directory.")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks)
    records = run_no_skill_baseline(tasks=tasks, workspaces_root=args.workspaces, traces_root=args.traces)
    passed = sum(1 for record in records if record.invocation and record.invocation.success)
    print(f"ran={len(records)} passed={passed} failed={len(records) - passed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
