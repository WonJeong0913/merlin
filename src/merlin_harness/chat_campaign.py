"""Provider-independent, verifier-backed lifecycle campaigns for chat exposure.

This core evaluates the effect of *prompt exposure* to a bounded skill library.
It deliberately does not reinterpret exposure as provider-native loading,
invocation, or a shadowing metric.  Lifecycle actions remain route-local
guards: they are proposed only after repeated wrong/mixed exposure records and
are applied to an in-memory copy of the frozen library snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .lifecycle import all_passed, stage_provisional_lifecycle_change
from .models import LifecycleAction, LifecycleDecision, LifecycleStatus, SkillArtifact, TaskSpec, ValidationResult


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_POINTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ChatCampaignError(ValueError):
    """Raised when a campaign or its immutable turn evidence is invalid."""


class ExposureRouteClass(str, Enum):
    NO_ORACLE_EMPTY = "no_oracle_empty"
    SPURIOUS = "spurious"
    ORACLE_ONLY = "oracle_only"
    MIXED = "mixed"
    WRONG = "wrong"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class ChatCampaignTurnEvidence:
    """Normalized turn facts supplied by a real or fake chat executor."""

    task_id: str
    verifier_id: str
    verifier_passed: bool
    exposure_skill_ids: tuple[str, ...]
    oracle_skill_ids: tuple[str, ...]
    raw_trace_pointer: str
    raw_trace_sha256: str
    actual_invocation_evidence_complete: bool = False


class CampaignTurnExecutor(Protocol):
    """Run one frozen task against one arm's active prompt-exposure library."""

    def run_turn(
        self,
        *,
        task: TaskSpec,
        skills: tuple[SkillArtifact, ...],
        arm: str,
        ordinal: int,
    ) -> ChatCampaignTurnEvidence: ...


