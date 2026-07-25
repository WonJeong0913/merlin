"""HarnessX-inspired hook, processor, and harness-evolution substrate.

Merlin's final direction includes harness co-evolution. The first executable
slice starts with typed hooks, processors, variant manifests, and promotion
gates so later work can evolve processor composition without replacing the whole
runtime at once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .metrics import self_harness_accept
from .models import InvocationRecord, LifecycleAction, LifecycleDecision, SkillArtifact, TaskSpec, TraceRecord, ValidationResult
from .provisioning import tokenize


class Hook(str, Enum):
    TASK_START = "task_start"
    BEFORE_PROVISION = "before_provision"
    AFTER_PROVISION = "after_provision"
    BEFORE_SELECT = "before_select"
    AFTER_SELECT = "after_select"
    AFTER_VERIFY = "after_verify"
    TRACE_CLOSED = "trace_closed"
    POLICY_REVIEW = "policy_review"


class ProcessorKind(str, Enum):
    PROVISIONING = "provisioning"
    SELECTION_GUARD = "selection_guard"
    MONITOR = "monitor"
    LIFECYCLE = "lifecycle"
    POLICY = "policy"
    TRACE = "trace"


@dataclass(slots=True)
class HarnessEvent:
    hook: Hook
    task: TaskSpec | None = None
    skills: list[SkillArtifact] = field(default_factory=list)
    provisioned_skills: list[SkillArtifact] = field(default_factory=list)
    selected_skill: SkillArtifact | None = None
    invocation: InvocationRecord | None = None
    trace: TraceRecord | None = None
    validation: list[ValidationResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)


class Processor(Protocol):
    name: str
    kind: ProcessorKind
    hooks: tuple[Hook, ...]

    def process(self, event: HarnessEvent) -> HarnessEvent:
        ...

    def config(self) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class HarnessVariantSpec:
    """Serializable manifest for one harness variant."""

    id: str
    parent_id: str | None
    summary: str
    processor_manifest: dict[str, list[dict[str, Any]]]
    policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HarnessEvolutionProposal:
    """Candidate harness edit before isolated evaluation."""

    id: str
    parent_variant_id: str
    candidate: HarnessVariantSpec
    rationale: str
    changed_hooks: list[str] = field(default_factory=list)
    evidence_trace_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HarnessEvolutionResult:
    """Held-in and held-out gate result for a candidate harness."""

    proposal_id: str
    accepted: bool
    delta_in: float
    delta_held_out: float
    evidence: str = ""
    metrics: dict[str, float] = field(default_factory=dict)


_SUPPORTED_HARNESS_POLICY_KEYS = frozenset({"exposure_budget"})


def _normalize_harness_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(policy or {})
    unsupported = sorted(set(normalized) - _SUPPORTED_HARNESS_POLICY_KEYS)
    if unsupported:
        raise ValueError(f"unsupported harness policy keys: {', '.join(unsupported)}")

    if "exposure_budget" in normalized:
        exposure_budget = normalized["exposure_budget"]
        if isinstance(exposure_budget, bool) or not isinstance(exposure_budget, int):
            raise ValueError("harness policy exposure_budget must be an integer")
        if exposure_budget < 1:
            raise ValueError("harness policy exposure_budget must be >= 1")
    return normalized


class HarnessRuntime:
    """Ordered processor registry keyed by lifecycle hook."""

    def __init__(self, processors: list[Processor] | None = None, *, policy: dict[str, Any] | None = None) -> None:
        self._processors: dict[Hook, list[Processor]] = {hook: [] for hook in Hook}
        self.policy = _normalize_harness_policy(policy)
        for processor in processors or []:
            self.register(processor)

    def register(self, processor: Processor) -> None:
        for hook in processor.hooks:
            self._processors[hook].append(processor)

    def emit(self, event: HarnessEvent) -> HarnessEvent:
        for key, value in self.policy.items():
            event.metadata.setdefault(key, value)
        for processor in self._processors.get(event.hook, []):
            event = processor.process(event)
            event.audit_events.append(
                {
                    "type": "PROCESSOR",
                    "hook": event.hook.value,
                    "processor": processor.name,
                    "kind": processor.kind.value,
                }
            )
        return event

    def processor_manifest(self) -> dict[str, list[dict[str, Any]]]:
        return {
            hook.value: [_processor_manifest_entry(processor) for processor in processors]
            for hook, processors in self._processors.items()
            if processors
        }


def _selected_route_event(selected_ids: set[str], oracle_ids: set[str]) -> str:
    if not oracle_ids:
        return "spurious" if selected_ids else "empty_no_oracle"
    if not selected_ids:
        return "empty"
    if selected_ids.issubset(oracle_ids):
        return "oracle_only"
    if selected_ids & oracle_ids:
        return "mixed"
    return "wrong"


class SkillStateProcessor:
    """Keep non-active or explicitly blocked skills out of the exposed library."""

    name = "skill_state_filter"
    kind = ProcessorKind.PROVISIONING
    hooks = (Hook.BEFORE_PROVISION,)

    def process(self, event: HarnessEvent) -> HarnessEvent:
        blocked = set(event.metadata.get("blocked_skill_ids", []))
        before = len(event.skills)
        event.skills = [
            skill
            for skill in event.skills
            if skill.status.value == "active" and skill.id not in blocked and not skill.metadata.get("harness_blocked", False)
        ]
        removed = before - len(event.skills)
        if removed:
            event.notes.append(f"filtered {removed} unavailable skills before provisioning")
        return event

    def config(self) -> dict[str, Any]:
        return {}


class ExposureBudgetProcessor:
    """Clamp the exposed skill count to the current harness policy surface."""

    name = "exposure_budget"
    kind = ProcessorKind.POLICY
    hooks = (Hook.BEFORE_PROVISION,)

    def __init__(self, *, max_exposure_budget: int) -> None:
        if max_exposure_budget < 1:
            raise ValueError("max_exposure_budget must be >= 1")
        self.max_exposure_budget = max_exposure_budget

    def process(self, event: HarnessEvent) -> HarnessEvent:
        requested = int(event.metadata.get("exposure_budget", self.max_exposure_budget))
        clamped = min(requested, self.max_exposure_budget)
        event.metadata["exposure_budget"] = clamped
        if clamped != requested:
            event.notes.append(f"clamped exposure budget from {requested} to {clamped}")
        return event

    def config(self) -> dict[str, Any]:
        return {"max_exposure_budget": self.max_exposure_budget}


class DoNotUseConstraintProcessor:
    """Remove provisioned skills whose local contract says not to use them."""

    name = "do_not_use_constraints"
    kind = ProcessorKind.SELECTION_GUARD
    hooks = (Hook.BEFORE_SELECT,)

    def __init__(self, *, min_token_overlap: float = 0.6) -> None:
        if not 0 < min_token_overlap <= 1:
            raise ValueError("min_token_overlap must be in (0, 1]")
        self.min_token_overlap = min_token_overlap

    def process(self, event: HarnessEvent) -> HarnessEvent:
        if event.task is None:
            return event
        task_text = event.task.instruction.lower()
        task_tokens = set(tokenize(event.task.instruction))
        kept: list[SkillArtifact] = []
        blocked: list[str] = []
        for skill in event.provisioned_skills:
            if _constraint_matches(task_text, task_tokens, skill.do_not_use_when, min_overlap=self.min_token_overlap):
                blocked.append(skill.id)
            else:
                kept.append(skill)
        event.provisioned_skills = kept
        if blocked:
            event.metadata["constraint_blocked_skill_ids"] = blocked
            event.notes.append(f"blocked skills by do-not-use constraints: {', '.join(blocked)}")
        return event

    def config(self) -> dict[str, Any]:
        return {"min_token_overlap": self.min_token_overlap}


class ShadowingMonitorProcessor:
    """Annotate traces with route events before lifecycle or policy decisions."""

    name = "shadowing_monitor"
    kind = ProcessorKind.MONITOR
    hooks = (Hook.AFTER_SELECT, Hook.AFTER_VERIFY, Hook.TRACE_CLOSED)

    def process(self, event: HarnessEvent) -> HarnessEvent:
        oracle_ids: set[str] = set()
        selected_ids: set[str] = set()
        if event.task is not None:
            oracle_ids = set(event.task.oracle_skill_ids)
        if event.invocation is not None:
            oracle_ids = set(event.invocation.oracle_skill_ids)
            selected_ids = set(event.invocation.selected_skill_ids)
        elif event.selected_skill is not None:
            selected_ids = {event.selected_skill.id}

        route_event = _selected_route_event(selected_ids, oracle_ids)
        event.metadata["route_event"] = route_event
        if route_event in {"wrong", "mixed", "spurious", "empty"}:
            event.notes.append(f"route risk event: {route_event}")
        if event.trace is not None:
            event.trace.metadata["route_event"] = route_event
        return event

    def config(self) -> dict[str, Any]:
        return {}


class ShadowingLifecycleProcessor:
    """Convert repeated wrong or mixed invocations into lifecycle decisions."""

    name = "shadowing_lifecycle"
    kind = ProcessorKind.LIFECYCLE
    hooks = (Hook.POLICY_REVIEW,)

    def __init__(self, *, min_shadowing_events: int = 2) -> None:
        if min_shadowing_events < 1:
            raise ValueError("min_shadowing_events must be >= 1")
        self.min_shadowing_events = min_shadowing_events

    def process(self, event: HarnessEvent) -> HarnessEvent:
        invocations = list(event.metadata.get("invocations", []))
        counts: dict[str, int] = {}
        for invocation in invocations:
            if not isinstance(invocation, InvocationRecord):
                continue
            selected = set(invocation.selected_skill_ids)
            oracle = set(invocation.oracle_skill_ids)
            route_event = _selected_route_event(selected, oracle)
            if route_event not in {"wrong", "mixed", "spurious"}:
                continue
            for skill_id in selected - oracle:
                counts[skill_id] = counts.get(skill_id, 0) + 1

        decisions = [
            LifecycleDecision(
                skill_id=skill_id,
                action=LifecycleAction.HIDE,
                reason=f"shadowing threshold exceeded: {count} route-risk events",
            )
            for skill_id, count in sorted(counts.items())
            if count >= self.min_shadowing_events
        ]
        event.metadata["lifecycle_decisions"] = decisions
        if decisions:
            event.notes.append(f"proposed {len(decisions)} lifecycle decisions from shadowing evidence")
        return event

    def config(self) -> dict[str, Any]:
        return {"min_shadowing_events": self.min_shadowing_events}


def _constraint_matches(task_text: str, task_tokens: set[str], constraints: list[str], *, min_overlap: float) -> bool:
    for constraint in constraints:
        normalized = constraint.strip().lower()
        if not normalized:
            continue
        if normalized in task_text:
            return True
        constraint_tokens = set(tokenize(normalized))
        if not constraint_tokens:
            continue
        if constraint_tokens.issubset(task_tokens):
            return True
        overlap = len(constraint_tokens & task_tokens) / max(1, min(len(constraint_tokens), len(task_tokens)))
        if overlap >= min_overlap:
            return True
    return False


def _processor_manifest_entry(processor: Processor) -> dict[str, Any]:
    return {
        "name": processor.name,
        "kind": processor.kind.value,
        "config": processor.config(),
    }


def _processor_signature(entry: dict[str, Any]) -> str:
    return json.dumps(
        {
            "name": entry["name"],
            "config": entry.get("config", {}),
        },
        sort_keys=True,
    )


ProcessorFactory = Callable[[dict[str, Any]], Processor]


def _build_skill_state_processor(config: dict[str, Any]) -> Processor:
    if config:
        raise ValueError("skill_state_filter does not accept config")
    return SkillStateProcessor()


def _build_exposure_budget_processor(config: dict[str, Any]) -> Processor:
    return ExposureBudgetProcessor(max_exposure_budget=int(config["max_exposure_budget"]))


def _build_do_not_use_processor(config: dict[str, Any]) -> Processor:
    return DoNotUseConstraintProcessor(min_token_overlap=float(config.get("min_token_overlap", 0.6)))


def _build_shadowing_monitor_processor(config: dict[str, Any]) -> Processor:
    if config:
        raise ValueError("shadowing_monitor does not accept config")
    return ShadowingMonitorProcessor()


def _build_shadowing_lifecycle_processor(config: dict[str, Any]) -> Processor:
    return ShadowingLifecycleProcessor(min_shadowing_events=int(config.get("min_shadowing_events", 2)))


PROCESSOR_FACTORIES: dict[str, ProcessorFactory] = {
    SkillStateProcessor.name: _build_skill_state_processor,
    ExposureBudgetProcessor.name: _build_exposure_budget_processor,
    DoNotUseConstraintProcessor.name: _build_do_not_use_processor,
    ShadowingMonitorProcessor.name: _build_shadowing_monitor_processor,
    ShadowingLifecycleProcessor.name: _build_shadowing_lifecycle_processor,
}


def make_default_harness_runtime(*, max_exposure_budget: int | None = None) -> HarnessRuntime:
    processors: list[Processor] = [
        SkillStateProcessor(),
        DoNotUseConstraintProcessor(),
        ShadowingMonitorProcessor(),
    ]
    policy: dict[str, Any] = {}
    if max_exposure_budget is not None:
        processors.insert(1, ExposureBudgetProcessor(max_exposure_budget=max_exposure_budget))
        policy["exposure_budget"] = max_exposure_budget
    return HarnessRuntime(processors, policy=policy)


def snapshot_harness_variant(
    runtime: HarnessRuntime,
    *,
    variant_id: str,
    parent_id: str | None = None,
    summary: str = "",
    policy: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> HarnessVariantSpec:
    """Capture the current processor composition as an evolvable variant."""

    snapshot_policy = runtime.policy if policy is None else _normalize_harness_policy(policy)
    return HarnessVariantSpec(
        id=variant_id,
        parent_id=parent_id,
        summary=summary,
        processor_manifest=runtime.processor_manifest(),
        policy=dict(snapshot_policy),
        metadata=dict(metadata or {}),
    )


def build_runtime_from_variant(spec: HarnessVariantSpec) -> HarnessRuntime:
    """Reconstruct a runtime from a variant manifest.

    Processor configs are part of the manifest so two variants with the same
    processor names but different thresholds remain distinguishable and
    rollback-capable.
    """

    policy = _normalize_harness_policy(spec.policy)
    processors: list[Processor] = []
    seen: set[str] = set()
    for hook in Hook:
        for entry in spec.processor_manifest.get(hook.value, []):
            signature = _processor_signature(entry)
            if signature in seen:
                continue
            name = entry["name"]
            factory = PROCESSOR_FACTORIES.get(name)
            if factory is None:
                raise ValueError(f"unknown processor in harness variant: {name}")
            processors.append(factory(dict(entry.get("config", {}))))
            seen.add(signature)
    return HarnessRuntime(processors, policy=policy)


def _harness_evolution_preflight_errors(proposal: HarnessEvolutionProposal) -> list[str]:
    _normalize_harness_policy(proposal.candidate.policy)
    errors: list[str] = []
    if not proposal.candidate.summary.strip():
        errors.append("candidate summary is required")
    if not proposal.candidate.processor_manifest or not any(proposal.candidate.processor_manifest.values()):
        errors.append("candidate processor manifest is required")
    if not proposal.rationale.strip():
        errors.append("proposal rationale is required")
    if proposal.candidate.parent_id != proposal.parent_variant_id:
        errors.append("candidate parent_id must match proposal parent_variant_id")
    changed_hooks = [hook for hook in proposal.changed_hooks if hook.strip()]
    if not changed_hooks:
        errors.append("changed_hooks are required")
    else:
        known_hooks = {hook.value for hook in Hook}
        unknown_hooks = sorted(set(changed_hooks) - known_hooks)
        if unknown_hooks:
            errors.append(f"unknown changed_hooks: {', '.join(unknown_hooks)}")
    if not any(trace_id.strip() for trace_id in proposal.evidence_trace_ids):
        errors.append("evidence_trace_ids are required")
    return errors


def evaluate_harness_evolution(
    proposal: HarnessEvolutionProposal,
    *,
    delta_in: float,
    delta_held_out: float,
    evidence: str = "",
    metrics: dict[str, float] | None = None,
) -> HarnessEvolutionResult:
    """Apply structural preflight and the caller-supplied aggregate-delta scaffold gate.

    This scaffold does not execute the candidate or recompute its deltas. Callers
    remain responsible for supplying paired held-in and held-out evaluation data.
    """

    preflight_errors = _harness_evolution_preflight_errors(proposal)
    if preflight_errors:
        preflight_evidence = f"preflight failed: {'; '.join(preflight_errors)}"
        if evidence:
            preflight_evidence = f"{preflight_evidence}; {evidence}"
        return HarnessEvolutionResult(
            proposal_id=proposal.id,
            accepted=False,
            delta_in=delta_in,
            delta_held_out=delta_held_out,
            evidence=preflight_evidence,
            metrics=dict(metrics or {}),
        )
    return HarnessEvolutionResult(
        proposal_id=proposal.id,
        accepted=self_harness_accept(delta_in, delta_held_out),
        delta_in=delta_in,
        delta_held_out=delta_held_out,
        evidence=evidence,
        metrics=dict(metrics or {}),
    )
