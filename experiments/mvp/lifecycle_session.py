"""Incremental service layer for Merlin lifecycle-recovery demonstration.

The deterministic runtime used by the one-shot demo and the localhost Console
is shared here.  A session deliberately exposes only a fixed sample corpus and
enforces the order in which evidence becomes available.
"""

from __future__ import annotations

import copy
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

from src.merlin_harness.harness import HarnessEvent, HarnessRuntime, Hook, ShadowingLifecycleProcessor
from src.merlin_harness.lifecycle import evaluate_lifecycle_promotion, stage_provisional_lifecycle_change
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.metrics import clean_oracle_invocation_rate, shadowing_rate
from src.merlin_harness.models import LifecyclePromotionCriteria, LifecycleVerificationSnapshot
from src.merlin_harness.runner import run_seeded_condition
from src.merlin_harness.task_io import load_tasks


REPO_ROOT = Path(__file__).resolve().parents[2]
MVP_ROOT = REPO_ROOT / "experiments" / "mvp"
MIN_SHADOWING_EVENTS_RANGE = range(2, 6)


class SessionStage(str, Enum):
    EMPTY = "empty"
    LOADED = "loaded"
    REFERENCE_COMPLETE = "reference_complete"
    OVERLOADED_COMPLETE = "overloaded_complete"
    DIAGNOSED = "diagnosed"
    STAGED = "staged"
    VERIFIED = "verified"


class LifecycleSessionError(RuntimeError):
    """A safe domain error for an impossible session transition."""

    def __init__(self, message: str, *, code: str = "invalid_transition") -> None:
        super().__init__(message)
        self.code = code


def _route_event(selected: list[str], oracle: list[str]) -> str:
    selected_ids = set(selected)
    oracle_ids = set(oracle)
    if not oracle_ids:
        return "spurious" if selected_ids else "empty_no_oracle"
    if not selected_ids:
        return "empty"
    if selected_ids.issubset(oracle_ids):
        return "oracle_only"
    if selected_ids & oracle_ids:
        return "mixed"
    return "wrong"


def summarize_records(records: list[Any]) -> dict[str, Any]:
    invocations = [record.invocation for record in records if record.invocation is not None]
    passed = sum(1 for invocation in invocations if invocation.success)
    route_counts: dict[str, int] = {}
    tasks: list[dict[str, Any]] = []
    for record, invocation in zip(records, invocations, strict=True):
        route = _route_event(invocation.selected_skill_ids, invocation.oracle_skill_ids)
        route_counts[route] = route_counts.get(route, 0) + 1
        tasks.append(
            {
                "task_id": invocation.task_id,
                "trace_id": record.id,
                "verifier_ids": [result.name for result in record.validation],
                "provisioned_skill_ids": invocation.provisioned_skill_ids,
                "selected_skill_ids": invocation.selected_skill_ids,
                "oracle_skill_ids": invocation.oracle_skill_ids,
                "route_event": route,
                "success": bool(invocation.success),
            }
        )
    return {
        "task_count": len(records),
        "passed": passed,
        "pass_rate": passed / len(records) if records else 0.0,
        "pi_o": clean_oracle_invocation_rate(invocations),
        "pi_m": shadowing_rate(invocations),
        "route_counts": route_counts,
        "tasks": tasks,
    }


def _verification_snapshot(records: list[Any], summary: dict[str, Any]) -> LifecycleVerificationSnapshot:
    return LifecycleVerificationSnapshot(
        task_ids=[record.task_id for record in records],
        verifier_ids_by_task={
            record.task_id: [result.name for result in record.validation]
            for record in records
        },
        passed=summary["passed"],
        pass_rate=summary["pass_rate"],
        pi_o=summary["pi_o"],
        pi_m=summary["pi_m"],
    )


def _decision_payload(decision: Any) -> dict[str, Any]:
    return {
        "skill_id": decision.skill_id,
        "action": decision.action.value,
        "reason": decision.reason,
        "evidence_trace_ids": decision.evidence_trace_ids,
    }


def _promotion_payload(result: Any) -> dict[str, Any]:
    return {
        "status": "accepted" if result.accepted else "rejected",
        "accepted": result.accepted,
        "reason": result.reason,
        "rollback_required": result.rollback_required,
        "criteria": {
            "require_same_task_coverage": result.criteria.require_same_task_coverage,
            "require_same_verifier_contract": result.criteria.require_same_verifier_contract,
            "min_pass_rate_delta": result.criteria.min_pass_rate_delta,
            "min_pi_o_delta": result.criteria.min_pi_o_delta,
            "min_pi_m_reduction": result.criteria.min_pi_m_reduction,
        },
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "score": check.score,
                "evidence": check.evidence,
            }
            for check in result.checks
        ],
    }


