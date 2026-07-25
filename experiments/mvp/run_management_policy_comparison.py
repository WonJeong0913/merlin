"""Create a network-free, controlled M0/M1/M2-H/M2-K comparison artifact.

The traces in this script are explicitly synthetic fixtures with provider-like
actual skill-body-load events.  They are not the Build Week 10-task traces and
are not evidence of a real model's skill invocation behaviour.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.merlin_harness.management import (
    DecisionAction,
    LibrarySnapshotIdentity,
    M2KTraceEvidence,
    ManagementArm,
    ManagementRoundInput,
    ManagementRunContract,
    ManagementThresholds,
    TaskExposure,
    TelemetryEvidence,
    build_management_round_report,
    compare_management_reports,
    management_report_to_dict,
    run_management_round,
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
from src.merlin_harness.traces import FileTraceStore, serialize_agent_run_evidence, validate_agent_trace_evidence


TASK_IDS = ("management-oracle", "management-wrong", "management-no-oracle")
SKILL_IDS = ("oracle", "distractor", "utility")


def _snapshot() -> LibrarySnapshotIdentity:
    return LibrarySnapshotIdentity(
        snapshot_id="management-fixture-library-v1",
        snapshot_sha256=hashlib.sha256(b"management-fixture-library-v1").hexdigest(),
        active_skill_ids=SKILL_IDS,
        active_library_capacity=len(SKILL_IDS),
    )


def _contract(snapshot: LibrarySnapshotIdentity) -> ManagementRunContract:
    return ManagementRunContract(
        library_snapshot=snapshot,
        split_id="controlled-management-fixture-v1",
        task_ids=TASK_IDS,
        base_agent_id="controlled-fixture-agent",
        base_agent_version="1",
        backend="synthetic-actual-event-fixture",
        model_id="no-network-fixture",
        effort="none",
        tools=("synthetic_skill_runtime",),
        verifier_ids_by_task=tuple((task_id, "exact") for task_id in TASK_IDS),
        budget_id="fixture-budget-v1",
        repeats=1,
    )


def _make_trace(
    output: Path,
    contract: ManagementRunContract,
    arm: ManagementArm,
    task_id: str,
    *,
    selected: tuple[str, ...],
    invoked: tuple[str, ...],
    oracle: tuple[str, ...],
    passed: bool,
    latency_s: float | None,
) -> TraceRecord:
    """Write a fixture raw trace and normalize explicit body-load events."""

    trace_id = f"fixture-{arm.value.lower().replace('-', '')}-{task_id}"
    workspace = output / "workspaces" / arm.value / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    raw_root = output / "raw" / arm.value
    raw_root.mkdir(parents=True, exist_ok=True)
    raw_path = raw_root / f"{trace_id}.jsonl"
    raw_payload = {
        "fixture": "controlled-synthetic-actual-event",
        "arm": arm.value,
        "task_id": task_id,
        "actual_skill_body_loads": list(invoked),
    }
    raw_text = json.dumps(raw_payload, sort_keys=True) + "\n"
    raw_path.write_text(raw_text, encoding="utf-8")
    agent_contract = AgentRunContract(
        run_id=trace_id,
        task_id=task_id,
        condition=f"controlled-{arm.value}",
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
        verifier_id="exact",
    )
    result = AgentRunResult(
        contract=agent_contract,
        workspace_root=str(workspace.resolve()),
        raw_trace=RawTraceReference(
            pointer=raw_path.relative_to(raw_root).as_posix(),
            sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        ),
        actual_invocation_evidence_complete=True,
        selected_skill_ids=list(selected),
        invocation_events=[
            SkillInvocationEvent(
                skill_id=skill_id,
                event_kind="skill_body_loaded",
                source="controlled-synthetic-fixture",
                event_id=f"{trace_id}-load-{index}",
                sequence=index,
            )
            for index, skill_id in enumerate(invoked)
        ],
    )
    return TraceRecord(
        id=trace_id,
        task_id=task_id,
        condition=agent_contract.condition,
        invocation=InvocationRecord(
            task_id=task_id,
            provisioned_skill_ids=list(SKILL_IDS),
            selected_skill_ids=list(selected),
            oracle_skill_ids=list(oracle),
            success=passed,
            score=1.0 if passed else 0.0,
            cost=0.01,
            latency_s=latency_s,
        ),
        validation=[ValidationResult(name="exact", passed=passed, score=1.0 if passed else 0.0, cost=0.01)],
        failure_label=None if passed else "verifier_failed",
        metadata={
            "workspace": str(workspace.resolve()),
            "latency_s": latency_s,
            "agent_run_evidence": serialize_agent_run_evidence(result),
            "fixture_notice": "synthetic actual-event fixture; not provider/model evidence",
        },
    )


def _traces_for_arm(output: Path, contract: ManagementRunContract, arm: ManagementArm) -> tuple[TraceRecord, ...]:
    return (
        _make_trace(
            output, contract, arm, "management-oracle", selected=("oracle",), invoked=("oracle",), oracle=("oracle",), passed=True, latency_s=0.2
        ),
        _make_trace(
            output, contract, arm, "management-wrong", selected=("distractor",), invoked=("distractor",), oracle=("oracle",), passed=False, latency_s=0.3
        ),
        _make_trace(
            output, contract, arm, "management-no-oracle", selected=("utility",), invoked=("utility",), oracle=(), passed=True, latency_s=None
        ),
    )


def _expanded_exposure() -> tuple[TaskExposure, ...]:
    return tuple(TaskExposure(task_id, SKILL_IDS) for task_id in TASK_IDS)


def _m1_exposure() -> tuple[TaskExposure, ...]:
    return (
        TaskExposure("management-oracle", ("oracle",)),
        TaskExposure("management-wrong", ("distractor",)),
        TaskExposure("management-no-oracle", ("utility",)),
    )


def _inputs(contract: ManagementRunContract, traces: dict[ManagementArm, tuple[TraceRecord, ...]]) -> tuple[ManagementRoundInput, ...]:
    thresholds = ManagementThresholds(m2h_max_usage=0, m2h_min_recency_rank=5, m2k_min_shadowing_events=1)
    common = {"contract": contract, "parent_snapshot": contract.library_snapshot, "thresholds": thresholds}
    m2k_rows = traces[ManagementArm.M2_K]
    return (
        ManagementRoundInput(
            arm=ManagementArm.M0,
            policy_version="m0-expanded-v1",
            allowed_actions=(),
            predeclared_exposure=_expanded_exposure(),
            **common,
        ),
        ManagementRoundInput(
            arm=ManagementArm.M1,
            policy_version="m1-fixed-top-k-v1",
            allowed_actions=(),
            fixed_top_k_exposure=_m1_exposure(),
            **common,
        ),
        ManagementRoundInput(
            arm=ManagementArm.M2_H,
            policy_version="m2h-telemetry-v1",
            allowed_actions=(DecisionAction.HIDE_SKILL,),
            predeclared_exposure=_expanded_exposure(),
            m2h_telemetry=(
                TelemetryEvidence(
                    trace_id=traces[ManagementArm.M2_H][1].id,
                    skill_id="distractor",
                    usage_count=0,
                    view_count=1,
                    patch_count=0,
                    recency_rank=9,
                ),
            ),
            **common,
        ),
        ManagementRoundInput(
            arm=ManagementArm.M2_K,
            policy_version="m2k-actual-invocation-v1",
            allowed_actions=(DecisionAction.GUARD_ROUTE,),
            predeclared_exposure=_expanded_exposure(),
            m2k_evidence=tuple(
                M2KTraceEvidence(
                    trace=trace,
                    parent_verifier_passed=True,
                    regression_group="controlled-management-fixture",
                )
                for trace in m2k_rows
            ),
            **common,
        ),
    )


def reuse_codex_smoke_artifact(trace_path: str | Path) -> dict[str, object]:
    """Validate the already-recorded Codex smoke as an incomplete M2-K row.

    It reads and rehashes the artifact only.  It does not start Codex or make a
    provider request.
    """

    path = Path(trace_path).resolve()
    stored = FileTraceStore(path.parent).load(path.stem)
    evidence = validate_agent_trace_evidence(stored, verify_raw_trace=True)
    snapshot = LibrarySnapshotIdentity(
        snapshot_id=evidence.contract.library_snapshot_id,
        snapshot_sha256=evidence.contract.library_snapshot_sha256,
        active_skill_ids=(),
        active_library_capacity=0,
    )
    contract = ManagementRunContract(
        library_snapshot=snapshot,
        split_id="recorded-codex-smoke-only",
        task_ids=(stored.task_id,),
        base_agent_id=evidence.contract.agent_id,
        base_agent_version=evidence.contract.agent_version,
        backend=evidence.contract.backend,
        model_id=evidence.contract.model_id,
        effort=evidence.contract.effort,
        tools=(),
        verifier_ids_by_task=((stored.task_id, evidence.contract.verifier_id),),
        budget_id=evidence.contract.budget_id,
        repeats=1,
    )
    round_input = ManagementRoundInput(
        contract=contract,
        arm=ManagementArm.M2_K,
        policy_version="m2k-codex-smoke-gap-v1",
        parent_snapshot=snapshot,
        thresholds=ManagementThresholds(),
        allowed_actions=(DecisionAction.GUARD_ROUTE,),
        predeclared_exposure=(TaskExposure(stored.task_id, ()),),
        m2k_evidence=(M2KTraceEvidence(stored, parent_verifier_passed=True, regression_group="smoke-only"),),
    )
    report = build_management_round_report(run_management_round(round_input), [stored])
    return {
        "scope": "read-only reuse of recorded Codex smoke; no provider/model re-execution",
        "trace_id": stored.id,
        "raw_trace_sha256": evidence.raw_trace.sha256,
        "actual_invocation_evidence_complete": evidence.actual_invocation_evidence_complete,
        "m2k_decision_denominator": management_report_to_dict(report.output.decision_evidence_denominator),
        "paper_metric_eligible": report.metrics.actual_metric_eligible,
        "paper_metric_incomplete": report.metrics.actual_evidence_incomplete,
        "n_count": report.metrics.n_count,
        "m_count": report.metrics.m_count,
        "o_count": report.metrics.o_count,
    }


def build_controlled_comparison(output_dir: str | Path, *, codex_smoke_trace: str | Path | None = None) -> dict[str, object]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot = _snapshot()
    contract = _contract(snapshot)
    traces = {arm: _traces_for_arm(output, contract, arm) for arm in ManagementArm}
    reports = tuple(
        build_management_round_report(run_management_round(round_input), traces[round_input.arm])
        for round_input in _inputs(contract, traces)
    )
    comparison = compare_management_reports(reports)
    report: dict[str, object] = {
        "scope": (
            "network-free controlled synthetic actual-event fixtures; not Build Week traces, "
            "not a real provider skill-invocation benchmark, and no library mutation"
        ),
        "comparison": management_report_to_dict(comparison),
    }
    if codex_smoke_trace is not None:
        report["recorded_codex_smoke_reuse"] = reuse_codex_smoke_artifact(codex_smoke_trace)
    (output / "management_policy_comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the deterministic Merlin management-policy comparison report.")
    parser.add_argument(
        "--output",
        default="experiments/mvp/results/management_policy_comparison",
        help="Excluded results directory for the controlled report.",
    )
    parser.add_argument(
        "--codex-smoke-trace",
        default="experiments/mvp/results/agent_trace_contract/live_gpt56/traces/codex-gpt56-smoke-20260718T042332Z-f2315a0c.json",
        help="Existing immutable Codex smoke trace to rehash and classify without model execution.",
    )
    args = parser.parse_args(argv)
    report = build_controlled_comparison(args.output, codex_smoke_trace=args.codex_smoke_trace)
    comparison = report["comparison"]
    assert isinstance(comparison, dict)
    print("Merlin management policy comparison")
    print(f"common_contract_sha256={comparison['common_contract_sha256']}")
    print("arms=M0,M1,M2-H,M2-K")
    print("M2-H=telemetry-only skill-local hide; M2-K=complete-evidence route-local guard")
    print(f"saved -> {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
