"""Strict, content-redacted observations of Codex CLI command lifecycles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


MAX_RAW_TRACE_BYTES = 32 * 1024 * 1024
MAX_EVENT_COUNT = 100_000
MAX_COMMAND_CHARS = 1_000_000
MAX_OUTPUT_CHARS = 8_000_000
MAX_COMMAND_OBSERVATIONS = 256


class CodexToolObservationError(ValueError):
    """Raised when command lifecycle evidence is malformed or incomplete."""


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CodexToolObservationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_text(value: Any, *, label: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise CodexToolObservationError(
            f"{label} must be a non-empty string of at most {max_chars} characters"
        )
    return value


@dataclass(frozen=True, slots=True)
class CodexCommandObservation:
    ordinal: int
    item_id_sha256: str
    command_sha256: str
    command_chars: int
    output_sha256: str
    output_chars: int
    status: str
    exit_code: int
    started_event_index: int
    completed_event_index: int

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "item_id_sha256": self.item_id_sha256,
            "command_sha256": self.command_sha256,
            "command_chars": self.command_chars,
            "output_sha256": self.output_sha256,
            "output_chars": self.output_chars,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_event_index": self.started_event_index,
            "completed_event_index": self.completed_event_index,
        }


@dataclass(frozen=True, slots=True)
class _PendingCommand:
    item_id: str
    command: str
    started_event_index: int


def parse_codex_command_observations(
    raw_trace: bytes,
) -> tuple[CodexCommandObservation, ...]:
    """Return only strictly paired ``command_execution`` start/completion events.

    Unknown non-command events are ignored. Once an event declares
    ``item.type=command_execution``, its lifecycle is strict and fail-closed.
    """

    if not isinstance(raw_trace, bytes) or len(raw_trace) > MAX_RAW_TRACE_BYTES:
        raise CodexToolObservationError(
            f"raw trace must be bytes of at most {MAX_RAW_TRACE_BYTES} bytes"
        )
    try:
        text = raw_trace.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CodexToolObservationError("raw trace must be UTF-8 JSONL") from exc

    pending: dict[str, _PendingCommand] = {}
    completed_ids: set[str] = set()
    observations: list[CodexCommandObservation] = []
    event_count = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        event_count += 1
        if event_count > MAX_EVENT_COUNT:
            raise CodexToolObservationError("raw trace event count exceeds limit")
        try:
            event = json.loads(line, object_pairs_hook=_duplicate_key_guard)
        except json.JSONDecodeError as exc:
            raise CodexToolObservationError(
                f"raw trace line {line_number} is not valid JSON"
            ) from exc
        if not isinstance(event, dict):
            raise CodexToolObservationError(
                f"raw trace line {line_number} must be a JSON object"
            )
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        event_type = event.get("type")
        if event_type not in {"item.started", "item.completed"}:
            raise CodexToolObservationError(
                "command_execution event type must be item.started or item.completed"
            )
        item_id = _require_text(item.get("id"), label="command item id", max_chars=256)
        command = _require_text(
            item.get("command"), label="command text", max_chars=MAX_COMMAND_CHARS
        )
        status = item.get("status")
        exit_code = item.get("exit_code")
        output = item.get("aggregated_output")
        if not isinstance(output, str) or len(output) > MAX_OUTPUT_CHARS:
            raise CodexToolObservationError(
                f"command output must be a string of at most {MAX_OUTPUT_CHARS} characters"
            )

        if event_type == "item.started":
            if status != "in_progress" or exit_code is not None:
                raise CodexToolObservationError(
                    "started command must be in_progress with null exit_code"
                )
            if item_id in pending or item_id in completed_ids:
                raise CodexToolObservationError("command item id is duplicated")
            pending[item_id] = _PendingCommand(
                item_id=item_id,
                command=command,
                started_event_index=event_count,
            )
            continue

        started = pending.pop(item_id, None)
        if started is None:
            raise CodexToolObservationError(
                "completed command has no matching started event"
            )
        if command != started.command:
            raise CodexToolObservationError(
                "completed command text differs from its started event"
            )
        if status not in {"completed", "failed"}:
            raise CodexToolObservationError(
                "completed command status must be completed or failed"
            )
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise CodexToolObservationError(
                "completed command exit_code must be an integer"
            )
        if (status == "completed") != (exit_code == 0):
            raise CodexToolObservationError(
                "completed command status and exit_code are inconsistent"
            )
        if len(observations) >= MAX_COMMAND_OBSERVATIONS:
            raise CodexToolObservationError("command observation count exceeds limit")
        completed_ids.add(item_id)
        observations.append(
            CodexCommandObservation(
                ordinal=len(observations) + 1,
                item_id_sha256=_sha256_text(item_id),
                command_sha256=_sha256_text(command),
                command_chars=len(command),
                output_sha256=_sha256_text(output),
                output_chars=len(output),
                status=status,
                exit_code=exit_code,
                started_event_index=started.started_event_index,
                completed_event_index=event_count,
            )
        )

    if pending:
        raise CodexToolObservationError("raw trace contains incomplete command lifecycle")
    return tuple(observations)


def command_observation_sha256(
    observations: tuple[CodexCommandObservation, ...],
) -> str:
    payload = [observation.to_safe_dict() for observation in observations]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CodexCommandObservation",
    "CodexToolObservationError",
    "command_observation_sha256",
    "parse_codex_command_observations",
]
