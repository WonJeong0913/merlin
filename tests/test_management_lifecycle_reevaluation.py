from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.run_management_policy_comparison import (
    SKILL_IDS,
    _contract,
    _inputs,
    _snapshot,
    _traces_for_arm,
)
from experiments.skillsbench.management_lifecycle_reevaluation import (
    LINEAGE_METADATA_KEY,
    M2KReevaluationCriteria,
    ManagementContractError,
    RoutePolicyCandidate,
    policy_lineage_payload,
    run_m2k_lifecycle_reevaluation,
    stage_m2k_route_policy,
)
from src.merlin_harness.management import ManagementArm, run_management_round
from src.merlin_harness.models import (
    AgentRunContract,
    AgentRunResult,
    InvocationRecord,
    RawTraceReference,
    SkillInvocationEvent,
    TraceRecord,
    ValidationResult,
)
from src.merlin_harness.traces import serialize_agent_run_evidence


def _make_trace(
    root: Path,
    contract,
    candidate: RoutePolicyCandidate,
    task_id: str,
    *,
    invoked: tuple[str, ...],
    passed: bool,
    lineage: dict[str, object] | None = None,
    provisioned: tuple[str, ...] | None = None,
) -> TraceRecord:
    trace_id = f"provisional-{task_id}"
    exposure = dict(
        (item.task_id, item.skill_ids) for item in candidate.exposure_decisions
    )[task_id]
    provisioned_ids = exposure if provisioned is None else provisioned
    workspace = root / "workspaces" / task_id
    raw_root = root / "raw"
    workspace.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    raw_path = raw_root / f"{trace_id}.jsonl"
    raw_text = json.dumps(
        {"task_id": task_id, "actual_skill_body_loads": list(invoked)},
        sort_keys=True,
    ) + "\n"
    raw_path.write_text(raw_text, encoding="utf-8")
    agent_contract = AgentRunContract(
        run_id=trace_id,
        task_id=task_id,
        condition="m2k-provisional-route-policy",
        workspace_root=str(workspace.resolve()),
        raw_trace_root=str(raw_root.resolve()),
        agent_id=contract.base_agent_id,
        agent_version=contract.base_agent_version,
        backend=contract.backend,
        model_id=contract.model_id,
        effort=contract.effort,
        budget_id=contract.budget_id,
        library_snapshot_id=contract.library_snapshot.snapshot_id,
        library_snapshot_sha256=contract.library_snapshot.snapshot_sha256,
        verifier_id=dict(contract.verifier_ids_by_task)[task_id],
    )
    result = AgentRunResult(
        contract=agent_contract,
        workspace_root=str(workspace.resolve()),
        raw_trace=RawTraceReference(
            pointer=raw_path.relative_to(raw_root).as_posix(),
            sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        ),
        actual_invocation_evidence_complete=True,
        selected_skill_ids=list(invoked),
        invocation_events=[
            SkillInvocationEvent(
                skill_id=skill_id,
                event_kind="skill_body_loaded",
                source="controlled-m2k-reevaluation-fixture",
                event_id=f"{trace_id}-load-{index}",
                sequence=index,
            )
            for index, skill_id in enumerate(invoked)
        ],
    )
    oracle = () if task_id == "management-no-oracle" else ("oracle",)
    return TraceRecord(
        id=trace_id,
        task_id=task_id,
        condition=agent_contract.condition,
        invocation=InvocationRecord(
            task_id=task_id,
            provisioned_skill_ids=list(provisioned_ids),
            selected_skill_ids=list(invoked),
            oracle_skill_ids=list(oracle),
            success=passed,
            score=1.0 if passed else 0.0,
            cost=0.01,
            latency_s=0.1,
        ),
        validation=[
            ValidationResult(
                name=dict(contract.verifier_ids_by_task)[task_id],
                passed=passed,
                score=1.0 if passed else 0.0,
                cost=0.01,
            )
        ],
        failure_label=None if passed else "verifier_failed",
        metadata={
            "latency_s": 0.1,
            "agent_run_evidence": serialize_agent_run_evidence(result),
            LINEAGE_METADATA_KEY: (
                policy_lineage_payload(candidate) if lineage is None else lineage
            ),
        },
    )


class FixtureExecutor:
    def __init__(
        self,
        root: Path,
        contract,
        *,
        wrong_invoked: tuple[str, ...] = ("oracle",),
        oracle_passed: bool = True,
        lineage: dict[str, object] | None = None,
        wrong_provisioned: tuple[str, ...] | None = None,
    ) -> None:
        self.root = root
        self.contract = contract
        self.wrong_invoked = wrong_invoked
        self.oracle_passed = oracle_passed
        self.lineage = lineage
        self.wrong_provisioned = wrong_provisioned
        self.candidate: RoutePolicyCandidate | None = None

    def run(self, candidate: RoutePolicyCandidate):
        self.candidate = candidate
        return (
            _make_trace(
                self.root,
                self.contract,
                candidate,
                "management-oracle",
                invoked=("oracle",),
                passed=self.oracle_passed,
                lineage=self.lineage,
            ),
            _make_trace(
                self.root,
                self.contract,
                candidate,
                "management-wrong",
                invoked=self.wrong_invoked,
                passed=self.wrong_invoked == ("oracle",),
                lineage=self.lineage,
                provisioned=self.wrong_provisioned,
            ),
            _make_trace(
                self.root,
                self.contract,
                candidate,
                "management-no-oracle",
                invoked=("utility",),
                passed=True,
                lineage=self.lineage,
            ),
        )


