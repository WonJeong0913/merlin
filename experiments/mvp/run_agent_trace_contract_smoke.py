"""Run the network-free BaseAgentAdapter trace-contract smoke test.

This is not a model benchmark.  It proves one strict vertical path:

    fake base agent -> actual invocation evidence -> one verifier ->
    immutable trace -> read-only metric conversion
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.merlin_harness.agent_adapter import AgentRunRequest
from src.merlin_harness.metrics import trace_to_invocation_observation
from src.merlin_harness.models import (
    AgentRunContract,
    AgentRunResult,
    RawTraceReference,
    SkillInvocationEvent,
    TaskSpec,
    VerifierSpec,
)
from src.merlin_harness.provisioning import make_single_step_skill
from src.merlin_harness.runner import run_agent_adapter_once
from src.merlin_harness.traces import AGENT_TRACE_EVIDENCE_KEY, FileTraceStore


class SmokeFakeAdapter:
    """Selects a skill but reports no actual body-load event."""

    name = "smoke-fake-adapter"

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        raw_root = Path(request.contract.raw_trace_root)
        raw_root.mkdir(parents=True, exist_ok=True)
        raw_content = '{"type":"agent_finished","selected":"oracle"}\n'
        raw_path = raw_root / "smoke-agent.jsonl"
        raw_path.write_text(raw_content, encoding="utf-8")
        return AgentRunResult(
            contract=request.contract,
            workspace_root=str(request.workspace.resolve()),
            raw_trace=RawTraceReference(
                pointer=raw_path.relative_to(raw_root).as_posix(),
                sha256=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            ),
            actual_invocation_evidence_complete=True,
            selected_skill_ids=["oracle"],
            invocation_events=[],
            answer="ok",
            events=[{"type": "AGENT_ACTION", "action": "return_answer"}],
        )


def run_smoke(output_dir: str | Path) -> dict[str, object]:
    output = Path(output_dir).resolve()
    workspace = output / "workspace"
    raw_root = output / "raw"
    trace_store = FileTraceStore(output / "traces")
    task = TaskSpec(
        id="agent-trace-contract-smoke",
        instruction="Return ok",
        verifier=VerifierSpec(name="exact", kind="exact_match", expected="ok"),
        oracle_skill_ids=["oracle"],
    )
    oracle = make_single_step_skill(
        skill_id="oracle",
        name="Oracle",
        description="Return ok.",
        trigger="Use for return-ok tasks.",
        step_description="Return ok.",
    )
    contract = AgentRunContract(
        run_id="agent-trace-contract-smoke",
        task_id=task.id,
        condition="fake-adapter-smoke",
        workspace_root=str(workspace),
        raw_trace_root=str(raw_root),
        agent_id="smoke-fake-agent",
        agent_version="1.0",
        backend="fake",
        model_id="fake-model",
        effort="none",
        budget_id="smoke-budget-v1",
        library_snapshot_id="smoke-library-v1",
        library_snapshot_sha256=hashlib.sha256(b"smoke-library-v1").hexdigest(),
        verifier_id=task.verifier.name,
    )
    trace = run_agent_adapter_once(
        task=task,
        workspace=workspace,
        condition=contract.condition,
        contract=contract,
        adapter=SmokeFakeAdapter(),
        provisioned_skills=[oracle],
        trace_id="agent-trace-contract-smoke",
        trace_store=trace_store,
    )
    stored = trace_store.load(trace.id)
    observation = trace_to_invocation_observation(stored)
    evidence = stored.metadata[AGENT_TRACE_EVIDENCE_KEY]
    report: dict[str, object] = {
        "scope": "network-free fake adapter contract smoke; not a model benchmark",
        "trace_id": stored.id,
        "verifier_passed": bool(stored.invocation and stored.invocation.success),
        "selected_skill_ids": list(stored.invocation.selected_skill_ids if stored.invocation else []),
        "actual_invoked_skill_ids": list(observation.invoked_skill_ids),
        "actual_invocation_evidence_complete": evidence["actual_invocation_evidence_complete"],
        "raw_trace": evidence["raw_trace"],
        "trace_path": str(trace_store.trace_path(stored.id)),
    }
    (output / "agent_trace_contract_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Merlin agent trace contract smoke.")
    parser.add_argument("--output", required=True, help="Directory for raw evidence, immutable trace, and summary.")
    args = parser.parse_args(argv)
    report = run_smoke(args.output)
    print("Merlin agent trace contract smoke")
    print(f"verifier_passed={report['verifier_passed']}")
    print(f"selected_skill_ids={report['selected_skill_ids']}")
    print(f"actual_invoked_skill_ids={report['actual_invoked_skill_ids']}")
    print(f"actual_invocation_evidence_complete={report['actual_invocation_evidence_complete']}")
    print(f"saved -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
