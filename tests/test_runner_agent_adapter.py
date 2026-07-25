from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.merlin_harness.agent_adapter import AgentContractError, AgentRunRequest
from src.merlin_harness.metrics import oracle_invocation_event_summary, trace_to_invocation_observation
from src.merlin_harness.models import (
    AgentRunContract,
    AgentRunResult,
    RawTraceReference,
    SkillInvocationEvent,
    TaskSpec,
    VerifierSpec,
)
from src.merlin_harness.provisioning import make_single_step_skill
from src.merlin_harness.runner import run_agent_adapter_once
from src.merlin_harness.tasks import run_verifier
from src.merlin_harness.traces import AGENT_TRACE_EVIDENCE_KEY, FileTraceStore


class FakeAdapter:
    name = "fake-adapter"

    def __init__(self, *, selected: list[str], invoked: list[str], complete: bool = True) -> None:
        self.selected = selected
        self.invoked = invoked
        self.complete = complete
        self.requests: list[AgentRunRequest] = []

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        raw_root = Path(request.contract.raw_trace_root)
        raw_root.mkdir(parents=True, exist_ok=True)
        raw_path = raw_root / f"{request.contract.run_id}.jsonl"
        raw_content = "sensitive raw provider transcript that must not be copied\n"
        raw_path.write_text(raw_content, encoding="utf-8")
        events = [
            SkillInvocationEvent(
                skill_id=skill_id,
                event_kind="skill_body_loaded",
                source="fake-provider",
                event_id=f"event-{index}",
                sequence=index,
            )
            for index, skill_id in enumerate(self.invoked)
        ]
        return AgentRunResult(
            contract=request.contract,
            workspace_root=str(request.workspace.resolve()),
            raw_trace=RawTraceReference(
                pointer=raw_path.relative_to(raw_root).as_posix(),
                sha256=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            ),
            actual_invocation_evidence_complete=self.complete,
            selected_skill_ids=list(self.selected),
            invocation_events=events,
            answer="ok",
            events=[{"type": "AGENT_ACTION", "action": "return_answer"}],
        )


class MutatingRequestAdapter(FakeAdapter):
    """Adversarial adapter that tries to rewrite frozen task/library inputs."""

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        request.task.verifier.expected = "tampered"
        request.task.oracle_skill_ids[:] = ["distractor"]
        request.provisioned_skills[0].id = "tampered-oracle"
        return super().run(request)


def _task() -> TaskSpec:
    return TaskSpec(
        id="adapter-run-task",
        instruction="Return ok",
        verifier=VerifierSpec(name="exact", kind="exact_match", expected="ok"),
        oracle_skill_ids=["oracle"],
    )


def _contract(
    root: Path,
    task: TaskSpec,
    *,
    run_id: str = "adapter-run",
    workspace_name: str = "workspace",
) -> AgentRunContract:
    return AgentRunContract(
        run_id=run_id,
        task_id=task.id,
        condition="agent-adapter",
        workspace_root=str((root / workspace_name).resolve()),
        raw_trace_root=str((root / "raw").resolve()),
        agent_id="fake-agent",
        agent_version="1.0",
        backend="fake",
        model_id="fake-model",
        effort="none",
        budget_id="budget-v1",
        library_snapshot_id="library-v1",
        library_snapshot_sha256=hashlib.sha256(b"library-v1").hexdigest(),
        verifier_id=task.verifier.name,
    )


def _skills():
    return [
        make_single_step_skill(
            skill_id="oracle",
            name="Oracle",
            description="Return ok.",
            trigger="Use for return ok.",
            step_description="Return ok.",
        ),
        make_single_step_skill(
            skill_id="distractor",
            name="Distractor",
            description="Looks relevant.",
            trigger="Use for return ok.",
            step_description="Do something else.",
        ),
    ]


