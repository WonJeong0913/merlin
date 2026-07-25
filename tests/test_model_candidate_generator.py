from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.model_candidate_generator import (
    CodexModelCandidateGenerator,
    ModelCandidateGeneratorError,
)
from src.merlin_harness.model_candidate_quarantine import quarantine_model_candidate


def _candidate_response() -> str:
    return json.dumps(
        {
            "candidate_skill_id": "extract-todo-items",
            "files": [
                {
                    "path": "SKILL.md",
                    "content": (
                        "---\n"
                        "name: extract-todo-items\n"
                        "description: Use when TODO lines must be extracted from backlog.todo.\n"
                        "---\n\n"
                        "# Extract TODO Items\n\nRun the isolated script and verify todo-items.json.\n"
                    ),
                },
                {
                    "path": "agents/openai.yaml",
                    "content": (
                        "interface:\n"
                        "  display_name: Extract TODO Items\n"
                        "  short_description: Extract TODO lines into JSON.\n"
                        "  default_prompt: Use $extract-todo-items to process backlog.todo.\n"
                    ),
                },
                {
                    "path": "scripts/run.py",
                    "content": "from pathlib import Path\nprint(Path.cwd())\n",
                },
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _jsonl(response: str, *, model: str = "gpt-5.6-terra", item_type: str = "agent_message") -> str:
    events = [
        {"type": "thread.started", "thread_id": "thread-123", "model": model},
        {"type": "turn.started", "turn_id": "turn-456"},
        {
            "type": "item.completed",
            "item": {"id": "item-1", "type": item_type, "text": response},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}},
    ]
    return "\n".join(json.dumps(item, separators=(",", ":")) for item in events) + "\n"


class _FakeRunner:
    def __init__(self, stdout: str, *, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, "")


class ModelCandidateGeneratorTests(unittest.TestCase):
    def _generator(self, runner: _FakeRunner) -> CodexModelCandidateGenerator:
        return CodexModelCandidateGenerator(
            executable=Path(__file__),
            cli_version="codex-cli test",
            model_id="gpt-5.6-terra",
            effort="high",
            runner=runner,
        )

    def test_strict_provider_run_is_bound_to_quarantine_provenance(self) -> None:
        response = _candidate_response()
        runner = _FakeRunner(_jsonl(response))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "provider-run"
            result = self._generator(runner).generate(
                candidate_skill_id="extract-todo-items",
                prompt="Return the frozen candidate only.",
                run_root=run_root,
            )
            quarantine = quarantine_model_candidate(
                envelope=result.envelope,
                output_root=root / "quarantine",
            )

            self.assertEqual(result.model_evidence_level, "provider_reported")
            self.assertEqual(result.provider_reported_model_ids, ("gpt-5.6-terra",))
            self.assertEqual(result.item_types, ("agent_message",))
            self.assertTrue((run_root / "provider.codex.jsonl").is_file())
            self.assertTrue((run_root / "generation_report.json").is_file())
            manifest = json.loads(
                (root / "quarantine" / "quarantine_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["generator_provider_reported_model_ids"], ["gpt-5.6-terra"]
            )
            self.assertEqual(manifest["generator_raw_trace_sha256"], result.raw_trace_sha256)
            self.assertFalse(quarantine.execution_allowed)

        command, kwargs = runner.calls[0]
        self.assertEqual(command[-1], "-")
        self.assertIn("--output-schema", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(kwargs["input"], "Return the frozen candidate only.")

    def test_provider_tool_item_is_rejected_even_when_response_is_valid(self) -> None:
        runner = _FakeRunner(_jsonl(_candidate_response(), item_type="command_execution"))
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "provider-run"
            with self.assertRaisesRegex(ModelCandidateGeneratorError, "provider tool"):
                self._generator(runner).generate(
                    candidate_skill_id="extract-todo-items",
                    prompt="Return JSON.",
                    run_root=run_root,
                )
            self.assertTrue((run_root / "provider.codex.jsonl").is_file())
            self.assertFalse((run_root / "generation_report.json").exists())

    def test_provider_model_mismatch_and_existing_root_are_rejected(self) -> None:
        mismatch = _FakeRunner(_jsonl(_candidate_response(), model="gpt-other"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ModelCandidateGeneratorError, "does not match"):
                self._generator(mismatch).generate(
                    candidate_skill_id="extract-todo-items",
                    prompt="Return JSON.",
                    run_root=root / "provider-run",
                )
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(ModelCandidateGeneratorError, "overwrite"):
                self._generator(_FakeRunner(_jsonl(_candidate_response()))).generate(
                    candidate_skill_id="extract-todo-items",
                    prompt="Return JSON.",
                    run_root=existing,
                )


if __name__ == "__main__":
    unittest.main()
