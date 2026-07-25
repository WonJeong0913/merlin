from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.aggregate_library_scale_results import (
    LibraryScaleAggregationError,
    aggregate_library_scale_run,
    load_empirical_oracle_mapping,
    main,
)
from experiments.skillsbench.bind_empirical_oracle_manifest import (
    build_oracle_bound_manifest_from_files,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file, sha256_json
from experiments.skillsbench.materialize_library_scale_cell import materialize_library_scale_cell
from src.merlin_harness.models import (
    AgentRunContract,
    AgentRunResult,
    InvocationRecord,
    RawTraceReference,
    SkillInvocationEvent,
    TraceRecord,
    ValidationResult,
)
from src.merlin_harness.traces import AGENT_TRACE_EVIDENCE_KEY, FileTraceStore, serialize_agent_run_evidence


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "experiments" / "skillsbench" / "library-scale-manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
CELL = MANIFEST["cells"][0]


def _save_one_trace(
    root: Path,
    materialization: dict,
    *,
    cell: dict = CELL,
) -> Path:
    raw_root = root / "raw"
    raw_root.mkdir()
    raw_path = raw_root / "turn.jsonl"
    raw_path.write_text("provider trace\n", encoding="utf-8")
    workspace = root / "workspace"
    workspace.mkdir()
    verifier_id = cell["verifier_contract_sha256"]
    invoked = list(cell["library_variant_ids"][:1])
    contract = AgentRunContract(
        run_id="file-backed-scale-test",
        task_id=cell["task_id"],
        condition=cell["cell_id"],
        workspace_root=str(workspace.resolve()),
        raw_trace_root=str(raw_root.resolve()),
        agent_id="fake-agent",
        agent_version="1",
        backend="fake-provider",
        model_id="fake-model",
        effort="high",
        budget_id="budget-v1",
        library_snapshot_id=cell["cell_id"],
        library_snapshot_sha256=materialization["materialized_byte_snapshot_sha256"],
        verifier_id=verifier_id,
    )
    result = AgentRunResult(
        contract=contract,
        workspace_root=str(workspace.resolve()),
        raw_trace=RawTraceReference(
            pointer=raw_path.name,
            sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        ),
        actual_invocation_evidence_complete=True,
        selected_skill_ids=invoked,
        invocation_events=[
            SkillInvocationEvent(
                skill_id=skill_id,
                event_kind="provider_skill_invocation",
                source="fake-provider",
                event_id=f"event-{index}",
                sequence=index,
            )
            for index, skill_id in enumerate(invoked)
        ],
    )
    trace = TraceRecord(
        id=cell["cell_id"],
        task_id=cell["task_id"],
        condition=cell["cell_id"],
        invocation=InvocationRecord(
            task_id=cell["task_id"],
            provisioned_skill_ids=list(cell["library_variant_ids"]),
            selected_skill_ids=invoked,
            oracle_skill_ids=[],
            success=True,
            score=1.0,
        ),
        validation=[ValidationResult(name=verifier_id, passed=True, score=1.0)],
        metadata={
            AGENT_TRACE_EVIDENCE_KEY: serialize_agent_run_evidence(result),
            "staged_verifier_tree_sha256": "a" * 64,
            "verifier_contract_sha256": verifier_id,
            "outcome_status": "scored_verifier",
            "harness_mode": "library-scale-agentic-v1",
        },
    )
    trace_root = root / "traces"
    FileTraceStore(trace_root).save_immutable(trace)
    return trace_root


class LibraryScaleAggregationCLITests(unittest.TestCase):
    def _run_fixture(self, root: Path) -> tuple[Path, Path]:
        cell_root = root / "cells"
        cell_root.mkdir()
        materialization = materialize_library_scale_cell(
            manifest_path=MANIFEST_PATH,
            cell_id=CELL["cell_id"],
            output_root=cell_root / CELL["cell_id"],
        )
        return cell_root, _save_one_trace(root, materialization)

    def test_file_backed_partial_aggregate_validates_bytes_and_raw_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cell_root, trace_root = self._run_fixture(root)
            payload = aggregate_library_scale_run(
                manifest_path=MANIFEST_PATH,
                cell_root=cell_root,
                trace_root=trace_root,
            )
            self.assertEqual(payload["trace_count"], 1)
            self.assertEqual(payload["summary"]["observed_cells"], 1)
            self.assertFalse(payload["summary"]["full_denominator_observed"])
            self.assertEqual(
                payload["summary"]["shadowing_summary"]["status"],
                "unavailable",
            )
            self.assertEqual(
                payload["summary"]["shadowing_summary"]["clustered_bootstrap"][
                    "status"
                ],
                "unavailable",
            )

    def test_staged_byte_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cell_root, trace_root = self._run_fixture(root)
            skill_md = cell_root / CELL["cell_id"] / "skills" / "mesh-analysis" / "SKILL.md"
            skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
            with self.assertRaisesRegex(LibraryScaleAggregationError, "bytes drifted"):
                aggregate_library_scale_run(
                    manifest_path=MANIFEST_PATH,
                    cell_root=cell_root,
                    trace_root=trace_root,
                )

    def test_cli_writes_once_and_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cell_root, trace_root = self._run_fixture(root)
            output = root / "aggregate.json"
            arguments = [
                "--manifest",
                str(MANIFEST_PATH),
                "--cell-root",
                str(cell_root),
                "--trace-root",
                str(trace_root),
                "--output",
                str(output),
            ]
            self.assertEqual(main(arguments), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["trace_count"], 1)
            with self.assertRaisesRegex(LibraryScaleAggregationError, "output already exists"):
                main(arguments)
            self.assertEqual(main([*arguments, "--overwrite"]), 0)

    def _oracle_manifest(self, root: Path) -> Path:
        evidence_root = root / "evidence"
        evidence_root.mkdir()
        tasks = []
        estimation_contract = {
            "model_id": "fake-model",
            "backend": "fake-provider",
            "harness_mode": "library-scale-agentic-v1",
            "tau": 0.1,
            "repeats": 3,
            "candidate_pool_sha256": MANIFEST["frozen_inputs"]["skill_pool_sha256"],
        }
        runtime_contract_sha256 = sha256_json(estimation_contract)

        def trials(task: dict, label: str, reward: float) -> list[dict]:
            rows = []
            task_raw_root = root / "raw" / task["task_id"]
            task_raw_root.mkdir(parents=True, exist_ok=True)
            safe_label = label.replace("@", "-")
            for trial_index in range(1, 4):
                raw = task_raw_root / f"{safe_label}-t{trial_index}.jsonl"
                raw.write_text(
                    json.dumps(
                        {
                            "task_id": task["task_id"],
                            "condition": label,
                            "trial_index": trial_index,
                            "reward": reward,
                        }
                    ),
                    encoding="utf-8",
                )
                rows.append(
                    {
                        "trial_index": trial_index,
                        "reward": reward,
                        "verifier_contract_sha256": task["verifier_contract_sha256"],
                        "runtime_contract_sha256": runtime_contract_sha256,
                        "raw_trace_pointer": raw.relative_to(root).as_posix(),
                        "raw_trace_sha256": sha256_file(raw),
                    }
                )
            return rows

        for task in MANIFEST["task_contracts"]:
            evidence = evidence_root / f"{task['task_id']}.json"
            task_evidence = {
                "schema_version": 1,
                "task_id": task["task_id"],
                "estimation_contract_sha256": runtime_contract_sha256,
                "verifier_contract_sha256": task["verifier_contract_sha256"],
                "no_skill_trials": trials(task, "no-skill", 0.0),
                "candidate_trials": [
                    {
                        "skill_variant_id": skill_id,
                        "trials": trials(task, f"skill-{skill_id}", 1.0),
                    }
                    for skill_id in task["reference_skill_variants"]
                ],
            }
            evidence.write_text(
                json.dumps(task_evidence),
                encoding="utf-8",
            )
            tasks.append(
                {
                    "task_id": task["task_id"],
                    "skill_variant_ids": task["reference_skill_variants"],
                    "evidence_pointer": evidence.relative_to(root).as_posix(),
                    "evidence_sha256": sha256_file(evidence),
                }
            )
        payload = {
            "schema_version": 1,
            "experiment_id": MANIFEST["experiment_id"],
            "library_scale_manifest_sha256": sha256_file(MANIFEST_PATH),
            "oracle_candidate_scope": "task_curated_bundle",
            "estimation_evidence_complete": True,
            "estimation_contract": estimation_contract,
            "tasks": tasks,
        }
        path = root / "empirical-oracle.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_empirical_oracle_requires_complete_hashed_87_task_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oracle_path = self._oracle_manifest(root)
            mapping, contract = load_empirical_oracle_mapping(
                oracle_path,
                manifest=MANIFEST,
                manifest_path=MANIFEST_PATH,
            )
            self.assertEqual(len(mapping), 87)
            self.assertEqual(contract["repeats"], 3)

            first = root / json.loads(oracle_path.read_text(encoding="utf-8"))["tasks"][0]["evidence_pointer"]
            first.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(LibraryScaleAggregationError, "hash-invalid"):
                load_empirical_oracle_mapping(
                    oracle_path,
                    manifest=MANIFEST,
                    manifest_path=MANIFEST_PATH,
                )

    def test_oracle_bound_manifest_materializes_and_aggregates_against_frozen_dependencies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oracle_path = self._oracle_manifest(root)
            derived = build_oracle_bound_manifest_from_files(
                base_manifest_path=MANIFEST_PATH,
                empirical_oracle_path=oracle_path,
                created="2026-07-19",
            )
            derived_path = root / "oracle-bound.json"
            derived_path.write_text(
                json.dumps(derived, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            cell = derived["cells"][0]
            self.assertEqual(cell["arm_id"], "oracle-only")
            cell_root = root / "cells"
            cell_root.mkdir()
            materialized = materialize_library_scale_cell(
                manifest_path=derived_path,
                cell_id=cell["cell_id"],
                output_root=cell_root / cell["cell_id"],
                base_manifest_path=MANIFEST_PATH,
                empirical_oracle_path=oracle_path,
            )
            self.assertEqual(materialized["manifest_schema_version"], 2)
            self.assertEqual(
                materialized["empirical_oracle_skill_variants"],
                cell["empirical_oracle_skill_variants"],
            )
            self.assertEqual(
                materialized["derived_manifest_dependencies"][
                    "base_manifest_file_sha256"
                ],
                sha256_file(MANIFEST_PATH),
            )
            self.assertEqual(
                materialized["derived_manifest_dependencies"][
                    "empirical_oracle_file_sha256"
                ],
                sha256_file(oracle_path),
            )

            run_root = root / "run"
            run_root.mkdir()
            trace_root = _save_one_trace(run_root, materialized, cell=cell)
            payload = aggregate_library_scale_run(
                manifest_path=derived_path,
                base_manifest_path=MANIFEST_PATH,
                empirical_oracle_path=oracle_path,
                cell_root=cell_root,
                trace_root=trace_root,
            )
            self.assertEqual(payload["manifest"]["schema_version"], 2)
            self.assertEqual(payload["summary"]["expected_cells"], 1566)
            self.assertEqual(payload["summary"]["observed_cells"], 1)
            self.assertIn(
                "1,566-cell denominator",
                payload["summary"]["shadowing_summary"]["reason"],
            )
            self.assertEqual(
                payload["summary"]["shadowing_summary"]["clustered_bootstrap"],
                {
                    "status": "unavailable",
                    "reason": "full 1,566-cell denominator is incomplete",
                    "comparisons": None,
                },
            )

            contract_path = cell_root / cell["cell_id"] / "cell-contract.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["derived_manifest_dependencies"][
                "empirical_oracle_file_sha256"
            ] = "0" * 64
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(
                LibraryScaleAggregationError,
                "derived-manifest dependency mismatch",
            ):
                aggregate_library_scale_run(
                    manifest_path=derived_path,
                    base_manifest_path=MANIFEST_PATH,
                    empirical_oracle_path=oracle_path,
                    cell_root=cell_root,
                    trace_root=trace_root,
                )


if __name__ == "__main__":
    unittest.main()
