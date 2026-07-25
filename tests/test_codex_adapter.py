from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.merlin_harness.agent_adapter import AgentContractError, AgentRunRequest, validate_agent_run_result
from src.merlin_harness.codex_adapter import CodexCliAdapter, CodexCliAdapterError, parse_codex_exec_jsonl
from src.merlin_harness.models import AgentRunContract, TaskSpec, VerifierSpec


def _contract(root: Path, task: TaskSpec, *, model_id: str = "gpt-5.6-terra") -> AgentRunContract:
    return AgentRunContract(
        run_id="codex-adapter-test-run",
        task_id=task.id,
        condition="codex-cli-smoke",
        workspace_root=str((root / "workspace").resolve()),
        raw_trace_root=str((root / "raw").resolve()),
        agent_id="codex-cli",
        agent_version="codex-cli 0.145.0-alpha.18",
        backend="openai-codex-cli",
        model_id=model_id,
        effort="low",
        budget_id="one-smoke",
        library_snapshot_id="no-skills",
        library_snapshot_sha256=hashlib.sha256(b"no-skills").hexdigest(),
        verifier_id=task.verifier.name,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        id="codex-adapter-task",
        instruction="Reply with exactly SMOKE_OK and nothing else.",
        verifier=VerifierSpec(name="exact", kind="exact_match", expected="SMOKE_OK"),
    )


def _valid_jsonl(*, model_id: str = "gpt-5.6-terra") -> str:
    return "\n".join(
        [
            json_line({"type": "thread.started", "thread_id": "thread-123", "model": model_id}),
            json_line({"type": "turn.started", "turn_id": "turn-456"}),
            json_line(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "SMOKE_OK"},
                }
            ),
            json_line({"type": "turn.completed"}),
        ]
    ) + "\n"


def json_line(payload: dict) -> str:
    import json

    return json.dumps(payload, separators=(",", ":"))


class CodexJsonlParserTests(unittest.TestCase):
    def test_valid_provider_events_preserve_ids_and_answer_without_skill_inference(self) -> None:
        summary = parse_codex_exec_jsonl(_valid_jsonl())

        self.assertEqual(summary.thread_id, "thread-123")
        self.assertEqual(summary.turn_id, "turn-456")
        self.assertEqual(summary.final_message, "SMOKE_OK")
        self.assertEqual(summary.reported_model_ids, ("gpt-5.6-terra",))
        self.assertNotIn("tool", summary.event_types)

    def test_malformed_or_truncated_jsonl_is_rejected(self) -> None:
        with self.assertRaisesRegex(CodexCliAdapterError, "malformed Codex JSONL"):
            parse_codex_exec_jsonl('{"type":"thread.started"\n')

    def test_missing_thread_identifier_is_rejected(self) -> None:
        with self.assertRaisesRegex(CodexCliAdapterError, "no thread_id"):
            parse_codex_exec_jsonl(json_line({"type": "thread.started"}) + "\n")


class CodexCliAdapterTests(unittest.TestCase):
    def _request(self, root: Path, *, model_id: str = "gpt-5.6-terra") -> AgentRunRequest:
        task = _task()
        workspace = root / "workspace"
        workspace.mkdir()
        return AgentRunRequest(contract=_contract(root, task, model_id=model_id), task=task, workspace=workspace)

    def _adapter(self) -> CodexCliAdapter:
        return CodexCliAdapter(
            executable="/Applications/ChatGPT.app/Contents/Resources/codex",
            cli_version="codex-cli 0.145.0-alpha.18",
            timeout_s=10,
        )

    @patch("src.merlin_harness.codex_adapter.subprocess.run")
    def test_no_skill_event_remains_explicitly_incomplete_and_raw_is_hashed(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_valid_jsonl(), stderr="benign warning"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            result = self._adapter().run(request)
            validate_agent_run_result(request, result)

            self.assertFalse(result.actual_invocation_evidence_complete)
            self.assertEqual(result.invocation_events, [])
            self.assertEqual(result.answer, "SMOKE_OK")
            self.assertEqual(result.metadata["thread_id"], "thread-123")
            self.assertNotIn(request.task.instruction, result.metadata["command"])
            raw_path = root / "raw" / result.raw_trace.pointer
            self.assertEqual(result.raw_trace.sha256, hashlib.sha256(raw_path.read_bytes()).hexdigest())

    @patch("src.merlin_harness.codex_adapter.subprocess.run")
    def test_provider_model_contract_mismatch_is_rejected(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_valid_jsonl(model_id="other-model"), stderr=""
        )
        with tempfile.TemporaryDirectory() as temporary:
            request = self._request(Path(temporary))
            with self.assertRaisesRegex(CodexCliAdapterError, "provider-reported model"):
                self._adapter().run(request)

    @patch("src.merlin_harness.codex_adapter.subprocess.run")
    def test_hash_tamper_is_rejected_after_adapter_returns(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_valid_jsonl(), stderr=""
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            result = self._adapter().run(request)
            (root / "raw" / result.raw_trace.pointer).write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(AgentContractError, "sha256 mismatch"):
                validate_agent_run_result(request, result)

    @patch("src.merlin_harness.codex_adapter.subprocess.run")
    def test_nonzero_subprocess_saves_raw_but_cannot_reach_verifier(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=_valid_jsonl(), stderr="configuration error"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            with self.assertRaisesRegex(CodexCliAdapterError, "no verifier will run"):
                self._adapter().run(request)
            self.assertTrue((root / "raw" / "codex-adapter-test-run.codex.jsonl").is_file())
