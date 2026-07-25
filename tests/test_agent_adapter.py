from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.merlin_harness.agent_adapter import (
    AgentContractError,
    AgentRunRequest,
    validate_agent_run_request,
    validate_agent_run_result,
)
from src.merlin_harness.models import (
    AgentRunContract,
    AgentRunResult,
    RawTraceReference,
    SkillInvocationEvent,
    VerifierSpec,
    TaskSpec,
)
from src.merlin_harness.provisioning import make_single_step_skill


def _contract(root: Path, task: TaskSpec) -> AgentRunContract:
    return AgentRunContract(
        run_id="adapter-test-run",
        task_id=task.id,
        condition="adapter-test",
        workspace_root=str((root / "workspace").resolve()),
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


def _raw_reference(root: Path, *, content: str = "raw event\n") -> RawTraceReference:
    path = root / "raw" / "agent.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return RawTraceReference(
        pointer=path.relative_to(root / "raw").as_posix(),
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


class AgentAdapterContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = TaskSpec(
            id="adapter-task",
            instruction="Return ok",
            verifier=VerifierSpec(name="exact", kind="exact_match", expected="ok"),
            oracle_skill_ids=["oracle"],
        )

    def _request(self, root: Path) -> AgentRunRequest:
        oracle = make_single_step_skill(
            skill_id="oracle",
            name="Oracle",
            description="Return ok.",
            trigger="Use for this task.",
            step_description="Return ok.",
        )
        distractor = make_single_step_skill(
            skill_id="distractor",
            name="Distractor",
            description="Looks relevant.",
            trigger="Use for this task.",
            step_description="Do something else.",
        )
        workspace = root / "workspace"
        workspace.mkdir()
        return AgentRunRequest(
            contract=_contract(root, self.task),
            task=self.task,
            workspace=workspace,
            provisioned_skills=[oracle, distractor],
        )

    def test_selected_skill_is_not_treated_as_actual_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            result = AgentRunResult(
                contract=request.contract,
                workspace_root=str(request.workspace.resolve()),
                raw_trace=_raw_reference(root),
                actual_invocation_evidence_complete=True,
                selected_skill_ids=["oracle"],
                invocation_events=[],
                answer="ok",
            )

            validate_agent_run_result(request, result)

        self.assertEqual(result.selected_skill_ids, ["oracle"])
        self.assertEqual(result.invocation_events, [])
        self.assertTrue(result.actual_invocation_evidence_complete)

    def test_oracle_and_distractor_loads_are_preserved_as_distinct_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            oracle_result = AgentRunResult(
                contract=request.contract,
                workspace_root=str(request.workspace.resolve()),
                raw_trace=_raw_reference(root, content="oracle load\n"),
                actual_invocation_evidence_complete=True,
                selected_skill_ids=["oracle"],
                invocation_events=[
                    SkillInvocationEvent("oracle", "skill_body_loaded", "fake", "event-1", 0)
                ],
            )
            validate_agent_run_result(request, oracle_result)
            distractor_result = replace(
                oracle_result,
                raw_trace=_raw_reference(root, content="distractor load\n"),
                selected_skill_ids=["distractor"],
                invocation_events=[
                    SkillInvocationEvent("distractor", "provider_skill_invocation", "fake", "event-2", 0)
                ],
            )

            validate_agent_run_result(request, distractor_result)

        self.assertEqual(oracle_result.invocation_events[0].skill_id, "oracle")
        self.assertEqual(distractor_result.invocation_events[0].skill_id, "distractor")

    def test_raw_trace_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            raw = _raw_reference(root, content="original\n")
            (root / "raw" / raw.pointer).write_text("tampered\n", encoding="utf-8")
            result = AgentRunResult(
                contract=request.contract,
                workspace_root=str(request.workspace.resolve()),
                raw_trace=raw,
                actual_invocation_evidence_complete=True,
            )

            with self.assertRaisesRegex(AgentContractError, "sha256 mismatch"):
                validate_agent_run_result(request, result)

    def test_missing_raw_trace_and_non_boolean_completeness_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            missing_raw = AgentRunResult(
                contract=request.contract,
                workspace_root=str(request.workspace.resolve()),
                raw_trace=None,  # type: ignore[arg-type]
                actual_invocation_evidence_complete=True,
            )
            with self.assertRaisesRegex(AgentContractError, "RawTraceReference"):
                validate_agent_run_result(request, missing_raw)

            non_boolean_complete = AgentRunResult(
                contract=request.contract,
                workspace_root=str(request.workspace.resolve()),
                raw_trace=_raw_reference(root),
                actual_invocation_evidence_complete="true",  # type: ignore[arg-type]
            )
            with self.assertRaisesRegex(AgentContractError, "must be boolean"):
                validate_agent_run_result(request, non_boolean_complete)

    def test_raw_trace_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            raw_root = root / "raw"
            raw_root.mkdir()
            outside = root / "outside.jsonl"
            outside_content = "outside trace\n"
            outside.write_text(outside_content, encoding="utf-8")
            (raw_root / "linked.jsonl").symlink_to(outside)
            result = AgentRunResult(
                contract=request.contract,
                workspace_root=str(request.workspace.resolve()),
                raw_trace=RawTraceReference(
                    pointer="linked.jsonl",
                    sha256=hashlib.sha256(outside_content.encode("utf-8")).hexdigest(),
                ),
                actual_invocation_evidence_complete=True,
            )

            with self.assertRaisesRegex(AgentContractError, "escapes raw_trace_root"):
                validate_agent_run_result(request, result)

    def test_model_budget_library_and_verifier_contract_mismatches_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            for field_name, replacement in {
                "model_id": "other-model",
                "budget_id": "other-budget",
                "library_snapshot_id": "other-library",
                "library_snapshot_sha256": hashlib.sha256(b"other-library").hexdigest(),
                "verifier_id": "other-verifier",
            }.items():
                with self.subTest(field_name=field_name):
                    result = AgentRunResult(
                        contract=replace(request.contract, **{field_name: replacement}),
                        workspace_root=str(request.workspace.resolve()),
                        raw_trace=_raw_reference(root),
                        actual_invocation_evidence_complete=True,
                    )
                    with self.assertRaisesRegex(AgentContractError, "does not exactly match"):
                        validate_agent_run_result(request, result)

    def test_workspace_escape_and_contract_mismatch_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            escaped_request = replace(request, workspace=root / "other-workspace")
            with self.assertRaisesRegex(AgentContractError, "workspace"):
                validate_agent_run_request(escaped_request)

            result = AgentRunResult(
                contract=replace(request.contract, task_id="wrong-task"),
                workspace_root=str(request.workspace.resolve()),
                raw_trace=_raw_reference(root),
                actual_invocation_evidence_complete=True,
            )
            with self.assertRaisesRegex(AgentContractError, "contract"):
                validate_agent_run_result(request, result)

            escaped_result = replace(
                result,
                contract=request.contract,
                workspace_root=str((root / "outside").resolve()),
            )
            with self.assertRaisesRegex(AgentContractError, "workspace_root"):
                validate_agent_run_result(request, escaped_result)


if __name__ == "__main__":
    unittest.main()