class M2KLifecycleReevaluationTests(unittest.TestCase):
    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        contract = _contract(_snapshot())
        traces = {
            arm: _traces_for_arm(root / "baseline", contract, arm)
            for arm in ManagementArm
        }
        round_input = {
            item.arm: item for item in _inputs(contract, traces)
        }[ManagementArm.M2_K]
        return root, contract, traces[ManagementArm.M2_K], round_input

    def test_route_guard_is_staged_task_locally_without_mutating_library(self) -> None:
        _, _, _, round_input = self._fixture()
        plan = run_management_round(round_input)
        candidate = stage_m2k_route_policy(round_input, plan)

        exposures = {
            item.task_id: item.skill_ids for item in candidate.exposure_decisions
        }
        self.assertEqual(exposures["management-oracle"], SKILL_IDS)
        self.assertEqual(
            exposures["management-wrong"], ("oracle", "utility")
        )
        self.assertEqual(exposures["management-no-oracle"], SKILL_IDS)
        self.assertEqual(
            [(item.task_id, item.skill_id) for item in candidate.guards],
            [("management-wrong", "distractor")],
        )
        self.assertTrue(candidate.applied)
        self.assertFalse(candidate.underlying_library_mutated)

    def test_same_contract_reexecution_promotes_recovered_route_policy(self) -> None:
        root, contract, baseline, round_input = self._fixture()
        executor = FixtureExecutor(root / "provisional", contract)

        result = run_m2k_lifecycle_reevaluation(
            round_input=round_input,
            baseline_traces=baseline,
            executor=executor,
        )

        self.assertTrue(result.accepted)
        self.assertFalse(result.rollback_required)
        self.assertEqual(result.resolution, "provisional_route_policy_promoted")
        self.assertEqual(
            result.resolved_policy_snapshot_sha256,
            result.candidate.policy_snapshot_sha256,
        )
        self.assertTrue(all(check.passed for check in result.checks))
        checks = {check.name: check for check in result.checks}
        self.assertAlmostEqual(checks["pass_rate_non_regression"].score, 1 / 3)
        self.assertAlmostEqual(checks["clean_oracle_non_regression"].score, 0.5)
        self.assertAlmostEqual(checks["shadowing_reduction"].score, 0.5)
        report = result.to_dict()
        self.assertTrue(report["evidence_boundary"]["actual_invocation_required"])
        self.assertFalse(report["evidence_boundary"]["underlying_library_mutated"])

    def test_metric_regression_rolls_back_to_parent_policy(self) -> None:
        root, contract, baseline, round_input = self._fixture()
        executor = FixtureExecutor(
            root / "provisional",
            contract,
            wrong_invoked=("utility",),
            oracle_passed=False,
        )

        result = run_m2k_lifecycle_reevaluation(
            round_input=round_input,
            baseline_traces=baseline,
            executor=executor,
        )

        self.assertFalse(result.accepted)
        self.assertTrue(result.rollback_required)
        self.assertEqual(result.resolution, "provisional_route_policy_rolled_back")
        self.assertNotEqual(
            result.resolved_policy_snapshot_sha256,
            result.candidate.policy_snapshot_sha256,
        )
        failed = {check.name for check in result.checks if not check.passed}
        self.assertIn("pass_rate_non_regression", failed)
        self.assertIn("shadowing_reduction", failed)

    def test_unbound_lineage_and_exposure_drift_fail_closed(self) -> None:
        root, contract, baseline, round_input = self._fixture()
        unbound = FixtureExecutor(
            root / "unbound",
            contract,
            lineage={"schema_version": 1, "wrong": True},
        )
        result = run_m2k_lifecycle_reevaluation(
            round_input=round_input,
            baseline_traces=baseline,
            executor=unbound,
        )
        self.assertFalse(result.accepted)
        self.assertFalse(
            next(
                check for check in result.checks if check.name == "policy_lineage_bound"
            ).passed
        )

        drifted = FixtureExecutor(
            root / "drifted",
            contract,
            wrong_provisioned=SKILL_IDS,
        )
        with self.assertRaisesRegex(
            ManagementContractError, "exact staged exposure"
        ):
            run_m2k_lifecycle_reevaluation(
                round_input=round_input,
                baseline_traces=baseline,
                executor=drifted,
            )

        bypass = FixtureExecutor(
            root / "bypass",
            contract,
            wrong_invoked=("distractor",),
        )
        with self.assertRaisesRegex(
            ManagementContractError, "outside the staged exposure"
        ):
            run_m2k_lifecycle_reevaluation(
                round_input=round_input,
                baseline_traces=baseline,
                executor=bypass,
            )

    def test_stricter_threshold_can_force_rollback(self) -> None:
        root, contract, baseline, round_input = self._fixture()
        result = run_m2k_lifecycle_reevaluation(
            round_input=round_input,
            baseline_traces=baseline,
            executor=FixtureExecutor(root / "provisional", contract),
            criteria=M2KReevaluationCriteria(min_pi_m_reduction=0.75),
        )
        self.assertFalse(result.accepted)
        self.assertTrue(result.rollback_required)
        self.assertFalse(
            next(
                check for check in result.checks if check.name == "shadowing_reduction"
            ).passed
        )


if __name__ == "__main__":
    unittest.main()
