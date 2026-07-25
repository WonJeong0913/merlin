from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.materialize_library_scale_cell import materialize_library_scale_cell
from experiments.skillsbench.bind_empirical_oracle_manifest import (
    build_oracle_bound_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file
from experiments.skillsbench.clustered_library_scale_bootstrap import (
    build_library_scale_clustered_bootstrap,
)
from src.merlin_harness.library_scale_results import (
    LibraryScaleResultError,
    ValidatedLibraryScaleCell,
    aggregate_library_scale_cells,
    validate_library_scale_cell_trace,
)
from src.merlin_harness.models import (
    AgentRunContract,
    AgentRunResult,
    InvocationRecord,
    RawTraceReference,
    SkillInvocationEvent,
    TraceRecord,
    ValidationResult,
)
from src.merlin_harness.traces import AGENT_TRACE_EVIDENCE_KEY, serialize_agent_run_evidence


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "experiments" / "skillsbench" / "library-scale-manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
CELL = MANIFEST["cells"][0]


def _agent_trace(
    root: Path,
    materialization: dict,
    *,
    invocation_complete: bool = True,
    invoked: tuple[str, ...] = ("mesh-analysis",),
    selected: tuple[str, ...] = ("mesh-analysis",),
    outcome_status: str = "scored_verifier",
) -> TraceRecord:
    raw_root = root / "raw"
    raw_root.mkdir(parents=True)
    raw_path = raw_root / "turn.jsonl"
    raw_path.write_text("provider trace\n", encoding="utf-8")
    raw = RawTraceReference(
        pointer=raw_path.name,
        sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    )
    workspace = root / "workspace"
    workspace.mkdir()
    verifier_id = CELL["verifier_contract_sha256"]
    contract = AgentRunContract(
        run_id="scale-test",
        task_id=CELL["task_id"],
        condition=CELL["cell_id"],
        workspace_root=str(workspace.resolve()),
        raw_trace_root=str(raw_root.resolve()),
        agent_id="fake-agent",
        agent_version="1",
        backend="fake-provider",
        model_id="fake-model",
        effort="high",
        budget_id="budget-v1",
        library_snapshot_id=CELL["cell_id"],
        library_snapshot_sha256=materialization["materialized_byte_snapshot_sha256"],
        verifier_id=verifier_id,
    )
    events = [
        SkillInvocationEvent(
            skill_id=skill_id,
            event_kind="skill_body_loaded",
            source="fake-provider",
            event_id=f"event-{index}",
            sequence=index,
        )
        for index, skill_id in enumerate(invoked)
    ]
    result = AgentRunResult(
        contract=contract,
        workspace_root=str(workspace.resolve()),
        raw_trace=raw,
        actual_invocation_evidence_complete=invocation_complete,
        selected_skill_ids=list(selected),
        invocation_events=events,
    )
    return TraceRecord(
        id=CELL["cell_id"],
        task_id=CELL["task_id"],
        condition=CELL["cell_id"],
        invocation=InvocationRecord(
            task_id=CELL["task_id"],
            provisioned_skill_ids=list(CELL["library_variant_ids"]),
            selected_skill_ids=list(selected),
            oracle_skill_ids=[],
            success=True,
            score=1.0,
        ),
        validation=[ValidationResult(name=verifier_id, passed=True, score=1.0)],
        metadata={
            AGENT_TRACE_EVIDENCE_KEY: serialize_agent_run_evidence(result),
            "staged_verifier_tree_sha256": "a" * 64,
            "verifier_contract_sha256": verifier_id,
            "outcome_status": outcome_status,
            "harness_mode": "library-scale-agentic-v1",
        },
    )


class LibraryScaleResultTests(unittest.TestCase):
    def _materialized(self, root: Path) -> dict:
        return materialize_library_scale_cell(
            manifest_path=MANIFEST_PATH,
            cell_id=CELL["cell_id"],
            output_root=root / "materialized",
        )

    def test_trace_validation_binds_raw_invocation_to_frozen_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialized = self._materialized(root)
            validated = validate_library_scale_cell_trace(
                manifest_cell=CELL,
                materialization_contract=materialized,
                trace=_agent_trace(root, materialized),
            )
            self.assertEqual(validated.cell_id, CELL["cell_id"])
            self.assertEqual(validated.invoked_skill_ids, ("mesh-analysis",))
            self.assertEqual(validated.selected_skill_ids, ("mesh-analysis",))
            self.assertTrue(validated.actual_invocation_evidence_complete)
            self.assertEqual(validated.reward, 1.0)

    def test_selected_skill_is_not_promoted_when_invocation_evidence_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialized = self._materialized(root)
            trace = _agent_trace(
                root,
                materialized,
                invocation_complete=False,
                invoked=(),
            )
            validated = validate_library_scale_cell_trace(
                manifest_cell=CELL,
                materialization_contract=materialized,
                trace=trace,
            )
            self.assertEqual(validated.selected_skill_ids, ("mesh-analysis",))
            self.assertEqual(validated.invoked_skill_ids, ())
            self.assertFalse(validated.actual_invocation_evidence_complete)

    def test_trace_contract_drift_and_unverified_oracle_ids_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialized = self._materialized(root)
            trace = _agent_trace(root, materialized)
            trace.invocation.oracle_skill_ids = ["mesh-analysis"]
            with self.assertRaisesRegex(LibraryScaleResultError, "must not embed"):
                validate_library_scale_cell_trace(
                    manifest_cell=CELL,
                    materialization_contract=materialized,
                    trace=trace,
                )

            trace = _agent_trace(root / "second", materialized)
            trace.metadata["staged_verifier_tree_sha256"] = "not-a-hash"
            with self.assertRaisesRegex(LibraryScaleResultError, "lowercase SHA-256"):
                validate_library_scale_cell_trace(
                    manifest_cell=CELL,
                    materialization_contract=materialized,
                    trace=trace,
                )

            outside_selected = _agent_trace(
                root / "outside-selected",
                materialized,
                invoked=(),
                selected=("not-staged",),
            )
            with self.assertRaisesRegex(LibraryScaleResultError, "selected.*outside"):
                validate_library_scale_cell_trace(
                    manifest_cell=CELL,
                    materialization_contract=materialized,
                    trace=outside_selected,
                )

            drifted = copy.deepcopy(materialized)
            drifted["materialized_byte_snapshot_sha256"] = "b" * 64
            with self.assertRaisesRegex(LibraryScaleResultError, "byte snapshot"):
                validate_library_scale_cell_trace(
                    manifest_cell=CELL,
                    materialization_contract=drifted,
                    trace=_agent_trace(root / "third", materialized),
                )

    @staticmethod
    def _complete_cells(*, invocation_complete: bool = True) -> list[ValidatedLibraryScaleCell]:
        results: list[ValidatedLibraryScaleCell] = []
        for cell in MANIFEST["cells"]:
            reference = list(cell["reference_skill_variants"])
            non_reference = [
                skill_id
                for skill_id in cell["library_variant_ids"]
                if skill_id not in set(reference)
            ]
            invoked = tuple(
                non_reference[:1]
                if cell["arm_id"] != "curated" and non_reference
                else reference[:1]
            )
            results.append(
                ValidatedLibraryScaleCell(
                    cell_id=cell["cell_id"],
                    task_id=cell["task_id"],
                    trial_index=cell["trial_index"],
                    arm_id=cell["arm_id"],
                    library_size=cell["library_size"],
                    outcome_status="scored_verifier",
                    verifier_passed=True,
                    reward=1.0,
                    actual_invocation_evidence_complete=invocation_complete,
                    invoked_skill_ids=invoked,
                    selected_skill_ids=invoked,
                    staged_verifier_tree_sha256=hashlib.sha256(
                        cell["task_id"].encode("utf-8")
                    ).hexdigest(),
                    runtime_key=(
                        "fake-agent",
                        "1",
                        "fake-provider",
                        "fake-model",
                        "high",
                        "budget-v1",
                        "library-scale-agentic-v1",
                    ),
                    raw_trace_sha256=hashlib.sha256(cell["cell_id"].encode("utf-8")).hexdigest(),
                )
            )
        return results

    def test_aggregate_refuses_incomplete_denominator_invocation_or_oracle(self) -> None:
        partial = aggregate_library_scale_cells(
            manifest=MANIFEST,
            cells=self._complete_cells()[:1],
        )
        self.assertFalse(partial["full_denominator_observed"])
        self.assertEqual(partial["shadowing_summary"]["status"], "unavailable")
        self.assertIn("1,305-cell denominator", partial["shadowing_summary"]["reason"])
        partial_bootstrap = build_library_scale_clustered_bootstrap(
            manifest=MANIFEST,
            cells=self._complete_cells()[:1],
            normalized_oracles=None,
            aggregate_summary=partial,
        )
        self.assertEqual(partial_bootstrap["status"], "unavailable")

        incomplete_invocation = aggregate_library_scale_cells(
            manifest=MANIFEST,
            cells=self._complete_cells(invocation_complete=False),
        )
        self.assertIn("actual invocation", incomplete_invocation["shadowing_summary"]["reason"])

        no_oracle = aggregate_library_scale_cells(
            manifest=MANIFEST,
            cells=self._complete_cells(),
        )
        self.assertIn("empirical oracle", no_oracle["shadowing_summary"]["reason"])

    def test_complete_actual_invocation_yields_event_curves_but_not_decomposition(self) -> None:
        oracle = {
            task["task_id"]: tuple(task["reference_skill_variants"][:1])
            for task in MANIFEST["task_contracts"]
        }
        summary = aggregate_library_scale_cells(
            manifest=MANIFEST,
            cells=self._complete_cells(),
            empirical_oracle_by_task=oracle,
        )
        self.assertTrue(summary["full_denominator_observed"])
        self.assertTrue(summary["full_denominator_scored"])
        self.assertTrue(summary["actual_invocation_evidence_complete"])
        self.assertEqual(
            summary["shadowing_summary"]["status"],
            "available_event_curves_only",
        )
        self.assertFalse(
            summary["shadowing_summary"]["more_skills_decomposition_eligible"]
        )
        event_only_bootstrap = build_library_scale_clustered_bootstrap(
            manifest=MANIFEST,
            cells=self._complete_cells(),
            normalized_oracles=oracle,
            aggregate_summary=summary,
        )
        self.assertEqual(event_only_bootstrap["status"], "unavailable")
        curated = summary["shadowing_summary"]["event_curves"]["curated"]
        expanded = summary["shadowing_summary"]["event_curves"]["plus-10"]
        self.assertGreater(curated["counts"]["o"], 0)
        self.assertGreater(expanded["counts"]["m"], 0)

        invalid_oracle = dict(oracle)
        invalid_oracle[CELL["task_id"]] = ("not-in-curated-scope",)
        with self.assertRaisesRegex(LibraryScaleResultError, "outside.*curated"):
            aggregate_library_scale_cells(
                manifest=MANIFEST,
                cells=self._complete_cells(),
                empirical_oracle_by_task=invalid_oracle,
            )

    @staticmethod
    def _oracle_bound_manifest() -> tuple[dict, dict[str, tuple[str, ...]]]:
        oracle = {
            task["task_id"]: tuple(task["reference_skill_variants"][:1])
            for task in MANIFEST["task_contracts"]
        }
        derived = build_oracle_bound_manifest(
            base_manifest=MANIFEST,
            empirical_oracle_by_task=oracle,
            oracle_estimation_contract={
                "backend": "fake-provider",
                "model_id": "fake-model",
                "harness_mode": "library-scale-agentic-v1",
                "repeats": 3,
                "tau": 0.1,
            },
            base_manifest_sha256=sha256_file(MANIFEST_PATH),
            empirical_oracle_manifest_sha256="a" * 64,
            created="2026-07-19",
        )
        return derived, oracle

    def test_oracle_bound_full_denominator_yields_more_skills_decomposition(self) -> None:
        derived, oracle = self._oracle_bound_manifest()
        results: list[ValidatedLibraryScaleCell] = []
        for cell in derived["cells"]:
            oracle_ids = oracle[cell["task_id"]]
            non_oracle = [
                skill_id
                for skill_id in cell["library_variant_ids"]
                if skill_id not in set(oracle_ids)
            ]
            use_distractor = (
                cell["arm_id"] not in {"oracle-only", "curated"}
                and cell["trial_index"] != 1
                and bool(non_oracle)
            )
            invoked = tuple(non_oracle[:1] if use_distractor else oracle_ids[:1])
            passed = not use_distractor
            results.append(
                ValidatedLibraryScaleCell(
                    cell_id=cell["cell_id"],
                    task_id=cell["task_id"],
                    trial_index=cell["trial_index"],
                    arm_id=cell["arm_id"],
                    library_size=cell["library_size"],
                    outcome_status="scored_verifier",
                    verifier_passed=passed,
                    reward=1.0 if passed else 0.0,
                    actual_invocation_evidence_complete=True,
                    invoked_skill_ids=invoked,
                    selected_skill_ids=invoked,
                    staged_verifier_tree_sha256=hashlib.sha256(
                        cell["task_id"].encode("utf-8")
                    ).hexdigest(),
                    runtime_key=(
                        "fake-agent",
                        "1",
                        "fake-provider",
                        "fake-model",
                        "high",
                        "budget-v1",
                        "library-scale-agentic-v1",
                    ),
                    raw_trace_sha256=hashlib.sha256(
                        cell["cell_id"].encode("utf-8")
                    ).hexdigest(),
                )
            )

        summary = aggregate_library_scale_cells(
            manifest=derived,
            cells=results,
            empirical_oracle_by_task=oracle,
        )

        shadowing = summary["shadowing_summary"]
        self.assertEqual(summary["expected_cells"], 1566)
        self.assertEqual(shadowing["status"], "available_with_decomposition")
        self.assertTrue(shadowing["more_skills_decomposition_eligible"])
        self.assertIsNone(shadowing["decomposition_blocker"])
        self.assertEqual(
            set(shadowing["more_skills_decomposition"]),
            {"curated", "plus-10", "plus-50", "plus-100", "full-209"},
        )
        self.assertEqual(
            shadowing["event_curves"]["oracle-only"]["counts"]["m"], 0
        )
        plus_ten = shadowing["more_skills_decomposition"]["plus-10"]
        self.assertAlmostEqual(plus_ten["observed_drop"], 2 / 3)
        self.assertAlmostEqual(plus_ten["delta_ctx"], 0.0)
        self.assertAlmostEqual(plus_ten["delta_shd"], 2 / 3)
        self.assertTrue(plus_ten["invariant_holds"])
        bootstrap = build_library_scale_clustered_bootstrap(
            manifest=derived,
            cells=results,
            normalized_oracles=oracle,
            aggregate_summary=summary,
        )
        self.assertEqual(bootstrap["status"], "available")
        bootstraps = bootstrap["comparisons"]
        self.assertEqual(
            set(bootstraps),
            {"curated", "plus-10", "plus-50", "plus-100", "full-209"},
        )
        plus_ten_bootstrap = bootstraps["plus-10"]
        self.assertEqual(plus_ten_bootstrap["status"], "available")
        self.assertEqual(plus_ten_bootstrap["cluster_count"], 87)
        self.assertEqual(plus_ten_bootstrap["trajectory_count"], 261)
        self.assertEqual(plus_ten_bootstrap["iterations"], 2000)
        self.assertEqual(plus_ten_bootstrap["paired_by"], ["task_id", "trial_index"])
        self.assertEqual(
            plus_ten_bootstrap["resampling_units"],
            {
                "stage_1": "task_cluster",
                "stage_2": "paired_trial_trajectory_within_task",
            },
        )
        for metric in (
            "p_oracle",
            "p_library",
            "observed_drop",
            "delta_ctx",
            "delta_shd",
            "total",
        ):
            interval = plus_ten_bootstrap["intervals"][metric]
            self.assertAlmostEqual(interval["estimate"], plus_ten[metric])
            self.assertLessEqual(interval["low"], interval["estimate"])
            self.assertGreaterEqual(interval["high"], interval["estimate"])

        tampered = copy.deepcopy(derived)
        tampered["cells"][0]["empirical_oracle_skill_variants"] = []
        with self.assertRaisesRegex(LibraryScaleResultError, "oracle binding drifted"):
            aggregate_library_scale_cells(
                manifest=tampered,
                cells=results,
                empirical_oracle_by_task=oracle,
            )

    def test_verifier_or_runtime_drift_is_rejected(self) -> None:
        cells = self._complete_cells()
        cells[1] = copy.copy(cells[1])
        object.__setattr__(cells[1], "staged_verifier_tree_sha256", "f" * 64)
        with self.assertRaisesRegex(LibraryScaleResultError, "verifier tree drift"):
            aggregate_library_scale_cells(manifest=MANIFEST, cells=cells)

        cells = self._complete_cells()
        object.__setattr__(
            cells[1],
            "runtime_key",
            (
                "other-agent",
                "1",
                "fake-provider",
                "fake-model",
                "high",
                "budget-v1",
                "library-scale-agentic-v1",
            ),
        )
        with self.assertRaisesRegex(LibraryScaleResultError, "runtime.*drift"):
            aggregate_library_scale_cells(manifest=MANIFEST, cells=cells)

        cells = self._complete_cells()
        object.__setattr__(cells[0], "library_size", 999)
        with self.assertRaisesRegex(LibraryScaleResultError, "does not match manifest"):
            aggregate_library_scale_cells(manifest=MANIFEST, cells=cells)


if __name__ == "__main__":
    unittest.main()