@dataclass(frozen=True, slots=True)
class ChatCampaignTurnRecord:
    """Validated campaign evidence with a campaign-local immutable trace ID."""

    trace_id: str
    task_id: str
    verifier_id: str
    verifier_passed: bool
    exposure_skill_ids: tuple[str, ...]
    oracle_skill_ids: tuple[str, ...]
    route_class: ExposureRouteClass
    raw_trace_pointer: str
    raw_trace_sha256: str
    evidence_level: str = "verifier_backed_prompt_exposure"
    actual_invocation_evidence_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize only exposure-level facts; no selection/invocation metrics."""

        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "verifier_id": self.verifier_id,
            "verifier_passed": self.verifier_passed,
            "exposure_skill_ids": list(self.exposure_skill_ids),
            "oracle_skill_ids": list(self.oracle_skill_ids),
            "route_class": self.route_class.value,
            "raw_trace": {
                "pointer": self.raw_trace_pointer,
                "sha256": self.raw_trace_sha256,
            },
            "evidence_level": self.evidence_level,
            "actual_invocation_evidence_complete": self.actual_invocation_evidence_complete,
        }


@dataclass(frozen=True, slots=True)
class ChatCampaignArmSummary:
    """Outcome and prompt-exposure summary for one frozen ordered campaign arm."""

    arm: str
    ordered_task_ids: tuple[str, ...]
    verifier_ids: tuple[str, ...]
    records: tuple[ChatCampaignTurnRecord, ...]
    passed: int
    pass_rate: float
    clean_oracle_exposure_rate: float
    exposure_shadowing_rate: float
    route_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "ordered_task_ids": list(self.ordered_task_ids),
            "verifier_ids": list(self.verifier_ids),
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "clean_oracle_exposure_rate": self.clean_oracle_exposure_rate,
            "exposure_shadowing_rate": self.exposure_shadowing_rate,
            "route_counts": dict(self.route_counts),
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(frozen=True, slots=True)
class ChatCampaignPromotionCriteria:
    """Pre-registered acceptance thresholds for prompt-exposure recovery."""

    min_pass_rate_delta: float = 0.0
    min_clean_oracle_exposure_delta: float = 0.0
    min_exposure_shadowing_reduction: float = 1e-12

    def __post_init__(self) -> None:
        for name, value in (
            ("min_pass_rate_delta", self.min_pass_rate_delta),
            ("min_clean_oracle_exposure_delta", self.min_clean_oracle_exposure_delta),
            ("min_exposure_shadowing_reduction", self.min_exposure_shadowing_reduction),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ChatCampaignError(f"{name} must be a non-negative number")


@dataclass(frozen=True, slots=True)
class ChatCampaignPromotionResult:
    """Promotion decision for a copy-on-write route-local guard change."""

    accepted: bool
    rollback_required: bool
    reason: str
    checks: tuple[ValidationResult, ...]
    baseline: ChatCampaignArmSummary
    provisional: ChatCampaignArmSummary
    library_resolution: str


def _require_skill_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SKILL_ID_RE.fullmatch(value):
        raise ChatCampaignError(f"{label} has an unsafe skill ID")
    return value


def _require_ordered_unique_ids(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ChatCampaignError(f"{label} must be a frozen tuple")
    ids = tuple(_require_skill_id(item, label=f"{label}[{index}]") for index, item in enumerate(value))
    if len(ids) != len(set(ids)):
        raise ChatCampaignError(f"{label} contains duplicate IDs")
    return ids


def _route_class(exposure_skill_ids: tuple[str, ...], oracle_skill_ids: tuple[str, ...]) -> ExposureRouteClass:
    exposed = set(exposure_skill_ids)
    oracle = set(oracle_skill_ids)
    if not oracle:
        return ExposureRouteClass.SPURIOUS if exposed else ExposureRouteClass.NO_ORACLE_EMPTY
    if not exposed:
        return ExposureRouteClass.EMPTY
    if exposed.issubset(oracle):
        return ExposureRouteClass.ORACLE_ONLY
    if exposed & oracle:
        return ExposureRouteClass.MIXED
    return ExposureRouteClass.WRONG


def _library_snapshot_hash(skills: tuple[SkillArtifact, ...]) -> str:
    payload = [skill.to_dict() for skill in skills]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _check_snapshot(tasks: tuple[TaskSpec, ...], skills: tuple[SkillArtifact, ...]) -> None:
    if not tasks:
        raise ChatCampaignError("frozen task tuple must not be empty")
    task_ids = tuple(task.id for task in tasks)
    if len(task_ids) != len(set(task_ids)):
        raise ChatCampaignError("frozen task tuple contains duplicate task IDs")
    skill_ids = tuple(skill.id for skill in skills)
    if len(skill_ids) != len(set(skill_ids)):
        raise ChatCampaignError("frozen library snapshot contains duplicate skill IDs")
    active = {skill.id for skill in skills if skill.status == LifecycleStatus.ACTIVE}
    for task in tasks:
        if len(task.oracle_skill_ids) != len(set(task.oracle_skill_ids)):
            raise ChatCampaignError(f"task {task.id} contains duplicate oracle IDs")
        outside = set(task.oracle_skill_ids) - active
        if outside:
            raise ChatCampaignError(
                f"task {task.id} has oracle IDs outside the active frozen library: {', '.join(sorted(outside))}"
            )


def _validate_evidence(
    evidence: ChatCampaignTurnEvidence,
    *,
    task: TaskSpec,
    active_skill_ids: set[str],
    known_skill_ids: set[str],
    exposure_budget: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if evidence.task_id != task.id:
        raise ChatCampaignError(
            f"campaign executor returned task_id {evidence.task_id!r}; expected {task.id!r}"
        )
    if evidence.verifier_id != task.verifier.name:
        raise ChatCampaignError(
            f"campaign executor returned verifier {evidence.verifier_id!r}; expected {task.verifier.name!r}"
        )
    if not isinstance(evidence.verifier_passed, bool):
        raise ChatCampaignError("campaign verifier_passed must be boolean")
    if evidence.actual_invocation_evidence_complete is not False:
        raise ChatCampaignError("campaign must not accept actual invocation evidence in an exposure campaign")
    if not isinstance(evidence.exposure_skill_ids, tuple):
        raise ChatCampaignError("campaign exposure_skill_ids must be a frozen tuple")
    exposures = tuple(
        _require_skill_id(skill_id, label=f"campaign exposure_skill_ids[{index}]")
        for index, skill_id in enumerate(evidence.exposure_skill_ids)
    )
    if len(exposures) != len(set(exposures)):
        raise ChatCampaignError("campaign exposure_skill_ids contains duplicates")
    if len(exposures) > exposure_budget:
        raise ChatCampaignError("campaign exposure budget exceeded")
    unknown = set(exposures) - known_skill_ids
    if unknown:
        raise ChatCampaignError(f"campaign exposure references unknown skills: {', '.join(sorted(unknown))}")
    inactive = set(exposures) - active_skill_ids
    if inactive:
        raise ChatCampaignError(f"campaign exposure references skills outside active state: {', '.join(sorted(inactive))}")
    if not isinstance(evidence.oracle_skill_ids, tuple):
        raise ChatCampaignError("campaign oracle_skill_ids must be a frozen tuple")
    oracle = tuple(
        _require_skill_id(skill_id, label=f"campaign oracle_skill_ids[{index}]")
        for index, skill_id in enumerate(evidence.oracle_skill_ids)
    )
    if len(oracle) != len(set(oracle)):
        raise ChatCampaignError("campaign oracle_skill_ids contains duplicates")
    if oracle != tuple(task.oracle_skill_ids):
        raise ChatCampaignError("campaign oracle IDs do not match the frozen task contract")
    if not isinstance(evidence.raw_trace_pointer, str) or not _SAFE_POINTER_RE.fullmatch(evidence.raw_trace_pointer):
        raise ChatCampaignError("campaign raw trace pointer is unsafe")
    if not isinstance(evidence.raw_trace_sha256, str) or not _SHA256_RE.fullmatch(evidence.raw_trace_sha256):
        raise ChatCampaignError("campaign raw trace SHA-256 has invalid format")
    return exposures, oracle


def _summarize_arm(
    *, arm: str, tasks: tuple[TaskSpec, ...], records: tuple[ChatCampaignTurnRecord, ...]
) -> ChatCampaignArmSummary:
    if len(tasks) != len(records):
        raise ChatCampaignError("campaign arm has missing or duplicate task records")
    task_ids = tuple(task.id for task in tasks)
    record_task_ids = tuple(record.task_id for record in records)
    if record_task_ids != task_ids or len(set(record_task_ids)) != len(record_task_ids):
        raise ChatCampaignError("campaign arm task records do not match frozen ordered coverage")
    verifier_ids = tuple(record.verifier_id for record in records)
    route_counts = {route.value: 0 for route in ExposureRouteClass}
    for record in records:
        route_counts[record.route_class.value] += 1
    oracle_records = [record for record in records if record.oracle_skill_ids]
    denominator = len(oracle_records)
    clean = sum(record.route_class == ExposureRouteClass.ORACLE_ONLY for record in oracle_records)
    route_risk = sum(
        record.route_class in {ExposureRouteClass.MIXED, ExposureRouteClass.WRONG}
        for record in oracle_records
    )
    passed = sum(record.verifier_passed for record in records)
    return ChatCampaignArmSummary(
        arm=arm,
        ordered_task_ids=task_ids,
        verifier_ids=verifier_ids,
        records=records,
        passed=passed,
        pass_rate=passed / len(records),
        clean_oracle_exposure_rate=clean / denominator if denominator else 0.0,
        exposure_shadowing_rate=route_risk / denominator if denominator else 0.0,
        route_counts=route_counts,
    )


def evaluate_chat_campaign_promotion(
    baseline: ChatCampaignArmSummary,
    provisional: ChatCampaignArmSummary,
    criteria: ChatCampaignPromotionCriteria | None = None,
) -> ChatCampaignPromotionResult:
    """Apply a same-task/verifier COW gate to prompt-exposure outcomes."""

    active = criteria or ChatCampaignPromotionCriteria()
    checks = (
        ValidationResult(
            "same_task_coverage",
            baseline.ordered_task_ids == provisional.ordered_task_ids,
            evidence="same ordered frozen task IDs were re-run",
        ),
        ValidationResult(
            "same_verifier_contract",
            baseline.verifier_ids == provisional.verifier_ids,
            evidence="same per-task verifier IDs were re-run",
        ),
        ValidationResult(
            "pass_rate_non_regression",
            provisional.pass_rate >= baseline.pass_rate + active.min_pass_rate_delta,
            score=provisional.pass_rate - baseline.pass_rate,
            evidence=(
                f"observed delta={provisional.pass_rate - baseline.pass_rate:+.6f}; "
                f"required >= {active.min_pass_rate_delta:+.6f}"
            ),
        ),
        ValidationResult(
            "clean_oracle_exposure_non_regression",
            provisional.clean_oracle_exposure_rate
            >= baseline.clean_oracle_exposure_rate + active.min_clean_oracle_exposure_delta,
            score=provisional.clean_oracle_exposure_rate - baseline.clean_oracle_exposure_rate,
            evidence=(
                "observed delta="
                f"{provisional.clean_oracle_exposure_rate - baseline.clean_oracle_exposure_rate:+.6f}; "
                f"required >= {active.min_clean_oracle_exposure_delta:+.6f}"
            ),
        ),
        ValidationResult(
            "exposure_shadowing_reduction",
            baseline.exposure_shadowing_rate - provisional.exposure_shadowing_rate
            >= active.min_exposure_shadowing_reduction,
            score=baseline.exposure_shadowing_rate - provisional.exposure_shadowing_rate,
            evidence=(
                "observed reduction="
                f"{baseline.exposure_shadowing_rate - provisional.exposure_shadowing_rate:+.6f}; "
                f"required >= {active.min_exposure_shadowing_reduction:+.6f}"
            ),
        ),
    )
    accepted = all_passed(list(checks))
    failed = [check.name for check in checks if not check.passed]
    return ChatCampaignPromotionResult(
        accepted=accepted,
        rollback_required=not accepted,
        reason=(
            "provisional route-local guard accepted after same-task/verifier re-run"
            if accepted
            else f"provisional route-local guard rejected: {', '.join(failed)}"
        ),
        checks=checks,
        baseline=baseline,
        provisional=provisional,
        library_resolution="provisional_promoted" if accepted else "original_retained",
    )


@dataclass(slots=True)
class ChatLifecycleCampaign:
    """One frozen baseline -> diagnose -> COW -> same-verifier campaign."""

    tasks: tuple[TaskSpec, ...]
    library_snapshot: tuple[SkillArtifact, ...]
    executor: CampaignTurnExecutor
    exposure_budget: int = 3
    promotion_criteria: ChatCampaignPromotionCriteria = field(
        default_factory=ChatCampaignPromotionCriteria
    )
    baseline: ChatCampaignArmSummary | None = field(default=None, init=False)
    decisions: tuple[LifecycleDecision, ...] = field(default=(), init=False)
    provisional_library: tuple[SkillArtifact, ...] | None = field(default=None, init=False)
    promotion: ChatCampaignPromotionResult | None = field(default=None, init=False)
    library_snapshot_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.tasks, tuple):
            raise ChatCampaignError("tasks must be a frozen ordered tuple")
        if not isinstance(self.library_snapshot, tuple):
            raise ChatCampaignError("library_snapshot must be a frozen tuple")
        if isinstance(self.exposure_budget, bool) or not isinstance(self.exposure_budget, int) or not 1 <= self.exposure_budget <= 10:
            raise ChatCampaignError("exposure_budget must be an integer from 1 through 10")
        _check_snapshot(self.tasks, self.library_snapshot)
        self.tasks = tuple(copy.deepcopy(self.tasks))
        self.library_snapshot = tuple(copy.deepcopy(self.library_snapshot))
        self.library_snapshot_sha256 = _library_snapshot_hash(self.library_snapshot)

    def _run_arm(
        self, *, arm: str, skills: tuple[SkillArtifact, ...]
    ) -> ChatCampaignArmSummary:
        known = {skill.id for skill in skills}
        active = {skill.id for skill in skills if skill.status == LifecycleStatus.ACTIVE}
        records: list[ChatCampaignTurnRecord] = []
        for ordinal, task in enumerate(self.tasks, start=1):
            evidence = self.executor.run_turn(
                task=copy.deepcopy(task),
                skills=tuple(copy.deepcopy(skills)),
                arm=arm,
                ordinal=ordinal,
            )
            if not isinstance(evidence, ChatCampaignTurnEvidence):
                raise ChatCampaignError("campaign executor must return ChatCampaignTurnEvidence")
            exposures, oracle = _validate_evidence(
                evidence,
                task=task,
                active_skill_ids=active,
                known_skill_ids=known,
                exposure_budget=self.exposure_budget,
            )
            records.append(
                ChatCampaignTurnRecord(
                    trace_id=f"{arm}:{ordinal:04d}:{task.id}",
                    task_id=task.id,
                    verifier_id=evidence.verifier_id,
                    verifier_passed=evidence.verifier_passed,
                    exposure_skill_ids=exposures,
                    oracle_skill_ids=oracle,
                    route_class=_route_class(exposures, oracle),
                    raw_trace_pointer=evidence.raw_trace_pointer,
                    raw_trace_sha256=evidence.raw_trace_sha256,
                )
            )
        return _summarize_arm(arm=arm, tasks=self.tasks, records=tuple(records))

    def run_baseline(self) -> ChatCampaignArmSummary:
        if self.baseline is not None:
            raise ChatCampaignError("baseline arm has already run")
        self.baseline = self._run_arm(arm="baseline", skills=self.library_snapshot)
        return self.baseline

    def diagnose_route_local(self, *, min_route_risk_events: int = 2) -> tuple[LifecycleDecision, ...]:
        if self.baseline is None:
            raise ChatCampaignError("run baseline before diagnosing route-local evidence")
        if isinstance(min_route_risk_events, bool) or not isinstance(min_route_risk_events, int) or min_route_risk_events < 1:
            raise ChatCampaignError("min_route_risk_events must be a positive integer")
        counts: dict[str, int] = {}
        trace_ids: dict[str, list[str]] = {}
        for record in self.baseline.records:
            if record.route_class not in {ExposureRouteClass.MIXED, ExposureRouteClass.WRONG}:
                continue
            oracle = set(record.oracle_skill_ids)
            for skill_id in set(record.exposure_skill_ids) - oracle:
                counts[skill_id] = counts.get(skill_id, 0) + 1
                trace_ids.setdefault(skill_id, []).append(record.trace_id)
        self.decisions = tuple(
            LifecycleDecision(
                skill_id=skill_id,
                action=LifecycleAction.HIDE,
                reason=(
                    "route-local prompt-exposure guard: "
                    f"{count} repeated wrong/mixed exposure records"
                ),
                evidence_trace_ids=trace_ids[skill_id],
            )
            for skill_id, count in sorted(counts.items())
            if count >= min_route_risk_events
        )
        return self.decisions

    def stage_copy_on_write(self) -> tuple[SkillArtifact, ...]:
        if self.baseline is None:
            raise ChatCampaignError("run baseline before staging a route-local guard")
        if self.provisional_library is not None:
            raise ChatCampaignError("copy-on-write library has already been staged")
        provisional, _change = stage_provisional_lifecycle_change(
            list(copy.deepcopy(self.library_snapshot)), list(self.decisions)
        )
        self.provisional_library = tuple(provisional)
        return tuple(copy.deepcopy(self.provisional_library))

    def run_provisional_and_promote(self) -> ChatCampaignPromotionResult:
        if self.baseline is None or self.provisional_library is None:
            raise ChatCampaignError("run baseline and stage copy-on-write before promotion")
        if self.promotion is not None:
            raise ChatCampaignError("provisional arm has already run")
        provisional = self._run_arm(arm="provisional", skills=self.provisional_library)
        self.promotion = evaluate_chat_campaign_promotion(
            self.baseline, provisional, self.promotion_criteria
        )
        return self.promotion

    def resolved_library(self) -> tuple[SkillArtifact, ...]:
        if self.promotion is None:
            raise ChatCampaignError("promotion is pending")
        source = self.provisional_library if self.promotion.accepted else self.library_snapshot
        assert source is not None
        return tuple(copy.deepcopy(source))
