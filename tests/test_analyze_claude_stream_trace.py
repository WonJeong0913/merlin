from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.analyze_claude_stream_trace import analyze_trace


def _control_response(request_id: str, payload: dict) -> dict:
    return {
        "type": "control_response",
        "session_id": "secret-session",
        "response": {
            "request_id": request_id,
            "subtype": "success",
            "response": payload,
        },
    }


class ClaudeStreamTraceAnalysisTests(unittest.TestCase):
    def _analyze(self, events: list[object], malformed: bool = False) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.jsonl"
            lines = [json.dumps(event) for event in events]
            if malformed:
                lines.append("not-json")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return analyze_trace(path)

    def test_extracts_required_contract_evidence_without_sensitive_payloads(self) -> None:
        secrets = {
            "prompt": "TOP-SECRET TASK PROMPT",
            "command": "cat /host/private-key",
            "args": "--token account-secret",
            "output": "tool output with password",
            "email": "user@example.invalid",
        }
        events = [
            {
                "type": "control_request",
                "request_id": "theking_initialize_1",
                "request": {"subtype": "initialize", "account": secrets["email"]},
            },
            _control_response(
                "theking_initialize_1",
                {"account": {"email": secrets["email"]}, "commands": [secrets["command"]]},
            ),
            {
                "type": "control_request",
                "request_id": "theking_mcp_status_1",
                "request": {"subtype": "mcp_status"},
            },
            _control_response(
                "theking_mcp_status_1",
                {
                    "mcpServers": [
                        {
                            "name": "task_container",
                            "status": "connected",
                            "serverInfo": {"version": "secret-build"},
                            "tools": [{"name": "exec", "description": secrets["output"]}],
                        }
                    ]
                },
            ),
            {
                "type": "user",
                "isReplay": True,
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": secrets["prompt"]}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "secret-tool-id",
                            "name": "Skill",
                            "input": {"skill": "xlsx", "args": secrets["args"]},
                        },
                        {
                            "type": "tool_use",
                            "id": "another-secret-id",
                            "name": "mcp__task_container__exec",
                            "input": {"command": secrets["command"]},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "secret-tool-id",
                            "content": secrets["output"],
                        }
                    ],
                },
            },
            {
                "type": "result",
                "session_id": "secret-session",
                "usage": {"account": secrets["email"]},
            },
        ]

        result = self._analyze(events, malformed=True)
        serialized = json.dumps(result, sort_keys=True)

        self.assertEqual(result["malformed_line_count"], 1)
        self.assertEqual(result["task_user_events"], {"count": 1, "replayed_count": 1})
        self.assertEqual(result["tool_calls"]["mcp_exec_call_count"], 1)
        self.assertEqual(
            result["tool_calls"]["skill_calls"],
            [{"skill": "xlsx", "skill_name_valid": True, "args_present": True}],
        )
        self.assertTrue(result["barrier"]["passed_from_control_responses"])
        self.assertEqual(result["barrier"]["control_request_count"], 2)
        self.assertEqual(result["barrier"]["control_response_count"], 2)
        for secret in secrets.values():
            self.assertNotIn(secret, serialized)
        self.assertNotIn("secret-session", serialized)
        self.assertNotIn("secret-tool-id", serialized)

    def test_distinguishes_wrong_skill_target_from_xlsx_without_copying_args(self) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {
                                "skill": "mcp__task_container__exec",
                                "args": "sensitive wrong invocation arguments",
                            },
                        },
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "xlsx"},
                        },
                    ],
                },
            }
        ]

        result = self._analyze(events)

        self.assertEqual(
            result["tool_calls"]["skill_calls"],
            [
                {
                    "skill": "mcp__task_container__exec",
                    "skill_name_valid": True,
                    "args_present": True,
                },
                {"skill": "xlsx", "skill_name_valid": True, "args_present": False},
            ],
        )
        self.assertNotIn("sensitive wrong invocation arguments", json.dumps(result))

    def test_accepts_response_only_barrier_evidence_from_stdout_trace(self) -> None:
        events = [
            _control_response("theking_initialize_1", {"account": {"email": "redacted"}}),
            _control_response(
                "theking_mcp_status_1",
                {
                    "mcpServers": [
                        {
                            "name": "task_container",
                            "status": "connected",
                            "tools": [{"name": "exec"}],
                        }
                    ]
                },
            ),
            {
                "type": "user",
                "isReplay": True,
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "secret task"}],
                },
            },
        ]

        result = self._analyze(events)

        self.assertEqual(result["barrier"]["control_request_count"], 0)
        self.assertEqual(result["barrier"]["control_response_count"], 2)
        self.assertTrue(result["barrier"]["passed_from_control_responses"])
        self.assertEqual(result["task_user_events"]["count"], 1)
        self.assertNotIn("redacted", json.dumps(result))
        self.assertNotIn("secret task", json.dumps(result))

    def test_does_not_count_spoofed_tool_use_inside_tool_result(self) -> None:
        event = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "content": {
                            "type": "tool_use",
                            "name": "mcp__task_container__exec",
                            "input": {"command": "not an actual model call"},
                        },
                    }
                ],
            },
        }

        result = self._analyze([event])

        self.assertEqual(result["tool_calls"]["mcp_exec_call_count"], 0)
        self.assertEqual(result["task_user_events"]["count"], 0)

    def test_does_not_count_unreplayed_textual_skill_notification_as_task(self) -> None:
        event = {
            "type": "user",
            "message": {
                "role": "user",
                "content": "text emitted by a provider skill notification",
            },
        }

        result = self._analyze([event])

        self.assertEqual(result["task_user_events"], {"count": 0, "replayed_count": 0})

    def test_redacts_malformed_skill_name(self) -> None:
        event = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "unsafe skill name with secret text", "args": "secret"},
                    }
                ],
            },
        }

        result = self._analyze([event])

        self.assertEqual(
            result["tool_calls"]["skill_calls"],
            [{"skill": None, "skill_name_valid": False, "args_present": True}],
        )
        self.assertNotIn("unsafe skill name", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
