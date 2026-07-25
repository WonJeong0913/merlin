"""Central action routing for skill lifecycle and harness evolution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .harnessx_aegis import (
    AegisActionSpace,
    AegisStageAgent,
    HarnessXAegisError,
    run_harnessx_aegis_round,
    validate_harnessx_aegis_round,
)
from .harnessx_runtime import HarnessXVariantSpec
from .harnessx_trace_ingestion import HarnessXTraceIngestion
from .harnessx_verifier_suites import ToolPolicyVerifierSuite
from .skill_repair import RepairDiagnosis
from .skill_retirement import (
    MIN_RETIREMENT_WINDOWS,
    RetirementObservationWindow,
)


class SelfManagingControllerError(ValueError):
    """Raised when a governance signal or execution result is inconsistent."""


class ManagedAction(str, Enum):
    HARNESS_EVOLVE = "harness_evolve"
    SKILL_REPAIR = "skill_repair"
    SKILL_RETIRE = "skill_retire"
    PROVISIONING_REPAIR = "provisioning_repair"
    HUMAN_REVIEW = "human_review"
    OBSERVE = "observe"


@dataclass(frozen=True, slots=True)
class ControllerDecision:
    action: ManagedAction
    automatic_execution_allowed: bool
    reason: str
    blockers: tuple[str, ...]
    target_id: str | None
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class SkillLifecycleSignal:
    signal_id: str
    signal_kind: str
    skill_id: str
    evidence_sha256: str
    verifier_trusted: bool
    actual_invocation_evidence_complete: bool
    already_hidden: bool = False
    independent_window_count: int = 0


ActionExecutor = Callable[[ControllerDecision], Mapping[str, Any]]


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def decide_trace_action(ingestion: HarnessXTraceIngestion) -> ControllerDecision:
    false_allows = [
        signal
        for signal in ingestion.matched_signals
        if signal.signal_kind == "false_allow"
    ]
    if false_allows:
        return ControllerDecision(
            action=ManagedAction.HUMAN_REVIEW,
            automatic_execution_allowed=False,
            reason="pre-execution safety false-allow cannot be auto-repaired",
            blockers=("safety_false_allow_requires_human_review",),
            target_id=ingestion.parent_variant_sha256,
            evidence_sha256=ingestion.sha256,
        )
    if ingestion.eligible_for_aegis and ingestion.actionable_case_ids:
        return ControllerDecision(
            action=ManagedAction.HARNESS_EVOLVE,
            automatic_execution_allowed=True,
            reason="verifier-matched pre-execution false-deny nominates bounded AEGIS",
            blockers=(),
            target_id=ingestion.parent_variant_sha256,
            evidence_sha256=ingestion.sha256,
        )
    return ControllerDecision(
        action=ManagedAction.OBSERVE,
        automatic_execution_allowed=False,
        reason="trace is observational or has no eligible harness failure",
        blockers=ingestion.blockers,
        target_id=ingestion.parent_variant_sha256,
        evidence_sha256=ingestion.sha256,
    )


def signal_from_repair_diagnosis(
    diagnosis: RepairDiagnosis,
    *,
    verifier_trusted: bool,
    actual_invocation_evidence_complete: bool,
) -> SkillLifecycleSignal:
    return SkillLifecycleSignal(
        signal_id=f"repair-{diagnosis.skill_id}",
        signal_kind=diagnosis.failure_kind,
        skill_id=diagnosis.skill_id,
        evidence_sha256=_sha256_json(
            {
                "skill_id": diagnosis.skill_id,
                "failure_kind": diagnosis.failure_kind,
                "trace_ids": list(diagnosis.trace_ids),
                "failed_target_case_ids": list(diagnosis.failed_target_case_ids),
                "library_snapshot_sha256": diagnosis.library_snapshot_sha256,
            }
        ),
        verifier_trusted=verifier_trusted,
        actual_invocation_evidence_complete=actual_invocation_evidence_complete,
    )


def signal_from_retirement_windows(
    *,
    skill_id: str,
    already_hidden: bool,
    windows: tuple[RetirementObservationWindow, ...],
    verifier_trusted: bool,
) -> SkillLifecycleSignal:
    complete = bool(windows) and all(
        window.actual_invocation_evidence_complete for window in windows
    )
    return SkillLifecycleSignal(
        signal_id=f"retire-{skill_id}",
        signal_kind="retirement",
        skill_id=skill_id,
        evidence_sha256=_sha256_json(
            {
                "skill_id": skill_id,
                "already_hidden": already_hidden,
                "windows": [asdict(window) for window in windows],
            }
        ),
        verifier_trusted=verifier_trusted,
        actual_invocation_evidence_complete=complete,
        already_hidden=already_hidden,
        independent_window_count=len(windows),
    )


def decide_skill_action(signal: SkillLifecycleSignal) -> ControllerDecision:
    blockers: list[str] = []
    if not signal.verifier_trusted:
        blockers.append("trusted_verifier_missing")
    if not signal.actual_invocation_evidence_complete:
        blockers.append("actual_invocation_evidence_incomplete")
    if signal.signal_kind == "route_local":
        return ControllerDecision(
            action=ManagedAction.PROVISIONING_REPAIR,
            automatic_execution_allowed=not blockers,
            reason="route-local failure belongs to provisioning, not skill content",
            blockers=tuple(blockers),
            target_id=signal.skill_id,
            evidence_sha256=signal.evidence_sha256,
        )
    if signal.signal_kind == "skill_local":
        return ControllerDecision(
            action=ManagedAction.SKILL_REPAIR,
            automatic_execution_allowed=not blockers,
            reason="skill-local verified failure enters copy-on-write repair",
            blockers=tuple(blockers),
            target_id=signal.skill_id,
            evidence_sha256=signal.evidence_sha256,
        )
    if signal.signal_kind == "retirement":
        if not signal.already_hidden:
            blockers.append("skill_not_already_hidden")
        if signal.independent_window_count < MIN_RETIREMENT_WINDOWS:
            blockers.append("insufficient_independent_windows")
        return ControllerDecision(
            action=ManagedAction.SKILL_RETIRE,
            automatic_execution_allowed=not blockers,
            reason="hidden unused skill enters copy-on-write retirement gate",
            blockers=tuple(blockers),
            target_id=signal.skill_id,
            evidence_sha256=signal.evidence_sha256,
        )
    return ControllerDecision(
        action=ManagedAction.OBSERVE,
        automatic_execution_allowed=False,
        reason="signal has no governed mutation lane",
        blockers=tuple(blockers or ["unsupported_signal_kind"]),
        target_id=signal.skill_id,
        evidence_sha256=signal.evidence_sha256,
    )


class SelfManagingHarnessController:
    """Dispatch only eligible decisions to explicitly registered action lanes."""

    def __init__(self, executors: Mapping[ManagedAction, ActionExecutor]) -> None:
        self._executors = dict(executors)

    def execute(self, decision: ControllerDecision) -> dict[str, Any]:
        if not decision.automatic_execution_allowed:
            return {
                "executed": False,
                "decision": asdict(decision),
                "result": None,
            }
        executor = self._executors.get(decision.action)
        if executor is None:
            raise SelfManagingControllerError(
                f"no executor registered for {decision.action.value}"
            )
        result = executor(decision)
        if not isinstance(result, Mapping):
            raise SelfManagingControllerError("controller executor result must be a mapping")
        success_field = {
            ManagedAction.HARNESS_EVOLVE: "promoted",
            ManagedAction.SKILL_REPAIR: "adopted",
            ManagedAction.SKILL_RETIRE: "retired",
            ManagedAction.PROVISIONING_REPAIR: "applied",
        }.get(decision.action)
        if success_field is not None and not isinstance(
            result.get(success_field), bool
        ):
            raise SelfManagingControllerError(
                f"{decision.action.value} result must contain boolean {success_field}"
            )
        return {
            "executed": True,
            "decision": asdict(decision),
            "result": dict(result),
        }


def run_trace_triggered_aegis_round(
    *,
    output_dir: str | Path,
    ingestion: HarnessXTraceIngestion,
    parent_variant: HarnessXVariantSpec,
    verifier_suite: ToolPolicyVerifierSuite,
    action_space: AegisActionSpace,
    stage_agent: AegisStageAgent,
) -> dict[str, Any]:
    """Bind one eligible live trace to a same-suite AEGIS round and replay it."""

    decision = decide_trace_action(ingestion)
    if (
        not decision.automatic_execution_allowed
        or decision.action is not ManagedAction.HARNESS_EVOLVE
    ):
        raise SelfManagingControllerError("trace is not eligible for automatic AEGIS")
    if ingestion.parent_variant_sha256 != parent_variant.sha256:
        raise SelfManagingControllerError("trace parent variant does not match current parent")
    if (
        ingestion.verifier_suite_id != verifier_suite.suite_id
        or ingestion.verifier_suite_sha256 != verifier_suite.sha256
    ):
        raise SelfManagingControllerError("trace verifier suite binding drifted")
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    ingestion_payload = ingestion.canonical_payload()
    ingestion_payload["ingestion_sha256"] = ingestion.sha256
    with (root / "trace-ingestion.json").open("x", encoding="utf-8") as handle:
        json.dump(ingestion_payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    round_report = run_harnessx_aegis_round(
        output_dir=root / "aegis-round",
        stage_agent=stage_agent,
        verifier_suite=verifier_suite,
        parent_variant=parent_variant,
        action_space=action_space,
    )
    validation = validate_harnessx_aegis_round(root / "aegis-round")
    report = {
        "schema_version": "merlin-trace-triggered-aegis-v1",
        "ingestion_sha256": ingestion.sha256,
        "actionable_case_ids": list(ingestion.actionable_case_ids),
        "decision": asdict(decision),
        "round_evidence_sha256": round_report["evidence_sha256"],
        "round_promoted": round_report["promoted"],
        "resolved_variant_sha256": round_report["resolved_variant_sha256"],
        "round_replay_valid": validation["valid"],
        "evidence_boundary": {
            "trace_nominates_only": True,
            "same_verifier_gate_required": True,
            "deterministic_gate_owns_promotion": True,
        },
    }
    report["evidence_sha256"] = _sha256_json(report)
    with (root / "trace-triggered-aegis-report.json").open(
        "x", encoding="utf-8"
    ) as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return report


__all__ = [
    "ControllerDecision",
    "ManagedAction",
    "SelfManagingControllerError",
    "SelfManagingHarnessController",
    "SkillLifecycleSignal",
    "decide_skill_action",
    "decide_trace_action",
    "run_trace_triggered_aegis_round",
    "signal_from_repair_diagnosis",
    "signal_from_retirement_windows",
]
