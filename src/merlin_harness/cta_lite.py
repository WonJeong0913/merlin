"""CTA-lite behavior delta calculations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import BehaviorDelta, TraceRecord


_PAIRED_METADATA_KEYS = (
    "model_id",
    "backend",
    "seed",
    "trial_index",
    "harness_mode",
    "verifier_id",
    "budget_id",
    "workspace_version",
)
_WORKSPACE_EVIDENCE_KEYS = (
    "workspace_manifest_before",
    "workspace_manifest_after",
    "expected_artifacts",
)


def _event_count(trace: TraceRecord, event_type: str) -> int:
    return sum(1 for event in trace.events if str(event.get("type", "")).upper() == event_type.upper())


def _success_score(trace: TraceRecord) -> float | None:
    if trace.invocation and trace.invocation.score is not None:
        return trace.invocation.score
    if trace.invocation and trace.invocation.success is not None:
        return 1.0 if trace.invocation.success else 0.0
    return None


def _validate_paired_traces(with_skill: TraceRecord, without_skill: TraceRecord) -> None:
    if with_skill.task_id != without_skill.task_id:
        raise ValueError(
            "CTA-lite traces must have matching task_id values: "
            f"{with_skill.task_id!r} != {without_skill.task_id!r}"
        )

    for key in _PAIRED_METADATA_KEYS:
        with_has_key = key in with_skill.metadata
        without_has_key = key in without_skill.metadata
        if with_has_key != without_has_key:
            raise ValueError(f"CTA-lite paired metadata key {key!r} is present on only one trace")
        if with_has_key and with_skill.metadata[key] != without_skill.metadata[key]:
            raise ValueError(
                f"CTA-lite paired metadata key {key!r} does not match: "
                f"{with_skill.metadata[key]!r} != {without_skill.metadata[key]!r}"
            )


def _normalize_artifact_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _artifact_paths(value: Any, *, field_name: str) -> set[str]:
    """Extract artifact paths from common JSON-compatible manifest shapes."""

    if isinstance(value, str):
        normalized = _normalize_artifact_path(value)
        return {normalized} if normalized else set()

    if isinstance(value, Mapping):
        if "files" in value:
            return _artifact_paths(value["files"], field_name=field_name)
        if isinstance(value.get("path"), str):
            return _artifact_paths(value["path"], field_name=field_name)
        return {
            normalized
            for key in value
            if (normalized := _normalize_artifact_path(str(key)))
        }

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        paths: set[str] = set()
        for item in value:
            paths.update(_artifact_paths(item, field_name=field_name))
        return paths

    raise ValueError(f"CTA-lite metadata field {field_name!r} is not a valid artifact manifest")


def _off_task_artifacts(with_skill: TraceRecord, without_skill: TraceRecord) -> list[str]:
    if not all(
        key in trace.metadata
        for trace in (with_skill, without_skill)
        for key in _WORKSPACE_EVIDENCE_KEYS
    ):
        return []

    with_before = _artifact_paths(
        with_skill.metadata["workspace_manifest_before"],
        field_name="workspace_manifest_before",
    )
    with_after = _artifact_paths(
        with_skill.metadata["workspace_manifest_after"],
        field_name="workspace_manifest_after",
    )
    without_before = _artifact_paths(
        without_skill.metadata["workspace_manifest_before"],
        field_name="workspace_manifest_before",
    )
    without_after = _artifact_paths(
        without_skill.metadata["workspace_manifest_after"],
        field_name="workspace_manifest_after",
    )
    expected = _artifact_paths(
        with_skill.metadata["expected_artifacts"],
        field_name="expected_artifacts",
    ) | _artifact_paths(
        without_skill.metadata["expected_artifacts"],
        field_name="expected_artifacts",
    )

    with_added = with_after - with_before
    without_added = without_after - without_before
    return sorted(with_added - without_added - expected)


def compare_traces(with_skill: TraceRecord, without_skill: TraceRecord) -> BehaviorDelta:
    _validate_paired_traces(with_skill, without_skill)

    with_score = _success_score(with_skill)
    without_score = _success_score(without_skill)
    success_delta = None if with_score is None or without_score is None else with_score - without_score

    with_cost = with_skill.invocation.cost if with_skill.invocation else None
    without_cost = without_skill.invocation.cost if without_skill.invocation else None
    cost_ratio = None
    if with_cost is not None and without_cost not in (None, 0):
        cost_ratio = with_cost / without_cost

    delta = BehaviorDelta(
        task_id=with_skill.task_id,
        with_skill_trace_id=with_skill.id,
        without_skill_trace_id=without_skill.id,
        success_delta=success_delta,
        cost_ratio=cost_ratio,
        tool_event_delta=_event_count(with_skill, "TOOL") - _event_count(without_skill, "TOOL"),
        write_event_delta=_event_count(with_skill, "WRITE") - _event_count(without_skill, "WRITE"),
        validation_event_delta=_event_count(with_skill, "VALIDATION") - _event_count(without_skill, "VALIDATION"),
        off_task_artifacts=_off_task_artifacts(with_skill, without_skill),
    )
    delta.labels = infer_behavior_labels(delta)
    return delta


def infer_behavior_labels(delta: BehaviorDelta) -> list[str]:
    labels: list[str] = []
    if delta.success_delta is not None and delta.success_delta > 0:
        labels.append("positive_success_delta")
    if delta.success_delta is not None and delta.success_delta < 0:
        labels.append("negative_success_delta")
    if delta.cost_ratio is not None and delta.cost_ratio >= 1.5 and (delta.success_delta is None or delta.success_delta <= 0):
        labels.append("cost_increase_without_gain")
    if delta.validation_event_delta < 0:
        labels.append("validation_suppressed")
    if delta.write_event_delta > 0:
        labels.append("additional_writes")
    return labels
