from __future__ import annotations

import json
import unittest

from src.merlin_harness.claude_adapter import (
    ClaudeCliAdapterError,
    parse_claude_stream_jsonl,
)

SESSION = "3f2a51c8-9b41-4d27-8e6a-1c0b7d94ae35"


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _assistant(*blocks: dict, model: str = "claude-sonnet-5") -> dict:
    return {
        "type": "assistant",
        "session_id": SESSION,
        "message": {"model": model, "content": list(blocks)},
    }


def _skill_block(skill: str, *, tool_use_id: str = "toolu_1", args: bool = False) -> dict:
    payload = {"skill": skill}
    if args:
        payload["args"] = "--flag"
    return {"type": "tool_use", "id": tool_use_id, "name": "Skill", "input": payload}


class ParsingTests(unittest.TestCase):
    def test_extracts_session_answer_and_model(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream(
                {"type": "system", "subtype": "init", "session_id": SESSION},
                _assistant({"type": "text", "text": "the answer"}),
                {"type": "result", "session_id": SESSION, "result": "the answer"},
            )
        )
        self.assertEqual(summary.session_id, SESSION)
        self.assertEqual(summary.final_message, "the answer")
        self.assertEqual(summary.reported_model_ids, ("claude-sonnet-5",))
        self.assertEqual(summary.event_count, 3)
        self.assertFalse(summary.is_error)

    def test_falls_back_to_the_last_assistant_text_without_a_result(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream(
                _assistant({"type": "text", "text": "first"}),
                _assistant({"type": "text", "text": "second"}),
            )
        )
        self.assertEqual(summary.final_message, "second")

    def test_an_empty_stream_is_an_error(self) -> None:
        with self.assertRaises(ClaudeCliAdapterError):
            parse_claude_stream_jsonl("   \n")

    def test_a_malformed_line_is_an_error(self) -> None:
        with self.assertRaises(ClaudeCliAdapterError):
            parse_claude_stream_jsonl(_stream({"type": "result"}) + "not-json\n")

    def test_an_event_without_a_type_is_an_error(self) -> None:
        with self.assertRaises(ClaudeCliAdapterError):
            parse_claude_stream_jsonl(_stream({"session_id": SESSION}))

    def test_a_non_object_event_is_an_error(self) -> None:
        with self.assertRaises(ClaudeCliAdapterError):
            parse_claude_stream_jsonl("[]\n")

    def test_conflicting_session_ids_fail_rather_than_picking_one(self) -> None:
        with self.assertRaises(ClaudeCliAdapterError):
            parse_claude_stream_jsonl(
                _stream(
                    {"type": "system", "session_id": SESSION},
                    {"type": "result", "session_id": "a-different-session"},
                )
            )

    def test_a_reported_error_result_is_carried_not_swallowed(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream({"type": "result", "session_id": SESSION, "is_error": True})
        )
        self.assertTrue(summary.is_error)


class ExposedSkillTests(unittest.TestCase):
    """`system/init` carries the provider's own account of what it exposed."""

    def test_init_skill_names_are_captured(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": SESSION,
                    "skills": ["alpha", "beta", "alpha"],
                },
                {"type": "result", "session_id": SESSION, "result": "ok"},
            )
        )
        self.assertEqual(summary.exposed_skills, ("alpha", "beta"))

    def test_object_shaped_skill_entries_are_accepted(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": SESSION,
                    "skills": [{"name": "alpha"}, {"nope": 1}],
                }
            )
        )
        self.assertEqual(summary.exposed_skills, ("alpha",))

    def test_exposure_is_not_reported_as_invocation(self) -> None:
        # Exposing a skill is provisioning. The Codex adapter refuses to treat
        # exposure as invocation and this one must not be laxer.
        summary = parse_claude_stream_jsonl(
            _stream(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": SESSION,
                    "skills": ["alpha"],
                },
                _assistant({"type": "text", "text": "done"}),
            )
        )
        self.assertEqual(summary.exposed_skills, ("alpha",))
        self.assertEqual(summary.invoked_skill_names, ())

    def test_a_non_init_system_event_declares_nothing(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream(
                {"type": "system", "subtype": "hook", "session_id": SESSION, "skills": ["x"]}
            )
        )
        self.assertEqual(summary.exposed_skills, ())


class SkillToolCallTests(unittest.TestCase):
    def test_native_skill_tool_calls_are_captured(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream(
                _assistant(
                    _skill_block("extract-todo-items", args=True),
                    {"type": "text", "text": "done"},
                ),
                {"type": "result", "session_id": SESSION, "result": "done"},
            )
        )
        self.assertEqual(len(summary.skill_tool_calls), 1)
        call = summary.skill_tool_calls[0]
        self.assertEqual(call.skill, "extract-todo-items")
        self.assertEqual(call.tool_use_id, "toolu_1")
        self.assertTrue(call.args_present)
        self.assertEqual(summary.invoked_skill_names, ("extract-todo-items",))

    def test_other_tools_are_not_counted_as_skill_invocations(self) -> None:
        # A Bash call is not a skill invocation. The Codex adapter refuses the
        # same inference and this one must not be laxer.
        summary = parse_claude_stream_jsonl(
            _stream(
                _assistant(
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}
                )
            )
        )
        self.assertEqual(summary.skill_tool_calls, ())
        self.assertEqual(summary.invoked_skill_names, ())

    def test_repeated_invocations_are_kept_but_names_deduplicate(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream(
                _assistant(_skill_block("alpha", tool_use_id="t1")),
                _assistant(_skill_block("alpha", tool_use_id="t2")),
                _assistant(_skill_block("beta", tool_use_id="t3")),
            )
        )
        self.assertEqual(len(summary.skill_tool_calls), 3)
        self.assertEqual(summary.invoked_skill_names, ("alpha", "beta"))

    def test_a_malformed_skill_input_does_not_invent_a_name(self) -> None:
        summary = parse_claude_stream_jsonl(
            _stream(
                _assistant(
                    {"type": "tool_use", "id": "t1", "name": "Skill", "input": "not-an-object"}
                )
            )
        )
        self.assertEqual(len(summary.skill_tool_calls), 1)
        self.assertIsNone(summary.skill_tool_calls[0].skill)
        self.assertEqual(summary.invoked_skill_names, ())


if __name__ == "__main__":
    unittest.main()
