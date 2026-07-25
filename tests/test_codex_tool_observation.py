from __future__ import annotations

import json
import unittest

from src.merlin_harness.codex_tool_observation import (
    CodexToolObservationError,
    command_observation_sha256,
    parse_codex_command_observations,
)


def _jsonl(*events: dict) -> bytes:
    return "".join(json.dumps(event) + "\n" for event in events).encode("utf-8")


def _command_event(
    event_type: str,
    *,
    item_id: str = "item-1",
    command: str = "rg --files",
    output: str = "",
    status: str = "in_progress",
    exit_code: int | None = None,
) -> dict:
    return {
        "type": event_type,
        "item": {
            "id": item_id,
            "type": "command_execution",
            "command": command,
            "aggregated_output": output,
            "status": status,
            "exit_code": exit_code,
        },
    }


class CodexToolObservationTests(unittest.TestCase):
    def test_pairs_commands_and_retains_only_hashes_and_counts(self) -> None:
        observations = parse_codex_command_observations(
            _jsonl(
                {"type": "thread.started", "thread_id": "thread"},
                _command_event("item.started"),
                _command_event(
                    "item.completed",
                    output="one.py\n",
                    status="completed",
                    exit_code=0,
                ),
                {"type": "turn.completed", "usage": {}},
            )
        )

        self.assertEqual(len(observations), 1)
        safe = observations[0].to_safe_dict()
        self.assertEqual(safe["ordinal"], 1)
        self.assertEqual(safe["command_chars"], len("rg --files"))
        self.assertEqual(safe["output_chars"], len("one.py\n"))
        self.assertNotIn("command", safe)
        self.assertNotIn("output", safe)
        self.assertEqual(len(command_observation_sha256(observations)), 64)

    def test_incomplete_mismatched_and_inconsistent_lifecycles_fail_closed(self) -> None:
        cases = (
            (
                _jsonl(_command_event("item.started")),
                "incomplete command lifecycle",
            ),
            (
                _jsonl(
                    _command_event("item.started"),
                    _command_event(
                        "item.completed",
                        command="different",
                        status="completed",
                        exit_code=0,
                    ),
                ),
                "differs",
            ),
            (
                _jsonl(
                    _command_event("item.started"),
                    _command_event(
                        "item.completed",
                        status="failed",
                        exit_code=0,
                    ),
                ),
                "inconsistent",
            ),
        )
        for raw, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                CodexToolObservationError, message
            ):
                parse_codex_command_observations(raw)

    def test_duplicate_json_keys_and_unknown_command_event_types_are_rejected(self) -> None:
        duplicate = (
            '{"type":"item.started","type":"item.started","item":'
            '{"id":"x","type":"command_execution","command":"pwd",'
            '"aggregated_output":"","status":"in_progress","exit_code":null}}\n'
        ).encode("utf-8")
        with self.assertRaisesRegex(CodexToolObservationError, "duplicate JSON key"):
            parse_codex_command_observations(duplicate)

        unknown = _jsonl(
            {
                "type": "item.updated",
                "item": {
                    "id": "x",
                    "type": "command_execution",
                    "command": "pwd",
                    "aggregated_output": "",
                    "status": "in_progress",
                    "exit_code": None,
                },
            }
        )
        with self.assertRaisesRegex(CodexToolObservationError, "event type"):
            parse_codex_command_observations(unknown)


if __name__ == "__main__":
    unittest.main()
