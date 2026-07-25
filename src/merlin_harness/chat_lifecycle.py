"""Read-only lifecycle observations for Merlin's chat runtime.

The chat beta can prove that a bounded skill context was *exposed* to a
provider-backed turn.  It cannot currently prove provider-native skill-body
loading or invocation.  This module preserves that boundary while making the
immutable turn/feedback evidence available for an observe-only review.

It intentionally does not stage, promote, roll back, or persist lifecycle
changes.  Those actions require a frozen task and verifier re-run contract in
addition to stronger invocation evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .codex_tool_observation import (
    CodexToolObservationError,
    command_observation_sha256,
    parse_codex_command_observations,
)
from .skill_name_governance import POLICY_VERSION as NAME_COLLISION_POLICY_VERSION


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_SAFE_BASENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")

_TURN_META_KEYS = frozenset(
    {
        "schema_version",
        "turn_number",
        "provider_thread_id",
        "provider_turn_id",
        "resumed",
        "user_input_sha256",
        "user_input_chars",
        "user_input_stored",
        "assistant_answer_sha256",
        "assistant_answer_chars",
        "assistant_answer_stored",
        "provisioned_skills",
        "deterministic_reference_decision",
        "routing_decision",
        "prompt_provisioning_is_provider_native_invocation",
        "actual_invocation_evidence_complete",
        "raw_trace",
        "backend_metadata",
        "feedback_status",
        "lifecycle_automatic_change",
    }
)
_RAW_TRACE_KEYS = frozenset({"pointer", "sha256"})
_HARNESSX_REFERENCE_KEYS = frozenset(
    {
        "pointer",
        "sha256",
        "report_sha256",
        "observed_hooks",
        "unobserved_hooks",
        "processor_audit_count",
        "shadow_change_count",
        "status",
        "mode",
    }
)
_HARNESSX_REPORT_KEYS_V1 = frozenset(
    {
        "schema_version",
        "mode",
        "status",
        "turn_number",
        "task_id",
        "bindings",
        "hook_sequence",
        "observed_hook_count",
        "unobserved_hooks",
        "processor_audit_count",
        "shadow_change_count",
        "records",
        "failure_class",
        "claim_boundary",
        "report_sha256",
    }
)
_HARNESSX_REPORT_KEYS_V2 = _HARNESSX_REPORT_KEYS_V1 | frozenset(
    {"tool_observation"}
)
_HARNESSX_BINDING_KEYS = frozenset(
    {
        "prompt_sha256",
        "prompt_chars",
        "answer_sha256",
        "answer_chars",
        "provider_raw_trace_pointer",
        "provider_raw_trace_sha256",
    }
)
_HARNESSX_RECORD_KEYS = frozenset(
    {
        "sequence",
        "hook",
        "event_id",
        "input_sha256",
        "output_sha256s",
        "output_count",
        "shadow_change_detected",
        "intercepted_in_shadow",
        "processor_audit",
    }
)
_HARNESSX_AUDIT_KEYS = frozenset(
    {
        "event_id",
        "hook",
        "processor",
        "singleton_group",
        "outcome",
        "output_event_ids",
    }
)
_HARNESSX_CLAIM_KEYS_V1 = frozenset(
    {
        "candidate_processor_outputs_applied_to_provider",
        "candidate_processor_outputs_applied_to_user_answer",
        "provider_tool_events_observed",
        "tool_hooks_synthesized",
        "provider_native_skill_invocation_claimed",
        "harness_candidate_promoted",
    }
)
_HARNESSX_CLAIM_KEYS_V2 = _HARNESSX_CLAIM_KEYS_V1 | frozenset(
    {
        "tool_hooks_replayed_after_provider_execution",
        "tool_policy_enforced_before_execution",
    }
)
_HARNESSX_TOOL_OBSERVATION_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "command_count",
        "observation_sha256",
        "observations",
        "coverage",
        "raw_command_text_stored",
        "raw_tool_output_stored",
        "replayed_after_provider_execution",
        "pre_execution_control_available",
    }
)
_HARNESSX_COMMAND_OBSERVATION_KEYS = frozenset(
    {
        "ordinal",
        "item_id_sha256",
        "command_sha256",
        "command_chars",
        "output_sha256",
        "output_chars",
        "status",
        "exit_code",
        "started_event_index",
        "completed_event_index",
    }
)
_HARNESSX_SUCCESS_HOOKS_NO_TOOLS = (
    "task_start",
    "step_start",
    "before_model",
    "after_model",
    "step_end",
    "task_end",
)
_HARNESSX_UNOBSERVED_HOOKS = ("before_tool", "after_tool")
_HARNESSX_PROCESSOR_OUTCOMES = frozenset(
    {"pass_through", "transform", "split", "intercept"}
)
_FEEDBACK_KEYS = frozenset(
    {
        "schema_version",
        "turn_number",
        "outcome",
        "raw_trace",
        "provisioned_skill_ids",
        "automatic_lifecycle_change",
        "lifecycle_note",
    }
)
_PROVISIONED_SKILL_KEYS = frozenset({"skill_id", "name", "score", "why"})
_ROUTING_KEYS_V1 = frozenset(
    {
        "schema_version",
        "routing_mode",
        "routing_source",
        "query_sha256",
        "query_chars",
        "query_stored",
        "active_skill_count",
        "candidate_skill_count",
        "candidate_skill_ids",
        "anchor_pool_preferred",
        "semantic_ranked_ids",
        "semantic_negative_excluded_ids",
        "semantic_abstained",
        "deterministic_guard_excluded_ids",
        "final_provisioned_ids",
        "final_abstain_reason",
        "authoritative_final_decision",
        "fallback_error_class",
        "model_call_skipped_no_active_skills",
        "requested_model_id",
        "requested_effort",
        "provider_reported_model_ids",
        "raw_trace",
        "ranked_ids_are_prompt_exposure_not_invocation",
    }
)
_ROUTING_KEYS_V2 = _ROUTING_KEYS_V1 | frozenset(
    {
        "name_collision_policy_version",
        "name_collision_group_count",
        "name_collision_suppressed_ids",
    }
)
_ROUTING_MODES = frozenset({"semantic", "deterministic", "controlled_lexical"})
_ROUTING_SOURCES = frozenset(
    {
        "deterministic",
        "semantic",
        "semantic_abstain",
        "deterministic_fallback",
        "controlled_lexical",
    }
)


class ChatLifecycleEvidenceError(ValueError):
    """Raised when immutable chat evidence cannot satisfy this adapter's contract."""


