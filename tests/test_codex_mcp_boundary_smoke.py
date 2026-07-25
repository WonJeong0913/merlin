from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from experiments.skillsbench.probe_codex_mcp_capability import (
    NATIVE_TOOL_FEATURES_TO_DISABLE,
)
from experiments.skillsbench.run_codex_mcp_boundary_smoke import (
    build_command,
    run_boundary_smoke,
)


class CodexMcpBoundarySmokeTests(unittest.TestCase):
    def test_command_disables_tool_features_and_uses_one_fixed_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = build_command(
                codex_executable=root / "codex",
                server_path=root / "container_exec_mcp.py",
                raw_root=root / "raw",
                model="gpt-5.6-terra",
                effort="low",
            )

        disabled = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--disable"
        ]
        self.assertEqual(disabled, list(NATIVE_TOOL_FEATURES_TO_DISABLE))
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--strict-config", command)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertNotIn("--sandbox", command)
        self.assertTrue(any("mcp_servers.merlin_harness_task.command" in value for value in command))
        self.assertIn("mcp_servers.merlin_harness_task.required=true", command)
        self.assertTrue(any("merlin-boundary-canary-missing-container" in value for value in command))

    def test_windows_codex_uses_wsl_stdio_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = build_command(
                codex_executable=root / "codex.exe",
                server_path=root / "container_exec_mcp.py",
                raw_root=root / "raw",
                model="gpt-5.6-terra",
                effort="high",
            )
        server_command = next(
            value
            for value in command
            if value.startswith("mcp_servers.merlin_harness_task.command=")
        )
        server_args = next(
            value
            for value in command
            if value.startswith("mcp_servers.merlin_harness_task.args=")
        )
        self.assertIn("wsl.exe", server_command)
        self.assertIn("--exec", server_args)
        self.assertIn("container_exec_mcp.py", server_args)

    def test_safe_smoke_requires_one_mcp_call_and_no_native_execution_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / "codex"
            server = root / "container_exec_mcp.py"
            codex.write_text("binary", encoding="utf-8")
            server.write_text("server", encoding="utf-8")
            raw = root / "raw"
            output = root / "safe.json"

            def fake_run(argv, **_kwargs):
                if argv[-2:] == ["features", "list"]:
                    stdout = "".join(
                        f"{feature} stable false\n"
                        for feature in NATIVE_TOOL_FEATURES_TO_DISABLE
                    )
                    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
                (raw / "mcp-audit.jsonl").write_text(
                    "".join(
                        json.dumps(event) + "\n"
                        for event in (
                            {"method": "initialize"},
                            {
                                "method": "initialize.response",
                                "negotiated_protocol_version": "2025-06-18",
                            },
                            {"method": "tools/list"},
                            {"method": "tools/list.response", "tool_count": 1},
                            {"method": "tools/call", "tool_name": "exec"},
                            {
                                "event": "response",
                                "tool_result_exit_code": 1,
                                "tool_result_timed_out": False,
                            },
                        )
                    ),
                    encoding="utf-8",
                )
                (raw / "last-message.json").write_text(
                    json.dumps(
                        {
                            "tool_call_attempted": True,
                            "mcp_exit_code": 1,
                            "mcp_timed_out": False,
                        }
                    ),
                    encoding="utf-8",
                )
                stdout = "".join(
                    json.dumps(event) + "\n"
                    for event in (
                        {"type": "thread.started", "thread_id": "thread-test"},
                        {"type": "turn.started"},
                        {
                            "type": "item.started",
                            "item": {"type": "mcp_tool_call", "id": "call-1"},
                        },
                        {
                            "type": "item.completed",
                            "item": {"type": "mcp_tool_call", "id": "call-1"},
                        },
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "agent_message",
                                "text": json.dumps(
                                    {
                                        "tool_call_attempted": True,
                                        "mcp_exit_code": 1,
                                        "mcp_timed_out": False,
                                    }
                                ),
                            },
                        },
                        {"type": "turn.completed", "usage": {}},
                    )
                )
                return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

            suppressed = {
                "all_requested_features_disabled": True,
                "observed_disabled_features": list(NATIVE_TOOL_FEATURES_TO_DISABLE),
                "features_list_sha256": "f" * 64,
            }
            with mock.patch(
                "experiments.skillsbench.run_codex_mcp_boundary_smoke."
                "probe_codex_feature_suppression",
                return_value=suppressed,
            ), mock.patch(
                "experiments.skillsbench.run_codex_mcp_boundary_smoke.subprocess.run",
                side_effect=fake_run,
            ):
                report = run_boundary_smoke(
                    codex_executable=codex,
                    server_path=server,
                    raw_root=raw,
                    output_path=output,
                )

            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["runtime_observation"]["mcp_exec_call_count"], 1)
            self.assertEqual(
                report["runtime_observation"]["forbidden_native_tool_item_types"],
                [],
            )
            self.assertFalse(report["claim_boundary"]["six_cell_execution_allowed"])
            self.assertFalse(report["claim_boundary"]["container_execution_succeeded"])
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
