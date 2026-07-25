"""Typed, composable harness runtime inspired by HarnessX.

This module implements the deterministic substrate needed by Merlin's
skill-harness policy evolution.  It deliberately does not execute model-written
Python or claim HarnessX's model co-evolution results.  Processor code must be
registered locally, events are checked against hook-specific mutation
contracts, and every candidate keeps an explicit rollback target.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import math
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from enum import Enum, IntEnum
from typing import Any, ClassVar, Protocol, TypeAlias


class HarnessXContractError(ValueError):
    """A typed event, composition, manifest, or gate contract was violated."""


class HarnessXExecutionError(RuntimeError):
    """A registered processor failed while running inside the harness."""


class HarnessXInterrupt(RuntimeError):
    """A processor intentionally interrupted the current agent loop."""


class HarnessXHook(str, Enum):
    TASK_START = "task_start"
    STEP_START = "step_start"
    BEFORE_MODEL = "before_model"
    AFTER_MODEL = "after_model"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    STEP_END = "step_end"
    TASK_END = "task_end"


class ProcessorOrder(IntEnum):
    PRE = -100
    NORMAL = 0
    POST = 100


class ProcessorOutcome(str, Enum):
    PASS_THROUGH = "pass_through"
    TRANSFORM = "transform"
    SPLIT = "split"
    INTERCEPT = "intercept"


class HarnessXEditKind(str, Enum):
    INSERT = "insert"
    REPLACE = "replace"
    REMOVE = "remove"


class HarnessRiskTier(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments_json: str = "{}"

    def __post_init__(self) -> None:
        _require_non_empty_string(self.name, label="tool call name")
        if not isinstance(self.arguments_json, str):
            raise HarnessXContractError("tool call arguments_json must be a string")
        try:
            parsed = json.loads(self.arguments_json)
        except json.JSONDecodeError as exc:
            raise HarnessXContractError("tool call arguments_json must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise HarnessXContractError("tool call arguments_json must encode an object")


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskStartEvent:
    hook: ClassVar[HarnessXHook] = HarnessXHook.TASK_START
    event_id: str
    task_id: str
    system_prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class StepStartEvent:
    hook: ClassVar[HarnessXHook] = HarnessXHook.STEP_START
    event_id: str
    task_id: str
    step_index: int
    history: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class BeforeModelEvent:
    hook: ClassVar[HarnessXHook] = HarnessXHook.BEFORE_MODEL
    event_id: str
    task_id: str
    step_index: int
    model_role: str
    last_user_content: str
    appended_user_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelResponseEvent:
    hook: ClassVar[HarnessXHook] = HarnessXHook.AFTER_MODEL
    event_id: str
    task_id: str
    step_index: int
    response_content: str
    tool_calls: tuple[ToolCall, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCallEvent:
    hook: ClassVar[HarnessXHook] = HarnessXHook.BEFORE_TOOL
    event_id: str
    task_id: str
    step_index: int
    tool_name: str
    tool_input_json: str
    approval_required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.tool_name, label="tool_name")
        if not isinstance(self.tool_input_json, str):
            raise HarnessXContractError("tool_input_json must be a string")
        try:
            parsed = json.loads(self.tool_input_json)
        except json.JSONDecodeError as exc:
            raise HarnessXContractError("tool_input_json must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise HarnessXContractError("tool_input_json must encode an object")


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolResultEvent:
    hook: ClassVar[HarnessXHook] = HarnessXHook.AFTER_TOOL
    event_id: str
    task_id: str
    step_index: int
    tool_name: str
    tool_result: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class StepEndEvent:
    hook: ClassVar[HarnessXHook] = HarnessXHook.STEP_END
    event_id: str
    task_id: str
    step_index: int
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskEndEvent:
    hook: ClassVar[HarnessXHook] = HarnessXHook.TASK_END
    event_id: str
    task_id: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


HarnessXEvent: TypeAlias = (
    TaskStartEvent
    | StepStartEvent
    | BeforeModelEvent
    | ModelResponseEvent
    | ToolCallEvent
    | ToolResultEvent
    | StepEndEvent
    | TaskEndEvent
)


EVENT_TYPES: dict[HarnessXHook, type[HarnessXEvent]] = {
    HarnessXHook.TASK_START: TaskStartEvent,
    HarnessXHook.STEP_START: StepStartEvent,
    HarnessXHook.BEFORE_MODEL: BeforeModelEvent,
    HarnessXHook.AFTER_MODEL: ModelResponseEvent,
    HarnessXHook.BEFORE_TOOL: ToolCallEvent,
    HarnessXHook.AFTER_TOOL: ToolResultEvent,
    HarnessXHook.STEP_END: StepEndEvent,
    HarnessXHook.TASK_END: TaskEndEvent,
}


PERMITTED_MUTATIONS: dict[HarnessXHook, frozenset[str]] = {
    HarnessXHook.TASK_START: frozenset({"system_prompt"}),
    HarnessXHook.STEP_START: frozenset({"history"}),
    HarnessXHook.BEFORE_MODEL: frozenset({"last_user_content", "appended_user_message"}),
    HarnessXHook.AFTER_MODEL: frozenset({"response_content", "tool_calls"}),
    HarnessXHook.BEFORE_TOOL: frozenset({"tool_input_json", "approval_required"}),
    HarnessXHook.AFTER_TOOL: frozenset({"tool_result"}),
    HarnessXHook.STEP_END: frozenset(),
    HarnessXHook.TASK_END: frozenset(),
}


class HarnessXProcessor(Protocol):
    name: str
    hook: HarnessXHook
    singleton_group: str
    order: ProcessorOrder
    after: tuple[str, ...]

    def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        ...

    def config(self) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True, slots=True)
class ProcessorManifestEntry:
    name: str
    hook: HarnessXHook
    singleton_group: str
    order: ProcessorOrder = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "hook": self.hook.value,
            "singleton_group": self.singleton_group,
            "order": int(self.order),
            "after": list(self.after),
            "config": copy.deepcopy(self.config),
        }


@dataclass(frozen=True, slots=True)
class ProcessorAuditRecord:
    event_id: str
    hook: HarnessXHook
    processor: str
    singleton_group: str
    outcome: ProcessorOutcome
    output_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HarnessXEmission:
    events: tuple[HarnessXEvent, ...]
    audit: tuple[ProcessorAuditRecord, ...]

    @property
    def intercepted(self) -> bool:
        return not self.events


@dataclass(frozen=True, slots=True)
class HarnessXVariantSpec:
    id: str
    parent_id: str | None
    summary: str
    processors: tuple[ProcessorManifestEntry, ...]
    slots: dict[str, str] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "summary": self.summary,
            "processors": [entry.to_dict() for entry in self.processors],
            "slots": copy.deepcopy(self.slots),
            "policy": copy.deepcopy(self.policy),
            "metadata": copy.deepcopy(self.metadata),
        }

    @property
    def sha256(self) -> str:
        return _sha256_json(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class HarnessXProcessorEdit:
    kind: HarnessXEditKind
    hook: HarnessXHook
    singleton_group: str
    dimension: str
    processor: ProcessorManifestEntry | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "hook": self.hook.value,
            "singleton_group": self.singleton_group,
            "dimension": self.dimension,
            "processor": self.processor.to_dict() if self.processor else None,
        }


@dataclass(frozen=True, slots=True)
class HarnessXChangeManifest:
    id: str
    candidate_variant_id: str
    parent_variant_sha256: str
    rollback_variant_sha256: str
    rationale: str
    evidence_trace_ids: tuple[str, ...]
    expected_improve_task_ids: tuple[str, ...]
    expected_regress_task_ids: tuple[str, ...]
    risk_tier: HarnessRiskTier
    edits: tuple[HarnessXProcessorEdit, ...]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "candidate_variant_id": self.candidate_variant_id,
            "parent_variant_sha256": self.parent_variant_sha256,
            "rollback_variant_sha256": self.rollback_variant_sha256,
            "rationale": self.rationale,
            "evidence_trace_ids": list(self.evidence_trace_ids),
            "expected_improve_task_ids": list(self.expected_improve_task_ids),
            "expected_regress_task_ids": list(self.expected_regress_task_ids),
            "risk_tier": self.risk_tier.name.lower(),
            "edits": [edit.to_dict() for edit in self.edits],
        }

    @property
    def sha256(self) -> str:
        return _sha256_json(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class HarnessXGateCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class HarnessXApprovalPolicy:
    require_at_or_above: HarnessRiskTier = HarnessRiskTier.HIGH
    max_consecutive_same_dimension_edits: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.require_at_or_above, HarnessRiskTier):
            raise HarnessXContractError("require_at_or_above must be HarnessRiskTier")
        if (
            isinstance(self.max_consecutive_same_dimension_edits, bool)
            or not isinstance(self.max_consecutive_same_dimension_edits, int)
            or self.max_consecutive_same_dimension_edits < 1
        ):
            raise HarnessXContractError("max_consecutive_same_dimension_edits must be >= 1")


@dataclass(frozen=True, slots=True)
class HarnessXGateDecision:
    accepted: bool
    requires_approval: bool
    resolution: str
    resolved_variant_id: str
    rollback_variant_id: str
    checks: tuple[HarnessXGateCheck, ...]


ProcessorFactory: TypeAlias = Callable[[Mapping[str, Any]], HarnessXProcessor]


_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 65_536


class HarnessXProcessorRegistry:
    """Explicit allowlist for reconstructing processor code from manifests."""

    def __init__(self) -> None:
        self._factories: dict[str, ProcessorFactory] = {}

    def register(self, name: str, factory: ProcessorFactory) -> None:
        _require_non_empty_string(name, label="processor factory name")
        if not callable(factory):
            raise HarnessXContractError("processor factory must be callable")
        if name in self._factories:
            raise HarnessXContractError(f"processor factory already registered: {name}")
        self._factories[name] = factory

    def build(self, entry: ProcessorManifestEntry) -> HarnessXProcessor:
        factory = self._factories.get(entry.name)
        if factory is None:
            raise HarnessXContractError(f"unregistered processor code: {entry.name}")
        processor = factory(copy.deepcopy(entry.config))
        actual = processor_manifest_entry(processor)
        if actual != entry:
            raise HarnessXContractError(f"processor factory manifest mismatch: {entry.name}")
        return processor


class HarnessXRuntime:
    """Async typed processor pipeline with fail-closed composition contracts."""

    def __init__(
        self,
        processors: Sequence[HarnessXProcessor] = (),
        *,
        max_split_fanout: int = 32,
        max_pipeline_events: int = 128,
        max_event_bytes: int = 1_048_576,
        processor_timeout_sec: float = 5.0,
    ) -> None:
        if max_split_fanout < 1 or max_pipeline_events < 1 or max_event_bytes < 1:
            raise HarnessXContractError("event bounds must be >= 1")
        if not math.isfinite(processor_timeout_sec) or processor_timeout_sec <= 0:
            raise HarnessXContractError("processor_timeout_sec must be finite and > 0")
        self.max_split_fanout = max_split_fanout
        self.max_pipeline_events = max_pipeline_events
        self.max_event_bytes = max_event_bytes
        self.processor_timeout_sec = processor_timeout_sec
        grouped: dict[HarnessXHook, list[tuple[int, HarnessXProcessor]]] = {
            hook: [] for hook in HarnessXHook
        }
        global_groups: set[str] = set()
        global_names: set[str] = set()
        for index, processor in enumerate(processors):
            _validate_processor(processor)
            if processor.singleton_group in global_groups:
                raise HarnessXContractError(
                    f"global singleton processor conflict: {processor.singleton_group}"
                )
            if processor.name in global_names:
                raise HarnessXContractError(f"duplicate processor name: {processor.name}")
            global_groups.add(processor.singleton_group)
            global_names.add(processor.name)
            grouped[processor.hook].append((index, processor))
        self._processors = {
            hook: tuple(_order_processors(items)) for hook, items in grouped.items()
        }

    def processors(self, hook: HarnessXHook | None = None) -> tuple[HarnessXProcessor, ...]:
        if hook is not None:
            return self._processors[hook]
        return tuple(processor for current in HarnessXHook for processor in self._processors[current])

    def manifest(self) -> tuple[ProcessorManifestEntry, ...]:
        return tuple(processor_manifest_entry(processor) for processor in self.processors())

    async def emit(self, event: HarnessXEvent) -> HarnessXEmission:
        _validate_event_type(event)
        _validate_event_size(event, self.max_event_bytes)
        current: tuple[HarnessXEvent, ...] = (copy.deepcopy(event),)
        audit: list[ProcessorAuditRecord] = []
        for processor in self._processors[event.hook]:
            next_events: list[HarnessXEvent] = []
            for input_event in current:
                before = copy.deepcopy(input_event)
                outputs: list[HarnessXEvent] = []
                try:
                    async with asyncio.timeout(self.processor_timeout_sec):
                        stream = processor.process(copy.deepcopy(input_event))
                        if not inspect.isasyncgen(stream) and not hasattr(stream, "__aiter__"):
                            raise HarnessXContractError(
                                f"processor {processor.name} must return an AsyncIterator"
                            )
                        async for output in stream:
                            _validate_processor_output(before, output, processor)
                            _validate_event_size(output, self.max_event_bytes)
                            outputs.append(copy.deepcopy(output))
                            if len(outputs) > self.max_split_fanout:
                                raise HarnessXContractError(
                                    f"processor {processor.name} exceeded split fanout"
                                )
                except HarnessXInterrupt:
                    raise
                except TimeoutError as exc:
                    raise HarnessXExecutionError(
                        f"processor {processor.name} exceeded timeout at {processor.hook.value}"
                    ) from exc
                except HarnessXContractError:
                    raise
                except Exception as exc:
                    raise HarnessXExecutionError(
                        f"processor {processor.name} failed at {processor.hook.value}"
                    ) from exc

                outcome = _processor_outcome(before, outputs)
                audit.append(
                    ProcessorAuditRecord(
                        event_id=before.event_id,
                        hook=processor.hook,
                        processor=processor.name,
                        singleton_group=processor.singleton_group,
                        outcome=outcome,
                        output_event_ids=tuple(output.event_id for output in outputs),
                    )
                )
                next_events.extend(outputs)
                if len(next_events) > self.max_pipeline_events:
                    raise HarnessXContractError("pipeline event bound exceeded")
            current = tuple(next_events)
            if not current:
                break
        return HarnessXEmission(events=current, audit=tuple(audit))

    def emit_sync(self, event: HarnessXEvent) -> HarnessXEmission:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.emit(event))
        raise HarnessXExecutionError("emit_sync cannot run inside an active event loop")


class SystemPromptAppendProcessor:
    name = "system_prompt_append"
    hook = HarnessXHook.TASK_START
    singleton_group = "system_prompt_policy"
    order = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()

    def __init__(self, *, suffix: str) -> None:
        if not isinstance(suffix, str) or not suffix:
            raise HarnessXContractError("system prompt suffix is required")
        self.suffix = suffix

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, TaskStartEvent):
            raise HarnessXContractError("system_prompt_append requires TaskStartEvent")
        yield replace(event, system_prompt=event.system_prompt + self.suffix)

    def config(self) -> Mapping[str, Any]:
        return {"suffix": self.suffix}


class HistoryWindowProcessor:
    name = "history_window"
    hook = HarnessXHook.STEP_START
    singleton_group = "history_policy"
    order = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()

    def __init__(self, *, max_messages: int) -> None:
        if isinstance(max_messages, bool) or not isinstance(max_messages, int) or max_messages < 1:
            raise HarnessXContractError("max_messages must be >= 1")
        self.max_messages = max_messages

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, StepStartEvent):
            raise HarnessXContractError("history_window requires StepStartEvent")
        yield replace(event, history=event.history[-self.max_messages :])

    def config(self) -> Mapping[str, Any]:
        return {"max_messages": self.max_messages}


class BeforeModelContentLimitProcessor:
    name = "before_model_content_limit"
    hook = HarnessXHook.BEFORE_MODEL
    singleton_group = "before_model_content_policy"
    order = ProcessorOrder.POST
    after: tuple[str, ...] = ()

    def __init__(self, *, max_chars: int) -> None:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
            raise HarnessXContractError("max_chars must be >= 1")
        self.max_chars = max_chars

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, BeforeModelEvent):
            raise HarnessXContractError("before_model_content_limit requires BeforeModelEvent")
        yield replace(event, last_user_content=event.last_user_content[: self.max_chars])

    def config(self) -> Mapping[str, Any]:
        return {"max_chars": self.max_chars}


class ToolCallAllowlistProcessor:
    name = "tool_call_allowlist"
    hook = HarnessXHook.AFTER_MODEL
    singleton_group = "tool_call_policy"
    order = ProcessorOrder.POST
    after: tuple[str, ...] = ()

    def __init__(self, *, allowed_tools: Sequence[str]) -> None:
        self.allowed_tools = _normalize_string_set(allowed_tools, label="allowed_tools")

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, ModelResponseEvent):
            raise HarnessXContractError("tool_call_allowlist requires ModelResponseEvent")
        allowed = set(self.allowed_tools)
        yield replace(event, tool_calls=tuple(call for call in event.tool_calls if call.name in allowed))

    def config(self) -> Mapping[str, Any]:
        return {"allowed_tools": list(self.allowed_tools)}


class SelectiveToolApprovalProcessor:
    name = "selective_tool_approval"
    hook = HarnessXHook.BEFORE_TOOL
    singleton_group = "tool_approval_policy"
    order = ProcessorOrder.POST
    after: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        auto_approve_tools: Sequence[str] = (),
        always_require_approval_tools: Sequence[str] = (),
    ) -> None:
        self.auto_approve_tools = _normalize_string_set(
            auto_approve_tools, label="auto_approve_tools"
        )
        self.always_require_approval_tools = _normalize_string_set(
            always_require_approval_tools,
            label="always_require_approval_tools",
        )
        overlap = set(self.auto_approve_tools) & set(self.always_require_approval_tools)
        if overlap:
            raise HarnessXContractError(
                f"tool approval sets overlap: {', '.join(sorted(overlap))}"
            )

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, ToolCallEvent):
            raise HarnessXContractError("selective_tool_approval requires ToolCallEvent")
        if event.tool_name in self.always_require_approval_tools:
            approval_required = True
        elif event.tool_name in self.auto_approve_tools:
            approval_required = False
        else:
            approval_required = True
        yield replace(event, approval_required=approval_required)

    def config(self) -> Mapping[str, Any]:
        return {
            "auto_approve_tools": list(self.auto_approve_tools),
            "always_require_approval_tools": list(self.always_require_approval_tools),
        }


class ExactToolInputPolicyProcessor:
    """Fail closed unless a live tool call exactly matches the bounded policy.

    This processor is intentionally narrower than a general shell parser.  A
    policy may admit exact command strings for one named tool and may deny
    whole tool classes such as ``apply_patch``.  Empty output is the HarnessX
    intercept signal consumed by the live Codex ``PreToolUse`` bridge.
    """

    name = "exact_tool_input_policy"
    hook = HarnessXHook.BEFORE_TOOL
    singleton_group = "live_tool_input_policy"
    order = ProcessorOrder.PRE
    after: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        allowed_commands: Sequence[str] = (),
        command_tool: str = "Bash",
        denied_tools: Sequence[str] = ("apply_patch",),
    ) -> None:
        _require_non_empty_string(command_tool, label="command_tool")
        self.command_tool = command_tool
        self.allowed_commands = _normalize_string_set(
            allowed_commands,
            label="allowed_commands",
        )
        self.denied_tools = _normalize_string_set(denied_tools, label="denied_tools")
        if command_tool in self.denied_tools:
            raise HarnessXContractError("command_tool must not also be denied")

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, ToolCallEvent):
            raise HarnessXContractError("exact_tool_input_policy requires ToolCallEvent")
        if event.tool_name in self.denied_tools:
            return
        if event.tool_name != self.command_tool:
            return
        payload = json.loads(event.tool_input_json)
        command = payload.get("command")
        if not isinstance(command, str) or command not in self.allowed_commands:
            return
        yield event

    def config(self) -> Mapping[str, Any]:
        return {
            "allowed_commands": list(self.allowed_commands),
            "command_tool": self.command_tool,
            "denied_tools": list(self.denied_tools),
        }


class ExactToolCallPolicyProcessor:
    """Allow exact canonical JSON inputs across multiple registered tools.

    The processor does not infer that a tool or path is read-only.  Every
    admitted call must be present as one exact ``(tool_name, tool_input)``
    contract entry.  Unknown tools, altered inputs, and explicitly denied tool
    classes are intercepted before execution.
    """

    name = "exact_tool_call_policy"
    hook = HarnessXHook.BEFORE_TOOL
    singleton_group = "live_tool_input_policy"
    order = ProcessorOrder.PRE
    after: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        allowed_tool_inputs: Sequence[Mapping[str, Any]] = (),
        denied_tools: Sequence[str] = (),
    ) -> None:
        self.denied_tools = _normalize_string_set(denied_tools, label="denied_tools")
        if isinstance(allowed_tool_inputs, (str, bytes)) or not isinstance(
            allowed_tool_inputs, Sequence
        ):
            raise HarnessXContractError(
                "allowed_tool_inputs must be a sequence of objects"
            )
        normalized: list[tuple[str, str]] = []
        for index, item in enumerate(allowed_tool_inputs):
            if not isinstance(item, Mapping) or set(item) != {
                "tool_name",
                "tool_input",
            }:
                raise HarnessXContractError(
                    f"allowed_tool_inputs[{index}] keys are invalid"
                )
            tool_name = item["tool_name"]
            tool_input = item["tool_input"]
            _require_non_empty_string(tool_name, label="allowed tool name")
            if not isinstance(tool_input, Mapping):
                raise HarnessXContractError(
                    f"allowed_tool_inputs[{index}].tool_input must be an object"
                )
            canonical_input = _canonical_json(dict(tool_input))
            normalized.append((tool_name, canonical_input))
        if len(set(normalized)) != len(normalized):
            raise HarnessXContractError(
                "allowed_tool_inputs must contain unique exact calls"
            )
        overlap = {name for name, _payload in normalized} & set(self.denied_tools)
        if overlap:
            raise HarnessXContractError(
                f"allowed and denied tools overlap: {', '.join(sorted(overlap))}"
            )
        self._allowed = tuple(sorted(normalized))

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, ToolCallEvent):
            raise HarnessXContractError("exact_tool_call_policy requires ToolCallEvent")
        if event.tool_name in self.denied_tools:
            return
        key = (event.tool_name, _canonical_json(json.loads(event.tool_input_json)))
        if key not in self._allowed:
            return
        yield event

    def config(self) -> Mapping[str, Any]:
        return {
            "allowed_tool_inputs": [
                {
                    "tool_name": tool_name,
                    "tool_input": json.loads(tool_input_json),
                }
                for tool_name, tool_input_json in self._allowed
            ],
            "denied_tools": list(self.denied_tools),
        }


class ToolResultLimitProcessor:
    name = "tool_result_limit"
    hook = HarnessXHook.AFTER_TOOL
    singleton_group = "tool_result_policy"
    order = ProcessorOrder.POST
    after: tuple[str, ...] = ()

    def __init__(self, *, max_chars: int) -> None:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
            raise HarnessXContractError("max_chars must be >= 1")
        self.max_chars = max_chars

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, ToolResultEvent):
            raise HarnessXContractError("tool_result_limit requires ToolResultEvent")
        yield replace(event, tool_result=event.tool_result[: self.max_chars])

    def config(self) -> Mapping[str, Any]:
        return {"max_chars": self.max_chars}


class StepEndAuditProcessor:
    name = "step_end_audit"
    hook = HarnessXHook.STEP_END
    singleton_group = "step_end_observer"
    order = ProcessorOrder.POST
    after: tuple[str, ...] = ()

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, StepEndEvent):
            raise HarnessXContractError("step_end_audit requires StepEndEvent")
        yield event

    def config(self) -> Mapping[str, Any]:
        return {}


class TaskEndAuditProcessor:
    name = "task_end_audit"
    hook = HarnessXHook.TASK_END
    singleton_group = "task_end_observer"
    order = ProcessorOrder.POST
    after: tuple[str, ...] = ()

    async def process(self, event: HarnessXEvent) -> AsyncIterator[HarnessXEvent]:
        if not isinstance(event, TaskEndEvent):
            raise HarnessXContractError("task_end_audit requires TaskEndEvent")
        yield event

    def config(self) -> Mapping[str, Any]:
        return {}


def make_default_harnessx_registry() -> HarnessXProcessorRegistry:
    registry = HarnessXProcessorRegistry()
    registry.register(
        SystemPromptAppendProcessor.name,
        lambda config: SystemPromptAppendProcessor(suffix=str(config["suffix"])),
    )
    registry.register(
        HistoryWindowProcessor.name,
        lambda config: HistoryWindowProcessor(max_messages=config["max_messages"]),
    )
    registry.register(
        BeforeModelContentLimitProcessor.name,
        lambda config: BeforeModelContentLimitProcessor(max_chars=config["max_chars"]),
    )
    registry.register(
        ToolCallAllowlistProcessor.name,
        lambda config: ToolCallAllowlistProcessor(
            allowed_tools=tuple(str(item) for item in config["allowed_tools"])
        ),
    )
    registry.register(
        SelectiveToolApprovalProcessor.name,
        lambda config: SelectiveToolApprovalProcessor(
            auto_approve_tools=tuple(str(item) for item in config["auto_approve_tools"]),
            always_require_approval_tools=tuple(
                str(item) for item in config["always_require_approval_tools"]
            ),
        ),
    )
    registry.register(
        ExactToolInputPolicyProcessor.name,
        lambda config: ExactToolInputPolicyProcessor(
            allowed_commands=tuple(str(item) for item in config["allowed_commands"]),
            command_tool=str(config["command_tool"]),
            denied_tools=tuple(str(item) for item in config["denied_tools"]),
        ),
    )
    registry.register(
        ExactToolCallPolicyProcessor.name,
        lambda config: ExactToolCallPolicyProcessor(
            allowed_tool_inputs=tuple(config["allowed_tool_inputs"]),
            denied_tools=tuple(str(item) for item in config["denied_tools"]),
        ),
    )
    registry.register(
        ToolResultLimitProcessor.name,
        lambda config: ToolResultLimitProcessor(max_chars=config["max_chars"]),
    )
    registry.register(StepEndAuditProcessor.name, lambda config: StepEndAuditProcessor())
    registry.register(TaskEndAuditProcessor.name, lambda config: TaskEndAuditProcessor())
    return registry


def make_default_harnessx_runtime(
    *,
    system_prompt_suffix: str,
    max_history_messages: int = 64,
    max_user_content_chars: int = 65_536,
    allowed_tool_calls: Sequence[str] = (),
    auto_approve_tools: Sequence[str] = (),
    always_require_approval_tools: Sequence[str] = (),
    max_tool_result_chars: int = 262_144,
) -> HarnessXRuntime:
    """Create a bounded processor at every HarnessX lifecycle hook."""

    return HarnessXRuntime(
        [
            SystemPromptAppendProcessor(suffix=system_prompt_suffix),
            HistoryWindowProcessor(max_messages=max_history_messages),
            BeforeModelContentLimitProcessor(max_chars=max_user_content_chars),
            ToolCallAllowlistProcessor(allowed_tools=allowed_tool_calls),
            SelectiveToolApprovalProcessor(
                auto_approve_tools=auto_approve_tools,
                always_require_approval_tools=always_require_approval_tools,
            ),
            ToolResultLimitProcessor(max_chars=max_tool_result_chars),
            StepEndAuditProcessor(),
            TaskEndAuditProcessor(),
        ]
    )


def processor_manifest_entry(processor: HarnessXProcessor) -> ProcessorManifestEntry:
    config = dict(processor.config())
    _validate_json_value(config, path="processor.config")
    encoded = _canonical_json(config)
    if len(encoded.encode("utf-8")) > 16_384:
        raise HarnessXContractError("processor config exceeds 16384 bytes")
    return ProcessorManifestEntry(
        name=processor.name,
        hook=processor.hook,
        singleton_group=processor.singleton_group,
        order=ProcessorOrder(processor.order),
        after=tuple(processor.after),
        config=config,
    )


def snapshot_harnessx_variant(
    runtime: HarnessXRuntime,
    *,
    variant_id: str,
    parent_id: str | None = None,
    summary: str,
    slots: Mapping[str, str] | None = None,
    policy: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> HarnessXVariantSpec:
    _require_non_empty_string(variant_id, label="variant id")
    _require_non_empty_string(summary, label="variant summary")
    normalized_slots = dict(slots or {})
    normalized_policy = dict(policy or {})
    normalized_metadata = dict(metadata or {})
    _validate_json_value(normalized_slots, path="variant.slots")
    _validate_json_value(normalized_policy, path="variant.policy")
    _validate_json_value(normalized_metadata, path="variant.metadata")
    return HarnessXVariantSpec(
        id=variant_id,
        parent_id=parent_id,
        summary=summary,
        processors=runtime.manifest(),
        slots=normalized_slots,
        policy=normalized_policy,
        metadata=normalized_metadata,
    )


def harnessx_variant_from_payload(payload: Mapping[str, Any]) -> HarnessXVariantSpec:
    """Strictly reconstruct a serialized typed variant without loading code."""

    expected_keys = {
        "id",
        "parent_id",
        "summary",
        "processors",
        "slots",
        "policy",
        "metadata",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected_keys:
        raise HarnessXContractError("variant payload keys are invalid")
    raw_processors = payload["processors"]
    if not isinstance(raw_processors, list):
        raise HarnessXContractError("variant processors payload must be a list")
    entries: list[ProcessorManifestEntry] = []
    processor_keys = {"name", "hook", "singleton_group", "order", "after", "config"}
    for raw_entry in raw_processors:
        if not isinstance(raw_entry, dict) or set(raw_entry) != processor_keys:
            raise HarnessXContractError("processor manifest payload keys are invalid")
        after = raw_entry["after"]
        config = raw_entry["config"]
        if not isinstance(after, list) or not isinstance(config, dict):
            raise HarnessXContractError("processor manifest payload has invalid containers")
        try:
            entry = ProcessorManifestEntry(
                name=raw_entry["name"],
                hook=HarnessXHook(raw_entry["hook"]),
                singleton_group=raw_entry["singleton_group"],
                order=ProcessorOrder(raw_entry["order"]),
                after=tuple(after),
                config=copy.deepcopy(config),
            )
        except (TypeError, ValueError) as exc:
            raise HarnessXContractError("processor manifest payload is not typed") from exc
        entries.append(entry)
    parent_id = payload["parent_id"]
    if parent_id is not None and not isinstance(parent_id, str):
        raise HarnessXContractError("variant parent_id must be a string or null")
    if not isinstance(payload["slots"], dict) or not isinstance(payload["policy"], dict) or not isinstance(
        payload["metadata"], dict
    ):
        raise HarnessXContractError("variant slots, policy, and metadata must be objects")
    spec = HarnessXVariantSpec(
        id=payload["id"],
        parent_id=parent_id,
        summary=payload["summary"],
        processors=tuple(entries),
        slots=copy.deepcopy(payload["slots"]),
        policy=copy.deepcopy(payload["policy"]),
        metadata=copy.deepcopy(payload["metadata"]),
    )
    _validate_variant(spec)
    if spec.canonical_payload() != dict(payload):
        raise HarnessXContractError("variant payload is not canonical")
    return spec


def build_harnessx_runtime_from_variant(
    spec: HarnessXVariantSpec,
    registry: HarnessXProcessorRegistry,
    *,
    max_split_fanout: int = 32,
    max_pipeline_events: int = 128,
    max_event_bytes: int = 1_048_576,
    processor_timeout_sec: float = 5.0,
) -> HarnessXRuntime:
    _validate_variant(spec)
    processors = [registry.build(entry) for entry in spec.processors]
    runtime = HarnessXRuntime(
        processors,
        max_split_fanout=max_split_fanout,
        max_pipeline_events=max_pipeline_events,
        max_event_bytes=max_event_bytes,
        processor_timeout_sec=processor_timeout_sec,
    )
    if runtime.manifest() != spec.processors:
        raise HarnessXContractError("variant processor order is not canonical")
    return runtime


def apply_harnessx_change_manifest(
    parent: HarnessXVariantSpec,
    manifest: HarnessXChangeManifest,
    registry: HarnessXProcessorRegistry,
    *,
    summary: str,
) -> HarnessXVariantSpec:
    _validate_change_manifest(parent, manifest)
    by_key = {(entry.hook, entry.singleton_group): entry for entry in parent.processors}
    for edit in manifest.edits:
        key = (edit.hook, edit.singleton_group)
        exists = key in by_key
        if edit.kind is HarnessXEditKind.INSERT:
            if exists or edit.processor is None:
                raise HarnessXContractError("insert requires a new processor entry")
            by_key[key] = edit.processor
        elif edit.kind is HarnessXEditKind.REPLACE:
            if not exists or edit.processor is None:
                raise HarnessXContractError("replace requires an existing processor entry")
            by_key[key] = edit.processor
        else:
            if not exists or edit.processor is not None:
                raise HarnessXContractError("remove requires an existing processor and no entry")
            del by_key[key]

    tentative = HarnessXVariantSpec(
        id=manifest.candidate_variant_id,
        parent_id=parent.id,
        summary=summary,
        processors=tuple(by_key.values()),
        slots=copy.deepcopy(parent.slots),
        policy=copy.deepcopy(parent.policy),
        metadata={
            **copy.deepcopy(parent.metadata),
            "change_manifest_id": manifest.id,
            "change_manifest_sha256": manifest.sha256,
            "rollback_variant_sha256": parent.sha256,
        },
    )
    processors = [registry.build(entry) for entry in tentative.processors]
    runtime = HarnessXRuntime(processors)
    return snapshot_harnessx_variant(
        runtime,
        variant_id=tentative.id,
        parent_id=parent.id,
        summary=summary,
        slots=tentative.slots,
        policy=tentative.policy,
        metadata=tentative.metadata,
    )


def gate_harnessx_candidate(
    *,
    parent: HarnessXVariantSpec,
    candidate: HarnessXVariantSpec,
    manifest: HarnessXChangeManifest,
    smoke_passed: bool,
    previously_passing_task_ids: Iterable[str],
    candidate_task_outcomes: Mapping[str, bool],
    approval_policy: HarnessXApprovalPolicy | None = None,
    approval_granted: bool = False,
    recent_shipped_dimensions: Sequence[str] = (),
) -> HarnessXGateDecision:
    """Apply deterministic shipping checks and selective human approval.

    The seesaw check is deliberately strict: every previously solved task must
    be present and remain passing.  Repeated edits in the same taxonomy
    dimension escalate to approval to limit cumulative sub-threshold drift.
    """

    policy = approval_policy or HarnessXApprovalPolicy()
    if not isinstance(smoke_passed, bool) or not isinstance(approval_granted, bool):
        raise HarnessXContractError("smoke_passed and approval_granted must be boolean")
    baseline_input = tuple(previously_passing_task_ids)
    if any(not isinstance(task_id, str) or not task_id.strip() for task_id in baseline_input):
        raise HarnessXContractError("previously passing task ids must be non-empty strings")
    if any(
        not isinstance(task_id, str)
        or not task_id.strip()
        or not isinstance(outcome, bool)
        for task_id, outcome in candidate_task_outcomes.items()
    ):
        raise HarnessXContractError("candidate task outcomes must map task ids to booleans")
    if any(
        not isinstance(dimension, str) or dimension not in {f"D{index}" for index in range(1, 10)}
        for dimension in recent_shipped_dimensions
    ):
        raise HarnessXContractError("recent shipped dimensions must be D1..D9")
    checks: list[HarnessXGateCheck] = []

    manifest_ok = True
    manifest_detail = "complete"
    try:
        _validate_change_manifest(parent, manifest)
    except HarnessXContractError as exc:
        manifest_ok = False
        manifest_detail = str(exc)
    checks.append(HarnessXGateCheck("manifest_complete", manifest_ok, manifest_detail))

    candidate_ok = True
    candidate_detail = "valid"
    try:
        _validate_variant(candidate)
    except HarnessXContractError as exc:
        candidate_ok = False
        candidate_detail = str(exc)
    checks.append(HarnessXGateCheck("candidate_structure", candidate_ok, candidate_detail))

    lineage_ok = candidate.parent_id == parent.id and manifest.candidate_variant_id == candidate.id
    checks.append(
        HarnessXGateCheck(
            "variant_lineage",
            lineage_ok,
            "candidate points to parent" if lineage_ok else "candidate lineage mismatch",
        )
    )
    binding_ok = (
        candidate.metadata.get("change_manifest_sha256") == manifest.sha256
        and candidate.metadata.get("rollback_variant_sha256") == parent.sha256
    )
    checks.append(
        HarnessXGateCheck(
            "candidate_manifest_binding",
            binding_ok,
            "candidate binds exact manifest and rollback target"
            if binding_ok
            else "candidate manifest or rollback binding mismatch",
        )
    )
    checks.append(
        HarnessXGateCheck(
            "smoke_test",
            bool(smoke_passed),
            "passed" if smoke_passed else "failed",
        )
    )

    baseline = tuple(sorted(set(baseline_input)))
    missing = tuple(task_id for task_id in baseline if task_id not in candidate_task_outcomes)
    regressed = tuple(task_id for task_id in baseline if candidate_task_outcomes.get(task_id) is False)
    seesaw_ok = not missing and not regressed
    seesaw_detail = "all previously passing tasks remain passing"
    if missing:
        seesaw_detail = f"missing outcomes: {', '.join(missing)}"
    elif regressed:
        seesaw_detail = f"regressed tasks: {', '.join(regressed)}"
    checks.append(HarnessXGateCheck("seesaw_regression", seesaw_ok, seesaw_detail))

    current_dimensions = tuple(edit.dimension for edit in manifest.edits)
    cumulative_risk = any(
        _trailing_count(recent_shipped_dimensions, dimension)
        >= policy.max_consecutive_same_dimension_edits
        for dimension in set(current_dimensions)
    )
    approval_required = (
        manifest.risk_tier >= policy.require_at_or_above
        or cumulative_risk
        or bool(manifest.expected_regress_task_ids)
    )
    approval_ok = not approval_required or approval_granted
    approval_detail = "not required"
    if approval_required:
        approval_detail = "granted" if approval_granted else "required"
        if cumulative_risk:
            approval_detail += "; repeated-dimension drift guard"
        if manifest.expected_regress_task_ids:
            approval_detail += "; declared-regression guard"
    checks.append(HarnessXGateCheck("selective_human_approval", approval_ok, approval_detail))

    deterministic_ok = all(check.passed for check in checks[:-1])
    if not deterministic_ok:
        resolution = "candidate_rejected_rollback_parent"
        accepted = False
    elif not approval_ok:
        resolution = "approval_required_parent_retained"
        accepted = False
    else:
        resolution = "candidate_harness_promoted"
        accepted = True
    return HarnessXGateDecision(
        accepted=accepted,
        requires_approval=approval_required and not approval_granted,
        resolution=resolution,
        resolved_variant_id=candidate.id if accepted else parent.id,
        rollback_variant_id=parent.id,
        checks=tuple(checks),
    )


def _validate_event_type(event: HarnessXEvent) -> None:
    expected = EVENT_TYPES.get(event.hook)
    if expected is None or type(event) is not expected:
        raise HarnessXContractError("event type does not match hook")
    _require_non_empty_string(event.event_id, label="event_id")
    _require_non_empty_string(event.task_id, label="task_id")
    if not isinstance(event.metadata, dict):
        raise HarnessXContractError("event metadata must be an object")
    if isinstance(event, TaskStartEvent):
        if not isinstance(event.system_prompt, str):
            raise HarnessXContractError("system_prompt must be a string")
    elif isinstance(event, StepStartEvent):
        _validate_step_index(event.step_index)
        if not isinstance(event.history, tuple) or any(
            not isinstance(item, str) for item in event.history
        ):
            raise HarnessXContractError("history must be a tuple of strings")
    elif isinstance(event, BeforeModelEvent):
        _validate_step_index(event.step_index)
        _require_non_empty_string(event.model_role, label="model_role")
        if not isinstance(event.last_user_content, str):
            raise HarnessXContractError("last_user_content must be a string")
        if event.appended_user_message is not None and not isinstance(
            event.appended_user_message, str
        ):
            raise HarnessXContractError("appended_user_message must be a string or null")
    elif isinstance(event, ModelResponseEvent):
        _validate_step_index(event.step_index)
        if not isinstance(event.response_content, str):
            raise HarnessXContractError("response_content must be a string")
        if not isinstance(event.tool_calls, tuple) or any(
            not isinstance(call, ToolCall) for call in event.tool_calls
        ):
            raise HarnessXContractError("tool_calls must be a tuple of ToolCall values")
    elif isinstance(event, ToolCallEvent):
        _validate_step_index(event.step_index)
        if not isinstance(event.approval_required, bool):
            raise HarnessXContractError("approval_required must be boolean")
    elif isinstance(event, ToolResultEvent):
        _validate_step_index(event.step_index)
        _require_non_empty_string(event.tool_name, label="tool_name")
        if not isinstance(event.tool_result, str):
            raise HarnessXContractError("tool_result must be a string")
    elif isinstance(event, StepEndEvent):
        _validate_step_index(event.step_index)
        _require_non_empty_string(event.status, label="step status")
    elif isinstance(event, TaskEndEvent):
        _require_non_empty_string(event.status, label="task status")
    _validate_json_value(event.metadata, path="event.metadata")


def _validate_processor(processor: HarnessXProcessor) -> None:
    _require_non_empty_string(processor.name, label="processor name")
    _require_non_empty_string(processor.singleton_group, label="processor singleton_group")
    if not isinstance(processor.hook, HarnessXHook):
        raise HarnessXContractError("processor hook must be HarnessXHook")
    ProcessorOrder(processor.order)
    if not isinstance(processor.after, tuple) or any(
        not isinstance(item, str) or not item.strip() for item in processor.after
    ):
        raise HarnessXContractError("processor after must be a tuple of non-empty strings")
    if len(set(processor.after)) != len(processor.after):
        raise HarnessXContractError(f"duplicate processor dependency: {processor.name}")
    if processor.singleton_group in processor.after:
        raise HarnessXContractError(f"processor cannot depend on itself: {processor.name}")
    processor_manifest_entry(processor)


def _order_processors(
    items: Sequence[tuple[int, HarnessXProcessor]],
) -> list[HarnessXProcessor]:
    by_group: dict[str, tuple[int, HarnessXProcessor]] = {}
    by_name: set[str] = set()
    for index, processor in items:
        if processor.singleton_group in by_group:
            raise HarnessXContractError(
                f"singleton processor conflict at {processor.hook.value}: {processor.singleton_group}"
            )
        if processor.name in by_name:
            raise HarnessXContractError(f"duplicate processor name at hook: {processor.name}")
        by_group[processor.singleton_group] = (index, processor)
        by_name.add(processor.name)

    outgoing: dict[str, set[str]] = {group: set() for group in by_group}
    indegree: dict[str, int] = {group: 0 for group in by_group}
    for group, (_, processor) in by_group.items():
        for dependency in processor.after:
            if dependency not in by_group:
                continue
            outgoing[dependency].add(group)
            indegree[group] += 1

    def sort_key(group: str) -> tuple[int, int, str]:
        index, processor = by_group[group]
        return int(processor.order), index, group

    ready = sorted((group for group, degree in indegree.items() if degree == 0), key=sort_key)
    ordered: list[HarnessXProcessor] = []
    while ready:
        group = ready.pop(0)
        ordered.append(by_group[group][1])
        for target in sorted(outgoing[group]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=sort_key)
    if len(ordered) != len(items):
        raise HarnessXContractError("processor dependency cycle detected")
    return ordered


def _validate_processor_output(
    before: HarnessXEvent,
    output: HarnessXEvent,
    processor: HarnessXProcessor,
) -> None:
    if type(output) is not type(before):
        raise HarnessXContractError(
            f"processor {processor.name} changed event type at {processor.hook.value}"
        )
    _validate_event_type(output)
    allowed = PERMITTED_MUTATIONS[processor.hook]
    before_values = {item.name: copy.deepcopy(getattr(before, item.name)) for item in fields(before)}
    output_values = {item.name: copy.deepcopy(getattr(output, item.name)) for item in fields(output)}
    changed = {name for name in before_values if before_values[name] != output_values[name]}
    forbidden = sorted(changed - allowed - {"event_id"})
    if forbidden:
        raise HarnessXContractError(
            f"processor {processor.name} modified forbidden fields: {', '.join(forbidden)}"
        )
    _validate_json_value(output.metadata, path="event.metadata")


def _processor_outcome(
    before: HarnessXEvent,
    outputs: Sequence[HarnessXEvent],
) -> ProcessorOutcome:
    if not outputs:
        return ProcessorOutcome.INTERCEPT
    if len(outputs) > 1:
        ids = [event.event_id for event in outputs]
        if len(set(ids)) != len(ids):
            raise HarnessXContractError("split outputs require unique event_id values")
        return ProcessorOutcome.SPLIT
    if outputs[0].event_id != before.event_id:
        raise HarnessXContractError("single processor output cannot change event_id")
    return ProcessorOutcome.PASS_THROUGH if outputs[0] == before else ProcessorOutcome.TRANSFORM


def _validate_variant(spec: HarnessXVariantSpec) -> None:
    _require_non_empty_string(spec.id, label="variant id")
    _require_non_empty_string(spec.summary, label="variant summary")
    _validate_json_value(spec.slots, path="variant.slots")
    _validate_json_value(spec.policy, path="variant.policy")
    _validate_json_value(spec.metadata, path="variant.metadata")
    for entry in spec.processors:
        _validate_processor_manifest_entry(entry)
    groups = [entry.singleton_group for entry in spec.processors]
    names = [entry.name for entry in spec.processors]
    if len(set(groups)) != len(groups):
        raise HarnessXContractError("variant contains singleton processor conflicts")
    if len(set(names)) != len(names):
        raise HarnessXContractError("variant contains duplicate processor names")


def _validate_change_manifest(
    parent: HarnessXVariantSpec,
    manifest: HarnessXChangeManifest,
) -> None:
    _validate_variant(parent)
    _require_non_empty_string(manifest.id, label="manifest id")
    _require_non_empty_string(manifest.candidate_variant_id, label="candidate variant id")
    if manifest.parent_variant_sha256 != parent.sha256:
        raise HarnessXContractError("manifest parent hash mismatch")
    if manifest.rollback_variant_sha256 != parent.sha256:
        raise HarnessXContractError("rollback target must be the exact parent variant")
    if not isinstance(manifest.risk_tier, HarnessRiskTier):
        raise HarnessXContractError("manifest risk_tier must be HarnessRiskTier")
    if not isinstance(manifest.rationale, str) or not manifest.rationale.strip() or not manifest.evidence_trace_ids:
        raise HarnessXContractError("manifest rationale and evidence traces are required")
    if not isinstance(manifest.evidence_trace_ids, tuple) or any(
        not isinstance(trace_id, str) or not trace_id.strip()
        for trace_id in manifest.evidence_trace_ids
    ):
        raise HarnessXContractError("manifest evidence trace ids must be non-empty")
    if len(set(manifest.evidence_trace_ids)) != len(manifest.evidence_trace_ids):
        raise HarnessXContractError("manifest evidence trace ids must be unique")
    if not isinstance(manifest.expected_improve_task_ids, tuple) or not isinstance(
        manifest.expected_regress_task_ids, tuple
    ):
        raise HarnessXContractError("manifest expected task claims must be tuples")
    task_claims = manifest.expected_improve_task_ids + manifest.expected_regress_task_ids
    if not task_claims or any(
        not isinstance(task_id, str) or not task_id.strip() for task_id in task_claims
    ):
        raise HarnessXContractError("manifest expected task claims are required")
    if len(set(task_claims)) != len(task_claims):
        raise HarnessXContractError("manifest expected task claims must be unique")
    if not isinstance(manifest.edits, tuple) or not manifest.edits:
        raise HarnessXContractError("manifest edits are required")
    if len(_canonical_json(manifest.canonical_payload()).encode("utf-8")) > 131_072:
        raise HarnessXContractError("manifest exceeds 131072 bytes")
    edit_keys: set[tuple[HarnessXHook, str]] = set()
    for edit in manifest.edits:
        if not isinstance(edit.kind, HarnessXEditKind) or not isinstance(edit.hook, HarnessXHook):
            raise HarnessXContractError("edit kind and hook must be typed enums")
        _require_non_empty_string(edit.singleton_group, label="edit singleton_group")
        if edit.dimension not in {f"D{index}" for index in range(1, 10)}:
            raise HarnessXContractError("edit dimension must be one of D1..D9")
        key = (edit.hook, edit.singleton_group)
        if key in edit_keys:
            raise HarnessXContractError("manifest edits the same processor slot more than once")
        edit_keys.add(key)
        if edit.kind in {HarnessXEditKind.INSERT, HarnessXEditKind.REPLACE}:
            if edit.processor is None:
                raise HarnessXContractError("insert/replace edit requires processor entry")
            if (
                edit.processor.hook is not edit.hook
                or edit.processor.singleton_group != edit.singleton_group
            ):
                raise HarnessXContractError("edit processor does not match target slot")
        elif edit.processor is not None:
            raise HarnessXContractError("remove edit cannot contain processor entry")


def _validate_json_value(
    value: Any,
    *,
    path: str,
    _depth: int = 0,
    _active_containers: set[int] | None = None,
    _node_count: list[int] | None = None,
) -> None:
    if _depth > _MAX_JSON_DEPTH:
        raise HarnessXContractError(f"{path} exceeds maximum JSON depth")
    if _active_containers is None:
        _active_containers = set()
    if _node_count is None:
        _node_count = [0]
    _node_count[0] += 1
    if _node_count[0] > _MAX_JSON_NODES:
        raise HarnessXContractError(f"{path} exceeds maximum JSON node count")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HarnessXContractError(f"{path} contains non-finite float")
        return
    if isinstance(value, (list, tuple, dict)):
        container_id = id(value)
        if container_id in _active_containers:
            raise HarnessXContractError(f"{path} contains a cyclic container")
        _active_containers.add(container_id)
        try:
            if isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    _validate_json_value(
                        item,
                        path=f"{path}[{index}]",
                        _depth=_depth + 1,
                        _active_containers=_active_containers,
                        _node_count=_node_count,
                    )
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise HarnessXContractError(f"{path} contains non-string key")
                    _validate_json_value(
                        item,
                        path=f"{path}.{key}",
                        _depth=_depth + 1,
                        _active_containers=_active_containers,
                        _node_count=_node_count,
                    )
        finally:
            _active_containers.remove(container_id)
        return
    raise HarnessXContractError(f"{path} contains unsupported value: {type(value).__name__}")


def _validate_processor_manifest_entry(entry: ProcessorManifestEntry) -> None:
    if not isinstance(entry, ProcessorManifestEntry):
        raise HarnessXContractError("variant processors must be ProcessorManifestEntry values")
    _require_non_empty_string(entry.name, label="processor manifest name")
    _require_non_empty_string(entry.singleton_group, label="processor manifest singleton_group")
    if not isinstance(entry.hook, HarnessXHook) or not isinstance(entry.order, ProcessorOrder):
        raise HarnessXContractError("processor manifest hook and order must be typed enums")
    if not isinstance(entry.after, tuple) or any(
        not isinstance(item, str) or not item.strip() for item in entry.after
    ):
        raise HarnessXContractError("processor manifest after must be a tuple of strings")
    if len(set(entry.after)) != len(entry.after):
        raise HarnessXContractError("processor manifest dependencies must be unique")
    if not isinstance(entry.config, dict):
        raise HarnessXContractError("processor manifest config must be an object")
    _validate_json_value(entry.config, path="processor.config")
    if len(_canonical_json(entry.config).encode("utf-8")) > 16_384:
        raise HarnessXContractError("processor config exceeds 16384 bytes")


def _canonical_json(value: Any) -> str:
    _validate_json_value(value, path="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_event_size(event: HarnessXEvent, max_event_bytes: int) -> None:
    payload = asdict(event)
    if len(_canonical_json(payload).encode("utf-8")) > max_event_bytes:
        raise HarnessXContractError("event exceeds configured byte bound")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _trailing_count(values: Sequence[str], target: str) -> int:
    count = 0
    for value in reversed(values):
        if value != target:
            break
        count += 1
    return count


def _normalize_string_set(values: Sequence[str], *, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise HarnessXContractError(f"{label} must be a sequence of strings")
    if any(not isinstance(value, str) for value in values):
        raise HarnessXContractError(f"{label} must contain strings only")
    normalized = tuple(sorted({str(value).strip() for value in values if str(value).strip()}))
    if len(normalized) != len(values):
        raise HarnessXContractError(f"{label} must contain unique non-empty strings")
    return normalized


def _require_non_empty_string(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise HarnessXContractError(f"{label} is required")


def _validate_step_index(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HarnessXContractError("step_index must be a non-negative integer")


__all__ = [
    "BeforeModelEvent",
    "BeforeModelContentLimitProcessor",
    "ExactToolCallPolicyProcessor",
    "ExactToolInputPolicyProcessor",
    "HarnessRiskTier",
    "HarnessXApprovalPolicy",
    "HarnessXChangeManifest",
    "HarnessXContractError",
    "HarnessXEditKind",
    "HarnessXEmission",
    "HarnessXEvent",
    "HarnessXExecutionError",
    "HarnessXGateCheck",
    "HarnessXGateDecision",
    "HarnessXHook",
    "HarnessXInterrupt",
    "HarnessXProcessor",
    "HarnessXProcessorEdit",
    "HarnessXProcessorRegistry",
    "HarnessXRuntime",
    "HarnessXVariantSpec",
    "ModelResponseEvent",
    "PERMITTED_MUTATIONS",
    "ProcessorAuditRecord",
    "ProcessorManifestEntry",
    "ProcessorOrder",
    "ProcessorOutcome",
    "StepEndEvent",
    "StepEndAuditProcessor",
    "StepStartEvent",
    "HistoryWindowProcessor",
    "SelectiveToolApprovalProcessor",
    "SystemPromptAppendProcessor",
    "TaskEndEvent",
    "TaskEndAuditProcessor",
    "TaskStartEvent",
    "ToolCall",
    "ToolCallAllowlistProcessor",
    "ToolCallEvent",
    "ToolResultEvent",
    "ToolResultLimitProcessor",
    "apply_harnessx_change_manifest",
    "build_harnessx_runtime_from_variant",
    "gate_harnessx_candidate",
    "make_default_harnessx_registry",
    "make_default_harnessx_runtime",
    "processor_manifest_entry",
    "snapshot_harnessx_variant",
]
