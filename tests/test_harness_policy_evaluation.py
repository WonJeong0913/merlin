from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.harness_policy_evaluation import (
    M3KCell,
    M3KContractError,
    M3KEvaluationContract,
    M3KPromotionCriteria,
    M3KSplit,
    M3KTaskContract,
    M3KTrajectoryResult,
    M3KVariantLineage,
    VariantRole,
    build_cells,
    build_full87_m3k_contract,
    run_m3k_policy_evaluation,
)
from src.merlin_harness.harness import (
    HarnessEvolutionProposal,
    HarnessVariantSpec,
    Hook,
    make_default_harness_runtime,
    snapshot_harness_variant,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _contract() -> M3KEvaluationContract:
    return M3KEvaluationContract(
        experiment_id="m3k-unit-v1",
        split_manifest_sha256=_sha("split"),
        task_contract_source_sha256=_sha("tasks"),
        tasks=(
            M3KTaskContract("held-in-task", M3KSplit.HELD_IN, "verify-in", _sha("in")),
            M3KTaskContract("held-out-task", M3KSplit.HELD_OUT, "verify-out", _sha("out")),
            M3KTaskContract("regression-task", M3KSplit.REGRESSION, "verify-reg", _sha("reg")),
        ),
        repeats=2,
        base_agent_id="fixture-agent",
        base_agent_version="1",
        backend="fixture",
        model_id="no-model",
        effort="none",
        tools=("fixture",),
        budget_id="fixture-budget",
    )


def _variants():
    parent = snapshot_harness_variant(
        make_default_harness_runtime(max_exposure_budget=2),
        variant_id="h-parent",
        summary="parent harness",
    )
    candidate = HarnessVariantSpec(
        id="h-candidate",
        parent_id=parent.id,
        summary="candidate lowers exposure budget",
        processor_manifest=parent.processor_manifest,
        policy={"exposure_budget": 1},
    )
    proposal = HarnessEvolutionProposal(
        id="proposal-m3k",
        parent_variant_id=parent.id,
        candidate=candidate,
        rationale="reduce repeated route shadowing",
        changed_hooks=[Hook.BEFORE_PROVISION.value],
        evidence_trace_ids=["risk-trace-1"],
    )
    return parent, proposal


class FixtureExecutor:
    def __init__(
        self,
        role: VariantRole,
        *,
        held_out_regression: bool = False,
        incomplete: bool = False,
        lineage_drift: bool = False,
        duplicate_raw: bool = False,
        high_cost: bool = False,
        parent_regression_fail: bool = False,
    ) -> None:
        self.role = role
        self.held_out_regression = held_out_regression
        self.incomplete = incomplete
        self.lineage_drift = lineage_drift
        self.duplicate_raw = duplicate_raw
        self.high_cost = high_cost
        self.parent_regression_fail = parent_regression_fail

    def run(
        self,
        variant: HarnessVariantSpec,
        cells: tuple[M3KCell, ...],
        lineage: M3KVariantLineage,
    ):
        rows = []
        for cell in cells:
            if self.role is VariantRole.PARENT:
                passed = cell.split is M3KSplit.REGRESSION
                if self.parent_regression_fail and cell.split is M3KSplit.REGRESSION:
                    passed = False
                if self.held_out_regression and cell.split is M3KSplit.HELD_OUT:
                    passed = True
                invoked = ("oracle",) if passed else ("distractor",)
            else:
                passed = True
                if self.held_out_regression and cell.split is M3KSplit.HELD_OUT:
                    passed = False
                invoked = ("oracle",) if passed else ("distractor",)
            raw_key = "duplicate" if self.duplicate_raw else f"{self.role.value}:{cell.cell_id}"
            rows.append(
                M3KTrajectoryResult(
                    cell_id=cell.cell_id,
                    task_id=cell.task_id,
                    split=cell.split,
                    trial_index=cell.trial_index,
                    verifier_id=cell.verifier_id,
                    task_instruction_sha256=cell.task_instruction_sha256,
                    variant_role=self.role,
                    variant_id=variant.id,
                    variant_sha256=("0" * 64 if self.lineage_drift else lineage.variant_sha256),
                    evaluation_contract_sha256=lineage.evaluation_contract_sha256,
                    trace_id=f"{self.role.value}:{cell.cell_id}",
                    raw_trace_sha256=_sha(raw_key),
                    verifier_passed=passed,
                    verifier_score=float(passed),
                    cost=(2.0 if self.high_cost and self.role is VariantRole.CANDIDATE else 1.0),
                    actual_invocation_evidence_complete=not self.incomplete,
                    invoked_skill_ids=invoked,
                    oracle_skill_ids=("oracle",),
                )
            )
        return tuple(rows)


class FixtureFactory:
    def __init__(self, **options) -> None:
        self.options = options
        self.roles: list[VariantRole] = []

    def __call__(self, role: VariantRole):
        self.roles.append(role)
        return FixtureExecutor(role, **self.options)


class M3KPolicyEvaluationTests(unittest.TestCase):
    def test_internal_paired_evaluation_promotes_without_caller_deltas(self) -> None:
        parent, proposal = _variants()
        factory = FixtureFactory()
        result = run_m3k_policy_evaluation(
            contract=_contract(),
            parent=parent,
            proposal=proposal,
            executor_factory=factory,
        )

        self.assertTrue(result.accepted)
        self.assertFalse(result.rollback_required)
        self.assertEqual(result.resolution, "candidate_harness_promoted")
        self.assertEqual(factory.roles, [VariantRole.PARENT, VariantRole.CANDIDATE])
        deltas = {item.split: item for item in result.deltas}
        self.assertEqual(deltas[M3KSplit.HELD_IN].pass_rate_delta, 1.0)
        self.assertEqual(deltas[M3KSplit.HELD_OUT].pass_rate_delta, 1.0)
        self.assertEqual(deltas[M3KSplit.REGRESSION].pass_rate_delta, 0.0)
        self.assertTrue(all(check.passed for check in result.checks))
        self.assertEqual(result.regression_candidate_task_count, 1)
        self.assertEqual(result.regression_eligible_task_ids, ("regression-task",))
        report = result.to_dict()
        self.assertFalse(report["evidence_boundary"]["caller_supplied_deltas_accepted"])
        self.assertFalse(report["evidence_boundary"]["held_out_visible_to_proposer"])

    def test_held_out_regression_rolls_back_to_parent(self) -> None:
        parent, proposal = _variants()
        result = run_m3k_policy_evaluation(
            contract=_contract(),
            parent=parent,
            proposal=proposal,
            executor_factory=FixtureFactory(held_out_regression=True),
        )
        self.assertFalse(result.accepted)
        self.assertTrue(result.rollback_required)
        self.assertEqual(result.resolved_variant_id, parent.id)
        failed = {check.name for check in result.checks if not check.passed}
        self.assertIn("held_out_non_regression", failed)

    def test_incomplete_invocation_and_cost_growth_reject_candidate(self) -> None:
        parent, proposal = _variants()
        incomplete = run_m3k_policy_evaluation(
            contract=_contract(),
            parent=parent,
            proposal=proposal,
            executor_factory=FixtureFactory(incomplete=True),
        )
        self.assertFalse(incomplete.accepted)
        self.assertFalse(
            next(check for check in incomplete.checks if check.name == "actual_invocation_complete").passed
        )

        costly = run_m3k_policy_evaluation(
            contract=_contract(),
            parent=parent,
            proposal=proposal,
            executor_factory=FixtureFactory(high_cost=True),
            criteria=M3KPromotionCriteria(max_mean_cost_ratio=1.1),
        )
        self.assertFalse(costly.accepted)
        self.assertFalse(
            next(check for check in costly.checks if check.name == "cost_guardrail_all_splits").passed
        )

        no_regression_baseline = run_m3k_policy_evaluation(
            contract=_contract(),
            parent=parent,
            proposal=proposal,
            executor_factory=FixtureFactory(parent_regression_fail=True),
        )
        self.assertFalse(no_regression_baseline.accepted)
        self.assertEqual(no_regression_baseline.regression_eligible_task_ids, ())
        self.assertFalse(
            next(
                check
                for check in no_regression_baseline.checks
                if check.name == "regression_baseline_eligible"
            ).passed
        )

    def test_lineage_and_raw_trace_reuse_fail_closed(self) -> None:
        parent, proposal = _variants()
        with self.assertRaisesRegex(M3KContractError, "trajectory lineage drifted"):
            run_m3k_policy_evaluation(
                contract=_contract(),
                parent=parent,
                proposal=proposal,
                executor_factory=FixtureFactory(lineage_drift=True),
            )
        with self.assertRaisesRegex(M3KContractError, "raw trace hashes must be unique"):
            run_m3k_policy_evaluation(
                contract=_contract(),
                parent=parent,
                proposal=proposal,
                executor_factory=FixtureFactory(duplicate_raw=True),
            )

    def test_contract_rejects_visible_held_out_and_missing_split(self) -> None:
        contract = _contract()
        visible = M3KEvaluationContract(
            **{
                **{name: getattr(contract, name) for name in contract.__dataclass_fields__ if name != "held_out_visible_to_proposer"},
                "held_out_visible_to_proposer": True,
            }
        )
        parent, proposal = _variants()
        with self.assertRaisesRegex(M3KContractError, "hidden from the proposer"):
            run_m3k_policy_evaluation(
                contract=visible,
                parent=parent,
                proposal=proposal,
                executor_factory=FixtureFactory(),
            )

        missing = M3KEvaluationContract(
            **{
                **{name: getattr(contract, name) for name in contract.__dataclass_fields__ if name != "tasks"},
                "tasks": tuple(task for task in contract.tasks if task.split is not M3KSplit.REGRESSION),
            }
        )
        with self.assertRaisesRegex(M3KContractError, "requires non-empty"):
            build_cells(missing)

    def test_canonical_full87_contract_builds_261_paired_cells(self) -> None:
        contract = build_full87_m3k_contract(
            split_manifest=Path("experiments/skillsbench/split-manifest.json"),
            library_scale_manifest=Path("experiments/skillsbench/library-scale-manifest.json"),
            experiment_id="m3k-full87-contract-v1",
            base_agent_id="merlin-agent",
            base_agent_version="1",
            backend="strict-executor-required",
            model_id="gpt-5.6-terra",
            effort="high",
            tools=("fixed-container-exec",),
            budget_id="m3k-full87-budget-v1",
            repeats=3,
        )
        cells = build_cells(contract)
        self.assertEqual(len(contract.tasks), 87)
        self.assertEqual(len(cells), 261)
        counts = {
            split: sum(task.split is split for task in contract.tasks)
            for split in M3KSplit
        }
        self.assertEqual(
            counts,
            {M3KSplit.HELD_IN: 35, M3KSplit.HELD_OUT: 30, M3KSplit.REGRESSION: 22},
        )


if __name__ == "__main__":
    unittest.main()
