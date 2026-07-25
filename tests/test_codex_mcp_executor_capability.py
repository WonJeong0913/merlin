from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    validate_executor_capability,
)
from experiments.skillsbench.compose_codex_mcp_executor_capability import (
    ExecutorCapabilityCompositionError,
    compose_executor_capability,
    write_executor_capability,
)
from experiments.skillsbench.probe_codex_mcp_capability import (
    NATIVE_TOOL_FEATURES_TO_DISABLE,
)
from src.merlin_harness.management import content_sha256


class CodexMcpExecutorCapabilityTests(unittest.TestCase):
    @staticmethod
    def _preflight() -> dict:
        return {
            "schema_version": 2,
            "diagnostic": "codex_mcp_capability",
            "codex_cli": {
                "version": "codex-cli synthetic",
                "version_sha256": "a" * 64,
                "capability_flags": {
                    "per_run_config_override": True,
                    "strict_config": True,
                    "ignore_user_config": True,
                    "ignore_rules": True,
                    "ephemeral": True,
                    "json_events": True,
                    "read_only_sandbox": False,
                    "native_tool_allowlist": False,
                    "native_tool_denylist": False,
                    "strict_mcp_config": False,
                },
            },
            "direct_mcp_server": {
                "passed": True,
                "tool_count": 1,
                "tool_names": ["exec"],
                "tool_argument_names": ["command", "timeout_sec"],
                "boundary_override_arguments_exposed": False,
                "tools_call_performed": False,
            },
            "native_tool_feature_suppression": {
                "provided": True,
                "requested_disabled_features": list(NATIVE_TOOL_FEATURES_TO_DISABLE),
                "observed_disabled_features": list(NATIVE_TOOL_FEATURES_TO_DISABLE),
                "all_requested_features_disabled": True,
                "features_list_sha256": "b" * 64,
                "feature_listing_is_runtime_tool_inventory_proof": False,
                "feature_listing_is_model_execution": False,
            },
        }

    @staticmethod
    def _canary() -> dict:
        value = {
            "schema_version": 1,
            "diagnostic": "codex_mcp_only_boundary_canary",
            "status": "passed",
            "requested_model_id": "gpt-5.6-terra",
            "requested_effort": "low",
            "provider_reported_model_ids": [],
            "model_evidence_level": "requested_cli_contract_only",
            "feature_suppression": {
                "requested_count": len(NATIVE_TOOL_FEATURES_TO_DISABLE),
                "observed_disabled_count": len(NATIVE_TOOL_FEATURES_TO_DISABLE),
                "all_requested_features_disabled": True,
                "features_list_sha256": "c" * 64,
            },
            "runtime_observation": {
                "codex_event_count": 5,
                "item_type_counts": {"mcp_tool_call": 2},
                "mcp_initialize_observed": True,
                "mcp_tools_list_observed": True,
                "mcp_tool_count": 1,
                "mcp_exec_call_count": 1,
                "forbidden_native_tool_item_types": [],
            },
            "source_hashes": {
                "codex_jsonl_sha256": "d" * 64,
                "mcp_audit_sha256": "e" * 64,
                "response_schema_sha256": "f" * 64,
                "last_message_sha256": "1" * 64,
                "command_contract_sha256": "2" * 64,
            },
            "claim_boundary": {
                "this_is_model_execution": True,
                "this_is_benchmark_execution": False,
                "this_is_task_utility_evidence": False,
                "container_execution_succeeded": False,
                "native_tool_inventory_absence_proven": False,
                "native_tool_execution_observed": False,
                "feature_listing_is_runtime_tool_inventory_proof": False,
                "codex_host_sandbox_enabled": False,
                "external_fixed_container_is_required_boundary": True,
                "raw_arguments_or_tool_output_packaged": False,
                "six_cell_execution_allowed": False,
            },
        }
        value["diagnostic_sha256"] = content_sha256(value)
        return value

    def _sources(self, root: Path) -> tuple[Path, Path, Path]:
        preflight = root / "preflight.json"
        canary = root / "canary.json"
        inspect = root / "container-inspect.json"
        preflight.write_text(json.dumps(self._preflight(), sort_keys=True) + "\n")
        canary.write_text(json.dumps(self._canary(), sort_keys=True) + "\n")
        inspect.write_text(
            json.dumps(
                [{"Id": "container-1", "Image": "image-1", "State": {"Running": True}}],
                sort_keys=True,
            )
            + "\n"
        )
        return preflight, canary, inspect

    def test_composed_v3_capability_opens_one_cell_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, canary, inspect = self._sources(root)
            report = compose_executor_capability(
                preflight_path=preflight,
                boundary_canary_path=canary,
                container_inspect_path=inspect,
                requested_model_id="gpt-5.6-terra",
                requested_effort="low",
            )
            eligible, failures, summary = validate_executor_capability(report)

            self.assertTrue(eligible)
            self.assertEqual(failures, [])
            self.assertTrue(summary["one_cell_execution_allowed"])
            self.assertFalse(summary["six_cell_execution_allowed"])
            self.assertTrue(
                summary["additional_pilot_cells_require_validated_first_cell"]
            )
            output = root / "executor-capability.json"
            write_executor_capability(output, report)
            self.assertEqual(json.loads(output.read_text()), report)

    def test_canary_tamper_and_capability_rehash_cannot_open_six_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, canary, inspect = self._sources(root)
            tampered_canary = json.loads(canary.read_text())
            tampered_canary["runtime_observation"]["mcp_exec_call_count"] = 2
            canary.write_text(json.dumps(tampered_canary, sort_keys=True) + "\n")
            with self.assertRaisesRegex(
                ExecutorCapabilityCompositionError, "semantic hash mismatch"
            ):
                compose_executor_capability(
                    preflight_path=preflight,
                    boundary_canary_path=canary,
                    container_inspect_path=inspect,
                    requested_model_id="gpt-5.6-terra",
                    requested_effort="low",
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, canary, inspect = self._sources(root)
            report = compose_executor_capability(
                preflight_path=preflight,
                boundary_canary_path=canary,
                container_inspect_path=inspect,
                requested_model_id="gpt-5.6-terra",
                requested_effort="low",
            )
            tampered = copy.deepcopy(report)
            tampered["readiness"]["six_cell_execution_allowed"] = True
            tampered.pop("capability_sha256")
            tampered["capability_sha256"] = content_sha256(tampered)
            eligible, failures, _summary = validate_executor_capability(tampered)
            self.assertFalse(eligible)
            self.assertIn("six_cell_must_require_first_cell_evidence", failures)


if __name__ == "__main__":
    unittest.main()
