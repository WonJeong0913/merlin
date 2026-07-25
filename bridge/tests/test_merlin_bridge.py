from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BRIDGE_PATH = Path(__file__).resolve().parents[1] / "merlin_bridge.py"
SPEC = importlib.util.spec_from_file_location("merlin_bridge", BRIDGE_PATH)
assert SPEC is not None and SPEC.loader is not None
merlin_bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merlin_bridge
SPEC.loader.exec_module(merlin_bridge)


class AccountStatusTests(unittest.TestCase):
    def test_connected_status_is_normalized_without_raw_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["codex", "login", "status"],
            returncode=0,
            stdout="Logged in using ChatGPT\n",
            stderr="warning that must not escape\n",
        )
        with patch.object(
            merlin_bridge,
            "detect_codex_runtime",
            return_value=(Path("/tmp/codex"), "codex-cli test"),
        ):
            status = merlin_bridge.account_status(runner=lambda *args, **kwargs: completed)
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["auth_method"], "chatgpt")
        self.assertNotIn("stdout", status)
        self.assertNotIn("stderr", status)

    def test_missing_cli_is_safe_state(self) -> None:
        with patch.object(
            merlin_bridge,
            "detect_codex_runtime",
            side_effect=ValueError("missing"),
        ):
            status = merlin_bridge.account_status()
        self.assertEqual(status["state"], "cli_missing")
        self.assertFalse(status["connected"])