def _raise_duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChatLifecycleEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_raise_duplicate_key
        )
    except FileNotFoundError as exc:
        raise ChatLifecycleEvidenceError(f"{label} is missing: {path.name}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChatLifecycleEvidenceError(f"{label} is not valid JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ChatLifecycleEvidenceError(f"{label} must be a JSON object")
    return payload


def _require_exact_keys(payload: dict[str, Any], *, expected: frozenset[str], label: str) -> None:
    unknown = sorted(set(payload) - expected)
    missing = sorted(expected - set(payload))
    if unknown or missing:
        parts: list[str] = []
        if unknown:
            parts.append(f"unknown keys={', '.join(unknown)}")
        if missing:
            parts.append(f"missing keys={', '.join(missing)}")
        raise ChatLifecycleEvidenceError(f"{label} schema mismatch: {'; '.join(parts)}")


def _require_positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ChatLifecycleEvidenceError(f"{label} must be a positive integer")
    return value


def _require_nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ChatLifecycleEvidenceError(f"{label} must be a non-negative integer")
    return value


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ChatLifecycleEvidenceError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChatLifecycleEvidenceError(f"{label} must be a non-empty string")
    return value


def _require_optional_string(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, label=label)


def _require_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise ChatLifecycleEvidenceError(f"{label} must be boolean")
    return value


def _require_skill_ids(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ChatLifecycleEvidenceError(f"{label} must be a list of skill IDs")
    ids: list[str] = []
    for index, skill_id in enumerate(value):
        if not isinstance(skill_id, str) or not _SKILL_ID_RE.fullmatch(skill_id):
            raise ChatLifecycleEvidenceError(f"{label}[{index}] has an unsafe skill ID")
        ids.append(skill_id)
    if len(ids) != len(set(ids)):
        raise ChatLifecycleEvidenceError(f"{label} contains duplicate skill IDs")
    return tuple(ids)


def _safe_raw_path(trace_root: Path, *, pointer: str, label: str) -> Path:
    if not _SAFE_BASENAME_RE.fullmatch(pointer) or Path(pointer).name != pointer:
        raise ChatLifecycleEvidenceError(f"{label} raw trace pointer is unsafe")
    candidate = trace_root / pointer
    if candidate.is_symlink():
        raise ChatLifecycleEvidenceError(f"{label} raw trace must not be a symlink")
    try:
        root = trace_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ChatLifecycleEvidenceError(f"{label} raw trace is unavailable") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ChatLifecycleEvidenceError(f"{label} raw trace is outside the session root")
    return resolved


def _validate_raw_trace(value: Any, *, trace_root: Path, label: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ChatLifecycleEvidenceError(f"{label} raw_trace must be an object")
    _require_exact_keys(value, expected=_RAW_TRACE_KEYS, label=f"{label}.raw_trace")
    pointer = value["pointer"]
    if not isinstance(pointer, str):
        raise ChatLifecycleEvidenceError(f"{label}.raw_trace.pointer must be a string")
    digest = _require_sha256(value["sha256"], label=f"{label}.raw_trace.sha256")
    raw_path = _safe_raw_path(trace_root, pointer=pointer, label=label)
    observed = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if observed != digest:
        raise ChatLifecycleEvidenceError(f"{label} raw trace SHA-256 mismatch")
    return pointer, digest


def _validate_harnessx_shadow(
    value: Any,
    *,
    trace_root: Path,
    turn_number: int,
    assistant_answer_sha256: str,
    assistant_answer_chars: int,
    raw_trace_pointer: str,
    raw_trace_sha256: str,
) -> None:
    """Validate an optional shadow-only HarnessX envelope and its claim boundary."""

    label = "turn meta harnessx_shadow"
    if not isinstance(value, dict):
        raise ChatLifecycleEvidenceError(f"{label} must be an object")
    _require_exact_keys(value, expected=_HARNESSX_REFERENCE_KEYS, label=label)
    if value["mode"] != "shadow_only" or value["status"] != "completed":
        raise ChatLifecycleEvidenceError(f"{label} must describe a completed shadow-only turn")
    pointer = value["pointer"]
    expected_pointer = f"harnessx-turn-{turn_number:04d}.shadow.json"
    if pointer != expected_pointer:
        raise ChatLifecycleEvidenceError(f"{label}.pointer does not match the requested turn")
    file_sha256 = _require_sha256(value["sha256"], label=f"{label}.sha256")
    report_sha256 = _require_sha256(
        value["report_sha256"], label=f"{label}.report_sha256"
    )
    report_path = _safe_raw_path(trace_root, pointer=pointer, label=label)
    observed_file_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    if observed_file_sha256 != file_sha256:
        raise ChatLifecycleEvidenceError(f"{label} file SHA-256 mismatch")

    report = _read_json_object(report_path, label="HarnessX shadow report")
    report_schema = report.get("schema_version")
    if report_schema not in {
        "merlin-harnessx-chat-shadow-v1",
        "merlin-harnessx-chat-shadow-v2",
    }:
        raise ChatLifecycleEvidenceError("HarnessX shadow report schema_version is unsupported")
    _require_exact_keys(
        report,
        expected=(
            _HARNESSX_REPORT_KEYS_V1
            if report_schema == "merlin-harnessx-chat-shadow-v1"
            else _HARNESSX_REPORT_KEYS_V2
        ),
        label="HarnessX shadow report",
    )
    if report["mode"] != "shadow_only" or report["status"] != "completed":
        raise ChatLifecycleEvidenceError(
            "HarnessX shadow report must describe a completed shadow-only turn"
        )
    if (
        _require_positive_int(
            report["turn_number"], label="HarnessX shadow report turn_number"
        )
        != turn_number
    ):
        raise ChatLifecycleEvidenceError(
            "HarnessX shadow report turn_number does not match turn meta"
        )
    if report["task_id"] != f"chat-turn-{turn_number:04d}":
        raise ChatLifecycleEvidenceError("HarnessX shadow report task_id does not match turn")
    if report["failure_class"] is not None:
        raise ChatLifecycleEvidenceError(
            "completed HarnessX shadow report must not contain a failure class"
        )

    observed_report_sha256 = _require_sha256(
        report["report_sha256"], label="HarnessX shadow report report_sha256"
    )
    unsigned_report = dict(report)
    del unsigned_report["report_sha256"]
    canonical = json.dumps(
        unsigned_report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != observed_report_sha256:
        raise ChatLifecycleEvidenceError("HarnessX shadow report semantic SHA-256 mismatch")
    if observed_report_sha256 != report_sha256:
        raise ChatLifecycleEvidenceError(
            "HarnessX shadow report digest does not match turn meta"
        )

    bindings = report["bindings"]
    if not isinstance(bindings, dict):
        raise ChatLifecycleEvidenceError("HarnessX shadow report bindings must be an object")
    _require_exact_keys(
        bindings, expected=_HARNESSX_BINDING_KEYS, label="HarnessX shadow report bindings"
    )
    _require_sha256(
        bindings["prompt_sha256"], label="HarnessX shadow report bindings.prompt_sha256"
    )
    _require_nonnegative_int(
        bindings["prompt_chars"], label="HarnessX shadow report bindings.prompt_chars"
    )
    if (
        _require_sha256(
            bindings["answer_sha256"],
            label="HarnessX shadow report bindings.answer_sha256",
        )
        != assistant_answer_sha256
        or _require_nonnegative_int(
            bindings["answer_chars"],
            label="HarnessX shadow report bindings.answer_chars",
        )
        != assistant_answer_chars
    ):
        raise ChatLifecycleEvidenceError(
            "HarnessX shadow report assistant binding does not match turn meta"
        )
    if (
        bindings["provider_raw_trace_pointer"] != raw_trace_pointer
        or _require_sha256(
            bindings["provider_raw_trace_sha256"],
            label="HarnessX shadow report bindings.provider_raw_trace_sha256",
        )
        != raw_trace_sha256
    ):
        raise ChatLifecycleEvidenceError(
            "HarnessX shadow report provider trace binding does not match turn meta"
        )

    command_observations = ()
    if report_schema == "merlin-harnessx-chat-shadow-v2":
        raw_path = _safe_raw_path(
            trace_root, pointer=raw_trace_pointer, label="HarnessX provider binding"
        )
        try:
            command_observations = parse_codex_command_observations(
                raw_path.read_bytes()
            )
        except CodexToolObservationError as exc:
            raise ChatLifecycleEvidenceError(
                f"HarnessX provider command lifecycle is invalid: {exc}"
            ) from exc
        tool_observation = report["tool_observation"]
        if not isinstance(tool_observation, dict):
            raise ChatLifecycleEvidenceError(
                "HarnessX shadow tool_observation must be an object"
            )
        _require_exact_keys(
            tool_observation,
            expected=_HARNESSX_TOOL_OBSERVATION_KEYS,
            label="HarnessX shadow tool_observation",
        )
        if (
            tool_observation["schema_version"]
            != "codex-command-execution-observation-v1"
            or tool_observation["source"] != "codex_exec_jsonl"
        ):
            raise ChatLifecycleEvidenceError(
                "HarnessX shadow tool observation source is unsupported"
            )
        command_count = _require_nonnegative_int(
            tool_observation["command_count"],
            label="HarnessX shadow tool_observation.command_count",
        )
        if command_count != len(command_observations):
            raise ChatLifecycleEvidenceError(
                "HarnessX shadow command observation count drifted"
            )
        reported_observations = tool_observation["observations"]
        if not isinstance(reported_observations, list):
            raise ChatLifecycleEvidenceError(
                "HarnessX shadow command observations must be a list"
            )
        for index, reported in enumerate(reported_observations):
            if not isinstance(reported, dict):
                raise ChatLifecycleEvidenceError(
                    f"HarnessX shadow command observations[{index}] must be an object"
                )
            _require_exact_keys(
                reported,
                expected=_HARNESSX_COMMAND_OBSERVATION_KEYS,
                label=f"HarnessX shadow command observations[{index}]",
            )
        expected_observations = [
            observation.to_safe_dict() for observation in command_observations
        ]
        if json.dumps(
            reported_observations,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) != json.dumps(
            expected_observations,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ):
            raise ChatLifecycleEvidenceError(
                "HarnessX shadow command observations do not match provider JSONL"
            )
        if (
            _require_sha256(
                tool_observation["observation_sha256"],
                label="HarnessX shadow tool_observation.observation_sha256",
            )
            != command_observation_sha256(command_observations)
        ):
            raise ChatLifecycleEvidenceError(
                "HarnessX shadow command observation digest drifted"
            )
        expected_coverage = (
            "paired_command_execution_events"
            if command_observations
            else "no_supported_command_events"
        )
        if tool_observation["coverage"] != expected_coverage:
            raise ChatLifecycleEvidenceError(
                "HarnessX shadow command observation coverage drifted"
            )
        for key in (
            "raw_command_text_stored",
            "raw_tool_output_stored",
            "pre_execution_control_available",
        ):
            if _require_bool(
                tool_observation[key],
                label=f"HarnessX shadow tool_observation.{key}",
            ):
                raise ChatLifecycleEvidenceError(
                    f"HarnessX shadow tool observation overclaims {key}"
                )
        if _require_bool(
            tool_observation["replayed_after_provider_execution"],
            label=(
                "HarnessX shadow "
                "tool_observation.replayed_after_provider_execution"
            ),
        ) != bool(command_observations):
            raise ChatLifecycleEvidenceError(
                "HarnessX shadow tool replay boundary drifted"
            )

    expected_hook_sequence = (
        ("task_start", "step_start", "before_model")
        + tuple(
            hook
            for _observation in command_observations
            for hook in ("before_tool", "after_tool")
        )
        + ("after_model", "step_end", "task_end")
    )
    hook_sequence = report["hook_sequence"]
    if (
        not isinstance(hook_sequence, list)
        or tuple(hook_sequence) != expected_hook_sequence
    ):
        raise ChatLifecycleEvidenceError(
            "HarnessX shadow report success hook sequence is incomplete"
        )
    unobserved_hooks = report["unobserved_hooks"]
    expected_unobserved_hooks = (
        () if command_observations else _HARNESSX_UNOBSERVED_HOOKS
    )
    if (
        not isinstance(unobserved_hooks, list)
        or tuple(unobserved_hooks) != expected_unobserved_hooks
    ):
        raise ChatLifecycleEvidenceError(
            "HarnessX shadow report tool-hook boundary is invalid"
        )
    if (
        value["observed_hooks"] != hook_sequence
        or value["unobserved_hooks"] != unobserved_hooks
    ):
        raise ChatLifecycleEvidenceError(
            "HarnessX shadow hook summary does not match its report"
        )
    observed_hook_count = _require_nonnegative_int(
        report["observed_hook_count"],
        label="HarnessX shadow report observed_hook_count",
    )
    if observed_hook_count != len(expected_hook_sequence):
        raise ChatLifecycleEvidenceError("HarnessX shadow observed hook count drifted")

    records = report["records"]
    if not isinstance(records, list) or len(records) != observed_hook_count:
        raise ChatLifecycleEvidenceError("HarnessX shadow report records are incomplete")
    prefix = f"hx-chat-{turn_number:04d}"
    expected_event_ids = (
        (
            f"{prefix}:task-start",
            f"{prefix}:step-start",
            f"{prefix}:before-model",
        )
        + tuple(
            event_id
            for observation in command_observations
            for event_id in (
                f"{prefix}:before-tool:{observation.ordinal:03d}",
                f"{prefix}:after-tool:{observation.ordinal:03d}",
            )
        )
        + (
            f"{prefix}:after-model",
            f"{prefix}:step-end",
            f"{prefix}:task-end",
        )
    )
    total_audits = 0
    shadow_change_count = 0
    for index, record in enumerate(records):
        record_label = f"HarnessX shadow report records[{index}]"
        if not isinstance(record, dict):
            raise ChatLifecycleEvidenceError(f"{record_label} must be an object")
        _require_exact_keys(record, expected=_HARNESSX_RECORD_KEYS, label=record_label)
        if _require_positive_int(record["sequence"], label=f"{record_label}.sequence") != index + 1:
            raise ChatLifecycleEvidenceError("HarnessX shadow record sequence drifted")
        expected_hook = expected_hook_sequence[index]
        if record["hook"] != expected_hook:
            raise ChatLifecycleEvidenceError("HarnessX shadow record hook sequence drifted")
        event_id = _require_string(record["event_id"], label=f"{record_label}.event_id")
        expected_event_id = expected_event_ids[index]
        if event_id != expected_event_id:
            raise ChatLifecycleEvidenceError("HarnessX shadow record event_id drifted")
        _require_sha256(record["input_sha256"], label=f"{record_label}.input_sha256")
        output_sha256s = record["output_sha256s"]
        if not isinstance(output_sha256s, list):
            raise ChatLifecycleEvidenceError(f"{record_label}.output_sha256s must be a list")
        for output_index, output_sha256 in enumerate(output_sha256s):
            _require_sha256(
                output_sha256,
                label=f"{record_label}.output_sha256s[{output_index}]",
            )
        output_count = _require_nonnegative_int(
            record["output_count"], label=f"{record_label}.output_count"
        )
        if output_count != len(output_sha256s):
            raise ChatLifecycleEvidenceError("HarnessX shadow output count drifted")
        changed = _require_bool(
            record["shadow_change_detected"],
            label=f"{record_label}.shadow_change_detected",
        )
        intercepted = _require_bool(
            record["intercepted_in_shadow"],
            label=f"{record_label}.intercepted_in_shadow",
        )
        if intercepted != (output_count == 0):
            raise ChatLifecycleEvidenceError("HarnessX shadow interception evidence drifted")
        shadow_change_count += int(changed)
        audits = record["processor_audit"]
        if not isinstance(audits, list):
            raise ChatLifecycleEvidenceError(f"{record_label}.processor_audit must be a list")
        total_audits += len(audits)
        for audit_index, audit in enumerate(audits):
            audit_label = f"{record_label}.processor_audit[{audit_index}]"
            if not isinstance(audit, dict):
                raise ChatLifecycleEvidenceError(f"{audit_label} must be an object")
            _require_exact_keys(audit, expected=_HARNESSX_AUDIT_KEYS, label=audit_label)
            if audit["event_id"] != event_id or audit["hook"] != expected_hook:
                raise ChatLifecycleEvidenceError("HarnessX shadow processor audit binding drifted")
            _require_string(audit["processor"], label=f"{audit_label}.processor")
            _require_string(
                audit["singleton_group"], label=f"{audit_label}.singleton_group"
            )
            if audit["outcome"] not in _HARNESSX_PROCESSOR_OUTCOMES:
                raise ChatLifecycleEvidenceError(
                    "HarnessX shadow processor audit outcome is unsupported"
                )
            output_event_ids = audit["output_event_ids"]
            if not isinstance(output_event_ids, list) or any(
                not isinstance(item, str) or not item.strip()
                for item in output_event_ids
            ):
                raise ChatLifecycleEvidenceError(
                    f"{audit_label}.output_event_ids must be non-empty strings"
                )

    report_audit_count = _require_nonnegative_int(
        report["processor_audit_count"],
        label="HarnessX shadow report processor_audit_count",
    )
    report_change_count = _require_nonnegative_int(
        report["shadow_change_count"],
        label="HarnessX shadow report shadow_change_count",
    )
    if (
        total_audits != report_audit_count
        or report_audit_count
        != _require_nonnegative_int(
            value["processor_audit_count"],
            label=f"{label}.processor_audit_count",
        )
    ):
        raise ChatLifecycleEvidenceError("HarnessX shadow processor audit count drifted")
    if (
        shadow_change_count != report_change_count
        or report_change_count
        != _require_nonnegative_int(
            value["shadow_change_count"], label=f"{label}.shadow_change_count"
        )
    ):
        raise ChatLifecycleEvidenceError("HarnessX shadow change count drifted")

    claims = report["claim_boundary"]
    if not isinstance(claims, dict):
        raise ChatLifecycleEvidenceError("HarnessX shadow claim_boundary must be an object")
    _require_exact_keys(
        claims,
        expected=(
            _HARNESSX_CLAIM_KEYS_V1
            if report_schema == "merlin-harnessx-chat-shadow-v1"
            else _HARNESSX_CLAIM_KEYS_V2
        ),
        label="HarnessX shadow claim_boundary",
    )
    expected_claims = {
        "candidate_processor_outputs_applied_to_provider": False,
        "candidate_processor_outputs_applied_to_user_answer": False,
        "provider_tool_events_observed": bool(command_observations),
        "tool_hooks_synthesized": False,
        "provider_native_skill_invocation_claimed": False,
        "harness_candidate_promoted": False,
    }
    if report_schema == "merlin-harnessx-chat-shadow-v2":
        expected_claims.update(
            {
                "tool_hooks_replayed_after_provider_execution": bool(
                    command_observations
                ),
                "tool_policy_enforced_before_execution": False,
            }
        )
    for claim, expected_claim in expected_claims.items():
        observed_claim = _require_bool(
            claims[claim], label=f"HarnessX shadow claim_boundary.{claim}"
        )
        if observed_claim != expected_claim:
            if observed_claim and not expected_claim:
                raise ChatLifecycleEvidenceError(
                    f"HarnessX shadow report overclaims {claim}"
                )
            raise ChatLifecycleEvidenceError(
                f"HarnessX shadow report claim boundary drifted for {claim}"
            )


def _validate_provisioned_skills(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ChatLifecycleEvidenceError("turn meta provisioned_skills must be a list")
    ids: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ChatLifecycleEvidenceError(f"turn meta provisioned_skills[{index}] must be an object")
        _require_exact_keys(
            item,
            expected=_PROVISIONED_SKILL_KEYS,
            label=f"turn meta provisioned_skills[{index}]",
        )
        skill_id = item["skill_id"]
        if not isinstance(skill_id, str) or not _SKILL_ID_RE.fullmatch(skill_id):
            raise ChatLifecycleEvidenceError(
                f"turn meta provisioned_skills[{index}].skill_id is unsafe"
            )
        _require_string(item["name"], label=f"turn meta provisioned_skills[{index}].name")
        _require_string(item["why"], label=f"turn meta provisioned_skills[{index}].why")
        score = item["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ChatLifecycleEvidenceError(
                f"turn meta provisioned_skills[{index}].score must be finite"
            )
        ids.append(skill_id)
    if len(ids) != len(set(ids)):
        raise ChatLifecycleEvidenceError("turn meta provisioned_skills contains duplicate skill IDs")
    return tuple(ids)


def _validate_routing(value: Any, *, trace_root: Path) -> tuple[str, ...]:
    if not isinstance(value, dict):
        raise ChatLifecycleEvidenceError("turn meta routing_decision must be an object")
    schema_version = value.get("schema_version")
    if schema_version not in {1, 2}:
        raise ChatLifecycleEvidenceError("turn meta routing_decision has an unsupported schema_version")
    _require_exact_keys(
        value,
        expected=_ROUTING_KEYS_V1 if schema_version == 1 else _ROUTING_KEYS_V2,
        label="turn meta routing_decision",
    )
    if value["routing_mode"] not in _ROUTING_MODES:
        raise ChatLifecycleEvidenceError("turn meta routing_decision has an unsafe routing_mode")
    if value["routing_source"] not in _ROUTING_SOURCES:
        raise ChatLifecycleEvidenceError("turn meta routing_decision has an unsafe routing_source")
    _require_sha256(value["query_sha256"], label="turn meta routing_decision.query_sha256")
    _require_nonnegative_int(value["query_chars"], label="turn meta routing_decision.query_chars")
    if _require_bool(value["query_stored"], label="turn meta routing_decision.query_stored"):
        raise ChatLifecycleEvidenceError("turn meta must not persist raw user query text")
    _require_nonnegative_int(value["active_skill_count"], label="turn meta routing_decision.active_skill_count")
    _require_nonnegative_int(value["candidate_skill_count"], label="turn meta routing_decision.candidate_skill_count")
    list_fields: dict[str, tuple[str, ...]] = {}
    for key in (
        "candidate_skill_ids",
        "semantic_ranked_ids",
        "semantic_negative_excluded_ids",
        "deterministic_guard_excluded_ids",
    ):
        list_fields[key] = _require_skill_ids(
            value[key], label=f"turn meta routing_decision.{key}"
        )
    if value["candidate_skill_count"] != len(list_fields["candidate_skill_ids"]):
        raise ChatLifecycleEvidenceError(
            "turn meta routing_decision candidate count drifted"
        )
    if schema_version == 2:
        suppressed = _require_skill_ids(
            value["name_collision_suppressed_ids"],
            label="turn meta routing_decision.name_collision_suppressed_ids",
        )
        if value["name_collision_policy_version"] != NAME_COLLISION_POLICY_VERSION:
            raise ChatLifecycleEvidenceError(
                "turn meta routing_decision name-collision policy version drifted"
            )
        _require_nonnegative_int(
            value["name_collision_group_count"],
            label="turn meta routing_decision.name_collision_group_count",
        )
        if set(list_fields["candidate_skill_ids"]) & set(suppressed):
            raise ChatLifecycleEvidenceError(
                "turn meta routing_decision exposes a suppressed same-name variant"
            )
        if (value["name_collision_group_count"] == 0) != (len(suppressed) == 0):
            raise ChatLifecycleEvidenceError(
                "turn meta routing_decision collision evidence is inconsistent"
            )
        if value["name_collision_group_count"] > len(suppressed):
            raise ChatLifecycleEvidenceError(
                "turn meta routing_decision collision group count is impossible"
            )
    exposure_ids = _require_skill_ids(
        value["final_provisioned_ids"], label="turn meta routing_decision.final_provisioned_ids"
    )
    for key in (
        "anchor_pool_preferred",
        "semantic_abstained",
        "authoritative_final_decision",
        "model_call_skipped_no_active_skills",
        "ranked_ids_are_prompt_exposure_not_invocation",
    ):
        _require_bool(value[key], label=f"turn meta routing_decision.{key}")
    if not value["authoritative_final_decision"]:
        raise ChatLifecycleEvidenceError("turn meta routing decision is not authoritative")
    if not value["ranked_ids_are_prompt_exposure_not_invocation"]:
        raise ChatLifecycleEvidenceError("turn meta routing decision has an unsafe invocation boundary")
    _require_optional_string(value["final_abstain_reason"], label="turn meta routing_decision.final_abstain_reason")
    _require_optional_string(value["fallback_error_class"], label="turn meta routing_decision.fallback_error_class")
    _require_optional_string(value["requested_model_id"], label="turn meta routing_decision.requested_model_id")
    _require_optional_string(value["requested_effort"], label="turn meta routing_decision.requested_effort")
    _require_skill_ids(
        value["provider_reported_model_ids"],
        label="turn meta routing_decision.provider_reported_model_ids",
    )
    routing_raw = value["raw_trace"]
    if routing_raw is not None:
        _validate_raw_trace(
            routing_raw,
            trace_root=trace_root,
            label="turn meta routing_decision",
        )
    return exposure_ids


@dataclass(frozen=True, slots=True)
class ChatLifecycleObservation:
    """One integrity-checked chat outcome, bounded to prompt-exposure evidence."""

    turn_number: int
    feedback_outcome: str
    exposure_skill_ids: tuple[str, ...]
    raw_trace_pointer: str
    raw_trace_sha256: str
    evidence_level: str = "exposure_outcome_proxy"
    actual_invocation_evidence_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return only exposure/outcome facts; never synthesize selection metrics."""

        return {
            "schema_version": 1,
            "turn_number": self.turn_number,
            "feedback_outcome": self.feedback_outcome,
            "exposure_skill_ids": list(self.exposure_skill_ids),
            "raw_trace": {
                "pointer": self.raw_trace_pointer,
                "sha256": self.raw_trace_sha256,
            },
            "evidence_level": self.evidence_level,
            "actual_invocation_evidence_complete": self.actual_invocation_evidence_complete,
        }


@dataclass(frozen=True, slots=True)
class ChatVerifierContract:
    """Identity of a future frozen verifier surface; no verifier is run here."""

    task_id: str
    verifier_id: str
    contract_sha256: str

    def __post_init__(self) -> None:
        _require_string(self.task_id, label="verifier contract task_id")
        _require_string(self.verifier_id, label="verifier contract verifier_id")
        _require_sha256(self.contract_sha256, label="verifier contract contract_sha256")


@dataclass(frozen=True, slots=True)
class LifecycleEligibility:
    """Observe-only lifecycle assessment for one chat observation."""

    observe_only: bool
    action_allowed: bool
    status: str
    blockers: tuple[str, ...]
    exposure_skill_ids: tuple[str, ...]
    evidence_boundary: str


def load_chat_lifecycle_observation(
    trace_root: str | Path, *, turn_number: int
) -> ChatLifecycleObservation:
    """Load one immutable turn plus its matching immutable feedback ledger.

    ``turn_number`` selects filenames constructed by this function; neither
    metadata nor feedback can redirect the reader to another session location.
    """

    expected_turn = _require_positive_int(turn_number, label="turn_number")
    root = Path(trace_root).expanduser()
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ChatLifecycleEvidenceError("trace_root must be an existing directory") from exc
    if not resolved_root.is_dir():
        raise ChatLifecycleEvidenceError("trace_root must be an existing directory")

    turn_meta = _read_json_object(
        resolved_root / f"turn-{expected_turn:04d}.meta.json", label="turn meta"
    )
    expected_turn_keys = (
        _TURN_META_KEYS | frozenset({"harnessx_shadow"})
        if "harnessx_shadow" in turn_meta
        else _TURN_META_KEYS
    )
    _require_exact_keys(turn_meta, expected=expected_turn_keys, label="turn meta")
    if turn_meta["schema_version"] != 1:
        raise ChatLifecycleEvidenceError("turn meta has an unsupported schema_version")
    if _require_positive_int(turn_meta["turn_number"], label="turn meta turn_number") != expected_turn:
        raise ChatLifecycleEvidenceError("turn meta turn_number does not match requested turn")
    _require_string(turn_meta["provider_thread_id"], label="turn meta provider_thread_id")
    _require_optional_string(turn_meta["provider_turn_id"], label="turn meta provider_turn_id")
    _require_bool(turn_meta["resumed"], label="turn meta resumed")
    _require_sha256(turn_meta["user_input_sha256"], label="turn meta user_input_sha256")
    _require_nonnegative_int(turn_meta["user_input_chars"], label="turn meta user_input_chars")
    if _require_bool(turn_meta["user_input_stored"], label="turn meta user_input_stored"):
        raise ChatLifecycleEvidenceError("turn meta must not store raw user input")
    _require_sha256(turn_meta["assistant_answer_sha256"], label="turn meta assistant_answer_sha256")
    _require_nonnegative_int(turn_meta["assistant_answer_chars"], label="turn meta assistant_answer_chars")
    if _require_bool(turn_meta["assistant_answer_stored"], label="turn meta assistant_answer_stored"):
        raise ChatLifecycleEvidenceError("turn meta must not store raw assistant answer")
    if not isinstance(turn_meta["deterministic_reference_decision"], dict):
        raise ChatLifecycleEvidenceError("turn meta deterministic_reference_decision must be an object")
    if not isinstance(turn_meta["backend_metadata"], dict):
        raise ChatLifecycleEvidenceError("turn meta backend_metadata must be an object")
    if turn_meta["feedback_status"] != "pending":
        raise ChatLifecycleEvidenceError("turn meta has an unsafe feedback status")
    if turn_meta["lifecycle_automatic_change"] != "deferred":
        raise ChatLifecycleEvidenceError("turn meta has an unsafe lifecycle status")
    if _require_bool(
        turn_meta["prompt_provisioning_is_provider_native_invocation"],
        label="turn meta prompt provisioning boundary",
    ):
        raise ChatLifecycleEvidenceError("turn meta improperly treats prompt exposure as invocation")
    if _require_bool(
        turn_meta["actual_invocation_evidence_complete"],
        label="turn meta actual invocation evidence",
    ):
        raise ChatLifecycleEvidenceError("turn meta has unsupported actual invocation evidence")

    exposure_from_records = _validate_provisioned_skills(turn_meta["provisioned_skills"])
    exposure_from_routing = _validate_routing(
        turn_meta["routing_decision"], trace_root=resolved_root
    )
    if exposure_from_records != exposure_from_routing:
        raise ChatLifecycleEvidenceError(
            "turn meta provisioned skill records do not match authoritative exposure IDs"
        )
    raw_pointer, raw_sha256 = _validate_raw_trace(
        turn_meta["raw_trace"], trace_root=resolved_root, label="turn meta"
    )
    if "harnessx_shadow" in turn_meta:
        _validate_harnessx_shadow(
            turn_meta["harnessx_shadow"],
            trace_root=resolved_root,
            turn_number=expected_turn,
            assistant_answer_sha256=turn_meta["assistant_answer_sha256"],
            assistant_answer_chars=turn_meta["assistant_answer_chars"],
            raw_trace_pointer=raw_pointer,
            raw_trace_sha256=raw_sha256,
        )

    feedback = _read_json_object(
        resolved_root / f"feedback-turn-{expected_turn:04d}.json", label="feedback ledger"
    )
    _require_exact_keys(feedback, expected=_FEEDBACK_KEYS, label="feedback ledger")
    if feedback["schema_version"] != 1:
        raise ChatLifecycleEvidenceError("feedback ledger has an unsupported schema_version")
    if _require_positive_int(feedback["turn_number"], label="feedback ledger turn_number") != expected_turn:
        raise ChatLifecycleEvidenceError("feedback ledger turn_number does not match requested turn")
    outcome = feedback["outcome"]
    if outcome not in {"pass", "fail"}:
        raise ChatLifecycleEvidenceError("feedback ledger outcome must be pass or fail")
    feedback_pointer, feedback_sha256 = _validate_raw_trace(
        feedback["raw_trace"], trace_root=resolved_root, label="feedback ledger"
    )
    if (feedback_pointer, feedback_sha256) != (raw_pointer, raw_sha256):
        raise ChatLifecycleEvidenceError("feedback ledger raw trace does not match turn meta")
    if _require_skill_ids(
        feedback["provisioned_skill_ids"], label="feedback ledger provisioned_skill_ids"
    ) != exposure_from_routing:
        raise ChatLifecycleEvidenceError("feedback ledger exposure IDs do not match turn meta")
    if _require_bool(
        feedback["automatic_lifecycle_change"],
        label="feedback ledger automatic_lifecycle_change",
    ):
        raise ChatLifecycleEvidenceError("feedback ledger records an unsafe automatic lifecycle change")
    _require_string(feedback["lifecycle_note"], label="feedback ledger lifecycle_note")

    return ChatLifecycleObservation(
        turn_number=expected_turn,
        feedback_outcome=outcome,
        exposure_skill_ids=exposure_from_routing,
        raw_trace_pointer=raw_pointer,
        raw_trace_sha256=raw_sha256,
    )


def assess_lifecycle_eligibility(
    observation: ChatLifecycleObservation,
    *,
    verifier_contract: ChatVerifierContract | None = None,
) -> LifecycleEligibility:
    """Return a conservative boundary assessment without proposing an action.

    A fail feedback entry records a user-observed outcome, not a deterministic
    verifier result.  Since this chat adapter also lacks actual invocation
    evidence, it intentionally never makes a skill lifecycle action eligible.
    """

    if observation.evidence_level != "exposure_outcome_proxy":
        raise ChatLifecycleEvidenceError("unsupported chat lifecycle evidence level")
    if observation.actual_invocation_evidence_complete:
        raise ChatLifecycleEvidenceError("chat observation violates the invocation boundary")

    blockers: list[str] = []
    if verifier_contract is None:
        blockers.append("verifier_missing")
    if observation.feedback_outcome == "fail":
        blockers.append("feedback_is_observational_not_a_verifier")
    blockers.append("actual_invocation_evidence_missing")
    status = "verifier_missing" if verifier_contract is None else "actual_invocation_evidence_missing"
    return LifecycleEligibility(
        observe_only=True,
        action_allowed=False,
        status=status,
        blockers=tuple(blockers),
        exposure_skill_ids=observation.exposure_skill_ids,
        evidence_boundary=(
            "Exposure IDs are prompt-provisioning evidence only. They are not selected, "
            "loaded, invoked, or shadowing metric inputs."
        ),
    )