def _ordered_unique_skill_ids(tasks: list[dict[str, Any]], key: str) -> list[str]:
    return list(dict.fromkeys(skill_id for task in tasks for skill_id in task[key]))


def _governance_loop_payload(
    *, overloaded: dict[str, Any], provisional: dict[str, Any], decisions: list[Any], promotion: Any
) -> dict[str, Any]:
    overloaded_tasks = overloaded["tasks"]
    provisional_tasks = provisional["tasks"]
    decision_trace_ids = list(
        dict.fromkeys(trace_id for decision in decisions for trace_id in decision.evidence_trace_ids)
    )
    promotion_checks = list(promotion.checks)
    verifier_ids = list(
        dict.fromkeys(verifier_id for task in provisional_tasks for verifier_id in task["verifier_ids"])
    )
    return {
        "schema_version": 1,
        "selection_evidence_note": (
            "Selected IDs are deterministic seeded route records, not provider-native "
            "skill-body invocation evidence."
        ),
        "stages": [
            {
                "id": "provision",
                "label": "Provision",
                "status": "observed",
                "evidence_keys": [
                    "conditions['Overloaded library'].tasks[].provisioned_skill_ids",
                    "conditions['Overloaded library'].tasks[].trace_id",
                ],
                "evidence": {
                    "task_count": len(overloaded_tasks),
                    "trace_count": len(overloaded_tasks),
                    "unique_skill_count": len(_ordered_unique_skill_ids(overloaded_tasks, "provisioned_skill_ids")),
                    "skill_ids": _ordered_unique_skill_ids(overloaded_tasks, "provisioned_skill_ids"),
                },
            },
            {
                "id": "select",
                "label": "Select",
                "status": "observed",
                "evidence_keys": [
                    "conditions['Overloaded library'].tasks[].selected_skill_ids",
                    "conditions['Overloaded library'].tasks[].trace_id",
                ],
                "evidence": {
                    "task_count": len(overloaded_tasks),
                    "selection_count": sum(len(task["selected_skill_ids"]) for task in overloaded_tasks),
                    "unique_skill_count": len(_ordered_unique_skill_ids(overloaded_tasks, "selected_skill_ids")),
                    "skill_ids": _ordered_unique_skill_ids(overloaded_tasks, "selected_skill_ids"),
                },
            },
            {
                "id": "observe_trace",
                "label": "Observe / Trace",
                "status": "observed",
                "evidence_keys": [
                    "conditions['Overloaded library'].tasks[].trace_id",
                    "conditions['Overloaded library'].tasks[].route_event",
                    "conditions['Overloaded library'].tasks[].success",
                ],
                "evidence": {
                    "trace_count": len(overloaded_tasks),
                    "failed_verifier_count": sum(not task["success"] for task in overloaded_tasks),
                    "route_risk_trace_count": len(decision_trace_ids),
                    "trace_ids": [task["trace_id"] for task in overloaded_tasks],
                },
            },
            {
                "id": "lifecycle_action",
                "label": "Lifecycle action",
                "status": "provisional_applied",
                "evidence_keys": [
                    "lifecycle_decisions[].evidence_trace_ids",
                    "provisional_change.mode",
                    "provisional_change.provisional_statuses",
                ],
                "evidence": {
                    "action": "hide",
                    "decision_count": len(decisions),
                    "target_skill_ids": [decision.skill_id for decision in decisions],
                    "evidence_trace_count": len(decision_trace_ids),
                    "evidence_trace_ids": decision_trace_ids,
                    "copy_on_write": True,
                },
            },
            {
                "id": "same_verifier_gate",
                "label": "Same-verifier gate",
                "status": "accepted" if promotion.accepted else "rejected",
                "evidence_keys": [
                    "provisional_verification.tasks[].verifier_ids",
                    "provisional_verification.tasks[].trace_id",
                    "promotion.checks",
                ],
                "evidence": {
                    "re_run_task_count": len(provisional_tasks),
                    "re_run_trace_count": len(provisional_tasks),
                    "verifier_id_count": len(verifier_ids),
                    "verifier_ids": verifier_ids,
                    "promotion_check_count": len(promotion_checks),
                    "passed_promotion_check_count": sum(check.passed for check in promotion_checks),
                    "promotion_check_names": [check.name for check in promotion_checks],
                    "re_run_trace_ids": [task["trace_id"] for task in provisional_tasks],
                },
            },
        ],
    }


