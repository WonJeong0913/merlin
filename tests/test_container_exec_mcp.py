from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.skillsbench.container_exec_mcp import (
    ExecutionReport,
    MAX_COMMAND_CHARS,
    MAX_OUTPUT_CHARS,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    ServerConfig,
    docker_exec_argv,
    execute_in_container,
    handle_message,
    negotiate_protocol_version,
    parse_config,
    serve,
)


class ContainerExecMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ServerConfig(
            container="merlin-weighted-gdp-c0-1",
            workdir="/root/task",
            timeout_sec=30,
        )

    def test_initialize_lists_tool_capability_and_server_version(self) -> None:
        response = handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            self.config,
        )

        self.assertEqual(response["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(response["result"]["capabilities"], {"tools": {"listChanged": False}})
        self.assertIn("name", response["result"]["serverInfo"])
        self.assertIn("version", response["result"]["serverInfo"])

    def test_initialize_negotiates_supported_requested_protocol_version(self) -> None:
        for requested in SUPPORTED_PROTOCOL_VERSIONS:
            with self.subTest(requested=requested):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": requested,
                        "method": "initialize",
                        "params": {"protocolVersion": requested},
                    },
                    self.config,
                )
                self.assertEqual(response["result"]["protocolVersion"], requested)

        self.assertEqual(
            negotiate_protocol_version({"protocolVersion": "2099-01-01"}),
            PROTOCOL_VERSION,
        )
        self.assertEqual(negotiate_protocol_version(None), PROTOCOL_VERSION)

    def test_tools_list_exposes_only_command_and_capped_timeout(self) -> None:
        response = handle_message(
            {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
            self.config,
        )

        tool = response["result"]["tools"][0]
        schema = tool["inputSchema"]
        self.assertEqual(tool["name"], "exec")
        self.assertEqual(set(schema["properties"]), {"command", "timeout_sec"})
        self.assertEqual(schema["properties"]["command"]["maxLength"], MAX_COMMAND_CHARS)
        self.assertEqual(schema["properties"]["timeout_sec"]["maximum"], 30)
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("container", schema["properties"])
        self.assertNotIn("workdir", schema["properties"])

    def test_tool_call_caps_timeout_and_returns_structured_text(self) -> None:
        calls: list[tuple[ServerConfig, str, int]] = []

        def fake_executor(config: ServerConfig, command: str, timeout_sec: int) -> ExecutionReport:
            calls.append((config, command, timeout_sec))
            return ExecutionReport(
                exit_code=7,
                duration_sec=1.25,
                stdout="out\n",
                stderr="err\n",
            )

        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "exec",
                    "arguments": {"command": "python3 solve.py", "timeout_sec": 300},
                },
            },
            self.config,
            executor=fake_executor,
        )

        self.assertEqual(calls, [(self.config, "python3 solve.py", 30)])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(
            payload,
            {
                "duration_sec": 1.25,
                "exit_code": 7,
                "stderr": "err\n",
                "stderr_truncated": False,
                "stdout": "out\n",
                "stdout_truncated": False,
                "timed_out": False,
                "timeout_capped": True,
                "timeout_sec": 30,
            },
        )
        self.assertFalse(response["result"]["isError"])

    def test_tool_call_rejects_boundary_override_arguments(self) -> None:
        called = False

        def fake_executor(config: ServerConfig, command: str, timeout_sec: int) -> ExecutionReport:
            nonlocal called
            called = True
            return ExecutionReport(exit_code=0, duration_sec=0.0)

        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "exec",
                    "arguments": {"command": "pwd", "container": "other"},
                },
            },
            self.config,
            executor=fake_executor,
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(response["error"]["data"], {"unexpected": ["container"]})
        self.assertFalse(called)

    def test_skill_associated_exec_is_limited_to_provisioned_ids(self) -> None:
        config = ServerConfig(
            container="fixed-container",
            workdir="/root",
            timeout_sec=30,
            allowed_skill_ids=("mesh-analysis", "obj-exporter@b6d1bcf98031"),
        )
        tool = handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            config,
        )["result"]["tools"][0]
        self.assertEqual(
            tool["inputSchema"]["properties"]["skill_id"]["enum"],
            ["mesh-analysis", "obj-exporter@b6d1bcf98031"],
        )

        accepted = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "exec",
                    "arguments": {"command": "python3 solve.py", "skill_id": "mesh-analysis"},
                },
            },
            config,
            executor=lambda *_args: ExecutionReport(exit_code=0, duration_sec=0.1),
        )
        self.assertNotIn("error", accepted)

        rejected = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "exec",
                    "arguments": {"command": "python3 solve.py", "skill_id": "not-provisioned"},
                },
            },
            config,
        )
        self.assertEqual(rejected["error"]["code"], -32602)

    def test_tool_call_rejects_oversized_command(self) -> None:
        called = False

        def fake_executor(config: ServerConfig, command: str, timeout_sec: int) -> ExecutionReport:
            nonlocal called
            called = True
            return ExecutionReport(exit_code=0, duration_sec=0.0)

        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": "oversized",
                "method": "tools/call",
                "params": {
                    "name": "exec",
                    "arguments": {"command": "x" * (MAX_COMMAND_CHARS + 1)},
                },
            },
            self.config,
            executor=fake_executor,
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertFalse(called)

    def test_tool_call_rejects_invalid_timeout(self) -> None:
        for invalid in (0, -1, 1.5, True, "10"):
            with self.subTest(timeout=invalid):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "exec",
                            "arguments": {"command": "pwd", "timeout_sec": invalid},
                        },
                    },
                    self.config,
                )
                self.assertEqual(response["error"]["code"], -32602)

    def test_notifications_have_no_response_and_ping_does(self) -> None:
        self.assertIsNone(
            handle_message(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                self.config,
            )
        )
        self.assertEqual(
            handle_message(
                {"jsonrpc": "2.0", "id": "ping-1", "method": "ping"},
                self.config,
            ),
            {"jsonrpc": "2.0", "id": "ping-1", "result": {}},
        )

    def test_docker_exec_uses_argv_and_fixed_boundary(self) -> None:
        self.assertEqual(
            docker_exec_argv(self.config, "printf '%s' hello; touch done", 17),
            [
                "docker",
                "exec",
                "-w",
                "/root/task",
                "merlin-weighted-gdp-c0-1",
                "timeout",
                "--signal=TERM",
                "--kill-after=2s",
                "17s",
                "bash",
                "-lc",
                "printf '%s' hello; touch done",
            ],
        )

    @patch("experiments.skillsbench.container_exec_mcp.subprocess.run")
    def test_executor_captures_docker_result(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=9,
            stdout="stdout",
            stderr="stderr",
        )

        with patch.dict(
            "experiments.skillsbench.container_exec_mcp.os.environ",
            {
                "PATH": "/trusted/bin",
                "DOCKER_HOST": "unix:///trusted/docker.sock",
                "SHOULD_NOT_LEAK": "secret",
            },
            clear=True,
        ):
            report = execute_in_container(self.config, "false", 20)

        self.assertEqual(report.exit_code, 9)
        self.assertEqual(report.stdout, "stdout")
        self.assertEqual(report.stderr, "stderr")
        argv = run_mock.call_args.args[0]
        self.assertEqual(argv, docker_exec_argv(self.config, "false", 20))
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 25)
        self.assertIs(run_mock.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(
            run_mock.call_args.kwargs["env"],
            {"PATH": "/trusted/bin", "DOCKER_HOST": "unix:///trusted/docker.sock"},
        )
        self.assertNotIn("shell", run_mock.call_args.kwargs)

    @patch("experiments.skillsbench.container_exec_mcp.subprocess.run")
    def test_executor_reports_timeout_with_normalized_output(self, run_mock) -> None:
        run_mock.side_effect = subprocess.TimeoutExpired(
            cmd=["docker", "exec"],
            timeout=30,
            output=b"partial-out\xff",
            stderr=b"partial-err\xff",
        )

        report = execute_in_container(self.config, "sleep 99", 300)

        self.assertEqual(report.exit_code, 124)
        self.assertTrue(report.timed_out)
        self.assertEqual(report.stdout, "partial-out\ufffd")
        self.assertEqual(report.stderr, "partial-err\ufffd")
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 35)

    def test_tool_call_caps_output_and_sets_truncation_flags(self) -> None:
        def fake_executor(config: ServerConfig, command: str, timeout_sec: int) -> ExecutionReport:
            return ExecutionReport(
                exit_code=0,
                duration_sec=0.1,
                stdout="a" * (MAX_OUTPUT_CHARS + 100),
                stderr="b" * (MAX_OUTPUT_CHARS + 200),
            )

        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": "large-output",
                "method": "tools/call",
                "params": {"name": "exec", "arguments": {"command": "generate-output"}},
            },
            self.config,
            executor=fake_executor,
        )

        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(len(payload["stdout"]), MAX_OUTPUT_CHARS)
        self.assertEqual(len(payload["stderr"]), MAX_OUTPUT_CHARS)
        self.assertTrue(payload["stdout_truncated"])
        self.assertTrue(payload["stderr_truncated"])
        self.assertIn("[output truncated]", payload["stdout"])
        self.assertIn("[output truncated]", payload["stderr"])

    def test_tool_call_marks_timeout_and_infrastructure_errors(self) -> None:
        cases = [
            ExecutionReport(exit_code=124, duration_sec=1.0, timed_out=True),
            ExecutionReport(exit_code=137, duration_sec=1.0, timed_out=True),
            ExecutionReport(exit_code=125, duration_sec=1.0),
            ExecutionReport(exit_code=126, duration_sec=1.0),
            ExecutionReport(exit_code=127, duration_sec=1.0),
        ]
        for index, report in enumerate(cases):
            with self.subTest(exit_code=report.exit_code):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": index,
                        "method": "tools/call",
                        "params": {"name": "exec", "arguments": {"command": "work"}},
                    },
                    self.config,
                    executor=lambda _config, _command, _timeout, report=report: report,
                )
                self.assertTrue(response["result"]["isError"])

    def test_internal_executor_exception_is_generic(self) -> None:
        def failing_executor(config: ServerConfig, command: str, timeout_sec: int) -> ExecutionReport:
            raise RuntimeError("secret host detail")

        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": "failure",
                "method": "tools/call",
                "params": {"name": "exec", "arguments": {"command": "work"}},
            },
            self.config,
            executor=failing_executor,
        )

        self.assertEqual(
            response,
            {
                "jsonrpc": "2.0",
                "id": "failure",
                "error": {"code": -32603, "message": "container execution failed"},
            },
        )
        self.assertNotIn("secret host detail", json.dumps(response))

    def test_stdio_server_emits_parse_error_skips_notification_and_handles_ping(self) -> None:
        instream = io.StringIO(
            "not-json\n"
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            '{"jsonrpc":"2.0","id":8,"method":"ping"}\n'
        )
        outstream = io.StringIO()

        self.assertEqual(serve(self.config, instream=instream, outstream=outstream), 0)

        responses = [json.loads(line) for line in outstream.getvalue().splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 8, "result": {}})

    def test_protocol_audit_records_handshake_metadata_but_not_command_or_output(self) -> None:
        requested = SUPPORTED_PROTOCOL_VERSIONS[0]
        secret_command = "printf super-secret-command"
        secret_output = "super-secret-output"
        instream = io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": requested},
                }
            )
            + "\n"
            + '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            + '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
            + json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "exec", "arguments": {"command": secret_command}},
                }
            )
            + "\n"
        )
        outstream = io.StringIO()
        auditstream = io.StringIO()

        serve(
            self.config,
            instream=instream,
            outstream=outstream,
            auditstream=auditstream,
            executor=lambda _config, _command, _timeout: ExecutionReport(
                exit_code=0,
                duration_sec=0.01,
                stdout=secret_output,
            ),
        )

        audit_text = auditstream.getvalue()
        events = [json.loads(line) for line in audit_text.splitlines()]
        self.assertNotIn(secret_command, audit_text)
        self.assertNotIn(secret_output, audit_text)
        self.assertTrue(
            any(
                event.get("requested_protocol_version") == requested
                for event in events
            )
        )
        self.assertTrue(
            any(
                event.get("negotiated_protocol_version") == requested
                for event in events
            )
        )
        self.assertTrue(any(event.get("tool_count") == 1 for event in events))
        self.assertTrue(
            any(
                event.get("tool_result_exit_code") == 0
                and event.get("tool_result_timed_out") is False
                for event in events
            )
        )
        self.assertTrue(any(event.get("tool_name") == "exec" for event in events))
        self.assertEqual(events[0]["event"], "start")
        self.assertEqual(events[-1]["event"], "eof")

    def test_protocol_audit_records_allowed_skill_id_but_not_command(self) -> None:
        config = ServerConfig(
            container="fixed-container",
            workdir="/root",
            timeout_sec=30,
            allowed_skill_ids=("mesh-analysis",),
        )
        command = "python3 confidential-solver.py"
        instream = io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "exec",
                        "arguments": {"command": command, "skill_id": "mesh-analysis"},
                    },
                }
            )
            + "\n"
        )
        auditstream = io.StringIO()
        serve(
            config,
            instream=instream,
            outstream=io.StringIO(),
            auditstream=auditstream,
            executor=lambda *_args: ExecutionReport(exit_code=0, duration_sec=0.1),
        )
        audit = auditstream.getvalue()
        self.assertNotIn(command, audit)
        self.assertTrue(
            any(
                event.get("skill_id") == "mesh-analysis"
                for event in map(json.loads, audit.splitlines())
            )
        )

    def test_config_comes_from_cli_or_environment_and_is_validated(self) -> None:
        config = parse_config(
            [],
            environ={
                "TASK_CONTAINER_ID": "fixed-container",
                "TASK_CONTAINER_WORKDIR": "/workspace",
                "TASK_CONTAINER_TIMEOUT_SEC": "12",
            },
        )
        self.assertEqual(config, ServerConfig("fixed-container", "/workspace", 12))

        audited = parse_config(
            [],
            environ={
                "TASK_CONTAINER_ID": "fixed-container",
                "TASK_CONTAINER_WORKDIR": "/workspace",
                "TASK_CONTAINER_TIMEOUT_SEC": "12",
                "TASK_CONTAINER_MCP_AUDIT_LOG": "/tmp/mcp-audit.jsonl",
            },
        )
        self.assertEqual(audited.audit_log, "/tmp/mcp-audit.jsonl")

        override = parse_config(
            ["--container", "cli-container", "--workdir", "/task", "--timeout-sec", "5"],
            environ={},
        )
        self.assertEqual(override, ServerConfig("cli-container", "/task", 5))

        with tempfile.TemporaryDirectory() as temporary:
            allowed = Path(temporary) / "allowed.json"
            allowed.write_text(
                json.dumps(["mesh-analysis", "obj-exporter@b6d1bcf98031"]),
                encoding="utf-8",
            )
            provisioned = parse_config(
                [
                    "--container",
                    "cli-container",
                    "--workdir",
                    "/task",
                    "--allowed-skill-ids-file",
                    str(allowed),
                ],
                environ={},
            )
            self.assertEqual(
                provisioned.allowed_skill_ids,
                ("mesh-analysis", "obj-exporter@b6d1bcf98031"),
            )


if __name__ == "__main__":
    unittest.main()
