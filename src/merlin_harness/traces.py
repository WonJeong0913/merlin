"""File-backed trace storage for MVP runs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .agent_adapter import AgentContractError, validate_agent_run_contract
from .models import (
    AgentRunContract,
    AgentRunResult,
    AgentTraceEvidence,
    InvocationRecord,
    RawTraceReference,
    SkillInvocationEvent,
    TraceRecord,
    ValidationResult,
)


AGENT_TRACE_EVIDENCE_KEY = "agent_run_evidence"
AGENT_TRACE_EVIDENCE_SCHEMA_VERSION = 1
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _require_exact_keys(payload: dict[str, Any], keys: set[str], *, label: str) -> None:
    actual = set(payload)
    if actual != keys:
        raise AgentContractError(
            f"{label} keys must be exactly {sorted(keys)}; got {sorted(actual)}"
        )


def verify_raw_trace_reference(reference: RawTraceReference, *, raw_trace_root: str | Path) -> Path:
    """Resolve and hash-check a raw trace without loading it into normalized trace data."""

    if not isinstance(reference, RawTraceReference):
        raise AgentContractError("raw trace reference must be a RawTraceReference")
    if not reference.pointer or Path(reference.pointer).is_absolute():
        raise AgentContractError("raw trace pointer must be a non-empty relative path")
    if len(reference.sha256) != 64 or any(char not in "0123456789abcdef" for char in reference.sha256):
        raise AgentContractError("raw trace sha256 must be a lowercase SHA-256")

    root = Path(raw_trace_root).expanduser().resolve()
    target = (root / reference.pointer).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise AgentContractError("raw trace pointer escapes raw_trace_root") from exc
    if not target.is_file():
        raise AgentContractError(f"raw trace pointer does not resolve to a file: {reference.pointer}")
    actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual_sha256 != reference.sha256:
        raise AgentContractError(
            f"raw trace sha256 mismatch for {reference.pointer}: expected={reference.sha256} actual={actual_sha256}"
        )
    return target


def serialize_agent_run_evidence(result: AgentRunResult) -> dict[str, Any]:
    """Create the versioned evidence block stored with a normalized trace.

    The raw provider trace is intentionally represented only by a relative
    pointer and content hash.  Its payload must never be copied into this
    block, which keeps trace summaries inspectable without duplicating model or
    provider transcripts.
    """

    validate_agent_run_contract(result.contract)
    verify_raw_trace_reference(result.raw_trace, raw_trace_root=result.contract.raw_trace_root)
    return {
        "schema_version": AGENT_TRACE_EVIDENCE_SCHEMA_VERSION,
        "immutable": True,
        "contract": asdict(result.contract),
        "workspace_root": result.workspace_root,
        "raw_trace": asdict(result.raw_trace),
        "actual_invocation_evidence_complete": result.actual_invocation_evidence_complete,
        "selected_skill_ids": list(result.selected_skill_ids),
        "invocation_events": [asdict(event) for event in result.invocation_events],
    }


def deserialize_agent_run_evidence(payload: Any) -> AgentTraceEvidence:
    """Parse a stored evidence block strictly, without accepting silent defaults."""

    if not isinstance(payload, dict):
        raise AgentContractError("agent trace evidence must be an object")
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "immutable",
            "contract",
            "workspace_root",
            "raw_trace",
            "actual_invocation_evidence_complete",
            "selected_skill_ids",
            "invocation_events",
        },
        label="agent trace evidence",
    )
    if payload["schema_version"] != AGENT_TRACE_EVIDENCE_SCHEMA_VERSION:
        raise AgentContractError(
            f"unsupported agent trace evidence schema_version: {payload['schema_version']}"
        )
    if payload["immutable"] is not True:
        raise AgentContractError("agent trace evidence must declare immutable=true")
    if not isinstance(payload["contract"], dict):
        raise AgentContractError("agent trace evidence contract must be an object")
    if not isinstance(payload["raw_trace"], dict):
        raise AgentContractError("agent trace evidence raw_trace must be an object")
    if not isinstance(payload["workspace_root"], str) or not payload["workspace_root"].strip():
        raise AgentContractError("agent trace evidence workspace_root must be non-empty")
    if not isinstance(payload["actual_invocation_evidence_complete"], bool):
        raise AgentContractError("agent trace evidence actual_invocation_evidence_complete must be boolean")
    if not isinstance(payload["selected_skill_ids"], list) or not all(
        isinstance(skill_id, str) and skill_id for skill_id in payload["selected_skill_ids"]
    ):
        raise AgentContractError("agent trace evidence selected_skill_ids must be a list of non-empty strings")
    if len(set(payload["selected_skill_ids"])) != len(payload["selected_skill_ids"]):
        raise AgentContractError("agent trace evidence selected_skill_ids must be unique")
    if not isinstance(payload["invocation_events"], list):
        raise AgentContractError("agent trace evidence invocation_events must be a list")

    try:
        contract = AgentRunContract(**payload["contract"])
        raw_trace = RawTraceReference(**payload["raw_trace"])
        invocation_events = tuple(SkillInvocationEvent(**event) for event in payload["invocation_events"])
    except (TypeError, ValueError) as exc:
        raise AgentContractError(f"agent trace evidence schema is invalid: {exc}") from exc

    validate_agent_run_contract(contract)
    event_ids: set[str] = set()
    previous_sequence = -1
    for event in invocation_events:
        if not event.skill_id.strip() or not event.source.strip() or not event.event_id.strip():
            raise AgentContractError("agent trace invocation events require skill_id, source, and event_id")
        if event.event_kind not in {"skill_body_loaded", "provider_skill_invocation"}:
            raise AgentContractError(f"unsupported agent trace event_kind: {event.event_kind}")
        if event.event_id in event_ids:
            raise AgentContractError(f"duplicate agent trace invocation event_id: {event.event_id}")
        if event.sequence < 0 or event.sequence <= previous_sequence:
            raise AgentContractError("agent trace invocation event sequences must be strictly increasing")
        event_ids.add(event.event_id)
        previous_sequence = event.sequence

    return AgentTraceEvidence(
        contract=contract,
        workspace_root=payload["workspace_root"],
        raw_trace=raw_trace,
        actual_invocation_evidence_complete=payload["actual_invocation_evidence_complete"],
        selected_skill_ids=tuple(payload["selected_skill_ids"]),
        invocation_events=invocation_events,
        schema_version=payload["schema_version"],
    )


def validate_agent_trace_evidence(
    trace: TraceRecord,
    *,
    verify_raw_trace: bool = True,
) -> AgentTraceEvidence:
    """Validate a stored evidence block against its normalized trace envelope."""

    if AGENT_TRACE_EVIDENCE_KEY not in trace.metadata:
        raise AgentContractError("trace has no agent run evidence block")
    evidence = deserialize_agent_run_evidence(trace.metadata[AGENT_TRACE_EVIDENCE_KEY])
    if evidence.contract.task_id != trace.task_id:
        raise AgentContractError("agent trace evidence task_id does not match normalized trace")
    if evidence.contract.condition != trace.condition:
        raise AgentContractError("agent trace evidence condition does not match normalized trace")
    if Path(evidence.workspace_root).expanduser().resolve() != Path(evidence.contract.workspace_root).expanduser().resolve():
        raise AgentContractError("agent trace evidence workspace_root does not match contract workspace_root")
    trace_workspace = trace.metadata.get("workspace")
    if trace_workspace is not None and Path(str(trace_workspace)).expanduser().resolve() != Path(evidence.workspace_root).expanduser().resolve():
        raise AgentContractError("normalized trace workspace does not match agent evidence workspace_root")
    if trace.invocation is None:
        raise AgentContractError("agent trace evidence requires a normalized invocation record")
    if trace.invocation.task_id != trace.task_id:
        raise AgentContractError("normalized invocation task_id does not match normalized trace")
    if len(set(trace.invocation.provisioned_skill_ids)) != len(trace.invocation.provisioned_skill_ids):
        raise AgentContractError("normalized provisioned_skill_ids must be unique")
    if len(set(trace.invocation.oracle_skill_ids)) != len(trace.invocation.oracle_skill_ids):
        raise AgentContractError("normalized oracle_skill_ids must be unique")
    if tuple(trace.invocation.selected_skill_ids) != evidence.selected_skill_ids:
        raise AgentContractError("normalized selected_skill_ids do not match agent evidence")
    provisioned_skill_ids = set(trace.invocation.provisioned_skill_ids)
    invoked_skill_ids = {event.skill_id for event in evidence.invocation_events}
    if not invoked_skill_ids.issubset(provisioned_skill_ids):
        raise AgentContractError("actual invocation evidence includes skills outside normalized provisioned_skill_ids")
    if not trace.validation or [result.name for result in trace.validation] != [evidence.contract.verifier_id]:
        raise AgentContractError("normalized verifier record does not match agent run contract")
    if verify_raw_trace:
        verify_raw_trace_reference(evidence.raw_trace, raw_trace_root=evidence.contract.raw_trace_root)
    return evidence


class FileTraceStore:
    """Tiny trace store before a run database is justified."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def trace_path(self, trace_id: str) -> Path:
        if not isinstance(trace_id, str) or not _TRACE_ID_RE.fullmatch(trace_id):
            raise AgentContractError(
                "trace id must be a non-empty ASCII filename component containing only letters, digits, '.', '_', or '-'"
            )
        return self.root / f"{trace_id}.json"

    @staticmethod
    def _reject_symlink(path: Path) -> None:
        if path.is_symlink():
            raise AgentContractError(f"trace path cannot be a symlink: {path}")

    def save(self, trace: TraceRecord) -> Path:
        if AGENT_TRACE_EVIDENCE_KEY in trace.metadata:
            return self.save_immutable(trace)
        path = self.trace_path(trace.id)
        self._reject_symlink(path)
        path.write_text(json.dumps(asdict(trace), indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
        return path

    def save_immutable(self, trace: TraceRecord) -> Path:
        """Write an evidence-bearing agent trace once; incompatible rewrites fail."""

        validate_agent_trace_evidence(trace)
        path = self.trace_path(trace.id)
        serialized = json.dumps(asdict(trace), indent=2, sort_keys=True, default=_json_default)
        self._reject_symlink(path)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(serialized)
        except FileExistsError:
            self._reject_symlink(path)
            existing = path.read_text(encoding="utf-8")
            if existing != serialized:
                raise AgentContractError(f"immutable trace already exists with different content: {trace.id}")
            return path
        return path

    def load(self, trace_id: str) -> TraceRecord:
        path = self.trace_path(trace_id)
        self._reject_symlink(path)
        trace = self._from_dict(json.loads(path.read_text(encoding="utf-8")))
        if AGENT_TRACE_EVIDENCE_KEY in trace.metadata:
            validate_agent_trace_evidence(trace)
        return trace

    def list(self, task_id: str | None = None) -> list[TraceRecord]:
        paths = sorted(self.root.glob("*.json"))
        for path in paths:
            self._reject_symlink(path)
        records = [self._from_dict(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
        for record in records:
            if AGENT_TRACE_EVIDENCE_KEY in record.metadata:
                validate_agent_trace_evidence(record)
        if task_id is None:
            return records
        return [record for record in records if record.task_id == task_id]

    @staticmethod
    def _from_dict(data: dict[str, Any]) -> TraceRecord:
        invocation_data = data.get("invocation")
        invocation = InvocationRecord(**invocation_data) if invocation_data else None
        return TraceRecord(
            id=data["id"],
            task_id=data["task_id"],
            condition=data["condition"],
            events=list(data.get("events", [])),
            invocation=invocation,
            validation=[ValidationResult(**item) for item in data.get("validation", [])],
            failure_label=data.get("failure_label"),
            metadata=dict(data.get("metadata", {})),
        )