class LifecycleRecoverySession:
    """A fixed-corpus, incremental lifecycle recovery state machine."""

    def __init__(
        self,
        *,
        min_shadowing_events: int = 2,
        promotion_criteria: LifecyclePromotionCriteria | None = None,
    ) -> None:
        self._validate_threshold(min_shadowing_events)
        self._promotion_criteria = promotion_criteria or LifecyclePromotionCriteria()
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._event_sequence = 0
        self._logs: list[dict[str, Any]] = []
        self.stage = SessionStage.EMPTY
        self.min_shadowing_events = min_shadowing_events
        self._clear_runtime_state()
        self._log("Session ready. Load the fixed sample workspace to begin.")

    @staticmethod
    def _validate_threshold(value: int) -> None:
        if isinstance(value, bool) or value not in MIN_SHADOWING_EVENTS_RANGE:
            raise LifecycleSessionError(
                "min_shadowing_events must be an integer from 2 through 5.",
                code="invalid_threshold",
            )

    def _clear_runtime_state(self) -> None:
        self.tasks: list[Any] = []
        self.seeds: list[Any] = []
        self.distractors: list[Any] = []
        self.reference_records: list[Any] = []
        self.overloaded_records: list[Any] = []
        self.provisional_records: list[Any] = []
        self.reference: dict[str, Any] | None = None
        self.overloaded: dict[str, Any] | None = None
        self.provisional: dict[str, Any] | None = None
        self.decisions: list[Any] = []
        self.provisional_library: list[Any] = []
        self.provisional_change: Any | None = None
        self.promotion: Any | None = None
        self.report: dict[str, Any] | None = None

    def _log(self, message: str) -> None:
        self._event_sequence += 1
        self._logs.append({"sequence": self._event_sequence, "stage": self.stage.value, "message": message})

    def _require(self, expected: SessionStage, action: str) -> None:
        if self.stage != expected:
            raise LifecycleSessionError(
                f"Cannot {action} while session is '{self.stage.value}'; expected '{expected.value}'."
            )

    @property
    def _run_root(self) -> Path:
        if self._temporary is None:
            raise LifecycleSessionError("Sample workspace is not loaded.")
        return Path(self._temporary.name)

    def _run_condition(self, *, label: str, skills: list[Any]) -> tuple[list[Any], dict[str, Any]]:
        records = run_seeded_condition(
            tasks=self.tasks,
            skills=skills,
            workspaces_root=self._run_root / label / "workspaces",
            traces_root=self._run_root / label / "traces",
            condition=label,
        )
        return records, summarize_records(records)

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def __enter__(self) -> "LifecycleRecoverySession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def reset(self) -> dict[str, Any]:
        self.close()
        self.stage = SessionStage.EMPTY
        self.min_shadowing_events = 2
        self._clear_runtime_state()
        self._logs = []
        self._event_sequence = 0
        self._log("Session reset. Load the fixed sample workspace to begin.")
        return self.public_state()

    def load_sample(self, *, min_shadowing_events: int | None = None) -> dict[str, Any]:
        self._require(SessionStage.EMPTY, "load sample workspace")
        if min_shadowing_events is not None:
            self._validate_threshold(min_shadowing_events)
            self.min_shadowing_events = min_shadowing_events
        self._temporary = tempfile.TemporaryDirectory(prefix="merlin-console-")
        self.tasks = load_tasks(MVP_ROOT / "tasks")
        self.seeds = FileSkillLibrary(MVP_ROOT / "skills").list()
        self.distractors = FileSkillLibrary(MVP_ROOT / "distractors").list()
        self.stage = SessionStage.LOADED
        self._log(f"Loaded fixed sample: {len(self.tasks)} tasks, {len(self.seeds)} curated skills, {len(self.distractors)} distractors.")
        return self.public_state()

    def configure_threshold(self, value: int) -> dict[str, Any]:
        if self.stage not in {
            SessionStage.LOADED,
            SessionStage.REFERENCE_COMPLETE,
            SessionStage.OVERLOADED_COMPLETE,
        }:
            raise LifecycleSessionError(
                "min_shadowing_events can only change after load and before diagnosis.",
                code="threshold_frozen",
            )
        self._validate_threshold(value)
        self.min_shadowing_events = value
        self._log(f"Diagnosis threshold set to {value} route-risk events.")
        return self.public_state()

    def run_reference(self) -> dict[str, Any]:
        self._require(SessionStage.LOADED, "run reference")
        self.reference_records, self.reference = self._run_condition(
            label="curated_reference", skills=copy.deepcopy(self.seeds)
        )
        self.stage = SessionStage.REFERENCE_COMPLETE
        self._log(f"Reference complete: {self.reference['passed']}/{self.reference['task_count']} passed.")
        return self.public_state()

    def run_overloaded(self) -> dict[str, Any]:
        self._require(SessionStage.REFERENCE_COMPLETE, "run overloaded condition")
        self.overloaded_records, self.overloaded = self._run_condition(
            label="overloaded_library", skills=copy.deepcopy(self.seeds + self.distractors)
        )
        self.stage = SessionStage.OVERLOADED_COMPLETE
        self._log(
            f"Overload observed: {self.overloaded['passed']}/{self.overloaded['task_count']} passed; "
            f"shadowing {self.overloaded['pi_m']:.0%}."
        )
        return self.public_state()

    def diagnose(self) -> dict[str, Any]:
        self._require(SessionStage.OVERLOADED_COMPLETE, "diagnose traces")
        review = HarnessRuntime(
            [ShadowingLifecycleProcessor(min_shadowing_events=self.min_shadowing_events)]
        )
        policy_event = review.emit(
            HarnessEvent(
                hook=Hook.POLICY_REVIEW,
                metadata={
                    "invocations": [
                        record.invocation
                        for record in self.overloaded_records
                        if record.invocation is not None
                    ]
                },
            )
        )
        self.decisions = list(policy_event.metadata["lifecycle_decisions"])
        for decision in self.decisions:
            decision.evidence_trace_ids = [
                record.id
                for record in self.overloaded_records
                if record.invocation is not None
                and decision.skill_id in record.invocation.selected_skill_ids
            ]
        self.stage = SessionStage.DIAGNOSED
        self._log(f"Diagnosis proposed {len(self.decisions)} trace-backed hide actions.")
        return self.public_state()

    def stage_hide(self) -> dict[str, Any]:
        self._require(SessionStage.DIAGNOSED, "stage copy-on-write hide")
        self.provisional_library, self.provisional_change = stage_provisional_lifecycle_change(
            copy.deepcopy(self.seeds + self.distractors), self.decisions
        )
        self.stage = SessionStage.STAGED
        self._log("Copy-on-write hide staged; live original remains unchanged.")
        return self.public_state()

    def verify_and_promote(self) -> dict[str, Any]:
        self._require(SessionStage.STAGED, "verify and promote")
        if self.overloaded is None or self.provisional_change is None:
            raise LifecycleSessionError("Required overloaded or provisional evidence is absent.")
        self.provisional_records, self.provisional = self._run_condition(
            label="lifecycle_provisional", skills=self.provisional_library
        )
        self.promotion = evaluate_lifecycle_promotion(
            _verification_snapshot(self.overloaded_records, self.overloaded),
            _verification_snapshot(self.provisional_records, self.provisional),
            self._promotion_criteria,
        )
        self.report = self._build_final_report()
        self.stage = SessionStage.VERIFIED
        status = "accepted" if self.promotion.accepted else "rejected; original retained"
        self._log(f"Same-verifier promotion {status}.")
        return self.public_state()

    def _build_final_report(self) -> dict[str, Any]:
        assert self.reference is not None
        assert self.overloaded is not None
        assert self.provisional is not None
        assert self.provisional_change is not None
        assert self.promotion is not None
        final_summary = self.provisional if self.promotion.accepted else self.overloaded
        final_label = (
            "Lifecycle recovered"
            if self.promotion.accepted
            else "Lifecycle rejected — original retained"
        )
        final_statuses = (
            self.provisional_change.provisional_statuses
            if self.promotion.accepted
            else self.provisional_change.original_statuses
        )
        rollback = {
            "performed": self.promotion.rollback_required,
            "evidence": (
                "provisional library discarded; original statuses retained after failed promotion gate"
                if self.promotion.rollback_required
                else "original library remained unchanged until the provisional state passed every promotion gate"
            ),
            "restored_statuses": (
                self.provisional_change.original_statuses
                if self.promotion.rollback_required
                else {}
            ),
        }
        report = {
            "schema_version": 2,
            "title": "Merlin shadowing recovery demo",
            "scope": "Deterministic controlled MVP; not a full benchmark or model-performance claim.",
            "scope_boundary": {
                "active_in_this_demo": [
                    "task-conditioned provisioning records",
                    "deterministic selection records",
                    "trace and outcome validation",
                    "trace-backed hide lifecycle action",
                    "copy-on-write promotion after a same-verifier gate",
                ],
                "deferred": [
                    "general multi-family model-authored skill generation",
                    "repair, merge, and retire actions",
                    "learned harness co-evolution",
                    "full-87 and model-backed evaluation",
                ],
                "actual_invocation_boundary": (
                    "The deterministic selected_skill_ids are route records, not provider-native skill-body invocation "
                    "evidence. The optional Codex adapter keeps that evidence incomplete unless the provider emits it."
                ),
                "system_claim": "This is an implemented harness-governance vertical slice, not a complete Merlin system.",
            },
            "conditions": {
                "Curated reference": self.reference,
                "Overloaded library": self.overloaded,
                final_label: final_summary,
            },
            "lifecycle_decisions": [_decision_payload(decision) for decision in self.decisions],
            "provisional_change": {
                "mode": "copy_on_write",
                "original_statuses": self.provisional_change.original_statuses,
                "provisional_statuses": self.provisional_change.provisional_statuses,
            },
            "provisional_verification": self.provisional,
            "promotion": _promotion_payload(self.promotion),
            "library_resolution": {
                "mode": "provisional_promoted" if self.promotion.accepted else "original_retained",
                "final_statuses": final_statuses,
                "rollback": rollback,
            },
            "recovery_delta": {
                "pass_rate_gain": self.provisional["pass_rate"] - self.overloaded["pass_rate"],
                "pi_o_gain": self.provisional["pi_o"] - self.overloaded["pi_o"],
                "pi_m_change": self.provisional["pi_m"] - self.overloaded["pi_m"],
            },
        }
        report["governance_loop"] = _governance_loop_payload(
            overloaded=self.overloaded,
            provisional=self.provisional,
            decisions=self.decisions,
            promotion=self.promotion,
        )
        return report

    def final_report(self) -> dict[str, Any]:
        if self.stage != SessionStage.VERIFIED or self.report is None:
            raise LifecycleSessionError("Final report is pending until verify and promote completes.")
        return copy.deepcopy(self.report)

    def public_state(self) -> dict[str, Any]:
        next_by_stage = {
            SessionStage.EMPTY: ["load_sample"],
            SessionStage.LOADED: ["run_reference", "configure_threshold"],
            SessionStage.REFERENCE_COMPLETE: ["run_overloaded", "configure_threshold"],
            SessionStage.OVERLOADED_COMPLETE: ["diagnose", "configure_threshold"],
            SessionStage.DIAGNOSED: ["stage_hide"],
            SessionStage.STAGED: ["verify_and_promote"],
            SessionStage.VERIFIED: [],
        }
        metrics: dict[str, Any] = {}
        for key, value in (
            ("reference", self.reference),
            ("overloaded", self.overloaded),
            ("provisional", self.provisional),
        ):
            if value is not None:
                metrics[key] = {
                    "task_count": value["task_count"],
                    "passed": value["passed"],
                    "pass_rate": value["pass_rate"],
                    "pi_o": value["pi_o"],
                    "pi_m": value["pi_m"],
                    "route_counts": value["route_counts"],
                }
        return {
            "schema_version": 1,
            "stage": self.stage.value,
            "sample": (
                {
                    "task_count": len(self.tasks),
                    "curated_skill_count": len(self.seeds),
                    "distractor_count": len(self.distractors),
                }
                if self.stage != SessionStage.EMPTY
                else None
            ),
            "min_shadowing_events": self.min_shadowing_events,
            "threshold_frozen": self.stage in {
                SessionStage.DIAGNOSED,
                SessionStage.STAGED,
                SessionStage.VERIFIED,
            },
            "metrics": metrics,
            "decisions": [_decision_payload(decision) for decision in self.decisions],
            "provisional_change": (
                {
                    "mode": "copy_on_write",
                    "original_statuses": self.provisional_change.original_statuses,
                    "provisional_statuses": self.provisional_change.provisional_statuses,
                }
                if self.provisional_change is not None
                else None
            ),
            "promotion": _promotion_payload(self.promotion) if self.promotion is not None else None,
            "report_status": "ready" if self.report is not None else "pending",
            "next_actions": ["reset", *next_by_stage[self.stage]],
            "logs": list(self._logs),
        }


def run_complete_session(
    *, promotion_criteria: LifecyclePromotionCriteria | None = None
) -> dict[str, Any]:
    """Execute the exact same incremental path used by the Console."""

    with LifecycleRecoverySession(promotion_criteria=promotion_criteria) as session:
        session.load_sample()
        session.run_reference()
        session.run_overloaded()
        session.diagnose()
        session.stage_hide()
        session.verify_and_promote()
        return session.final_report()
