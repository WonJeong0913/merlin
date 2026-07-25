from __future__ import annotations

import hashlib
import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.skillsbench.run_model_c0_c1_pilot import (
    BENCHMARK_INELIGIBILITY_REASONS,
    DEFAULT_HARNESS_MODE,
    PAPER_CLI_MCP_PROMPT_CONTRACT,
    assistant_models_match_request,
    build_agent_prompt,
    container_process_snapshot,
    directory_manifest,
    extract_agent_trace,
    make_agent_command,
    prepare_isolated_claude_environment,
    run_agent,
    stage_and_run_verifier,
    start_bound_task_container,
    validate_readiness_selection,
)
from experiments.skillsbench.run_oracle_readiness import CommandReport


CONDITION = {
    "backend": "claude",
    "model_id": "claude-sonnet-5",
    "effort": "high",
    "runtime_effort": "high",
}


def _flag_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


class _CaptureStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.closed = False

    def write(self, value: str) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _StreamingProcess:
    def __init__(self, stdout: str, stderr: str = "") -> None:
        self.stdin = _CaptureStdin()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.pid = 4242
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return self.returncode


def _control_response(request_id: str, response: dict) -> dict:
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": response,
        },
    }


class SecureAgenticBridgeTests(unittest.TestCase):
    def test_prompt_is_exact_task_body_and_hash_matches_task_instruction(self) -> None:
        task_body = "Calculate the requested weighted GDP output.\n\nKeep this trailing line.\n"
        kwargs = {
            "task_id": "weighted-gdp-calc",
            "task_text": task_body,
            "container_workdir": "/workspace",
        }

        c0_prompt = build_agent_prompt(**kwargs)
        c1_prompt = build_agent_prompt(**kwargs)

        self.assertEqual(c0_prompt, c1_prompt)
        self.assertEqual(c0_prompt, task_body)
        prompt_sha = hashlib.sha256(c0_prompt.encode("utf-8")).hexdigest()
        task_instruction_sha = hashlib.sha256(task_body.encode("utf-8")).hexdigest()
        self.assertEqual(prompt_sha, task_instruction_sha)

    def test_paper_cli_mcp_contract_and_ineligibility_reasons_are_explicit(self) -> None:
        self.assertEqual(DEFAULT_HARNESS_MODE, "H_paper_cli_mcp_v1")
        self.assertEqual(
            PAPER_CLI_MCP_PROMPT_CONTRACT,
            {
                "task_user_message": "task_md_body_only",
                "execution_contract_source": "provider_tool_schema",
                "prompt_equals_task_instruction": True,
            },
        )
        self.assertEqual(len(BENCHMARK_INELIGIBILITY_REASONS), 3)
        rendered = " ".join(BENCHMARK_INELIGIBILITY_REASONS)
        self.assertIn("temperature=0", rendered)
        self.assertIn("8K token cap and storage cap", rendered)
        self.assertIn("distinct evaluation cell", rendered)

    def test_claude_command_exposes_only_mcp_and_skill_with_fresh_session_flags(self) -> None:
        mcp_config = {
            "mcpServers": {
                "task_container": {
                    "type": "stdio",
                    "command": "/usr/bin/python3",
                    "args": [
                        "container_exec_mcp.py",
                        "--container",
                        "fixed-container",
                        "--workdir",
                        "/workspace",
                    ],
                }
            }
        }

        settings = {"enableAllProjectMcpServers": True}
        first = make_agent_command(CONDITION, mcp_config=mcp_config, settings=settings)
        second = make_agent_command(CONDITION, mcp_config=mcp_config, settings=settings)

        self.assertEqual(first, second)
        self.assertEqual(_flag_value(first, "--tools"), "mcp__task_container__exec,Skill")
        self.assertEqual(
            _flag_value(first, "--allowedTools"),
            "mcp__task_container__exec,Skill",
        )
        self.assertEqual(_flag_value(first, "--setting-sources"), "project")
        self.assertEqual(_flag_value(first, "--permission-mode"), "dontAsk")
        self.assertEqual(_flag_value(first, "--input-format"), "stream-json")
        self.assertEqual(_flag_value(first, "--output-format"), "stream-json")
        self.assertIn("--replay-user-messages", first)
        self.assertIn("--strict-mcp-config", first)
        self.assertIn("--no-chrome", first)
        self.assertIn("--no-session-persistence", first)
        self.assertNotIn("--continue", first)
        self.assertNotIn("--resume", first)
        self.assertEqual(json.loads(_flag_value(first, "--mcp-config")), mcp_config)
        self.assertEqual(json.loads(_flag_value(first, "--settings")), settings)

        disallowed = set(_flag_value(first, "--disallowedTools").split(","))
        self.assertTrue({"Bash", "Edit", "Read", "Write", "Agent"}.issubset(disallowed))

    @patch("experiments.skillsbench.run_model_c0_c1_pilot.subprocess.Popen")
    def test_run_agent_gates_single_real_user_event_on_connected_mcp(
        self, popen_mock
    ) -> None:
        init = _control_response(
            "theking_initialize_1",
            {"commands": [], "account": {"email": "must-not-appear-in-evidence"}},
        )
        status = _control_response(
            "theking_mcp_status_1",
            {
                "mcpServers": [
                    {
                        "name": "task_container",
                        "status": "connected",
                        "serverInfo": {"name": "bridge", "version": "1.1.0"},
                        "tools": [{"name": "exec", "annotations": {}}],
                    }
                ]
            },
        )
        replay = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "TASK"}]},
            "isReplay": True,
        }
        system_init = {
            "type": "system",
            "subtype": "init",
            "tools": ["mcp__task_container__exec"],
            "mcp_servers": [{"name": "task_container", "status": "connected"}],
        }
        result = {"type": "result", "subtype": "success", "num_turns": 1}
        output = "".join(
            json.dumps(event) + "\n"
            for event in (init, status, replay, system_init, result)
        )
        fake_process = _StreamingProcess(output)
        popen_mock.return_value = fake_process
        evidence: dict = {}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "agent-output.jsonl"
            report = run_agent(
                CONDITION,
                "TASK",
                provider_project=root,
                timeout_sec=10,
                mcp_config={"mcpServers": {}},
                settings={"enableAllProjectMcpServers": True},
                raw_output_path=raw_path,
                barrier_evidence=evidence,
            )
            captured_raw = raw_path.read_text(encoding="utf-8")

        writes = [json.loads(item) for item in fake_process.stdin.writes]
        self.assertEqual(report.exit_code, 0)
        self.assertEqual([item["type"] for item in writes], [
            "control_request",
            "control_request",
            "user",
        ])
        self.assertEqual(writes[0]["request"]["subtype"], "initialize")
        self.assertEqual(writes[1]["request"]["subtype"], "mcp_status")
        self.assertEqual(writes[2]["message"]["content"][0]["text"], "TASK")
        self.assertEqual(sum(item["type"] == "user" for item in writes), 1)
        self.assertTrue(evidence["passed"])
        self.assertEqual(evidence["warmup_model_turn_count"], 0)
        self.assertTrue(evidence["first_model_input_is_task"])
        self.assertEqual(evidence["task_event"]["count"], 1)
        self.assertEqual(evidence["mcp_status"]["ready_server"]["tool_names"], ["exec"])
        self.assertNotIn("must-not-appear-in-evidence", json.dumps(evidence))
        self.assertEqual(captured_raw, output)

    @patch("experiments.skillsbench.run_model_c0_c1_pilot.os.killpg")
    @patch("experiments.skillsbench.run_model_c0_c1_pilot.subprocess.Popen")
    def test_run_agent_fails_before_task_when_connected_mcp_lacks_exec(
        self, popen_mock, _killpg_mock
    ) -> None:
        init = _control_response("theking_initialize_1", {"commands": []})
        status = _control_response(
            "theking_mcp_status_1",
            {
                "mcpServers": [
                    {
                        "name": "task_container",
                        "status": "connected",
                        "tools": [],
                    }
                ]
            },
        )
        output = json.dumps(init) + "\n" + json.dumps(status) + "\n"
        fake_process = _StreamingProcess(output)
        popen_mock.return_value = fake_process
        evidence: dict = {}

        with tempfile.TemporaryDirectory() as tmp:
            report = run_agent(
                CONDITION,
                "MUST NOT BE SENT",
                provider_project=Path(tmp),
                timeout_sec=10,
                mcp_config={"mcpServers": {}},
                settings={"enableAllProjectMcpServers": True},
                barrier_evidence=evidence,
            )

        writes = [json.loads(item) for item in fake_process.stdin.writes]
        self.assertEqual(report.exit_code, 78)
        self.assertFalse(report.timed_out)
        self.assertFalse(evidence["passed"])
        self.assertFalse(evidence["task_event"]["sent"])
        self.assertEqual(evidence["task_event"]["count"], 0)
        self.assertFalse(any(item.get("type") == "user" for item in writes))
        self.assertIn("non-ready status", evidence["failure_reason"])

    @patch("experiments.skillsbench.run_model_c0_c1_pilot.run_command")
    @patch(
        "experiments.skillsbench.run_model_c0_c1_pilot.docker_resource_args",
        return_value=["--memory", "2g", "--cpus", "1"],
    )
    def test_c1_container_has_only_workspace_and_read_only_skill_mounts(
        self,
        _resource_args,
        run_command_mock,
    ) -> None:
        run_command_mock.return_value = CommandReport(
            argv=[], exit_code=0, duration_sec=0.01
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            skills = root / "curated-skills"
            workspace.mkdir()
            skills.mkdir()

            _, report = start_bound_task_container(
                image="skillsbench-task:test",
                workspace=workspace,
                task_id="weighted-gdp-calc",
                task_text="""---
environment:
  bugswarm_image_tag: benchmark-image
---
task body
""",
                container_workdir="/workspace",
                skills_source=skills,
                native_skill_container_path=Path("/root/.claude/skills"),
            )

        self.assertIs(report, run_command_mock.return_value)
        command = run_command_mock.call_args.args[0]
        rendered = " ".join(command)
        self.assertEqual(command[:3], ["docker", "run", "-d"])
        self.assertEqual(_flag_value(command, "--entrypoint"), "/bin/sh")
        self.assertEqual(_flag_value(command, "-e"), "bugswarm_image_tag=benchmark-image")
        self.assertNotIn("/verifier", rendered)
        self.assertNotIn("/logs", rendered)
        self.assertNotIn("docker.sock", rendered)

        volume_values = [
            command[index + 1]
            for index, token in enumerate(command[:-1])
            if token == "-v"
        ]
        self.assertIn(f"{workspace.resolve()}:/workspace", volume_values)
        self.assertIn(f"{skills.resolve()}:/workspace/skills:ro", volume_values)
        self.assertIn(f"{skills.resolve()}:/root/.claude/skills:ro", volume_values)
        skill_mounts = [value for value in volume_values if str(skills.resolve()) in value]
        self.assertTrue(skill_mounts)
        self.assertTrue(all(value.endswith(":ro") for value in skill_mounts))

    def test_full_denominator_readiness_policy_records_but_does_not_drop_exceptions(self) -> None:
        records = {
            "ready": {"task_id": "ready", "passed": True, "status": "passed"},
            "exception": {
                "task_id": "exception",
                "passed": False,
                "status": "oracle_failed",
            },
        }

        with self.assertRaisesRegex(ValueError, "not in executable readiness passed set"):
            validate_readiness_selection(
                ["ready", "exception"],
                records,
                policy="passed",
            )
        self.assertEqual(
            validate_readiness_selection(
                ["ready", "exception"],
                records,
                policy="all",
            ),
            ["exception"],
        )
        with self.assertRaisesRegex(ValueError, "absent from readiness evidence"):
            validate_readiness_selection(["missing"], records, policy="all")

    @patch("experiments.skillsbench.run_model_c0_c1_pilot.run_command")
    def test_verifier_staging_creates_compat_tests_dir_and_passes_declared_env(
        self,
        run_command_mock,
    ) -> None:
        run_command_mock.return_value = CommandReport(
            argv=[], exit_code=0, duration_sec=0.01
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = root / "task"
            verifier = task_dir / "verifier"
            verifier.mkdir(parents=True)
            (verifier / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            stage_and_run_verifier(
                container="fixed-container",
                task_dir=task_dir,
                task_text="""---
verifier:
  env:
    REPO_ID: example/repo
---
Task
""",
                logs_dir=root / "logs",
                timeout_sec=60,
            )

        calls = [call.args[0] for call in run_command_mock.call_args_list]
        prepare = calls[0]
        verifier_call = calls[3]
        self.assertIn("/tests", prepare[-1])
        self.assertEqual(calls[2][0:2], ["docker", "cp"])
        self.assertIn("fixed-container:/tests/", calls[2])
        self.assertEqual(_flag_value(verifier_call, "-e"), "REPO_ID=example/repo")

    def test_stream_trace_detects_mcp_skill_and_unexpected_tools(self) -> None:
        events = [
            {
                "type": "system",
                "subtype": "init",
                "tools": ["mcp__task_container__exec", "Skill", "Bash"],
                "mcp_servers": [{"name": "task_container", "status": "connected"}],
                "plugins": [],
                "skills": ["xlsx"],
            },
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-5",
                    "content": [
                        {"type": "tool_use", "name": "mcp__task_container__exec"},
                        {"type": "tool_use", "name": "Skill"},
                        {"type": "tool_use", "name": "Read"},
                    ]
                },
            },
            {"type": "result", "subtype": "success"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "agent-output.jsonl"
            trace_path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\nnot-json\n",
                encoding="utf-8",
            )
            trace = extract_agent_trace(trace_path)

        self.assertEqual(trace["event_count"], 3)
        self.assertEqual(trace["malformed_line_count"], 1)
        self.assertEqual(trace["mcp_exec_call_count"], 1)
        self.assertEqual(trace["skill_call_count"], 1)
        self.assertEqual(trace["unexpected_tool_calls"], ["Read"])
        self.assertEqual(trace["unexpected_advertised_tools"], ["Bash"])
        self.assertEqual(trace["mcp_servers"], ["task_container"])
        self.assertEqual(trace["mcp_server_statuses"], {"task_container": "connected"})
        self.assertEqual(trace["advertised_skills"], ["xlsx"])
        self.assertEqual(trace["assistant_models"], ["claude-sonnet-5"])
        self.assertEqual(trace["rejected_assistant_model_count"], 0)
        self.assertEqual(trace["result_subtype"], "success")

    def test_stream_trace_sanitizes_and_deduplicates_assistant_models(self) -> None:
        events = [
            {"type": "assistant", "message": {"model": "claude-sonnet-5", "content": []}},
            {"type": "assistant", "message": {"model": "claude-sonnet-5", "content": []}},
            {"type": "assistant", "message": {"model": "bad model\nvalue", "content": []}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "agent-output.jsonl"
            trace_path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            trace = extract_agent_trace(trace_path)

        self.assertEqual(trace["assistant_models"], ["claude-sonnet-5"])
        self.assertEqual(trace["rejected_assistant_model_count"], 1)

    def test_requested_model_requires_one_exact_sanitized_assistant_model(self) -> None:
        self.assertTrue(
            assistant_models_match_request(
                {
                    "assistant_models": ["claude-sonnet-5"],
                    "rejected_assistant_model_count": 0,
                },
                "claude-sonnet-5",
            )
        )
        for trace in (
            {"assistant_models": [], "rejected_assistant_model_count": 0},
            {"assistant_models": ["claude-opus-5"], "rejected_assistant_model_count": 0},
            {
                "assistant_models": ["claude-sonnet-5", "claude-sonnet-5-20260701"],
                "rejected_assistant_model_count": 0,
            },
            {"assistant_models": ["claude-sonnet-5"], "rejected_assistant_model_count": 1},
        ):
            self.assertFalse(assistant_models_match_request(trace, "claude-sonnet-5"))

    def test_isolated_claude_environment_links_only_credentials_and_removes_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_home = root / "original-home"
            original_config = original_home / ".claude"
            original_config.mkdir(parents=True)
            credentials = original_config / ".credentials.json"
            credentials.write_text('{"auth": "account"}', encoding="utf-8")
            (original_config / "settings.json").write_text(
                '{"hooks": {"enabled": true}}', encoding="utf-8"
            )
            isolated_root = root / "isolated"

            with patch.dict(
                os.environ,
                {
                    "HOME": str(original_home),
                    "CLAUDE_CONFIG_DIR": str(original_config),
                    "ANTHROPIC_API_KEY": "must-not-survive",
                    "OPENAI_API_KEY": "must-not-survive",
                },
                clear=False,
            ), patch(
                "experiments.skillsbench.run_model_c0_c1_pilot.Path.home",
                return_value=original_home,
            ):
                env, audit = prepare_isolated_claude_environment(isolated_root)

            isolated_config = Path(env["CLAUDE_CONFIG_DIR"])
            linked_credentials = isolated_config / ".credentials.json"
            self.assertTrue(linked_credentials.is_symlink())
            self.assertEqual(linked_credentials.resolve(), credentials.resolve())
            self.assertFalse((isolated_config / "settings.json").exists())
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertEqual(env["HOME"], str(isolated_root / "home"))
            self.assertEqual(audit["credential_link_count"], 1)
            self.assertFalse(audit["credential_content_copied"])
            self.assertTrue(audit["api_key_env_removed"])

    @patch("experiments.skillsbench.run_model_c0_c1_pilot.run_command")
    def test_process_snapshot_parser_accepts_only_idle_shell_and_sleep(
        self, run_command_mock
    ) -> None:
        run_command_mock.return_value = CommandReport(
            argv=[],
            exit_code=0,
            duration_sec=0.01,
            stdout_tail=(
                "PID PPID COMMAND COMMAND\n"
                "101 0 sh sh -lc while :; do sleep 3600; done\n"
                "102 101 sleep sleep 3600\n"
            ),
        )

        snapshot, _ = container_process_snapshot("fixed-container")

        self.assertTrue(snapshot["passed"])
        self.assertEqual([item["comm"] for item in snapshot["processes"]], ["sh", "sleep"])
        self.assertEqual(snapshot["unexpected_processes"], [])
        self.assertEqual(
            run_command_mock.call_args.args[0],
            ["docker", "top", "fixed-container", "-eo", "pid,ppid,comm,args"],
        )

    @patch("experiments.skillsbench.run_model_c0_c1_pilot.run_command")
    def test_process_snapshot_parser_rejects_agent_child_process(
        self, run_command_mock
    ) -> None:
        run_command_mock.return_value = CommandReport(
            argv=[],
            exit_code=0,
            duration_sec=0.01,
            stdout_tail=(
                "PID PPID COMMAND COMMAND\n"
                "101 0 sh sh -lc while :; do sleep 3600; done\n"
                "103 101 python python worker.py\n"
            ),
        )

        snapshot, _ = container_process_snapshot("fixed-container")

        self.assertFalse(snapshot["passed"])
        self.assertEqual(
            [item["comm"] for item in snapshot["unexpected_processes"]], ["python"]
        )

    def test_directory_manifest_is_content_and_path_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            (first / "nested").mkdir(parents=True)
            second.mkdir()
            (first / "b.txt").write_bytes(b"beta")
            (first / "nested" / "a.txt").write_bytes(b"alpha")
            (second / "nested").mkdir()
            (second / "nested" / "a.txt").write_bytes(b"alpha")
            (second / "b.txt").write_bytes(b"beta")

            first_manifest = directory_manifest(first)
            second_manifest = directory_manifest(second)
            (second / "b.txt").write_bytes(b"changed")
            changed_manifest = directory_manifest(second)

        self.assertEqual(first_manifest, second_manifest)
        self.assertEqual(first_manifest["file_count"], 2)
        self.assertEqual(first_manifest["total_bytes"], 9)
        self.assertNotEqual(first_manifest["sha256"], changed_manifest["sha256"])


if __name__ == "__main__":
    unittest.main()
