from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.chat_session import TheKingChatSession
from src.merlin_harness.chat_lifecycle import (
    ChatLifecycleEvidenceError,
    load_chat_lifecycle_observation,
)
from src.merlin_harness.codex_chat import CodexChatTurnResult
from src.merlin_harness.harnessx_chat_shadow import (
    HarnessXChatShadow,
    HarnessXChatShadowError,
)
from src.merlin_harness.harnessx_runtime import (
    BeforeModelContentLimitProcessor,
    HarnessXRuntime,
    StepEndAuditProcessor,
    TaskEndAuditProcessor,
    make_default_harnessx_runtime,
)
from src.merlin_harness.library import FileSkillLibrary


class RecordingBackend:
    def __init__(self, trace_root: Path) -> None:
        self.trace_root = trace_root
        self.prompts: list[str] = []

    def run_turn(self, *, prompt: str, turn_number: int, thread_id: str | None):
        self.prompts.append(prompt)
        raw = self.trace_root / f"provider-turn-{turn_number:04d}.jsonl"
        raw.write_text('{"type":"provider-fixture"}\n', encoding="utf-8")
        return CodexChatTurnResult(
            turn_number=turn_number,
            resumed=thread_id is not None,
            thread_id=thread_id or "thread-fixture",
            turn_id=f"turn-{turn_number}",
            answer="fixture answer",
            raw_trace_pointer=raw.name,
            raw_trace_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
            metadata={"provider": "fixture"},
        )


class FailingBackend:
    def run_turn(self, *, prompt: str, turn_number: int, thread_id: str | None):
        del prompt, turn_number, thread_id
        raise RuntimeError("private provider failure detail")


class CommandRecordingBackend:
    def __init__(self, trace_root: Path, *, complete: bool = True) -> None:
        self.trace_root = trace_root
        self.complete = complete

    def run_turn(self, *, prompt: str, turn_number: int, thread_id: str | None):
        del prompt
        raw = self.trace_root / f"provider-turn-{turn_number:04d}.jsonl"
        events = [
            {"type": "thread.started", "thread_id": thread_id or "thread-fixture"},
            {
                "type": "item.started",
                "item": {
                    "id": "command-item-1",
                    "type": "command_execution",
                    "command": "sensitive read-only command",
                    "aggregated_output": "",
                    "status": "in_progress",
                    "exit_code": None,
                },
            },
        ]
        if self.complete:
            events.append(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "command-item-1",
                        "type": "command_execution",
                        "command": "sensitive read-only command",
                        "aggregated_output": "sensitive command output",
                        "status": "completed",
                        "exit_code": 0,
                    },
                }
            )
        events.append({"type": "turn.completed", "usage": {}})
        raw.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        return CodexChatTurnResult(
            turn_number=turn_number,
            resumed=thread_id is not None,
            thread_id=thread_id or "thread-fixture",
            turn_id=f"turn-{turn_number}",
            answer="fixture answer",
            raw_trace_pointer=raw.name,
            raw_trace_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
            metadata={"provider": "openai-codex-cli"},
        )


def _shadow(trace_root: Path) -> HarnessXChatShadow:
    runtime = HarnessXRuntime(
        [
            BeforeModelContentLimitProcessor(max_chars=8),
            StepEndAuditProcessor(),
            TaskEndAuditProcessor(),
        ]
    )
    return HarnessXChatShadow(runtime=runtime, trace_root=trace_root)


def _tool_shadow(trace_root: Path) -> HarnessXChatShadow:
    return HarnessXChatShadow(
        runtime=make_default_harnessx_runtime(
            system_prompt_suffix="\nshadow candidate",
        ),
        trace_root=trace_root,
    )


