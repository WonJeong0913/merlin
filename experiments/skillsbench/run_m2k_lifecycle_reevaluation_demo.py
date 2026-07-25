"""Run the controlled M2-K plan -> apply -> re-evaluate -> promote loop.

This command is deliberately model-free.  Its skill-body-load events are
synthetic controlled fixtures used to exercise the evidence and rollback
contract; they are not a GPT-5.6, provider-native, or full-87 result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.mvp.run_management_policy_comparison import (
    _contract,
    _inputs,
    _snapshot,
    _traces_for_arm,
)
from experiments.skillsbench.management_lifecycle_reevaluation import (
    LINEAGE_METADATA_KEY,
    RoutePolicyCandidate,
    policy_lineage_payload,
    run_m2k_lifecycle_reevaluation,
)
from src.merlin_harness.management import ManagementArm
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


class ControlledRecoveredRouteExecutor:
    """Re-run the three frozen fixture tasks under the staged route policy."""

    def __init__(self, output: Path, contract) -> None:
        self.output = output
        self.contract = contract

    def _trace(
        self,
        candidate: RoutePolicyCandidate,
        task_id: str,
        *,
        invoked: tuple[str, ...],
        oracle: tuple[str, ...],
        passed: bool,
    ) -> TraceRecord:
        trace_id = f"controlled-m2k-provisional-{task_id}"
        exposures = {
            item.task_id: item.skill_ids for item in candidate.exposure_decisions
        }
        provisioned = exposures[task_id]
        workspace = self.output / "workspaces" / task_id
        raw_root = self.output / "raw"
        workspace.mkdir(parents=True, exist_ok=True)
        raw_root.mkdir(parents=True, exist_ok=True)
        raw_path = raw_root / f"{trace_id}.jsonl"
        raw_text = json.dumps(
            {
                "fixture": "controlled-synthetic-actual-event",
                "task_id": task_id,
                "actual_skill_body_loads": list(invoked),
            },
            sort_keys=True,
        ) + "\n"
        raw_path.write_text(raw_text, encoding="utf-8")
        verifier_id = dict(self.contract.verifier_ids_by_task)[task_id]
        contract = AgentRunContract(
            run_id=trace_id,
            task_id=task_id,
            condition="controlled-m2k-provisional-route-policy",
            workspace_root=str(workspace.resolve()),
            raw_trace_root=str(raw_root.resolve()),
            agent_id=self.contract.base_agent_id,
            agent_version=self.contract.base_agent_version,
            backend=self.contract.backend,
            model_id=self.contract.model_id,
            effort=self.contract.effort,
            budget_id=self.contract.budget_id,
            library_snapshot_id=self.contract.library_snapshot.snapshot_id,
            library_snapshot_sha256=self.contract.library_snapshot.snapshot_sha256,
            verifier_id=verifier_id,
        )
        result = AgentRunResult(
            contract=contract,
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
        return TraceRecord(
            id=trace_id,
            task_id=task_id,
            condition=contract.condition,
            invocation=InvocationRecord(
                task_id=task_id,
                provisioned_skill_ids=list(provisioned),
                selected_skill_ids=list(invoked),
                oracle_skill_ids=list(oracle),
                success=passed,
                score=1.0 if passed else 0.0,
                cost=0.01,
                latency_s=0.1,
            ),
            validation=[
                ValidationResult(
                    name=verifier_id,
                    passed=passed,
                    score=1.0 if passed else 0.0,
                    cost=0.01,
                )
            ],
            failure_label=None if passed else "verifier_failed",
            metadata={
                "latency_s": 0.1,
                "agent_run_evidence": serialize_agent_run_evidence(result),
                LINEAGE_METADATA_KEY: policy_lineage_payload(candidate),
                "fixture_notice": (
                    "synthetic actual-event fixture; not provider/model evidence"
                ),
            },
        )

    def run(self, candidate: RoutePolicyCandidate):
        return (
            self._trace(
                candidate,
                "management-oracle",
                invoked=("oracle",),
                oracle=("oracle",),
                passed=True,
            ),
            self._trace(
                candidate,
                "management-wrong",
                invoked=("oracle",),
                oracle=("oracle",),
                passed=True,
            ),
            self._trace(
                candidate,
                "management-no-oracle",
                invoked=("utility",),
                oracle=(),
                passed=True,
            ),
        )


def run_demo(output: Path) -> dict[str, object]:
    output = output.expanduser().resolve(strict=False)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite M2-K evidence root: {output}")
    output.mkdir(parents=True)

    contract = _contract(_snapshot())
    baseline_by_arm = {
        arm: _traces_for_arm(output / "baseline", contract, arm)
        for arm in ManagementArm
    }
    round_input = {
        item.arm: item for item in _inputs(contract, baseline_by_arm)
    }[ManagementArm.M2_K]
    result = run_m2k_lifecycle_reevaluation(
        round_input=round_input,
        baseline_traces=baseline_by_arm[ManagementArm.M2_K],
        executor=ControlledRecoveredRouteExecutor(output / "provisional", contract),
    )
    report = result.to_dict()
    report["experiment"] = {
        "name": "controlled-m2k-lifecycle-reevaluation-v1",
        "scope": (
            "network-free controlled synthetic actual-event fixture; validates the "
            "M2-K apply/re-evaluate/promotion-or-rollback contract only"
        ),
        "claims_not_made": [
            "GPT-5.6 execution",
            "provider-native skill invocation",
            "full-87 management result",
            "production performance",
        ],
    }
    evidence_path = output / "m2k_lifecycle_reevaluation.json"
    evidence_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_demo(args.output)
    checks = report["checks"]
    assert isinstance(checks, list)
    print("Merlin controlled M2-K lifecycle re-evaluation")
    print(f"accepted={str(report['accepted']).lower()}")
    print(f"guards={len(report['candidate']['guards'])}")
    print(f"checks={sum(bool(item['passed']) for item in checks)}/{len(checks)}")
    print(f"resolution={report['resolution']}")
    print(f"saved -> {(args.output.resolve() / 'm2k_lifecycle_reevaluation.json')}")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