class ProtocolTests(unittest.TestCase):
    def test_hello_and_unknown_command_are_one_line_responses(self) -> None:
        source = io.StringIO(
            '{"request_id":"1","command":"bridge.hello","payload":{}}\n'
            '{"request_id":"2","command":"nope","payload":{}}\n'
        )
        sink = io.StringIO()
        self.assertEqual(merlin_bridge.serve(source, sink), 0)
        lines = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(lines[0]["event"], "bridge.hello")
        self.assertTrue(lines[0]["ok"])
        self.assertEqual(lines[1]["event"], "error.safe_stop")
        self.assertFalse(lines[1]["ok"])

    def test_account_connect_spec_is_pty_and_transient(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        with patch.object(
            merlin_bridge,
            "detect_codex_runtime",
            return_value=(Path("/tmp/codex"), "codex-cli test"),
        ):
            event, data = bridge.dispatch(
                {"command": "account.connect_spec", "payload": {}}
            )
        self.assertEqual(event, "account.connect_spec")
        self.assertEqual(data["transport"], "pty")
        self.assertEqual(data["arguments"], ["login", "--device-auth"])
        self.assertIn("do_not_persist", data["output_policy"])

    def test_account_models_uses_runtime_catalog_without_an_app_allowlist(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/tmp/fake-codex",
            "cli_version": "codex-cli test",
            "auth_method": "chatgpt",
        }
        catalog = {
            "source": "codex_app_server_model_list",
            "default_model": "gpt-6-luna",
            "models": [
                {
                    "model": "gpt-6-luna",
                    "display_name": "Luna",
                    "description": "Account-visible experimental model",
                    "is_default": True,
                    "default_effort": "high",
                    "supported_efforts": ["low", "high"],
                }
            ],
        }
        with patch.object(merlin_bridge, "account_status", return_value=connected), patch.object(
            merlin_bridge, "query_codex_models", return_value=catalog
        ):
            event, data = bridge.dispatch({"command": "account.models", "payload": {}})

        self.assertEqual(event, "account.models")
        self.assertTrue(data["available"])
        self.assertEqual(data["models"][0]["id"], "gpt-6-luna")
        self.assertEqual(data["models"][0]["supported_efforts"], ["low", "high"])
        self.assertEqual(
            set(data["models"][0]),
            {"id", "display_name", "description", "is_default", "default_effort", "supported_efforts"},
        )

    def test_account_models_falls_back_when_app_server_catalog_is_unavailable(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/tmp/fake-codex",
            "cli_version": "codex-cli test",
            "auth_method": "chatgpt",
        }
        with patch.object(merlin_bridge, "account_status", return_value=connected), patch.object(
            merlin_bridge,
            "query_codex_models",
            side_effect=merlin_bridge.CodexModelCatalogError("unavailable"),
        ):
            event, data = bridge.dispatch({"command": "account.models", "payload": {}})

        self.assertEqual(event, "account.models")
        self.assertFalse(data["available"])
        self.assertEqual(data["models"], [])

    def test_session_start_wires_real_harness_without_model_call(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/tmp/fake-codex",
            "cli_version": "codex-cli test",
            "auth_method": "chatgpt",
        }
        with tempfile.TemporaryDirectory() as workspace, patch.object(
            merlin_bridge,
            "account_status",
            return_value=connected,
        ):
            event, data = bridge.dispatch(
                {
                    "command": "session.start",
                    "payload": {
                        "workspace": workspace,
                        "model": "gpt-5.6-terra",
                        "effort": "high",
                        "routing_mode": "deterministic",
                        "autonomy_mode": "strict",
                    },
                }
            )
            self.assertEqual(event, "session.started")
            self.assertEqual(data["primary_surface"], "chat")
            status_event, status = bridge.dispatch(
                {"command": "session.status", "payload": {}}
            )
            self.assertEqual(status_event, "session.status")
            self.assertEqual(status["harness_autonomy"]["mode"], "strict")
            self.assertGreater(status["active_skill_count"], 0)
            self.assertTrue(status["declared_runtime_contract"])
            self.assertEqual(len(status["hook_contracts"]), 8)
            self.assertTrue(all(item["declared_runtime_contract"] for item in status["hook_contracts"]))
            # Evidence deliberately did not carry over from the pre-Merlin tree
            # (MIGRATION.md, "Evidence reset"). The bridge must therefore report
            # an empty, well-formed list rather than surfacing legacy artifacts
            # or fabricating Merlin-namespaced ones. Restore per-artifact
            # assertions here once real Merlin evidence exists on disk.
            self.assertIsInstance(status["recorded_evidence"], list)
            for item in status["recorded_evidence"]:
                self.assertEqual(len(item["source_sha256"]), 64)
                self.assertIn("source_path", item)
                self.assertLessEqual(item["gates_passed"], item["gates_total"])
            self.assertTrue(status["skill_contracts"])
            contract = status["skill_contracts"][0]
            self.assertEqual(
                set(contract),
                {
                    "id", "name", "status", "description", "trigger", "version",
                    "validators", "step_count", "edge_count", "expected_artifacts", "failure_modes",
                },
            )
            approval_event, approval = bridge.dispatch(
                {
                    "command": "chat.send",
                    "payload": {
                        "text": (
                            "Extract TODO entries from backlog.todo and write "
                            "todo-items.json."
                        )
                    },
                }
            )
            self.assertEqual(approval_event, "approval.required")
            self.assertTrue(approval["original_request_resumes_after_approval"])
            denied_event, denied = bridge.dispatch(
                {
                    "command": "approval.resolve",
                    "payload": {"approved": False},
                }
            )
            self.assertEqual(denied_event, "approval.declined")
            self.assertFalse(denied["original_request_executed"])

    def test_session_start_omits_model_for_the_codex_account_default(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/tmp/fake-codex",
            "cli_version": "codex-cli test",
            "auth_method": "chatgpt",
        }
        with tempfile.TemporaryDirectory() as workspace, patch.object(
            merlin_bridge,
            "account_status",
            return_value=connected,
        ):
            event, started = bridge.dispatch(
                {
                    "command": "session.start",
                    "payload": {
                        "workspace": workspace,
                        "effort": "high",
                        "routing_mode": "deterministic",
                        "autonomy_mode": "managed",
                    },
                }
            )
            self.assertEqual(event, "session.started")
            self.assertIsNone(started["model"])
            backend = bridge.state.session.backend
            command, _redacted = backend.build_first_command(
                last_message_path=Path(workspace) / "last-message.txt"
            )
            self.assertNotIn("--model", command)

    def test_session_start_uses_the_user_selected_codex_executable(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/opt/custom/bin/codex",
            "cli_version": "codex-cli custom",
            "auth_method": "chatgpt",
        }
        with tempfile.TemporaryDirectory() as workspace, patch.object(
            merlin_bridge,
            "account_status",
            return_value=connected,
        ) as status:
            event, _started = bridge.dispatch(
                {
                    "command": "session.start",
                    "payload": {
                        "workspace": workspace,
                        "executable": "/opt/custom/bin/codex",
                        "effort": "high",
                        "routing_mode": "deterministic",
                        "autonomy_mode": "managed",
                    },
                }
            )

        self.assertEqual(event, "session.started")
        status.assert_called_once_with("/opt/custom/bin/codex")
        self.assertEqual(str(bridge.state.session.backend.executable), "/opt/custom/bin/codex")

    def test_session_accepts_a_custom_safe_model_id_and_rejects_unsafe_ids(self) -> None:
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/tmp/fake-codex",
            "cli_version": "codex-cli test",
            "auth_method": "chatgpt",
        }
        with tempfile.TemporaryDirectory() as workspace, patch.object(
            merlin_bridge,
            "account_status",
            return_value=connected,
        ):
            bridge = merlin_bridge.MerlinBridge()
            event, started = bridge.dispatch(
                {
                    "command": "session.start",
                    "payload": {
                        "workspace": workspace,
                        "model": "gpt-6-luna",
                        "effort": "high",
                        "routing_mode": "deterministic",
                        "autonomy_mode": "managed",
                    },
                }
            )
            self.assertEqual(event, "session.started")
            self.assertEqual(started["model"], "gpt-6-luna")
            command, _redacted = bridge.state.session.backend.build_first_command(
                last_message_path=Path(workspace) / "last-message.txt"
            )
            self.assertIn("--model", command)
            self.assertEqual(command[command.index("--model") + 1], "gpt-6-luna")

        for unsafe in ("gpt 6", " gpt-6-luna", "gpt;rm", "a" * 129):
            bridge = merlin_bridge.MerlinBridge()
            with tempfile.TemporaryDirectory() as workspace:
                with self.assertRaisesRegex(merlin_bridge.BridgeError, "safe model ID"):
                    bridge.dispatch(
                        {
                            "command": "session.start",
                            "payload": {"workspace": workspace, "model": unsafe, "effort": "high"},
                        }
                    )

    def test_session_restart_preserves_prior_trace_directory(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/tmp/fake-codex",
            "cli_version": "codex-cli test",
            "auth_method": "chatgpt",
        }
        with tempfile.TemporaryDirectory() as workspace, patch.object(
            merlin_bridge,
            "account_status",
            return_value=connected,
        ):
            _event, started = bridge.dispatch(
                {
                    "command": "session.start",
                    "payload": {"workspace": workspace, "effort": "high", "routing_mode": "deterministic"},
                }
            )
            first_trace_root = Path(started["trace_root"])

            event, restarted = bridge.dispatch(
                {
                    "command": "session.restart",
                    "payload": {
                        "workspace": workspace,
                        "model": "gpt-6-luna",
                        "effort": "xhigh",
                        "routing_mode": "semantic",
                        "autonomy_mode": "strict",
                    },
                }
            )

            self.assertEqual(event, "session.restarted")
            self.assertEqual(restarted["model"], "gpt-6-luna")
            self.assertEqual(restarted["previous_trace_root"], str(first_trace_root))
            self.assertNotEqual(restarted["trace_root"], str(first_trace_root))
            self.assertTrue(first_trace_root.is_dir())
            self.assertTrue(Path(restarted["trace_root"]).is_dir())

    def test_thread_resume_and_setting_update_preserve_thread_and_trace(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/tmp/fake-codex",
            "cli_version": "codex-cli test",
            "auth_method": "chatgpt",
        }
        with tempfile.TemporaryDirectory() as workspace, patch.object(
            merlin_bridge,
            "account_status",
            return_value=connected,
        ):
            _event, started = bridge.dispatch({
                "command": "session.start",
                "payload": {
                    "workspace": workspace,
                    "effort": "low",
                    "routing_mode": "deterministic",
                    "autonomy_mode": "managed",
                },
            })
            trace_root = started["trace_root"]
            resume_event, resume = bridge.dispatch({
                "command": "session.resume_thread",
                "payload": {"thread_id": "thread-retained-1"},
            })
            self.assertEqual(resume_event, "session.resume_thread")
            self.assertFalse(resume["provider_resume_verified"])

            update_event, updated = bridge.dispatch({
                "command": "session.update_settings",
                "payload": {
                    "model": "gpt-6-luna",
                    "effort": "high",
                    "routing_mode": "semantic",
                    "autonomy_mode": "strict",
                },
            })

            self.assertEqual(update_event, "session.settings_updated")
            self.assertTrue(updated["provider_thread_preserved"])
            self.assertTrue(updated["trace_root_preserved"])
            self.assertEqual(bridge.state.trace_root, Path(trace_root))
            self.assertEqual(bridge.state.session.thread_id, "thread-retained-1")
            self.assertEqual(bridge.state.session.backend.model_id, "gpt-6-luna")
            self.assertEqual(bridge.state.session.backend.effort, "high")
            self.assertEqual(bridge.state.session.routing_mode, "semantic")
            self.assertEqual(bridge.state.governor.approval_mode, "strict")

    def test_explicit_skill_slash_command_is_parsed_without_library_mutation(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        connected = {
            "state": "connected",
            "connected": True,
            "executable": "/tmp/fake-codex",
            "cli_version": "codex-cli test",
            "auth_method": "chatgpt",
        }
        with tempfile.TemporaryDirectory() as workspace, patch.object(
            merlin_bridge,
            "account_status",
            return_value=connected,
        ):
            bridge.dispatch({
                "command": "session.start",
                "payload": {
                    "workspace": workspace,
                    "effort": "low",
                    "routing_mode": "deterministic",
                    "autonomy_mode": "managed",
                },
            })
            session = bridge.state.session
            self.assertIsNotNone(session)
            response = merlin_bridge.ChatResponse(
                answer="done",
                thread_id="thread-1",
                turn_id="turn-1",
                turn_number=1,
                provisioned_skills=(),
                routing_decision={
                    "routing_source": "explicit_skill",
                    "final_provisioned_ids": ["file-artifact-basic"],
                },
                raw_trace_pointer="turn-0001.codex.jsonl",
            )
            with patch.object(session, "send", return_value=response) as send:
                event, data = bridge.dispatch({
                    "command": "chat.send",
                    "payload": {
                        "text": "/skill file-artifact-basic create result.txt",
                    },
                })

            self.assertEqual(event, "chat.completed")
            self.assertEqual(data["answer"], "done")
            send.assert_called_once_with(
                "create result.txt",
                explicit_skill_id="file-artifact-basic",
            )
            with self.assertRaisesRegex(merlin_bridge.BridgeError, "usage"):
                bridge.dispatch({
                    "command": "chat.send",
                    "payload": {"text": "/skill missing-request"},
                })


class HarnessGovernanceTests(unittest.TestCase):
    def test_governance_is_available_without_a_session(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        event, data = bridge.dispatch(
            {"command": "harness.governance", "payload": {}}
        )
        self.assertEqual(event, "harness.governance")
        self.assertEqual(
            set(data),
            {
                "campaign",
                "evolution",
                "invocation_evidence",
                "lifecycle_operations",
                "evidence_boundary",
            },
        )

    def test_governance_payload_matches_the_core_view(self) -> None:
        # The bridge is a transport for the core view, not a second
        # implementation of it. Detailed governance behaviour is covered by
        # tests/test_governance_view.py.
        bridge = merlin_bridge.MerlinBridge()
        _event, data = bridge.dispatch(
            {"command": "harness.governance", "payload": {}}
        )
        self.assertEqual(data, merlin_bridge.harness_governance_summary())

    def test_governance_does_not_require_an_account(self) -> None:
        bridge = merlin_bridge.MerlinBridge()
        with patch.object(
            merlin_bridge,
            "account_status",
            side_effect=AssertionError("governance must not query the account"),
        ):
            _event, data = bridge.dispatch(
                {"command": "harness.governance", "payload": {}}
            )
        self.assertIn("campaign", data)


if __name__ == "__main__":
    unittest.main()
