"""Data model for the first Merlin prototype.

The model is intentionally small. It captures the AIP/SkillOps direction without
committing the project to a graph database or a full skill runtime yet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


class LifecycleStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    HIDDEN = "hidden"
    REPAIR = "repair"
    RETIRED = "retired"
    REJECTED = "rejected"


class LifecycleAction(str, Enum):
    ADOPT = "adopt"
    HIDE = "hide"
    REPAIR = "repair"
    MERGE = "merge"
    RETIRE = "retire"
    REJECT = "reject"
    ADD_VALIDATOR = "add_validator"


@dataclass(slots=True)
class SkillStep:
    id: str
    description: str
    kind: Literal["instruction", "script"] = "instruction"
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    script_path: str | None = None


@dataclass(slots=True)
class SkillEdge:
    source: str
    target: str
    kind: Literal["depends_on", "input_output"] = "depends_on"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VerifierSpec:
    name: str
    kind: Literal["exact_match", "file_exists", "command"]
    expected: str | None = None
    target_path: str | None = None
    command: list[str] = field(default_factory=list)
    timeout_s: float = 10.0


@dataclass(slots=True)
class TaskSpec:
    id: str
    instruction: str
    verifier: VerifierSpec
    setup_files: dict[str, str] = field(default_factory=dict)
    oracle_skill_ids: list[str] = field(default_factory=list)
    regression_group: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ValidationResult:
    name: str
    passed: bool
    score: float | None = None
    evidence: str = ""
    cost: float | None = None


@dataclass(slots=True)
class SkillArtifact:
    id: str
    name: str
    description: str
    trigger: str
    do_not_use_when: list[str] = field(default_factory=list)
    steps: list[SkillStep] = field(default_factory=list)
    edges: list[SkillEdge] = field(default_factory=list)
    validators: list[str] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    provenance_trace_ids: list[str] = field(default_factory=list)
    status: LifecycleStatus = LifecycleStatus.CANDIDATE
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(slots=True)
class InvocationRecord:
    task_id: str
    provisioned_skill_ids: list[str]
    selected_skill_ids: list[str]
    oracle_skill_ids: list[str] = field(default_factory=list)
    success: bool | None = None
    score: float | None = None
    cost: float | None = None
    latency_s: float | None = None


@dataclass(frozen=True, slots=True)
class RawTraceReference:
    """Pointer to immutable raw agent evidence, never its duplicated payload.

    ``pointer`` is a path relative to ``AgentRunContract.raw_trace_root``.  The
    content hash is validated before a run becomes a Merlin agent trace and
    again before strict invocation metrics consume it.
    """

    pointer: str
    sha256: str


@dataclass(frozen=True, slots=True)
class AgentRunContract:
    """Frozen conditions for one adapter-mediated agent task attempt."""

    run_id: str
    task_id: str
    condition: str
    workspace_root: str
    raw_trace_root: str
    agent_id: str
    agent_version: str
    backend: str
    model_id: str
    effort: str | None
    budget_id: str
    library_snapshot_id: str
    library_snapshot_sha256: str
    verifier_id: str
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class SkillInvocationEvent:
    """Observed loading or provider-native invocation of one skill body.

    Selection, retrieval, ranking, and planning are intentionally not valid
    event kinds.  They are logged separately in ``InvocationRecord``.
    """

    skill_id: str
    event_kind: Literal["skill_body_loaded", "provider_skill_invocation"]
    source: str
    event_id: str
    sequence: int


@dataclass(slots=True)
class AgentRunResult:
    """Normalized adapter result before the deterministic verifier runs.

    ``metadata`` is persisted with the normalized trace, so adapters may put
    only safe provenance summaries there (for example CLI version, redacted
    command shape, provider event identifiers, hashes, and exit status).  Raw
    provider transcripts belong exclusively behind ``raw_trace``.
    """

    contract: AgentRunContract
    workspace_root: str
    raw_trace: RawTraceReference
    actual_invocation_evidence_complete: bool
    selected_skill_ids: list[str] = field(default_factory=list)
    invocation_events: list[SkillInvocationEvent] = field(default_factory=list)
    answer: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentTraceEvidence:
    """Versioned immutable evidence stored inside a normalized trace."""

    contract: AgentRunContract
    workspace_root: str
    raw_trace: RawTraceReference
    actual_invocation_evidence_complete: bool
    selected_skill_ids: tuple[str, ...]
    invocation_events: tuple[SkillInvocationEvent, ...]
    schema_version: int = 1


@dataclass(slots=True)
class TraceRecord:
    id: str
    task_id: str
    condition: str
    events: list[dict[str, Any]] = field(default_factory=list)
    invocation: InvocationRecord | None = None
    validation: list[ValidationResult] = field(default_factory=list)
    failure_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LifecycleDecision:
    skill_id: str
    action: LifecycleAction
    reason: str
    evidence_trace_ids: list[str] = field(default_factory=list)
    validation_results: list[ValidationResult] = field(default_factory=list)


@dataclass(slots=True)
class LifecycleVerificationSnapshot:
    """Metrics and verifier coverage captured before or after a lifecycle edit.

    A lifecycle edit may only compare like-for-like verifier runs.  Keeping the
    task IDs and per-task verifier IDs next to the routing metrics prevents a
    superficially better result from being promoted after silently dropping a
    task or changing its verifier contract.
    """

    task_ids: list[str]
    verifier_ids_by_task: dict[str, list[str]]
    passed: int
    pass_rate: float
    pi_o: float
    pi_m: float


@dataclass(slots=True)
class LifecyclePromotionCriteria:
    """Pre-registered safety requirements for a provisional lifecycle edit."""

    require_same_task_coverage: bool = True
    require_same_verifier_contract: bool = True
    min_pass_rate_delta: float = 0.0
    min_pi_o_delta: float = 0.0
    min_pi_m_reduction: float = 1e-12


@dataclass(slots=True)
class LifecyclePromotionResult:
    """Accept or reject a provisional lifecycle edit with auditable evidence."""

    accepted: bool
    reason: str
    criteria: LifecyclePromotionCriteria
    baseline: LifecycleVerificationSnapshot
    provisional: LifecycleVerificationSnapshot
    checks: list[ValidationResult] = field(default_factory=list)
    rollback_required: bool = False


@dataclass(slots=True)
class ProvisionalLifecycleChange:
    """A copy-on-write lifecycle edit, including the state needed for rollback."""

    decisions: list[LifecycleDecision]
    original_statuses: dict[str, str]
    provisional_statuses: dict[str, str]


@dataclass(slots=True)
class HarnessPolicyChange:
    id: str
    surface: str
    summary: str
    delta_in: float
    delta_held_out: float
    accepted: bool
    evidence: str = ""


@dataclass(slots=True)
class BehaviorDelta:
    task_id: str
    with_skill_trace_id: str
    without_skill_trace_id: str
    success_delta: float | None = None
    cost_ratio: float | None = None
    tool_event_delta: int = 0
    write_event_delta: int = 0
    validation_event_delta: int = 0
    off_task_artifacts: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