class RunnerAgentAdapterTests(unittest.TestCase):
    def test_workspace_mismatch_is_rejected_before_setup_or_adapter_execution(self) -> None:
        task = _task()
        adapter = FakeAdapter(selected=[], invoked=[])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            escaped_workspace = root / "escaped-workspace"
            with self.assertRaisesRegex(AgentContractError, "workspace"):
                run_agent_adapter_once(
                    task=task,
                    workspace=escaped_workspace,
                    condition="agent-adapter",
                    contract=_contract(root, task),
                    adapter=adapter,
                    provisioned_skills=_skills(),
                )

            self.assertEqual(adapter.requests, [])
            self.assertFalse(escaped_workspace.exists())

    def test_adapter_cannot_mutate_task_verifier_or_provisioned_skill_snapshot(self) -> None:
        task = _task()
        skills = _skills()
        adapter = MutatingRequestAdapter(selected=[], invoked=[])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(AgentContractError, "mutated the requested task or verifier"):
                run_agent_adapter_once(
                    task=task,
                    workspace=root / "workspace",
                    condition="agent-adapter",
                    contract=_contract(root, task),
                    adapter=adapter,
                    provisioned_skills=skills,
                )

        self.assertEqual(task.verifier.expected, "ok")
        self.assertEqual(task.oracle_skill_ids, ["oracle"])
        self.assertEqual([skill.id for skill in skills], ["oracle", "distractor"])

    def test_selected_but_not_loaded_stays_empty_and_verifier_runs_once(self) -> None:
        task = _task()
        adapter = FakeAdapter(selected=["oracle"], invoked=[])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = FileTraceStore(root / "traces")
            with patch("src.merlin_harness.runner.run_verifier", wraps=run_verifier) as verifier:
                trace = run_agent_adapter_once(
                    task=task,
                    workspace=root / "workspace",
                    condition="agent-adapter",
                    contract=_contract(root, task),
                    adapter=adapter,
                    provisioned_skills=_skills(),
                    trace_id="selected-not-loaded",
                    trace_store=store,
                )
            loaded = store.load("selected-not-loaded")
            observation = trace_to_invocation_observation(loaded)
            evidence = loaded.metadata[AGENT_TRACE_EVIDENCE_KEY]

        self.assertEqual(verifier.call_count, 1)
        self.assertTrue(trace.invocation.success if trace.invocation else False)
        self.assertEqual(trace.invocation.selected_skill_ids if trace.invocation else [], ["oracle"])
        self.assertEqual(tuple(observation.invoked_skill_ids), ())
        self.assertTrue(evidence["actual_invocation_evidence_complete"])
        self.assertEqual(evidence["invocation_events"], [])
        self.assertNotIn("sensitive raw provider transcript", json.dumps(evidence))

    def test_oracle_and_distractor_actual_loads_produce_o_and_m_events(self) -> None:
        task = _task()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oracle_trace = run_agent_adapter_once(
                task=task,
                workspace=root / "workspace-oracle",
                condition="agent-adapter",
                contract=_contract(root, task, run_id="oracle-run", workspace_name="workspace-oracle"),
                adapter=FakeAdapter(selected=["oracle"], invoked=["oracle"]),
                provisioned_skills=_skills(),
            )
            distractor_trace = run_agent_adapter_once(
                task=task,
                workspace=root / "workspace-distractor",
                condition="agent-adapter",
                contract=_contract(root, task, run_id="distractor-run", workspace_name="workspace-distractor"),
                adapter=FakeAdapter(selected=["distractor"], invoked=["distractor"]),
                provisioned_skills=_skills(),
            )
            summary = oracle_invocation_event_summary(
                [
                    trace_to_invocation_observation(oracle_trace),
                    trace_to_invocation_observation(distractor_trace),
                ]
            )

        self.assertEqual(summary.counts, {"n": 0, "m": 1, "o": 1})

    def test_incomplete_actual_evidence_is_not_coerced_to_empty(self) -> None:
        task = _task()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = run_agent_adapter_once(
                task=task,
                workspace=root / "workspace",
                condition="agent-adapter",
                contract=_contract(root, task),
                adapter=FakeAdapter(selected=["oracle"], invoked=[], complete=False),
                provisioned_skills=_skills(),
            )

            with self.assertRaisesRegex(ValueError, "evidence is incomplete"):
                trace_to_invocation_observation(trace)

    def test_immutable_store_rejects_trace_rewrite(self) -> None:
        task = _task()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = FileTraceStore(root / "traces")
            trace = run_agent_adapter_once(
                task=task,
                workspace=root / "workspace",
                condition="agent-adapter",
                contract=_contract(root, task),
                adapter=FakeAdapter(selected=["oracle"], invoked=[]),
                provisioned_skills=_skills(),
                trace_id="immutable",
                trace_store=store,
            )
            trace.metadata["agent_adapter"] = "tampered"
            with self.assertRaisesRegex(ValueError, "immutable trace already exists"):
                store.save_immutable(trace)

    def test_trace_store_rejects_escape_ids_and_symlink_collisions(self) -> None:
        task = _task()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = FileTraceStore(root / "traces")
            for trace_id in ("../escape", "nested/trace", "/absolute"):
                with self.subTest(trace_id=trace_id):
                    with self.assertRaisesRegex(AgentContractError, "trace id"):
                        store.trace_path(trace_id)

            trace = run_agent_adapter_once(
                task=task,
                workspace=root / "workspace",
                condition="agent-adapter",
                contract=_contract(root, task),
                adapter=FakeAdapter(selected=[], invoked=[]),
                provisioned_skills=_skills(),
                trace_id="symlink-collision",
            )
            destination = store.trace_path(trace.id)
            outside = root / "outside-trace.json"
            outside.write_text("do not overwrite", encoding="utf-8")
            destination.symlink_to(outside)

            with self.assertRaisesRegex(AgentContractError, "cannot be a symlink"):
                store.save_immutable(trace)
            self.assertEqual(outside.read_text(encoding="utf-8"), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