class HarnessXChatShadowTests(unittest.TestCase):
    def test_chat_turn_records_six_observed_hooks_without_applying_shadow_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            trace_root = workspace / "traces"
            trace_root.mkdir()
            library_root = workspace / "library"
            library_root.mkdir()
            backend = RecordingBackend(trace_root)
            session = TheKingChatSession(
                workspace=workspace,
                library=FileSkillLibrary(library_root),
                backend=backend,
                trace_root=trace_root,
                harnessx_shadow=_shadow(trace_root),
            )

            response = session.send("keep this complete user request")
            metadata = json.loads(
                (trace_root / "turn-0001.meta.json").read_text(encoding="utf-8")
            )
            reference = metadata["harnessx_shadow"]
            shadow_path = trace_root / reference["pointer"]
            report = json.loads(shadow_path.read_text(encoding="utf-8"))

            self.assertEqual(response.answer, "fixture answer")
            self.assertIn("keep this complete user request", backend.prompts[0])
            self.assertEqual(
                report["hook_sequence"],
                [
                    "task_start",
                    "step_start",
                    "before_model",
                    "after_model",
                    "step_end",
                    "task_end",
                ],
            )
            self.assertEqual(report["unobserved_hooks"], ["before_tool", "after_tool"])
            self.assertGreaterEqual(report["shadow_change_count"], 1)
            self.assertFalse(
                report["claim_boundary"][
                    "candidate_processor_outputs_applied_to_provider"
                ]
            )
            self.assertFalse(report["claim_boundary"]["tool_hooks_synthesized"])
            self.assertNotIn("keep this complete user request", shadow_path.read_text())
            self.assertNotIn("fixture answer", shadow_path.read_text())
            self.assertEqual(
                reference["sha256"],
                hashlib.sha256(shadow_path.read_bytes()).hexdigest(),
            )
            session.record_feedback("pass")
            observation = load_chat_lifecycle_observation(trace_root, turn_number=1)
            self.assertEqual(observation.feedback_outcome, "pass")
            self.assertEqual(
                session.status()["harnessx_shadow"],
                {
                    "enabled": True,
                    "mode": "shadow_only",
                    "evidence_turns": 1,
                    "candidate_outputs_applied": False,
                    "tool_hooks_synthesized": False,
                },
            )

    def test_codex_command_lifecycle_replays_tool_hooks_without_raw_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            trace_root = workspace / "traces"
            trace_root.mkdir()
            library_root = workspace / "library"
            library_root.mkdir()
            session = TheKingChatSession(
                workspace=workspace,
                library=FileSkillLibrary(library_root),
                backend=CommandRecordingBackend(trace_root),
                trace_root=trace_root,
                harnessx_shadow=_tool_shadow(trace_root),
            )

            session.send("request")
            session.record_feedback("pass")
            metadata = json.loads(
                (trace_root / "turn-0001.meta.json").read_text(encoding="utf-8")
            )
            shadow_path = trace_root / metadata["harnessx_shadow"]["pointer"]
            report = json.loads(shadow_path.read_text(encoding="utf-8"))
            rendered = shadow_path.read_text(encoding="utf-8")

            self.assertEqual(
                report["hook_sequence"],
                [
                    "task_start",
                    "step_start",
                    "before_model",
                    "before_tool",
                    "after_tool",
                    "after_model",
                    "step_end",
                    "task_end",
                ],
            )
            self.assertEqual(report["unobserved_hooks"], [])
            self.assertEqual(report["tool_observation"]["command_count"], 1)
            self.assertEqual(
                report["tool_observation"]["coverage"],
                "paired_command_execution_events",
            )
            self.assertTrue(
                report["claim_boundary"]["provider_tool_events_observed"]
            )
            self.assertTrue(
                report["claim_boundary"][
                    "tool_hooks_replayed_after_provider_execution"
                ]
            )
            self.assertFalse(
                report["claim_boundary"]["tool_policy_enforced_before_execution"]
            )
            self.assertNotIn("sensitive read-only command", rendered)
            self.assertNotIn("sensitive command output", rendered)
            self.assertEqual(
                load_chat_lifecycle_observation(
                    trace_root, turn_number=1
                ).feedback_outcome,
                "pass",
            )

    def test_incomplete_command_lifecycle_cannot_claim_tool_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            trace_root = workspace / "traces"
            trace_root.mkdir()
            library_root = workspace / "library"
            library_root.mkdir()
            session = TheKingChatSession(
                workspace=workspace,
                library=FileSkillLibrary(library_root),
                backend=CommandRecordingBackend(trace_root, complete=False),
                trace_root=trace_root,
                harnessx_shadow=_tool_shadow(trace_root),
            )

            with self.assertRaisesRegex(
                HarnessXChatShadowError, "incomplete command lifecycle"
            ):
                session.send("request")
            self.assertFalse((trace_root / "turn-0001.meta.json").exists())
            self.assertFalse(
                (trace_root / "harnessx-turn-0001.shadow.json").exists()
            )

    def test_lifecycle_rejects_rehashed_shadow_overclaim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            trace_root = workspace / "traces"
            trace_root.mkdir()
            library_root = workspace / "library"
            library_root.mkdir()
            session = TheKingChatSession(
                workspace=workspace,
                library=FileSkillLibrary(library_root),
                backend=RecordingBackend(trace_root),
                trace_root=trace_root,
                harnessx_shadow=_shadow(trace_root),
            )
            session.send("request")
            session.record_feedback("fail")

            turn_path = trace_root / "turn-0001.meta.json"
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            shadow_path = trace_root / turn["harnessx_shadow"]["pointer"]
            report = json.loads(shadow_path.read_text(encoding="utf-8"))
            report["claim_boundary"]["provider_native_skill_invocation_claimed"] = True
            unsigned_report = dict(report)
            del unsigned_report["report_sha256"]
            report["report_sha256"] = hashlib.sha256(
                json.dumps(
                    unsigned_report,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            shadow_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            turn["harnessx_shadow"]["report_sha256"] = report["report_sha256"]
            turn["harnessx_shadow"]["sha256"] = hashlib.sha256(
                shadow_path.read_bytes()
            ).hexdigest()
            turn_path.write_text(
                json.dumps(turn, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ChatLifecycleEvidenceError,
                "overclaims provider_native_skill_invocation_claimed",
            ):
                load_chat_lifecycle_observation(trace_root, turn_number=1)

    def test_provider_failure_records_only_observed_failure_hooks_and_class(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            trace_root = workspace / "traces"
            trace_root.mkdir()
            library_root = workspace / "library"
            library_root.mkdir()
            session = TheKingChatSession(
                workspace=workspace,
                library=FileSkillLibrary(library_root),
                backend=FailingBackend(),
                trace_root=trace_root,
                harnessx_shadow=_shadow(trace_root),
            )

            with self.assertRaisesRegex(RuntimeError, "private provider failure"):
                session.send("request")
            shadow_path = trace_root / "harnessx-turn-0001.shadow.json"
            report = json.loads(shadow_path.read_text(encoding="utf-8"))
            rendered = shadow_path.read_text(encoding="utf-8")

            self.assertEqual(report["status"], "provider_error")
            self.assertEqual(
                report["hook_sequence"],
                [
                    "task_start",
                    "step_start",
                    "before_model",
                    "step_end",
                    "task_end",
                ],
            )
            self.assertEqual(report["failure_class"], "RuntimeError")
            self.assertNotIn("private provider failure detail", rendered)
            self.assertFalse((trace_root / "turn-0001.meta.json").exists())

    def test_shadow_artifacts_are_new_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root = Path(temporary)
            raw = trace_root / "raw.jsonl"
            raw.write_text('{"type":"turn.completed","usage":{}}\n', encoding="utf-8")
            raw_sha256 = hashlib.sha256(raw.read_bytes()).hexdigest()
            shadow = _shadow(trace_root)
            first = shadow.start(turn_number=1, prompt="prompt", resumed=False)
            shadow.finish(
                first,
                answer="answer",
                provider_turn_id="turn",
                raw_trace_pointer=raw.name,
                raw_trace_sha256=raw_sha256,
            )
            second = shadow.start(turn_number=1, prompt="prompt", resumed=False)
            with self.assertRaisesRegex(HarnessXChatShadowError, "overwrite"):
                shadow.finish(
                    second,
                    answer="answer",
                    provider_turn_id="turn",
                    raw_trace_pointer=raw.name,
                    raw_trace_sha256=raw_sha256,
                )


if __name__ == "__main__":
    unittest.main()
