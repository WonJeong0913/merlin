from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.claude_chat import (
    ClaudeChatBackend,
    ClaudeChatBackendError,
)

SESSION = "3f2a51c8-9b41-4d27-8e6a-1c0b7d94ae35"


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _ok_stream(*, skills: list[str] | None = None, invoked: str | None = None) -> str:
    content: list[dict] = []
    if invoked is not None:
        content.append(
            {"type": "tool_use", "id": "toolu_1", "name": "Skill", "input": {"skill": invoked}}
        )
    content.append({"type": "text", "text": "done"})
    return _stream(
        {
            "type": "system",
            "subtype": "init",
            "session_id": SESSION,
            "skills": skills if skills is not None else [],
        },
        {
            "type": "assistant",
            "session_id": SESSION,
            "message": {"model": "claude-sonnet-5", "content": content},
        },
        {"type": "result", "session_id": SESSION, "result": "done", "is_error": False},
    )


class _Recorder:
    """Stands in for subprocess.run and records what the backend asked for."""

    def __init__(self, stdout: str, *, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[dict] = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": list(command), **kwargs})
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


class BackendFixture:
    def __init__(self, directory: Path) -> None:
        self.workspace = directory
        self.trace_root = directory / "trace"

    def backend(self, runner, **kwargs) -> ClaudeChatBackend:
        options = {
            "executable": "/usr/bin/env",
            "cli_version": "2.1.90 (Claude Code)",
            "workspace": self.workspace,
            "trace_root": self.trace_root,
            "model_id": "claude-sonnet-5",
            "effort": "high",
            "runner": runner,
        }
        options.update(kwargs)
        return ClaudeChatBackend(**options)


class ConstructionTests(unittest.TestCase):
    def test_a_model_alias_is_refused(self) -> None:
        # `--model sonnet` resolves to whatever the install considers current.
        # Accepting it would let a run claim one model and use another.
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            with self.assertRaises(ValueError):
                fixture.backend(_Recorder(""), model_id="sonnet")

    def test_a_trace_root_outside_the_workspace_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            with self.assertRaises(ValueError):
                fixture.backend(_Recorder(""), trace_root=Path(directory).parent / "elsewhere")

    def test_an_unsupported_effort_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            with self.assertRaises(ValueError):
                fixture.backend(_Recorder(""), effort="turbo")


class TurnTests(unittest.TestCase):
    def test_a_successful_turn_records_session_answer_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(_ok_stream(skills=["alpha"]))
            backend = fixture.backend(runner)
            result = backend.run_turn(prompt="do the thing", turn_number=1)
        self.assertEqual(result.session_id, SESSION)
        self.assertEqual(result.answer, "done")
        self.assertFalse(result.resumed)
        self.assertEqual(len(result.raw_trace_sha256), 64)
        self.assertEqual(result.exposed_skills, ("alpha",))

    def test_the_prompt_goes_on_stdin_and_never_into_metadata(self) -> None:
        secret = "PRIVATE-PROMPT-c41f"
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(_ok_stream())
            backend = fixture.backend(runner)
            result = backend.run_turn(prompt=secret, turn_number=1)
        self.assertEqual(runner.calls[0]["input"], secret)
        self.assertNotIn(secret, json.dumps(result.metadata))
        self.assertIn("<prompt-via-stdin>", result.metadata["command"])

    def test_a_non_zero_exit_refuses_to_claim_the_thread_advanced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder("", returncode=1, stderr="boom")
            backend = fixture.backend(runner)
            with self.assertRaises(ClaudeChatBackendError):
                backend.run_turn(prompt="x", turn_number=1)
            # The trace is still written; failure does not erase evidence.
            self.assertTrue((fixture.trace_root / "turn-0001.claude.jsonl").is_file())
            self.assertTrue((fixture.trace_root / "turn-0001.stderr.txt").is_file())

    def test_a_reported_error_result_fails_the_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(
                _stream({"type": "result", "session_id": SESSION, "is_error": True, "result": "x"})
            )
            backend = fixture.backend(runner)
            with self.assertRaises(ClaudeChatBackendError):
                backend.run_turn(prompt="x", turn_number=1)

    def test_a_model_mismatch_fails_the_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(_ok_stream())
            backend = fixture.backend(runner, model_id="claude-opus-5")
            with self.assertRaises(ClaudeChatBackendError):
                backend.run_turn(prompt="x", turn_number=1)

    def test_a_turn_refuses_to_overwrite_an_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(_ok_stream())
            backend = fixture.backend(runner)
            backend.run_turn(prompt="x", turn_number=1)
            with self.assertRaises(ClaudeChatBackendError):
                backend.run_turn(prompt="y", turn_number=1)

    def test_resume_passes_the_session_and_marks_the_turn_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(_ok_stream())
            backend = fixture.backend(runner)
            result = backend.run_turn(prompt="x", turn_number=2, session_id=SESSION)
        self.assertTrue(result.resumed)
        self.assertIn("--resume", runner.calls[0]["command"])
        self.assertIn(SESSION, runner.calls[0]["command"])

    def test_an_unsafe_session_id_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            backend = fixture.backend(_Recorder(_ok_stream()))
            with self.assertRaises(ClaudeChatBackendError):
                backend.run_turn(prompt="x", turn_number=2, session_id="../../etc/passwd")


class ProviderSkillEvidenceTests(unittest.TestCase):
    def test_exposure_alone_is_not_reported_as_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(_ok_stream(skills=["alpha"]))
            result = fixture.backend(runner).run_turn(prompt="x", turn_number=1)
        self.assertEqual(result.exposed_skills, ("alpha",))
        self.assertEqual(result.invoked_skills, ())
        self.assertFalse(result.metadata["provider_native_skill_invocation_observed"])

    def test_a_skill_tool_call_is_reported_as_provider_observed_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(_ok_stream(skills=["alpha"], invoked="alpha"))
            result = fixture.backend(runner).run_turn(prompt="x", turn_number=1)
        self.assertEqual(result.invoked_skills, ("alpha",))
        self.assertTrue(result.metadata["provider_native_skill_invocation_observed"])

    def test_the_name_level_limit_is_carried_in_metadata(self) -> None:
        # A `Skill` call names a skill. It does not pin a SKILL.md body hash,
        # and the metadata has to say so where a reader will see it.
        with tempfile.TemporaryDirectory() as directory:
            fixture = BackendFixture(Path(directory))
            runner = _Recorder(_ok_stream(skills=["alpha"], invoked="alpha"))
            result = fixture.backend(runner).run_turn(prompt="x", turn_number=1)
        self.assertTrue(
            result.metadata["provider_native_invocation_is_name_level_not_body_level"]
        )
        self.assertFalse(
            result.metadata["prompt_provisioning_is_provider_native_invocation"]
        )


if __name__ == "__main__":
    unittest.main()
