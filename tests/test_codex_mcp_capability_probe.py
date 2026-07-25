from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from experiments.skillsbench.probe_codex_mcp_capability import (
    CapabilityProbeError,
    compute_readiness,
    detect_codex_executable,
    inspect_codex_app_server_schemas,
    probe_codex_cli,
    probe_codex_feature_suppression,
    probe_direct_mcp_server,
    summarize_recorded_audit,
    write_report,
)


class CodexMcpCapabilityProbeTests(unittest.TestCase):
    def test_auto_detection_skips_executable_broken_path_shim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broken = root / "broken-codex"
            broken.write_text("#!/definitely/missing/interpreter\n", encoding="utf-8")
            broken.chmod(0o755)
            working = root / "bundled-codex"
            working.write_text("#!/bin/sh\nprintf 'codex-cli test\\n'\n", encoding="utf-8")
            working.chmod(0o755)
            with (
                mock.patch(
                    "experiments.skillsbench.probe_codex_mcp_capability.shutil.which",
                    return_value=str(broken),
                ),
                mock.patch(
                    "experiments.skillsbench.probe_codex_mcp_capability.DEFAULT_CODEX_CANDIDATES",
                    (working,),
                ),
            ):
                self.assertEqual(detect_codex_executable(), working.resolve())

            with self.assertRaisesRegex(CapabilityProbeError, "not runnable"):
                detect_codex_executable(broken)

    def test_direct_server_handshake_exposes_only_bounded_exec(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        result = probe_direct_mcp_server(
            repo / "experiments" / "skillsbench" / "container_exec_mcp.py",
            python_executable=Path(sys.executable),
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["tool_names"], ["exec"])
        self.assertEqual(result["tool_argument_names"], ["command", "timeout_sec"])
        self.assertFalse(result["boundary_override_arguments_exposed"])
        self.assertFalse(result["tools_call_performed"])

    def test_current_style_cli_help_keeps_missing_tool_controls_explicit(self) -> None:
        def fake_runner(argv, **_kwargs):
            if argv[-1] == "--version":
                stdout = "codex-cli 0.test\n"
            elif argv[1:3] == ["exec", "--help"]:
                stdout = (
                    "--config --strict-config --sandbox read-only --ephemeral "
                    "--ignore-user-config --ignore-rules --json"
                )
            else:
                stdout = "--config --json"
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        report = probe_codex_cli(Path("/fake/codex"), runner=fake_runner)
        flags = report["capability_flags"]
        self.assertTrue(flags["per_run_config_override"])
        self.assertTrue(flags["ignore_user_config"])
        self.assertTrue(flags["ignore_rules"])
        self.assertFalse(flags["native_tool_allowlist"])
        self.assertFalse(flags["native_tool_denylist"])
        self.assertFalse(flags["strict_mcp_config"])

    def test_feature_suppression_is_recorded_without_becoming_tool_inventory_proof(self) -> None:
        def fake_runner(argv, **_kwargs):
            disabled = [
                argv[index + 1]
                for index, value in enumerate(argv[:-1])
                if value == "--disable"
            ]
            stdout = "".join(
                f"{feature} stable false\n" for feature in disabled
            )
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        report = probe_codex_feature_suppression(
            Path("/fake/codex"),
            runner=fake_runner,
        )

        self.assertTrue(report["provided"])
        self.assertTrue(report["all_requested_features_disabled"])
        self.assertIn("shell_tool", report["observed_disabled_features"])
        self.assertIn("unified_exec", report["observed_disabled_features"])
        self.assertFalse(report["feature_listing_is_runtime_tool_inventory_proof"])
        self.assertFalse(report["feature_listing_is_model_execution"])

    def test_app_server_schema_does_not_promote_additive_dynamic_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            v2 = root / "v2"
            v2.mkdir()
            (v2 / "ConfigReadResponse.json").write_text(
                json.dumps(
                    {
                        "definitions": {
                            "Config": {
                                "properties": {
                                    "approval_policy": {},
                                    "sandbox_mode": {},
                                    "tools": {},
                                }
                            },
                            "ToolsV2": {"properties": {"web_search": {}}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (v2 / "ThreadStartParams.json").write_text(
                json.dumps({"properties": {"dynamicTools": {}, "sandbox": {}}}),
                encoding="utf-8",
            )
            (v2 / "TurnStartParams.json").write_text(
                json.dumps({"properties": {"sandboxPolicy": {}}}),
                encoding="utf-8",
            )

            report = inspect_codex_app_server_schemas(root)

        self.assertTrue(report["provided"])
        self.assertTrue(report["thread_start_has_dynamic_tools"])
        self.assertFalse(report["turn_start_has_dynamic_tools"])
        self.assertEqual(report["config_tools_property_names"], ["web_search"])
        self.assertFalse(report["native_tool_allowlist_schema_key"])
        self.assertFalse(report["native_tool_denylist_schema_key"])
        self.assertFalse(report["strict_mcp_config_schema_key"])
        self.assertFalse(report["schema_is_executor_restriction_proof"])

    def test_audit_summary_observes_exec_without_copying_arguments_or_output(self) -> None:
        events = [
            {
                "direction": "client_to_server",
                "event": "request",
                "method": "initialize",
            },
            {
                "direction": "server_to_client",
                "event": "response",
                "negotiated_protocol_version": "2025-06-18",
            },
            {
                "direction": "client_to_server",
                "event": "request",
                "method": "tools/list",
            },
            {
                "direction": "server_to_client",
                "event": "response",
                "tool_count": 1,
            },
            {
                "direction": "client_to_server",
                "event": "request",
                "method": "tools/call",
                "tool_name": "exec",
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.jsonl"
            path.write_text(
                "".join(json.dumps(item) + "\n" for item in events),
                encoding="utf-8",
            )
            report = summarize_recorded_audit(path)

        self.assertTrue(report["initialize_observed"])
        self.assertTrue(report["tools_list_observed"])
        self.assertTrue(report["exec_tool_call_observed"])
        self.assertEqual(report["exec_tool_call_count"], 1)
        self.assertEqual(report["observed_tool_counts"], [1])
        self.assertFalse(report["raw_arguments_or_tool_output_copied"])
        self.assertNotIn("events", report)

    def test_readiness_fails_closed_until_every_strict_control_is_proven(self) -> None:
        cli = {
            "capability_flags": {
                "per_run_config_override": True,
                "ignore_user_config": True,
                "ignore_rules": True,
                "ephemeral": True,
                "json_events": True,
                "read_only_sandbox": True,
                "native_tool_allowlist": False,
                "native_tool_denylist": False,
                "strict_mcp_config": False,
            }
        }
        result = compute_readiness(
            cli=cli,
            direct_mcp={"passed": True},
            recorded_audit={"exec_tool_call_observed": True},
            container_runtime={"container_inspect_passed": True},
        )

        self.assertFalse(result["strict_benchmark_bridge_eligible"])
        self.assertFalse(result["six_cell_execution_allowed"])
        self.assertIn("native_tool_allowlist_available", result["failed_required_checks"])
        self.assertIn("strict_mcp_config_available", result["failed_required_checks"])
        self.assertFalse(result["handshake_only_is_benchmark_evidence"])
        self.assertFalse(result["this_probe_is_model_execution"])

    def test_readiness_can_open_only_when_all_strict_evidence_is_true(self) -> None:
        cli = {
            "capability_flags": {
                "per_run_config_override": True,
                "ignore_user_config": True,
                "ignore_rules": True,
                "ephemeral": True,
                "json_events": True,
                "read_only_sandbox": True,
                "native_tool_allowlist": True,
                "native_tool_denylist": True,
                "strict_mcp_config": True,
            }
        }
        result = compute_readiness(
            cli=cli,
            direct_mcp={"passed": True},
            recorded_audit={"exec_tool_call_observed": True},
            container_runtime={"container_inspect_passed": True},
        )

        self.assertTrue(result["strict_benchmark_bridge_eligible"])
        self.assertTrue(result["six_cell_execution_allowed"])
        self.assertEqual(result["failed_required_checks"], [])

    def test_report_writer_is_new_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.json"
            write_report(path, {"schema_version": 1, "safe": True})
            self.assertEqual(json.loads(path.read_text()), {"safe": True, "schema_version": 1})
            with self.assertRaisesRegex(CapabilityProbeError, "already exists"):
                write_report(path, {"schema_version": 1})

    def test_packaged_local_diagnostic_remains_non_result_and_fail_closed(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        path = (
            repo
            / "experiments"
            / "skillsbench"
            / "results"
            / "codex-mcp-capability-local-20260719.json"
        )
        report = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(report["diagnostic"], "codex_mcp_capability")
        self.assertTrue(report["direct_mcp_server"]["passed"])
        self.assertFalse(report["recorded_mcp_audit"]["exec_tool_call_observed"])
        self.assertFalse(report["recorded_mcp_audit"]["raw_audit_packaged"])
        self.assertFalse(report["readiness"]["six_cell_execution_allowed"])
        self.assertFalse(report["readiness"]["this_probe_is_model_execution"])
        self.assertFalse(report["readiness"]["this_probe_is_benchmark_result"])


if __name__ == "__main__":
    unittest.main()
