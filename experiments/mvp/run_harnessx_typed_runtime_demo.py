"""Run the deterministic HarnessX-inspired harness-policy demonstration.

This is next-version local implementation evidence.  It is intentionally
separate from the frozen DESKTOP 435-cell execution and makes no model-backed
performance claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.merlin_harness.harnessx_runtime import (
    BeforeModelEvent,
    HarnessRiskTier,
    HarnessXChangeManifest,
    HarnessXEditKind,
    HarnessXHook,
    HarnessXProcessorEdit,
    ModelResponseEvent,
    StepEndEvent,
    StepStartEvent,
    SystemPromptAppendProcessor,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
    ToolCallAllowlistProcessor,
    ToolCallEvent,
    ToolResultEvent,
    apply_harnessx_change_manifest,
    build_harnessx_runtime_from_variant,
    gate_harnessx_candidate,
    make_default_harnessx_registry,
    make_default_harnessx_runtime,
    processor_manifest_entry,
    snapshot_harnessx_variant,
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decision_payload(decision: Any) -> dict[str, Any]:
    return {
        "accepted": decision.accepted,
        "requires_approval": decision.requires_approval,
        "resolution": decision.resolution,
        "resolved_variant_id": decision.resolved_variant_id,
        "rollback_variant_id": decision.rollback_variant_id,
        "checks": [asdict(check) for check in decision.checks],
    }


def run_harnessx_typed_runtime_demo(output_dir: str | Path) -> dict[str, Any]:
    runtime = make_default_harnessx_runtime(
        system_prompt_suffix="\nApply governed skill-harness policy.",
        max_history_messages=2,
        max_user_content_chars=24,
        allowed_tool_calls=("read",),
        auto_approve_tools=("read",),
        always_require_approval_tools=("shell", "write"),
        max_tool_result_chars=16,
    )
    registry = make_default_harnessx_registry()

    emissions = [
        runtime.emit_sync(
            TaskStartEvent(event_id="e1", task_id="demo", system_prompt="You are Merlin.")
        ),
        runtime.emit_sync(
            StepStartEvent(
                event_id="e2",
                task_id="demo",
                step_index=1,
                history=("old", "recent", "latest"),
            )
        ),
        runtime.emit_sync(
            BeforeModelEvent(
                event_id="e3",
                task_id="demo",
                step_index=1,
                model_role="main",
                last_user_content="diagnose and repair the shadowed skill route",
            )
        ),
        runtime.emit_sync(
            ModelResponseEvent(
                event_id="e4",
                task_id="demo",
                step_index=1,
                response_content="inspect evidence",
                tool_calls=(ToolCall("read"), ToolCall("shell")),
            )
        ),
        runtime.emit_sync(
            ToolCallEvent(
                event_id="e5-safe",
                task_id="demo",
                step_index=1,
                tool_name="read",
                tool_input_json='{"path":"trace.json"}',
            )
        ),
        runtime.emit_sync(
            ToolCallEvent(
                event_id="e5-risky",
                task_id="demo",
                step_index=1,
                tool_name="shell",
                tool_input_json='{"command":"run"}',
            )
        ),
        runtime.emit_sync(
            ToolResultEvent(
                event_id="e6",
                task_id="demo",
                step_index=1,
                tool_name="read",
                tool_result="0123456789abcdefghijklmnopqrstuvwxyz",
            )
        ),
        runtime.emit_sync(
            StepEndEvent(event_id="e7", task_id="demo", step_index=1, status="ok")
        ),
        runtime.emit_sync(TaskEndEvent(event_id="e8", task_id="demo", status="ok")),
    ]

    parent = snapshot_harnessx_variant(
        runtime,
        variant_id="harnessx-demo-parent",
        summary="Eight-hook bounded parent harness",
        slots={"skill_policy": "governed-provisioning-v2"},
        policy={"approval_mode": "risk-tiered-selective"},
    )
    rebuilt = build_harnessx_runtime_from_variant(parent, registry)
    if rebuilt.manifest() != runtime.manifest():
        raise RuntimeError("rebuilt runtime manifest drift")

    low_manifest = HarnessXChangeManifest(
        id="trace-backed-prompt-repair",
        candidate_variant_id="harnessx-demo-low-risk-candidate",
        parent_variant_sha256=parent.sha256,
        rollback_variant_sha256=parent.sha256,
        rationale="Bounded trace-backed prompt policy repair",
        evidence_trace_ids=("demo-trace-001",),
        expected_improve_task_ids=("shadowed-skill-route",),
        expected_regress_task_ids=(),
        risk_tier=HarnessRiskTier.LOW,
        edits=(
            HarnessXProcessorEdit(
                kind=HarnessXEditKind.REPLACE,
                hook=HarnessXHook.TASK_START,
                singleton_group="system_prompt_policy",
                dimension="D2",
                processor=processor_manifest_entry(
                    SystemPromptAppendProcessor(
                        suffix="\nApply trace-repaired governed skill-harness policy."
                    )
                ),
            ),
        ),
    )
    low_candidate = apply_harnessx_change_manifest(
        parent,
        low_manifest,
        registry,
        summary="Low-risk reversible trace-backed repair",
    )
    low_decision = gate_harnessx_candidate(
        parent=parent,
        candidate=low_candidate,
        manifest=low_manifest,
        smoke_passed=True,
        previously_passing_task_ids=("known-good-route",),
        candidate_task_outcomes={"known-good-route": True, "shadowed-skill-route": True},
    )

    high_manifest = HarnessXChangeManifest(
        id="expanded-tool-policy",
        candidate_variant_id="harnessx-demo-high-risk-candidate",
        parent_variant_sha256=low_candidate.sha256,
        rollback_variant_sha256=low_candidate.sha256,
        rationale="Expand model-visible tool-call policy",
        evidence_trace_ids=("demo-trace-002",),
        expected_improve_task_ids=("write-required-route",),
        expected_regress_task_ids=(),
        risk_tier=HarnessRiskTier.HIGH,
        edits=(
            HarnessXProcessorEdit(
                kind=HarnessXEditKind.REPLACE,
                hook=HarnessXHook.AFTER_MODEL,
                singleton_group="tool_call_policy",
                dimension="D4",
                processor=processor_manifest_entry(
                    ToolCallAllowlistProcessor(allowed_tools=("read", "write"))
                ),
            ),
        ),
    )
    high_candidate = apply_harnessx_change_manifest(
        low_candidate,
        high_manifest,
        registry,
        summary="High-risk tool-policy candidate awaiting approval",
    )
    high_decision = gate_harnessx_candidate(
        parent=low_candidate,
        candidate=high_candidate,
        manifest=high_manifest,
        smoke_passed=True,
        previously_passing_task_ids=("known-good-route", "shadowed-skill-route"),
        candidate_task_outcomes={"known-good-route": True, "shadowed-skill-route": True},
    )

    audits = [record for emission in emissions for record in emission.audit]
    before_model = emissions[2].events[0]
    after_model = emissions[3].events[0]
    safe_call = emissions[4].events[0]
    risky_call = emissions[5].events[0]
    tool_result = emissions[6].events[0]
    report: dict[str, Any] = {
        "schema_version": "merlin-harnessx-typed-runtime-demo-v1",
        "evidence_class": "deterministic_local_implementation",
        "frozen_435_execution_included": False,
        "hook_coverage": sorted({record.hook.value for record in audits}),
        "hook_coverage_count": len({record.hook for record in audits}),
        "processor_manifest_count": len(runtime.manifest()),
        "audit_record_count": len(audits),
        "runtime_observations": {
            "system_prompt_transformed": emissions[0].events[0].system_prompt.endswith(
                "Apply governed skill-harness policy."
            ),
            "history_window": list(emissions[1].events[0].history),
            "model_input_chars_after_limit": len(before_model.last_user_content),
            "allowed_model_tool_calls": [call.name for call in after_model.tool_calls],
            "read_requires_approval": safe_call.approval_required,
            "shell_requires_approval": risky_call.approval_required,
            "tool_result_chars_after_limit": len(tool_result.tool_result),
        },
        "variant_chain": {
            "parent_sha256": parent.sha256,
            "low_candidate_sha256": low_candidate.sha256,
            "high_candidate_sha256": high_candidate.sha256,
            "round_trip_manifest_equal": rebuilt.manifest() == runtime.manifest(),
        },
        "low_risk_reversible_change": _decision_payload(low_decision),
        "high_risk_change": _decision_payload(high_decision),
    }
    report["evidence_sha256"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "harnessx_typed_runtime.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the typed HarnessX policy demo.")
    parser.add_argument(
        "--output",
        default="experiments/mvp/results/harnessx_typed_runtime_v1",
        help="Output directory for the deterministic JSON evidence.",
    )
    args = parser.parse_args(argv)
    report = run_harnessx_typed_runtime_demo(args.output)
    print("Merlin HarnessX typed-runtime demo")
    print(f"hooks={report['hook_coverage_count']}/8")
    print(
        "low_risk="
        f"{report['low_risk_reversible_change']['resolution']}"
    )
    print(
        "high_risk="
        f"{report['high_risk_change']['resolution']}"
    )
    print(f"evidence_sha256={report['evidence_sha256']}")
    print(f"saved -> {Path(args.output) / 'harnessx_typed_runtime.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
