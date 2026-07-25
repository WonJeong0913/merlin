"""Shadow-only typed HarnessX instrumentation for provider-backed chat turns.

The adapter dispatches only lifecycle events that Merlin can observe at the
chat-session boundary or strictly reconstruct from retained Codex JSONL.
Candidate processor outputs are audited but never applied to the live provider
prompt, tool execution, or user-visible answer. Reconstructed tool hooks are a
post-execution shadow replay, never a claim of pre-execution enforcement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .codex_tool_observation import (
    CodexCommandObservation,
    CodexToolObservationError,
    command_observation_sha256,
    parse_codex_command_observations,
)
from .harnessx_runtime import (
    BeforeModelEvent,
    HarnessXEmission,
    HarnessXEvent,
    HarnessXHook,
    HarnessXRuntime,
    ModelResponseEvent,
    StepEndEvent,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class HarnessXChatShadowError(RuntimeError):
    """Raised when a chat shadow trace cannot be dispatched or persisted."""


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _event_payload(event: HarnessXEvent) -> dict[str, Any]:
    return {
        "hook": event.hook.value,
        "type": type(event).__name__,
        "event": _json_ready(asdict(event)),
    }


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise HarnessXChatShadowError(
            f"refusing to overwrite HarnessX shadow artifact: {path.name}"
        ) from exc


@dataclass(slots=True)
class HarnessXChatShadowContext:
    turn_number: int
    task_id: str
    prompt_sha256: str
    prompt_chars: int
    records: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HarnessXChatShadowReference:
    pointer: str
    sha256: str
    report_sha256: str
    observed_hooks: tuple[str, ...]
    unobserved_hooks: tuple[str, ...]
    processor_audit_count: int
    shadow_change_count: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pointer": self.pointer,
            "sha256": self.sha256,
            "report_sha256": self.report_sha256,
            "observed_hooks": list(self.observed_hooks),
            "unobserved_hooks": list(self.unobserved_hooks),
            "processor_audit_count": self.processor_audit_count,
            "shadow_change_count": self.shadow_change_count,
            "status": self.status,
            "mode": "shadow_only",
        }


class HarnessXChatShadow:
    """Dispatch observable chat hooks and persist a content-redacted envelope."""

    def __init__(self, *, runtime: HarnessXRuntime, trace_root: str | Path) -> None:
        root = Path(trace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("HarnessX shadow trace_root must be an existing directory")
        self.runtime = runtime
        self.trace_root = root

    def _load_command_observations(
        self, *, pointer: str, expected_sha256: str
    ) -> tuple[CodexCommandObservation, ...]:
        if not isinstance(pointer, str) or Path(pointer).name != pointer:
            raise HarnessXChatShadowError("provider raw trace pointer is unsafe")
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise HarnessXChatShadowError("provider raw trace SHA-256 is invalid")
        candidate = self.trace_root / pointer
        if candidate.is_symlink():
            raise HarnessXChatShadowError("provider raw trace must not be a symlink")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise HarnessXChatShadowError("provider raw trace is unavailable") from exc
        if not resolved.is_file() or not resolved.is_relative_to(self.trace_root):
            raise HarnessXChatShadowError(
                "provider raw trace is outside the HarnessX trace root"
            )
        raw_bytes = resolved.read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != expected_sha256:
            raise HarnessXChatShadowError("provider raw trace SHA-256 mismatch")
        try:
            return parse_codex_command_observations(raw_bytes)
        except CodexToolObservationError as exc:
            raise HarnessXChatShadowError(
                f"provider command lifecycle evidence is invalid: {exc}"
            ) from exc

    def _dispatch(
        self,
        context: HarnessXChatShadowContext,
        event: HarnessXEvent,
    ) -> HarnessXEmission:
        emission = self.runtime.emit_sync(event)
        input_payload = _event_payload(event)
        output_payloads = [_event_payload(item) for item in emission.events]
        input_sha256 = _sha256(input_payload)
        output_sha256s = [_sha256(item) for item in output_payloads]
        unchanged = len(output_sha256s) == 1 and output_sha256s[0] == input_sha256
        context.records.append(
            {
                "sequence": len(context.records) + 1,
                "hook": event.hook.value,
                "event_id": event.event_id,
                "input_sha256": input_sha256,
                "output_sha256s": output_sha256s,
                "output_count": len(output_sha256s),
                "shadow_change_detected": not unchanged,
                "intercepted_in_shadow": emission.intercepted,
                "processor_audit": [
                    {
                        "event_id": item.event_id,
                        "hook": item.hook.value,
                        "processor": item.processor,
                        "singleton_group": item.singleton_group,
                        "outcome": item.outcome.value,
                        "output_event_ids": list(item.output_event_ids),
                    }
                    for item in emission.audit
                ],
            }
        )
        return emission

    def start(
        self,
        *,
        turn_number: int,
        prompt: str,
        resumed: bool,
    ) -> HarnessXChatShadowContext:
        if isinstance(turn_number, bool) or not isinstance(turn_number, int) or turn_number < 1:
            raise HarnessXChatShadowError("turn_number must be a positive integer")
        if not isinstance(prompt, str) or not prompt:
            raise HarnessXChatShadowError("prompt must be a non-empty string")
        task_id = f"chat-turn-{turn_number:04d}"
        context = HarnessXChatShadowContext(
            turn_number=turn_number,
            task_id=task_id,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            prompt_chars=len(prompt),
        )
        prefix = f"hx-chat-{turn_number:04d}"
        self._dispatch(
            context,
            TaskStartEvent(
                event_id=f"{prefix}:task-start",
                task_id=task_id,
                system_prompt="Merlin governed chat session",
                metadata={"turn_number": turn_number, "shadow_only": True},
            ),
        )
        self._dispatch(
            context,
            StepStartEvent(
                event_id=f"{prefix}:step-start",
                task_id=task_id,
                step_index=1,
                history=(),
                metadata={"provider_thread_resumed": resumed, "shadow_only": True},
            ),
        )
        self._dispatch(
            context,
            BeforeModelEvent(
                event_id=f"{prefix}:before-model",
                task_id=task_id,
                step_index=1,
                model_role="main",
                last_user_content=prompt,
                metadata={"shadow_only": True},
            ),
        )
        return context

    def finish(
        self,
        context: HarnessXChatShadowContext,
        *,
        answer: str,
        provider_turn_id: str | None,
        raw_trace_pointer: str,
        raw_trace_sha256: str,
    ) -> HarnessXChatShadowReference:
        if not isinstance(answer, str):
            raise HarnessXChatShadowError("answer must be a string")
        prefix = f"hx-chat-{context.turn_number:04d}"
        command_observations = self._load_command_observations(
            pointer=raw_trace_pointer,
            expected_sha256=raw_trace_sha256,
        )
        for observation in command_observations:
            ordinal = observation.ordinal
            self._dispatch(
                context,
                ToolCallEvent(
                    event_id=f"{prefix}:before-tool:{ordinal:03d}",
                    task_id=context.task_id,
                    step_index=1,
                    tool_name="command_execution",
                    tool_input_json=json.dumps(
                        {
                            "command_sha256": observation.command_sha256,
                            "command_chars": observation.command_chars,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    approval_required=False,
                    metadata={
                        "provider_item_id_sha256": observation.item_id_sha256,
                        "provider_started_event_index": observation.started_event_index,
                        "replayed_after_provider_execution": True,
                        "raw_command_stored": False,
                        "shadow_only": True,
                    },
                ),
            )
            self._dispatch(
                context,
                ToolResultEvent(
                    event_id=f"{prefix}:after-tool:{ordinal:03d}",
                    task_id=context.task_id,
                    step_index=1,
                    tool_name="command_execution",
                    tool_result=json.dumps(
                        {
                            "status": observation.status,
                            "exit_code": observation.exit_code,
                            "output_sha256": observation.output_sha256,
                            "output_chars": observation.output_chars,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    metadata={
                        "provider_item_id_sha256": observation.item_id_sha256,
                        "provider_completed_event_index": (
                            observation.completed_event_index
                        ),
                        "replayed_after_provider_execution": True,
                        "raw_tool_output_stored": False,
                        "shadow_only": True,
                    },
                ),
            )
        self._dispatch(
            context,
            ModelResponseEvent(
                event_id=f"{prefix}:after-model",
                task_id=context.task_id,
                step_index=1,
                response_content=answer,
                tool_calls=(),
                metadata={
                    "provider_turn_id_present": provider_turn_id is not None,
                    "provider_tool_events_observed": bool(command_observations),
                    "provider_command_observation_count": len(command_observations),
                    "shadow_only": True,
                },
            ),
        )
        self._dispatch(
            context,
            StepEndEvent(
                event_id=f"{prefix}:step-end",
                task_id=context.task_id,
                step_index=1,
                status="completed",
                metadata={"shadow_only": True},
            ),
        )
        self._dispatch(
            context,
            TaskEndEvent(
                event_id=f"{prefix}:task-end",
                task_id=context.task_id,
                status="completed",
                metadata={"shadow_only": True},
            ),
        )
        return self._persist(
            context,
            status="completed",
            answer_sha256=hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            answer_chars=len(answer),
            raw_trace_pointer=raw_trace_pointer,
            raw_trace_sha256=raw_trace_sha256,
            failure_class=None,
            command_observations=command_observations,
        )

    def fail(
        self,
        context: HarnessXChatShadowContext,
        *,
        failure: BaseException,
    ) -> HarnessXChatShadowReference:
        prefix = f"hx-chat-{context.turn_number:04d}"
        failure_class = type(failure).__name__
        self._dispatch(
            context,
            StepEndEvent(
                event_id=f"{prefix}:step-end",
                task_id=context.task_id,
                step_index=1,
                status="provider_error",
                metadata={"failure_class": failure_class, "shadow_only": True},
            ),
        )
        self._dispatch(
            context,
            TaskEndEvent(
                event_id=f"{prefix}:task-end",
                task_id=context.task_id,
                status="provider_error",
                metadata={"failure_class": failure_class, "shadow_only": True},
            ),
        )
        return self._persist(
            context,
            status="provider_error",
            answer_sha256=None,
            answer_chars=None,
            raw_trace_pointer=None,
            raw_trace_sha256=None,
            failure_class=failure_class,
            command_observations=(),
        )

    def _persist(
        self,
        context: HarnessXChatShadowContext,
        *,
        status: str,
        answer_sha256: str | None,
        answer_chars: int | None,
        raw_trace_pointer: str | None,
        raw_trace_sha256: str | None,
        failure_class: str | None,
        command_observations: tuple[CodexCommandObservation, ...],
    ) -> HarnessXChatShadowReference:
        processor_audit_count = sum(
            len(record["processor_audit"]) for record in context.records
        )
        shadow_change_count = sum(
            bool(record["shadow_change_detected"]) for record in context.records
        )
        observed_hooks = tuple(record["hook"] for record in context.records)
        unobserved_hooks = (
            ()
            if command_observations
            else (
                HarnessXHook.BEFORE_TOOL.value,
                HarnessXHook.AFTER_TOOL.value,
            )
        )
        report: dict[str, Any] = {
            "schema_version": "merlin-harnessx-chat-shadow-v2",
            "mode": "shadow_only",
            "status": status,
            "turn_number": context.turn_number,
            "task_id": context.task_id,
            "bindings": {
                "prompt_sha256": context.prompt_sha256,
                "prompt_chars": context.prompt_chars,
                "answer_sha256": answer_sha256,
                "answer_chars": answer_chars,
                "provider_raw_trace_pointer": raw_trace_pointer,
                "provider_raw_trace_sha256": raw_trace_sha256,
            },
            "hook_sequence": list(observed_hooks),
            "observed_hook_count": len(observed_hooks),
            "unobserved_hooks": list(unobserved_hooks),
            "processor_audit_count": processor_audit_count,
            "shadow_change_count": shadow_change_count,
            "records": context.records,
            "failure_class": failure_class,
            "tool_observation": {
                "schema_version": "codex-command-execution-observation-v1",
                "source": "codex_exec_jsonl",
                "command_count": len(command_observations),
                "observation_sha256": command_observation_sha256(
                    command_observations
                ),
                "observations": [
                    observation.to_safe_dict()
                    for observation in command_observations
                ],
                "coverage": (
                    "paired_command_execution_events"
                    if command_observations
                    else "no_supported_command_events"
                ),
                "raw_command_text_stored": False,
                "raw_tool_output_stored": False,
                "replayed_after_provider_execution": bool(command_observations),
                "pre_execution_control_available": False,
            },
            "claim_boundary": {
                "candidate_processor_outputs_applied_to_provider": False,
                "candidate_processor_outputs_applied_to_user_answer": False,
                "provider_tool_events_observed": bool(command_observations),
                "tool_hooks_synthesized": False,
                "tool_hooks_replayed_after_provider_execution": bool(
                    command_observations
                ),
                "tool_policy_enforced_before_execution": False,
                "provider_native_skill_invocation_claimed": False,
                "harness_candidate_promoted": False,
            },
        }
        report["report_sha256"] = _sha256(report)
        path = self.trace_root / f"harnessx-turn-{context.turn_number:04d}.shadow.json"
        _write_new_json(path, report)
        file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        return HarnessXChatShadowReference(
            pointer=path.name,
            sha256=file_sha256,
            report_sha256=report["report_sha256"],
            observed_hooks=observed_hooks,
            unobserved_hooks=unobserved_hooks,
            processor_audit_count=processor_audit_count,
            shadow_change_count=shadow_change_count,
            status=status,
        )


__all__ = [
    "HarnessXChatShadow",
    "HarnessXChatShadowContext",
    "HarnessXChatShadowError",
    "HarnessXChatShadowReference",
]
