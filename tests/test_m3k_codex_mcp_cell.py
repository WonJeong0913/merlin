from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from experiments.skillsbench.probe_codex_mcp_capability import (
    NATIVE_TOOL_FEATURES_TO_DISABLE,
)
from experiments.skillsbench.bind_m3k_proposal_manifest import (
    REQUIRED_CAPABILITY_CHECKS,
)
from experiments.skillsbench.run_m3k_codex_mcp_cell import (
    M3KCodexCellError,
    _audit_skill_ids,
    _docker_image_name,
    _token_cost,
    build_codex_command,
    derive_provisioning,
    validate_admission_binding,
    validate_executor_binding,
    validate_materialized_corpus_binding,
    resolve_m3k_operator_cell,
)
from experiments.skillsbench.materialize_m3k_external_cell import (
    _copy_skill_library,
)
from experiments.skillsbench.create_library_scale_manifest import tree_sha256
from src.merlin_harness.harness import make_default_harness_runtime, snapshot_harness_variant
from src.merlin_harness.management import content_sha256


class M3KCodexMcpCellTests(unittest.TestCase):
    @staticmethod
    def _eligible_capability() -> dict:
        return {
            "schema_version": 2,
            "diagnostic": "codex_mcp_capability",
            "readiness": {
                "checks": {name: True for name in REQUIRED_CAPABILITY_CHECKS},
                "strict_benchmark_bridge_eligible": True,
                "six_cell_execution_allowed": True,
                "this_probe_is_model_execution": False,
                "this_probe_is_benchmark_result": False,
                "handshake_only_is_benchmark_evidence": False,
            },
            "direct_mcp_server": {
                "tool_names": ["exec"],
                "tool_count": 1,
                "boundary_override_arguments_exposed": False,
            },
            "container_runtime": {
                "container_id_provided": True,
                "container_inspect_passed": True,
            },
        }

    def test_executor_binding_requires_exact_real_capability_and_model_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capability_path = root / "capability.json"
            capability_bytes = (
                json.dumps(self._eligible_capability(), sort_keys=True) + "\n"
            ).encode("utf-8")
            capability_path.write_bytes(capability_bytes)
            bound_path = root / "bound.json"
            bound_path.write_text(
                json.dumps(
                    {
                        "status": "ready",
                        "executor_capability": {
                            "provided": True,
                            "eligible": True,
                            "file_sha256": hashlib.sha256(capability_bytes).hexdigest(),
                        },
                        "execution_gate": {"execution_allowed": True},
                        "evaluation_contract": {
                            "backend": "strict-container-agent-executor-unbound",
                            "model_id": "gpt-5.6-terra",
                            "effort": "high",
                            "tools": ["fixed-container-exec"],
                        },
                    }
                ),
                encoding="utf-8",
            )

            _, sealed_capability_bytes, summary = validate_executor_binding(
                bound_manifest_path=bound_path,
                executor_capability_path=capability_path,
                model="gpt-5.6-terra",
                effort="high",
            )
            self.assertEqual(json.loads(sealed_capability_bytes)["schema_version"], 2)
            self.assertTrue(summary["strict_benchmark_bridge_eligible"])
            with self.assertRaisesRegex(M3KCodexCellError, "model/tool contract"):
                validate_executor_binding(
                    bound_manifest_path=bound_path,
                    executor_capability_path=capability_path,
                    model="gpt-5.6-terra",
                    effort="xhigh",
                )

            capability_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(M3KCodexCellError, "hash drifted"):
                validate_executor_binding(
                    bound_manifest_path=bound_path,
                    executor_capability_path=capability_path,
                    model="gpt-5.6-terra",
                    effort="high",
                )

    def test_pilot_ordinals_two_through_six_require_replayed_first_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            cells = []
            for ordinal in range(1, 7):
                cell = bundle / f"cell-{ordinal}"
                cell.mkdir()
                cells.append(
                    {
                        "cell_pointer": cell.name,
                        "execution_contract_sha256": str(ordinal) * 64,
                    }
                )
            operator_manifest = {
                "cells": cells,
                "operator_bundle_sha256": "a" * 64,
                "source": {"pilot_manifest_sha256": "b" * 64},
            }
            contract = {
                "execution_contract_sha256": "2" * 64,
                "trajectory": {"trajectory_id": "pilot-second"},
            }
            common = {
                "bound_manifest_path": root / "bound.json",
                "library_scale_manifest_path": root / "scale.json",
                "pilot_manifest_path": root / "pilot.json",
                "evidence_root": root / "evidence",
                "operator_bundle": bundle,
                "ordinal": 2,
            }
            with (
                mock.patch(
                    "experiments.skillsbench.run_m3k_codex_mcp_cell."
                    "validate_m3k_pilot_operator_bundle",
                    return_value=operator_manifest,
                ),
                mock.patch(
                    "experiments.skillsbench.run_m3k_codex_mcp_cell."
                    "validate_materialized_m3k_cell",
                    return_value=contract,
                ),
            ):
                with self.assertRaisesRegex(
                    M3KCodexCellError, "require a validated ordinal-1 report"
                ):
                    resolve_m3k_operator_cell(**common)

                first_report_path = root / "first-cell-report.json"
                first_report_path.write_text("{}\n", encoding="utf-8")
                first_report = {
                    "report_sha256": "c" * 64,
                    "first_cell": {"trajectory_id": "pilot-first"},
                }
                with mock.patch(
                    "experiments.skillsbench.run_m3k_codex_mcp_cell."
                    "validate_m3k_first_cell_report",
                    return_value=first_report,
                ) as validator:
                    resolved = resolve_m3k_operator_cell(
                        **common, first_cell_report_path=first_report_path
                    )
                validator.assert_called_once()
                self.assertEqual(resolved["ordinal"], 2)
                self.assertEqual(
                    resolved["operator_source"]["first_cell_gate"][
                        "ordinal_1_trajectory_id"
                    ],
                    "pilot-first",
                )

            ordinal_one = dict(common)
            ordinal_one["ordinal"] = 1
            with self.assertRaisesRegex(M3KCodexCellError, "must run before"):
                resolve_m3k_operator_cell(
                    **ordinal_one, first_cell_report_path=first_report_path
                )

    def test_admission_binding_requires_exact_lease_and_snapshot_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot_path = root / "snapshot.json"
            snapshot = {
                "schema_version": 1,
                "entries_sha256": "a" * 64,
                "entry_count": 500,
                "external_pinned_corpus": {
                    "upstream_commit": "b" * 40,
                    "expected_manifest_sha256": "e" * 64,
                    "corpus_provenance_file_sha256": "f" * 64,
                    "regular_blob_count": 2160,
                },
            }
            snapshot_bytes = (json.dumps(snapshot, sort_keys=True) + "\n").encode()
            snapshot_path.write_bytes(snapshot_bytes)
            command_sha = "c" * 64
            external_corpus = {
                "schema_version": 1,
                "diagnostic": "external_task_corpus_admission",
                "source_snapshot_manifest_sha256": hashlib.sha256(
                    snapshot_bytes
                ).hexdigest(),
                "corpus_provenance_file_sha256": "f" * 64,
                "upstream_commit": "b" * 40,
                "upstream_head": "b" * 40,
                "regular_blob_count": 2160,
                "expected_manifest_sha256": "e" * 64,
                "local_manifest_sha256": "e" * 64,
                "gitlinks": [],
                "tasks_root_path_sha256": "1" * 64,
                "verification": {
                    "regular_blobs_exact": True,
                    "gitlink_placeholders_present": True,
                    "task_tree_has_no_symlinks": True,
                    "task_tree_is_outside_source_snapshot": True,
                },
                "claim_boundary": {
                    "corpus_verification_is_model_execution": False,
                    "corpus_verification_is_benchmark_result": False,
                    "external_task_bytes_are_not_source_snapshot_entries": True,
                    "materializer_must_use_this_external_tasks_root": True,
                },
            }
            external_corpus["report_sha256"] = content_sha256(external_corpus)
            start_path = root / "start.json"
            start = {
                "schema_version": 1,
                "diagnostic": "desktop_host_admission",
                "started_unix": 1.0,
                "global_lock_path_sha256": "d" * 64,
                "legacy_runs": [{"pid_alive": False, "lock_held": False}],
                "docker": {"running_container_count": 0, "running_containers": []},
                "source_snapshot": {
                    "manifest_file_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                    "entries_sha256": "a" * 64,
                    "entry_count": 500,
                    "pinned_upstream_commit": "b" * 40,
                },
                "external_task_corpus": external_corpus,
                "command_sha256": command_sha,
                "command_recorded": False,
            }
            start_bytes = (json.dumps(start, sort_keys=True) + "\n").encode()
            start_path.write_bytes(start_bytes)
            summary = validate_admission_binding(
                admission_start_audit_path=start_path,
                source_snapshot_manifest_path=snapshot_path,
                expected_start_sha256=hashlib.sha256(start_bytes).hexdigest(),
                expected_command_sha256=command_sha,
            )[2]
            self.assertEqual(summary["source_snapshot_entry_count"], 500)
            self.assertEqual(summary["external_corpus_regular_blob_count"], 2160)
            self.assertEqual(
                summary["external_corpus_tasks_root_path_sha256"], "1" * 64
            )
            start["legacy_runs"][0]["pid_alive"] = True
            drifted = (json.dumps(start, sort_keys=True) + "\n").encode()
            start_path.write_bytes(drifted)
            with self.assertRaisesRegex(M3KCodexCellError, "legacy manager"):
                validate_admission_binding(
                    admission_start_audit_path=start_path,
                    source_snapshot_manifest_path=snapshot_path,
                    expected_start_sha256=hashlib.sha256(drifted).hexdigest(),
                    expected_command_sha256=command_sha,
                )

    def test_materialized_corpus_must_equal_live_admission(self) -> None:
        corpus = {
            "upstream_commit": "a" * 40,
            "corpus_provenance_file_sha256": "b" * 64,
            "expected_manifest_sha256": "c" * 64,
            "local_manifest_sha256": "c" * 64,
            "regular_blob_count": 2160,
            "tasks_root_path_sha256": "d" * 64,
            "runtime_admission_must_match": True,
        }
        admission = {
            "pinned_upstream_commit": "a" * 40,
            "external_corpus_provenance_file_sha256": "b" * 64,
            "external_corpus_manifest_sha256": "c" * 64,
            "external_corpus_regular_blob_count": 2160,
            "external_corpus_tasks_root_path_sha256": "d" * 64,
        }
        validate_materialized_corpus_binding(
            contract={"task_corpus_source": corpus},
            admission_summary=admission,
        )
        corpus["local_manifest_sha256"] = "d" * 64
        with self.assertRaisesRegex(M3KCodexCellError, "differs from live"):
            validate_materialized_corpus_binding(
                contract={"task_corpus_source": corpus},
                admission_summary=admission,
            )

    def test_command_is_one_mcp_feature_suppressed_and_container_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = build_codex_command(
                codex_executable=root / "codex",
                server_path=root / "container_exec_mcp.py",
                raw_root=root,
                container_id="a" * 64,
                container_workdir="/root",
                allowed_skill_ids_file=root / "allowed.json",
                model="gpt-5.6-terra",
                effort="high",
                timeout_sec=900,
            )

        disabled = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--disable"
        ]
        self.assertEqual(disabled, list(NATIVE_TOOL_FEATURES_TO_DISABLE))
        self.assertIn("--strict-config", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertNotIn("--sandbox", command)
        server_args = next(
            value for value in command if value.startswith("mcp_servers.merlin_harness_task.args=")
        )
        self.assertIn("a" * 64, server_args)
        self.assertIn("--allowed-skill-ids-file", server_args)
        self.assertIn("mcp_servers.merlin_harness_task.required=true", command)

    def test_skill_materialization_excludes_runtime_cache_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "mesh-analysis"
            cache = source / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (source / "SKILL.md").write_text("# mesh\n", encoding="utf-8")
            (source / "scripts" / "solve.py").write_text(
                "print('ok')\n", encoding="utf-8"
            )
            (cache / "solve.pyc").write_bytes(b"runtime-only")
            destination = root / "staged"

            records, combined = _copy_skill_library(
                variant_ids=["mesh-analysis"],
                skills_root=root / "source",
                destination=destination,
            )

            package = destination / "mesh-analysis"
            self.assertFalse((package / "scripts" / "__pycache__").exists())
            self.assertEqual(records[0]["source_tree_sha256"], tree_sha256(package))
            self.assertEqual(records[0]["staged_tree_sha256"], tree_sha256(package))
            self.assertEqual(len(combined), 64)

    def test_provisioning_reconstructs_variant_and_binds_oracle_without_exposing_all(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            order = [f"skill-{index:03d}" for index in range(209)]
            for index, skill_id in enumerate(order):
                folder = root / "skills" / skill_id
                folder.mkdir(parents=True)
                description = (
                    "Parse binary STL mesh and calculate connected component volume"
                    if index == 0
                    else f"Unrelated generic capability number {index}"
                )
                (folder / "SKILL.md").write_text(
                    f"---\nname: {skill_id}\ndescription: {description}\n---\n\n# {skill_id}\n",
                    encoding="utf-8",
                )
            (root / "task-visible").mkdir()
            (root / "task-visible" / "task.md").write_text(
                "---\nmetadata:\n  difficulty: hard\n---\n\nParse a binary STL mesh and calculate its volume.",
                encoding="utf-8",
            )
            variant = snapshot_harness_variant(
                make_default_harness_runtime(max_exposure_budget=1),
                variant_id="bounded-harness",
                summary="one-skill exposure",
            )
            variant_payload = asdict(variant)
            (root / "harness-variant.json").write_text(
                json.dumps(variant_payload), encoding="utf-8"
            )
            (root / "attestation.template.json").write_text(
                json.dumps({"oracle_skill_ids": ["skill-000"]}), encoding="utf-8"
            )
            contract = {
                "trajectory": {
                    "task_id": "3d-scan-calc",
                    "verifier_id": "v" * 64,
                    "task_instruction_sha256": "t" * 64,
                    "library_order_sha256": "o" * 64,
                },
                "staged_artifacts": {"presentation_order": order},
            }

            result = derive_provisioning(root, contract)

        self.assertEqual(result["variant_id"], "bounded-harness")
        self.assertEqual(result["variant_sha256"], content_sha256(variant_payload))
        self.assertEqual(result["effective_exposure_budget"], 1)
        self.assertEqual(result["provisioned_skill_ids"], ["skill-000"])
        self.assertEqual(result["oracle_skill_ids"], ["skill-000"])
        self.assertTrue(result["boundary"]["provisioned_ids_are_not_invocation_evidence"])
        self.assertFalse(result["boundary"]["provider_native_skill_invocation_claimed"])

    def test_mcp_audit_derives_only_allowed_skill_associated_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audit = Path(temporary) / "audit.jsonl"
            audit.write_text(
                "".join(
                    json.dumps(event) + "\n"
                    for event in (
                        {"method": "tools/call", "tool_name": "exec"},
                        {
                            "method": "tools/call",
                            "tool_name": "exec",
                            "skill_id": "mesh-analysis",
                        },
                        {
                            "method": "tools/call",
                            "tool_name": "exec",
                            "skill_id": "mesh-analysis",
                        },
                    )
                ),
                encoding="utf-8",
            )
            invoked, calls = _audit_skill_ids(audit, ["mesh-analysis"])
        self.assertEqual(calls, 3)
        self.assertEqual(invoked, ["mesh-analysis"])

    def test_token_cost_and_image_identity_are_deterministic(self) -> None:
        raw = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "t"}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 12, "output_tokens": 8},
                    }
                ),
            )
        )
        self.assertEqual(_token_cost(raw), 20.0)
        contract = {
            "trajectory": {"task_id": "3D Scan Calc"},
            "staged_artifacts": {
                "task_environment": {"records_sha256": "a" * 64}
            },
        }
        self.assertEqual(_docker_image_name(contract), "theking-m3k-3d-scan-calc:aaaaaaaaaaaa")


if __name__ == "__main__":
    unittest.main()
