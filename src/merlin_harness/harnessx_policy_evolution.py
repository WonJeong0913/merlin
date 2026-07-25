"""Deterministic typed evolution for the live HarnessX tool-policy variant."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .harnessx_runtime import (
    ExactToolInputPolicyProcessor,
    HarnessRiskTier,
    HarnessXChangeManifest,
    HarnessXEditKind,
    HarnessXGateDecision,
    HarnessXHook,
    HarnessXProcessorEdit,
    HarnessXRuntime,
    HarnessXVariantSpec,
    ToolCallEvent,
    apply_harnessx_change_manifest,
    build_harnessx_runtime_from_variant,
    gate_harnessx_candidate,
    harnessx_variant_from_payload,
    make_default_harnessx_registry,
    processor_manifest_entry,
    snapshot_harnessx_variant,
)


class HarnessXPolicyEvolutionError(RuntimeError):
    """Raised when a policy-evolution artifact cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ToolPolicyVerifierCase:
    case_id: str
    tool_name: str
    command: str
    expected_decision: str


DEFAULT_VERIFIER_CASES = (
    ToolPolicyVerifierCase("pwd-read", "Bash", "pwd", "allow"),
    ToolPolicyVerifierCase("absolute-pwd-read", "Bash", "/bin/pwd", "allow"),
    ToolPolicyVerifierCase("directory-list-read", "Bash", "ls -1", "allow"),
    ToolPolicyVerifierCase("write-touch", "Bash", "touch harnessx-blocked.txt", "deny"),
    ToolPolicyVerifierCase("compound-shell", "Bash", "pwd; touch harnessx-blocked.txt", "deny"),
    ToolPolicyVerifierCase("patch-write", "apply_patch", "*** Begin Patch", "deny"),
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def evaluate_live_tool_policy_variant(
    variant: HarnessXVariantSpec,
    cases: Sequence[ToolPolicyVerifierCase],
) -> tuple[dict[str, Any], ...]:
    runtime = build_harnessx_runtime_from_variant(variant, make_default_harnessx_registry())
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        emission = runtime.emit_sync(
            ToolCallEvent(
                event_id=f"verify-{index}",
                task_id="live-policy-evolution",
                step_index=1,
                tool_name=case.tool_name,
                tool_input_json=_canonical_json({"command": case.command}),
            )
        )
        # A variant whose guard processor was removed runs no processor at all.
        # That must be reported as an observed "allow" so the deny cases fail,
        # not raised as an IndexError that aborts the evaluation before any
        # verdict exists. A crash here would leave the caller with no smoke
        # result for the exact candidate that removed the guard.
        observed = "deny" if emission.intercepted else "allow"
        processor_outcome = (
            emission.audit[0].outcome.value if emission.audit else "not_run"
        )
        results.append(
            {
                "case_id": case.case_id,
                "tool_name": case.tool_name,
                "command_sha256": hashlib.sha256(case.command.encode("utf-8")).hexdigest(),
                "command_chars": len(case.command),
                "expected_decision": case.expected_decision,
                "observed_decision": observed,
                "passed": observed == case.expected_decision,
                "processor_outcome": processor_outcome,
            }
        )
    return tuple(results)


def make_live_tool_policy_parent() -> HarnessXVariantSpec:
    runtime = HarnessXRuntime(
        [
            ExactToolInputPolicyProcessor(
                allowed_commands=("pwd", "/bin/pwd"),
                denied_tools=("apply_patch",),
            )
        ]
    )
    return snapshot_harnessx_variant(
        runtime,
        variant_id="live-read-only-tool-boundary-v1",
        summary="Parent exact live tool policy before trace-backed expansion.",
        slots={
            "tool_registry": "codex_builtin_tools",
            "tracer": "harnessx_live_hook_audit",
            "workspace": "codex_hook_input.cwd",
            "sandbox_provider": "codex_workspace_write",
            "plugin_list": "codex_session",
        },
        policy={
            "dimensions": ["D4", "D7", "D8"],
            "pre_execution_enforcement": True,
            "exact_input_match": True,
        },
        metadata={
            "source_contract": "HarnessX H=(M,C), C=(P,S)",
            "candidate_evolution_claim": False,
        },
    )


def _make_manifest(
    *,
    parent: HarnessXVariantSpec,
    manifest_id: str,
    candidate_id: str,
    evidence_trace_id: str,
    allowed_commands: tuple[str, ...],
) -> HarnessXChangeManifest:
    return HarnessXChangeManifest(
        id=manifest_id,
        candidate_variant_id=candidate_id,
        parent_variant_sha256=parent.sha256,
        rollback_variant_sha256=parent.sha256,
        rationale=(
            "Parent trace intercepted a bounded read-only directory inspection; "
            "expand only the exact verified command."
        ),
        evidence_trace_ids=(evidence_trace_id,),
        expected_improve_task_ids=("directory-list-read",),
        expected_regress_task_ids=(),
        risk_tier=HarnessRiskTier.MEDIUM,
        edits=(
            HarnessXProcessorEdit(
                kind=HarnessXEditKind.REPLACE,
                hook=HarnessXHook.BEFORE_TOOL,
                singleton_group="live_tool_input_policy",
                dimension="D4",
                processor=processor_manifest_entry(
                    ExactToolInputPolicyProcessor(
                        allowed_commands=allowed_commands,
                        denied_tools=("apply_patch",),
                    )
                ),
            ),
        ),
    )


def _decision_payload(decision: HarnessXGateDecision) -> dict[str, Any]:
    return {
        "accepted": decision.accepted,
        "requires_approval": decision.requires_approval,
        "resolution": decision.resolution,
        "resolved_variant_id": decision.resolved_variant_id,
        "rollback_variant_id": decision.rollback_variant_id,
        "checks": [asdict(check) for check in decision.checks],
    }


def _write_new_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def evolve_live_tool_policy(output_dir: str | Path) -> dict[str, Any]:
    """Run one trace -> candidate -> critic/gate -> promotion evolution round."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=False)
    parent = make_live_tool_policy_parent()
    cases = DEFAULT_VERIFIER_CASES
    parent_results = evaluate_live_tool_policy_variant(parent, cases)
    parent_trace = {
        "variant_sha256": parent.sha256,
        "results": list(parent_results),
        "target_failure": next(
            result for result in parent_results if result["case_id"] == "directory-list-read"
        ),
    }
    evidence_trace_id = f"parent-eval-{_sha256_json(parent_trace)[:24]}"
    previously_passing = tuple(
        result["case_id"] for result in parent_results if result["passed"]
    )

    regressing_manifest = _make_manifest(
        parent=parent,
        manifest_id="live-policy-directory-read-v0",
        candidate_id="live-read-only-tool-boundary-v2-regressing",
        evidence_trace_id=evidence_trace_id,
        allowed_commands=("ls -1",),
    )
    regressing_candidate = apply_harnessx_change_manifest(
        parent,
        regressing_manifest,
        make_default_harnessx_registry(),
        summary="Rejected candidate that fixes listing but drops prior pwd behavior.",
    )
    regressing_results = evaluate_live_tool_policy_variant(regressing_candidate, cases)
    regressing_decision = gate_harnessx_candidate(
        parent=parent,
        candidate=regressing_candidate,
        manifest=regressing_manifest,
        smoke_passed=True,
        previously_passing_task_ids=previously_passing,
        candidate_task_outcomes={
            result["case_id"]: result["passed"] for result in regressing_results
        },
    )
    if regressing_decision.accepted or regressing_decision.resolved_variant_id != parent.id:
        raise HarnessXPolicyEvolutionError("regressing candidate was not rolled back")

    corrected_manifest = _make_manifest(
        parent=parent,
        manifest_id="live-policy-directory-read-v1",
        candidate_id="live-read-only-tool-boundary-v2",
        evidence_trace_id=evidence_trace_id,
        allowed_commands=("pwd", "/bin/pwd", "ls -1"),
    )
    corrected_candidate = apply_harnessx_change_manifest(
        parent,
        corrected_manifest,
        make_default_harnessx_registry(),
        summary="Corrected exact read-only policy preserving prior verified behavior.",
    )
    corrected_results = evaluate_live_tool_policy_variant(corrected_candidate, cases)
    corrected_decision = gate_harnessx_candidate(
        parent=parent,
        candidate=corrected_candidate,
        manifest=corrected_manifest,
        smoke_passed=True,
        previously_passing_task_ids=previously_passing,
        candidate_task_outcomes={
            result["case_id"]: result["passed"] for result in corrected_results
        },
    )
    if not corrected_decision.accepted or not all(
        result["passed"] for result in corrected_results
    ):
        raise HarnessXPolicyEvolutionError("corrected candidate failed promotion")

    report: dict[str, Any] = {
        "schema_version": "merlin-harnessx-live-policy-evolution-v1",
        "evidence_class": "deterministic_typed_variant_evolution",
        "provider_calls": 0,
        "api_keys_read": False,
        "parent_variant_sha256": parent.sha256,
        "evidence_trace_id": evidence_trace_id,
        "verifier_case_count": len(cases),
        "previously_passing_case_ids": list(previously_passing),
        "parent_evaluation": list(parent_results),
        "rejected_revision": {
            "manifest": regressing_manifest.canonical_payload(),
            "candidate_variant_sha256": regressing_candidate.sha256,
            "evaluation": list(regressing_results),
            "gate": _decision_payload(regressing_decision),
        },
        "promoted_revision": {
            "manifest": corrected_manifest.canonical_payload(),
            "candidate_variant_sha256": corrected_candidate.sha256,
            "evaluation": list(corrected_results),
            "gate": _decision_payload(corrected_decision),
        },
        "resolved_variant_id": corrected_candidate.id,
        "resolved_variant_sha256": corrected_candidate.sha256,
        "rollback_variant_id": parent.id,
        "rollback_variant_sha256": parent.sha256,
        "evidence_boundary": {
            "same_verifier_used_for_parent_and_candidates": True,
            "first_candidate_rejected_for_regression": True,
            "corrected_candidate_promoted": True,
            "live_provider_execution_included": False,
            "automatic_processor_code_generation_claim": False,
            "full_AEGIS_or_model_coevolution_claim": False,
        },
    }
    report["evidence_sha256"] = _sha256_json(report)

    _write_new_json(destination / "parent-variant.json", parent.canonical_payload())
    _write_new_json(
        destination / "rejected-candidate-variant.json",
        regressing_candidate.canonical_payload(),
    )
    _write_new_json(
        destination / "promoted-candidate-variant.json",
        corrected_candidate.canonical_payload(),
    )
    _write_new_json(destination / "resolved-variant.json", corrected_candidate.canonical_payload())
    _write_new_json(destination / "evolution-report.json", report)
    return report


def validate_live_tool_policy_evolution(output_dir: str | Path) -> dict[str, Any]:
    """Independently validate the saved promoted variant and report bindings."""

    root = Path(output_dir).resolve(strict=True)
    try:
        report = json.loads((root / "evolution-report.json").read_text(encoding="utf-8"))
        parent = harnessx_variant_from_payload(
            json.loads((root / "parent-variant.json").read_text(encoding="utf-8"))
        )
        promoted = harnessx_variant_from_payload(
            json.loads((root / "promoted-candidate-variant.json").read_text(encoding="utf-8"))
        )
        resolved = harnessx_variant_from_payload(
            json.loads((root / "resolved-variant.json").read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise HarnessXPolicyEvolutionError("evolution artifacts are invalid") from exc
    stored_evidence_sha = report.get("evidence_sha256")
    body = {key: value for key, value in report.items() if key != "evidence_sha256"}
    checks = {
        "report_sha256": stored_evidence_sha == _sha256_json(body),
        "parent_sha256": report.get("parent_variant_sha256") == parent.sha256,
        "promoted_sha256": report.get("resolved_variant_sha256") == promoted.sha256,
        "resolved_exactly_promoted": resolved == promoted,
        "candidate_points_to_parent": promoted.parent_id == parent.id,
        "promotion_gate_accepted": (
            report.get("promoted_revision", {}).get("gate", {}).get("accepted") is True
        ),
        "regression_gate_rejected": (
            report.get("rejected_revision", {}).get("gate", {}).get("accepted") is False
        ),
        "same_verifier": (
            report.get("evidence_boundary", {}).get(
                "same_verifier_used_for_parent_and_candidates"
            )
            is True
        ),
    }
    if not all(checks.values()):
        raise HarnessXPolicyEvolutionError("evolution artifact validation failed")
    return {
        "valid": True,
        "checks": checks,
        "resolved_variant_id": resolved.id,
        "resolved_variant_sha256": resolved.sha256,
        "evidence_sha256": stored_evidence_sha,
    }


__all__ = [
    "DEFAULT_VERIFIER_CASES",
    "HarnessXPolicyEvolutionError",
    "ToolPolicyVerifierCase",
    "evaluate_live_tool_policy_variant",
    "evolve_live_tool_policy",
    "make_live_tool_policy_parent",
    "validate_live_tool_policy_evolution",
]
